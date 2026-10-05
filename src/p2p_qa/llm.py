from __future__ import annotations

import json
import os
from typing import Any, Protocol


class Reasoner(Protocol):
    model_name: str

    def plan(self, discovery: dict[str, Any]) -> list[dict[str, str]]: ...
    def repair(self, operation: dict[str, Any], payload: dict[str, Any], error: Any) -> dict[str, Any] | None: ...
    def explain(self, title: str, evidence: dict[str, Any]) -> str: ...


class LocalReasoner:
    model_name = "deterministic-fallback"

    def plan(self, discovery: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {"step": "vendor", "reason": "Select an active seed vendor or create one from schema."},
            {"step": "purchase_order", "reason": "Create a schema-valid PO using a discovered SKU."},
            {"step": "submit", "reason": "Move the PO into a receivable state."},
            {"step": "receive", "reason": "Establish deterministic received value."},
            {"step": "invoice", "reason": "Exercise the exact received-value boundary."},
            {"step": "match", "reason": "Require PO/receipt/invoice agreement."},
            {"step": "approve", "reason": "Test payable creation and inspect GL evidence."},
        ]

    def repair(self, operation: dict[str, Any], payload: dict[str, Any], error: Any) -> dict[str, Any] | None:
        return None

    def explain(self, title: str, evidence: dict[str, Any]) -> str:
        return f"{title}: deterministic evidence was evaluated; no LLM explanation was available."


class OpenAIReasoner:
    """Small optional Responses API boundary; it never receives network or shell tools."""

    def __init__(self, model: str) -> None:
        from openai import OpenAI

        self.model_name = model
        self._client = OpenAI()

    def _json_response(self, instruction: str, payload: Any) -> Any:
        response = self._client.responses.create(
            model=self.model_name,
            instructions=(
                "You are a P2P QA planning assistant. Return compact valid JSON only. "
                "Never make monetary pass/fail decisions and never invent fields absent from the supplied schema."
            ),
            input=f"{instruction}\n\nINPUT:\n{json.dumps(payload, default=str)[:60000]}",
            store=False,
        )
        return json.loads(response.output_text)

    def plan(self, discovery: dict[str, Any]) -> list[dict[str, str]]:
        try:
            operations = discovery.get("operations", [])
            tools = [{
                "type": "function",
                "name": "inspect_operation",
                "description": "Read one discovered operation's request schema. This is local read-only metadata, not network access.",
                "parameters": {
                    "type": "object",
                    "properties": {"index": {"type": "integer", "minimum": 0}},
                    "required": ["index"],
                    "additionalProperties": False,
                },
                "strict": True,
            }]
            input_items: list[Any] = [{
                "role": "user",
                "content": (
                    "Create a JSON array of workflow steps, each with step and reason. "
                    "Use only these discovered operation briefs and inspect schemas when useful: "
                    + json.dumps([
                        {"index": index, "method": item.get("method"), "path": item.get("path"), "summary": item.get("summary")}
                        for index, item in enumerate(operations)
                    ])
                ),
            }]
            response = self._client.responses.create(
                model=self.model_name,
                instructions=(
                    "You are a P2P QA planner. Use only supplied local metadata. "
                    "Return compact valid JSON only and never make financial pass/fail decisions."
                ),
                tools=tools,
                input=input_items,
                store=False,
            )
            input_items += response.output
            called = False
            for item in response.output:
                if item.type == "function_call" and item.name == "inspect_operation":
                    called = True
                    index = int(json.loads(item.arguments)["index"])
                    output = operations[index] if 0 <= index < len(operations) else {"error": "index out of range"}
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": json.dumps(output, default=str),
                    })
            if called:
                response = self._client.responses.create(
                    model=self.model_name,
                    instructions="Return only the requested JSON workflow plan.",
                    tools=tools,
                    input=input_items,
                    store=False,
                )
            value = json.loads(response.output_text)
            return value if isinstance(value, list) else LocalReasoner().plan(discovery)
        except Exception:
            return LocalReasoner().plan(discovery)

    def repair(self, operation: dict[str, Any], payload: dict[str, Any], error: Any) -> dict[str, Any] | None:
        try:
            value = self._json_response(
                "Return a corrected JSON request object using only properties in request_schema, or null.",
                {"operation": operation, "failed_payload": payload, "validation_error": error},
            )
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    def explain(self, title: str, evidence: dict[str, Any]) -> str:
        try:
            response = self._client.responses.create(
                model=self.model_name,
                instructions="Explain QA evidence in at most two factual sentences. Do not change deterministic verdicts.",
                input=f"{title}\n{json.dumps(evidence, default=str)[:30000]}",
                store=False,
            )
            return response.output_text.strip()
        except Exception:
            return LocalReasoner().explain(title, evidence)


def make_reasoner() -> Reasoner:
    model = os.getenv("OPENAI_MODEL", "").strip()
    if model and os.getenv("OPENAI_API_KEY"):
        return OpenAIReasoner(model)
    return LocalReasoner()
