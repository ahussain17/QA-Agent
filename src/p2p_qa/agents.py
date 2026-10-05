from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .client import ApiClient, ApiResult
from .judge import InvariantJudge
from .llm import Reasoner
from .models import AssertionResult, ControlResult, HappyPathReport, StepEvidence
from .payloads import PayloadFactory
from .schema import Operation, SchemaExplorer
from .utils import find_first


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "data", "results", "vendors", "records"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
    return []


def _resource_id(value: Any, kind: str) -> Any:
    aliases = {
        "vendor": ["vendor_id", "supplier_id", "id"],
        "po": ["purchase_order_id", "po_id", "id"],
        "invoice": ["invoice_id", "id"],
        "line": ["line_id", "po_line_id", "id"],
    }
    return find_first(value, aliases[kind])


def _is_active(record: dict[str, Any]) -> bool:
    active = find_first(record, ["active", "is_active", "enabled"])
    status = find_first(record, ["status", "vendor_status"])
    if active is not None:
        return active is True or str(active).lower() in {"true", "1", "yes", "active", "enabled"}
    return status is None or str(status).lower() in {"active", "enabled", "approved"}


def _path(operation: Operation, resource_id: Any | None = None) -> str:
    if "{" not in operation.path:
        return operation.path
    if resource_id is None:
        raise ValueError(f"resource id required for {operation.path}")
    return re.sub(r"\{[^}]+\}", str(resource_id), operation.path)


def _step(
    result: ApiResult,
    name: str,
    purpose: str,
    expected: str,
    interpretation: str,
    verdict: str,
    assertions: list[AssertionResult] | None = None,
    attempt: int = 1,
) -> StepEvidence:
    observed = result.error or f"HTTP {result.status_code}"
    return StepEvidence(
        step_name=name,
        timestamp=result.timestamp,
        purpose=purpose,
        method=result.method,
        url=result.url,
        request_body=result.request_body,
        status_code=result.status_code,
        response_body=result.response_body,
        duration_ms=result.duration_ms,
        expected_outcome=expected,
        observed_outcome=observed,
        interpretation=interpretation,
        deterministic_assertions=assertions or [],
        verdict=verdict,
        attempt=attempt,
    )


class AdaptiveRequester:
    def __init__(
        self,
        client: ApiClient,
        explorer: SchemaExplorer,
        reasoner: Reasoner,
        factory: PayloadFactory,
    ) -> None:
        self.client = client
        self.explorer = explorer
        self.reasoner = reasoner
        self.factory = factory

    def request(
        self,
        operation: Operation,
        path: str,
        payload: dict[str, Any] | None,
        context: dict[str, Any],
        *,
        name: str,
        purpose: str,
        expected: str,
    ) -> tuple[ApiResult, list[StepEvidence], dict[str, Any] | None]:
        steps: list[StepEvidence] = []
        current = payload
        for attempt in range(1, 4):  # initial request + at most two repairs
            result = self.client.request(operation.method, path, json=current)
            verdict = "PASS" if result.ok else ("ERROR" if result.status_code is None else "FAIL")
            steps.append(
                _step(
                    result,
                    name,
                    purpose,
                    expected,
                    "Request succeeded." if result.ok else "Request did not meet the positive expectation.",
                    verdict,
                    attempt=attempt,
                )
            )
            if result.ok or result.status_code is None or result.status_code >= 500 or attempt == 3:
                return result, steps, current
            if result.status_code < 400 or result.status_code >= 500 or current is None:
                return result, steps, current
            repaired = self.factory.repair(
                operation.request_schema, current, result.response_body, context
            )
            if repaired is None or repaired == current:
                repaired = self.reasoner.repair(
                    {
                        "method": operation.method,
                        "path": operation.path,
                        "request_schema": operation.request_schema,
                    },
                    current,
                    result.response_body,
                )
                if repaired is not None and not self._schema_constrained(repaired, operation.request_schema):
                    repaired = None
            if repaired is None or repaired == current:
                return result, steps, current
            current = repaired
        return result, steps, current

    @staticmethod
    def _schema_constrained(payload: dict[str, Any], schema: dict[str, Any] | None) -> bool:
        if not schema:
            return False
        allowed = set(schema.get("properties", {}))
        return set(payload).issubset(allowed)


@dataclass
class WorkflowState:
    vendor: dict[str, Any] | None = None
    vendor_id: Any = None
    po_id: Any = None
    po_response: Any = None
    invoice_id: Any = None
    invoice_payload: dict[str, Any] | None = None
    approval_response: Any = None
    steps: list[StepEvidence] = field(default_factory=list)
    error: str | None = None


class BaseAgent:
    def __init__(
        self,
        client: ApiClient,
        explorer: SchemaExplorer,
        reasoner: Reasoner,
        run_id: str,
    ) -> None:
        self.client = client
        self.explorer = explorer
        self.reasoner = reasoner
        self.run_id = run_id
        self.factory = PayloadFactory()
        self.requester = AdaptiveRequester(client, explorer, reasoner, self.factory)
        self.judge = InvariantJudge()

    def _op(self, method: str, path: str, *terms: str) -> Operation | None:
        return self.explorer.operations.get((method, path)) or self.explorer.find_operation(method, *terms)

    def _vendor(self, active: bool, suffix: str) -> tuple[dict[str, Any] | None, list[StepEvidence]]:
        vendors = _records(self.explorer.safe_data.get("/vendors"))
        selected = next((v for v in vendors if _is_active(v) is active), None)
        if selected:
            return selected, []
        op = self._op("POST", "/vendors", "vendor")
        if not op or not op.request_schema:
            return None, []
        context = {
            "run_id": f"{self.run_id}-{suffix}",
            "name": f"{self.run_id}-{suffix}",
            "code": f"{self.run_id}-{suffix}",
            "status": "active" if active else "inactive",
            "active": active,
            "email": f"{self.run_id}-{suffix}@example.invalid",
        }
        try:
            payload = self.factory.build(op.request_schema, context)
        except ValueError:
            return None, []
        result, steps, _ = self.requester.request(
            op,
            op.path,
            payload,
            context,
            name="create_vendor",
            purpose=f"Create an isolated {'active' if active else 'inactive'} vendor.",
            expected="Vendor created",
        )
        return (result.response_body if result.ok and isinstance(result.response_body, dict) else None), steps

    def _exposure(self, vendor_id: Any) -> tuple[Any, list[StepEvidence]]:
        op = self._op("GET", "/vendors/{id}/exposure", "vendor", "exposure")
        if not op or vendor_id is None:
            return None, []
        result = self.client.request("GET", _path(op, vendor_id))
        step = _step(
            result,
            "observe_vendor_exposure",
            "Observe payable exposure for before/after mutation verification.",
            "Exposure is observable",
            "Exposure captured." if result.ok else "Exposure unavailable.",
            "PASS" if result.ok else "INCONCLUSIVE",
        )
        return (result.response_body if result.ok else None), [step]

    def _detail(self, po_id: Any) -> tuple[Any, list[StepEvidence]]:
        op = self._op("GET", "/purchase-orders/{id}", "purchase", "order")
        if not op or po_id is None:
            return None, []
        result = self.client.request("GET", _path(op, po_id))
        return (result.response_body if result.ok else None), [
            _step(
                result,
                "observe_purchase_order",
                "Observe PO receipt state.",
                "PO state is observable",
                "PO captured." if result.ok else "PO unavailable.",
                "PASS" if result.ok else "INCONCLUSIVE",
            )
        ]

    def setup(
        self,
        suffix: str,
        *,
        ordered_qty: Decimal = Decimal("2"),
        received_qty: Decimal | None = Decimal("2"),
        invoice_amount: Decimal | None = Decimal("20"),
        do_match: bool = False,
        do_approve: bool = False,
        vendor: dict[str, Any] | None = None,
    ) -> WorkflowState:
        state = WorkflowState()
        if vendor is None:
            vendor, steps = self._vendor(True, suffix)
            state.steps.extend(steps)
        if not vendor:
            state.error = "No usable active vendor could be selected or created."
            return state
        state.vendor = vendor
        state.vendor_id = _resource_id(vendor, "vendor")
        if state.vendor_id is None:
            state.error = "Selected vendor has no discoverable identifier."
            return state
        if not self.explorer.sku_candidates:
            state.error = "No valid SKU/item identifier was discovered; dependent workflow was not attempted."
            return state
        sku = self.explorer.sku_candidates[0]
        po_op = self._op("POST", "/purchase-orders", "purchase", "order")
        if not po_op or not po_op.request_schema:
            state.error = "Purchase-order request schema unavailable."
            return state
        context = {
            "run_id": f"{self.run_id}-{suffix}",
            "vendor_id": state.vendor_id,
            "sku": sku,
            "quantity": ordered_qty,
            "unit_price": Decimal("10"),
            "description": f"QA {suffix}",
        }
        try:
            payload = self.factory.build(po_op.request_schema, context)
        except ValueError as exc:
            state.error = str(exc)
            return state
        result, steps, _ = self.requester.request(
            po_op, po_op.path, payload, context,
            name="create_purchase_order", purpose="Create an isolated PO from discovered schema and SKU.",
            expected="Draft PO created",
        )
        state.steps.extend(steps)
        if not result.ok:
            state.error = "PO creation failed."
            return state
        state.po_response = result.response_body
        state.po_id = _resource_id(result.response_body, "po")
        if state.po_id is None:
            state.error = "PO response had no discoverable identifier."
            return state
        line_id = _resource_id(result.response_body, "line")
        submit = self._op("POST", "/purchase-orders/{id}/submit", "purchase", "submit")
        if not submit:
            state.error = "PO submit operation unavailable."
            return state
        submit_payload = self.factory.build(submit.request_schema, context) if submit.request_schema else None
        result, steps, _ = self.requester.request(
            submit, _path(submit, state.po_id), submit_payload, context,
            name="submit_purchase_order", purpose="Move the PO from draft to submitted state.",
            expected="PO submitted",
        )
        state.steps.extend(steps)
        if not result.ok:
            state.error = "PO submission failed."
            return state
        if received_qty is not None:
            receive = self._op("POST", "/purchase-orders/{id}/receive", "purchase", "receive")
            if not receive or not receive.request_schema:
                state.error = "Receipt request schema unavailable."
                return state
            receipt_context = {**context, "quantity": received_qty, "line_id": line_id, "po_id": state.po_id}
            receipt_payload = self.factory.build(receive.request_schema, receipt_context)
            result, steps, _ = self.requester.request(
                receive, _path(receive, state.po_id), receipt_payload, receipt_context,
                name="receive_goods", purpose="Record the intended goods receipt quantity.",
                expected="Goods receipt recorded",
            )
            state.steps.extend(steps)
            if not result.ok:
                state.error = "Goods receipt failed."
                return state
        if invoice_amount is None:
            return state
        invoice = self._op("POST", "/invoices", "invoice")
        if not invoice or not invoice.request_schema:
            state.error = "Invoice request schema unavailable."
            return state
        invoice_context = {
            **context,
            "po_id": state.po_id,
            "line_id": line_id,
            "amount": invoice_amount,
            "invoice_number": f"{self.run_id}-{suffix}-invoice",
        }
        state.invoice_payload = self.factory.build(invoice.request_schema, invoice_context)
        result, steps, _ = self.requester.request(
            invoice, invoice.path, state.invoice_payload, invoice_context,
            name="create_invoice", purpose="Create an invoice against the isolated PO.",
            expected="Invoice created",
        )
        state.steps.extend(steps)
        if not result.ok:
            state.error = "Invoice creation failed."
            return state
        state.invoice_id = _resource_id(result.response_body, "invoice")
        if state.invoice_id is None:
            state.error = "Invoice response had no discoverable identifier."
            return state
        if do_match:
            match = self._op("POST", "/invoices/{id}/match", "invoice", "match")
            if not match:
                state.error = "Invoice match operation unavailable."
                return state
            match_payload = self.factory.build(match.request_schema, invoice_context) if match.request_schema else None
            result, steps, _ = self.requester.request(
                match, _path(match, state.invoice_id), match_payload, invoice_context,
                name="match_invoice", purpose="Run the three-way match.", expected="Invoice matched",
            )
            state.steps.extend(steps)
            if not result.ok:
                state.error = "Invoice match failed."
                return state
        if do_approve:
            approve = self._op("POST", "/invoices/{id}/approve", "invoice", "approve")
            if not approve:
                state.error = "Invoice approval operation unavailable."
                return state
            approve_payload = self.factory.build(approve.request_schema, invoice_context) if approve.request_schema else None
            result, steps, _ = self.requester.request(
                approve, _path(approve, state.invoice_id), approve_payload, invoice_context,
                name="approve_invoice", purpose="Approve matched invoice and inspect GL posting.",
                expected="Invoice approved and journal posted",
            )
            state.steps.extend(steps)
            if not result.ok:
                state.error = "Invoice approval failed."
                return state
            state.approval_response = result.response_body
        return state


class ExplorationAgent(BaseAgent):
    def run(self) -> HappyPathReport:
        plan = self.reasoner.plan(self.explorer.summary_for_agent())
        state = self.setup("happy", do_match=True, do_approve=True)
        if state.error:
            if not self.explorer.document and not self.explorer.safe_data:
                status = "ERROR"
            elif "No valid SKU" in state.error or "schema unavailable" in state.error:
                status = "INCONCLUSIVE"
            else:
                status = "FAIL"
            return HappyPathReport(
                status=status,
                steps=state.steps,
                plan=plan,
                resources={
                    "limitation": state.error,
                    "agent_explanation": self.reasoner.explain(
                        "Happy-path execution failure",
                        {"error": state.error, "steps": [step.model_dump(mode="json") for step in state.steps]},
                    ),
                },
            )
        return HappyPathReport(
            status="PASS",
            steps=state.steps,
            plan=plan,
            resources={
                "vendor_id": state.vendor_id,
                "purchase_order_id": state.po_id,
                "invoice_id": state.invoice_id,
                "approval": state.approval_response,
                "exact_boundary_received_value": "20",
                "exact_boundary_invoice_value": "20",
            },
        )


class AdversarialAgent(BaseAgent):
    def run(self) -> list[ControlResult]:
        results = [
            self._unmatched_approval(),
            self._excess_receipt(),
            self._overpayment(),
            self._inactive_vendor(),
            self._partial_receipt(),
            self._duplicate_invoice(),
            self._gl_balance(),
        ]
        for result in results:
            if result.status in {"BREACHED", "INCONCLUSIVE", "ERROR"}:
                result.explanation = self.reasoner.explain(
                    f"Control {result.rule} was {result.status}",
                    {
                        "deterministic_status": result.status,
                        "evidence": result.evidence,
                        "assertions": [item.model_dump(mode="json") for item in result.assertions],
                    },
                )
        return results

    def _inconclusive(self, rule: str, state: WorkflowState, detail: str | None = None) -> ControlResult:
        explanation = detail or state.error or "Required evidence unavailable."
        return ControlResult(rule=rule, status="INCONCLUSIVE", steps=state.steps, explanation=explanation)

    def _unmatched_approval(self) -> ControlResult:
        state = self.setup("unmatched")
        if state.error or state.invoice_id is None:
            return self._inconclusive("three_way_match_gate", state)
        before, before_steps = self._exposure(state.vendor_id)
        approve = self._op("POST", "/invoices/{id}/approve", "invoice", "approve")
        if not approve:
            return self._inconclusive("three_way_match_gate", state, "Approval operation unavailable.")
        result = self.client.request("POST", _path(approve, state.invoice_id))
        attack_step = _step(result, "approve_unmatched_invoice", "Attempt approval without matching.", "Approval rejected", "Negative control attempt.", "HELD" if 400 <= (result.status_code or 0) < 500 else "BREACHED")
        after, after_steps = self._exposure(state.vendor_id)
        return self.judge.rejection_control(
            "three_way_match_gate", result.status_code, before, after,
            steps=state.steps + before_steps + [attack_step] + after_steps,
        )

    def _excess_receipt(self) -> ControlResult:
        state = self.setup("over-receipt", received_qty=None, invoice_amount=None)
        if state.error or state.po_id is None:
            return self._inconclusive("excess_goods_receipt", state)
        before, before_steps = self._detail(state.po_id)
        op = self._op("POST", "/purchase-orders/{id}/receive", "purchase", "receive")
        if not op or not op.request_schema:
            return self._inconclusive("excess_goods_receipt", state, "Receipt schema unavailable.")
        context = {"run_id": self.run_id, "quantity": Decimal("3"), "po_id": state.po_id, "sku": self.explorer.sku_candidates[0]}
        payload = self.factory.build(op.request_schema, context)
        result = self.client.request("POST", _path(op, state.po_id), json=payload)
        attack = _step(result, "receive_excess_quantity", "Attempt to receive 3 units against 2 ordered.", "Receipt rejected", "Negative control attempt.", "HELD" if 400 <= (result.status_code or 0) < 500 else "BREACHED")
        after, after_steps = self._detail(state.po_id)
        return self.judge.rejection_control(
            "excess_goods_receipt", result.status_code, before, after,
            steps=state.steps + before_steps + [attack] + after_steps,
        )

    def _overpayment(self) -> ControlResult:
        # Partial receipt establishes received value 1 * 10 = 10; attack uses the smallest currency increment.
        state = self.setup("overpay", received_qty=Decimal("1"), invoice_amount=None)
        if state.error:
            return self._inconclusive("overpayment_protection", state)
        before, before_steps = self._exposure(state.vendor_id)
        op = self._op("POST", "/invoices", "invoice")
        if not op or not op.request_schema:
            return self._inconclusive("overpayment_protection", state, "Invoice schema unavailable.")
        context = {
            "run_id": self.run_id, "vendor_id": state.vendor_id, "po_id": state.po_id,
            "sku": self.explorer.sku_candidates[0], "quantity": Decimal("1"), "unit_price": Decimal("10"),
            "amount": Decimal("10.01"), "invoice_number": f"{self.run_id}-overpay-invoice",
        }
        payload = self.factory.build(op.request_schema, context)
        create = self.client.request("POST", op.path, json=payload)
        steps = state.steps + before_steps + [_step(create, "create_over_value_invoice", "Attempt invoice one cent above received value.", "Invoice cannot become payable", "Overpayment attack initiated.", "PASS" if create.ok else "HELD")]
        approved: bool | None = False if create.status_code and 400 <= create.status_code < 500 else None
        if create.ok:
            invoice_id = _resource_id(create.response_body, "invoice")
            if invoice_id is None:
                approved = None
            else:
                match = self._op("POST", "/invoices/{id}/match", "invoice", "match")
                approve = self._op("POST", "/invoices/{id}/approve", "invoice", "approve")
                match_result = self.client.request("POST", _path(match, invoice_id)) if match else None
                if match_result:
                    steps.append(_step(match_result, "match_over_value_invoice", "Attempt to match over-value invoice.", "Match rejected", "Overpayment path continued.", "FAIL" if match_result.ok else "HELD"))
                if approve and match_result and match_result.ok:
                    approval = self.client.request("POST", _path(approve, invoice_id))
                    steps.append(_step(approval, "approve_over_value_invoice", "Attempt to approve over-value invoice.", "Approval rejected", "Final overpayment attempt.", "BREACHED" if approval.ok else "HELD"))
                    approved = approval.ok
                else:
                    approved = False
        after, after_steps = self._exposure(state.vendor_id)
        steps.extend(after_steps)
        return self.judge.overpayment(Decimal("10"), Decimal("10.01"), approved, before, after, steps=steps)

    def _inactive_vendor(self) -> ControlResult:
        vendor, vendor_steps = self._vendor(False, "inactive")
        empty = WorkflowState(steps=vendor_steps)
        if not vendor:
            return self._inconclusive("inactive_vendor_gate", empty, "No inactive vendor could be selected or created.")
        vendor_id = _resource_id(vendor, "vendor")
        before, before_steps = self._exposure(vendor_id)
        op = self._op("POST", "/purchase-orders", "purchase", "order")
        if not op or not op.request_schema or not self.explorer.sku_candidates:
            return self._inconclusive("inactive_vendor_gate", empty, "PO schema or SKU unavailable.")
        context = {"run_id": self.run_id, "vendor_id": vendor_id, "sku": self.explorer.sku_candidates[0], "quantity": Decimal("1"), "unit_price": Decimal("10")}
        payload = self.factory.build(op.request_schema, context)
        result = self.client.request("POST", op.path, json=payload)
        attack = _step(result, "create_po_inactive_vendor", "Attempt PO creation for inactive vendor.", "PO rejected", "Negative control attempt.", "HELD" if 400 <= (result.status_code or 0) < 500 else "BREACHED")
        after, after_steps = self._exposure(vendor_id)
        return self.judge.rejection_control(
            "inactive_vendor_gate", result.status_code, before, after,
            steps=vendor_steps + before_steps + [attack] + after_steps,
        )

    def _partial_receipt(self) -> ControlResult:
        state = self.setup("partial", received_qty=Decimal("1"), invoice_amount=Decimal("10"))
        if state.error:
            return self._inconclusive("partial_receipt_disclosure", state)
        match = self._op("POST", "/invoices/{id}/match", "invoice", "match")
        if not match:
            return self._inconclusive("partial_receipt_disclosure", state, "Match operation unavailable.")
        result = self.client.request("POST", _path(match, state.invoice_id))
        step = _step(result, "match_partial_receipt", "Match invoice against partially received PO.", "Explicit partial/mismatch state", "Partial-receipt control observed.", "PASS" if result.status_code else "ERROR")
        return self.judge.partial_receipt(Decimal("2"), Decimal("1"), result.status_code, result.response_body, steps=state.steps + [step])

    def _duplicate_invoice(self) -> ControlResult:
        state = self.setup("duplicate")
        if state.error or not state.invoice_payload:
            return self._inconclusive("duplicate_invoice_protection", state)
        before, before_steps = self._exposure(state.vendor_id)
        op = self._op("POST", "/invoices", "invoice")
        if not op:
            return self._inconclusive("duplicate_invoice_protection", state, "Invoice operation unavailable.")
        result = self.client.request("POST", op.path, json=state.invoice_payload)
        attack = _step(result, "create_duplicate_invoice", "Repeat same vendor and invoice number.", "Duplicate rejected", "Duplicate control attempt.", "HELD" if 400 <= (result.status_code or 0) < 500 else "BREACHED")
        after, after_steps = self._exposure(state.vendor_id)
        return self.judge.rejection_control(
            "duplicate_invoice_protection", result.status_code, before, after,
            steps=state.steps + before_steps + [attack] + after_steps,
        )

    def _gl_balance(self) -> ControlResult:
        state = self.setup("gl", do_match=True, do_approve=True)
        if state.error:
            return self._inconclusive("gl_balance", state)
        evidence = state.approval_response
        if not any(word in str(evidence).lower() for word in ("debit", "credit")):
            ledger = self.explorer.find_operation("GET", "journal") or self.explorer.find_operation("GET", "ledger")
            if ledger and state.invoice_id is not None:
                journal_id = find_first(
                    state.approval_response,
                    ["journal_id", "ledger_entry_id", "posting_id"],
                )
                result = self.client.request("GET", _path(ledger, journal_id or state.invoice_id))
                state.steps.append(_step(result, "observe_journal", "Fetch journal evidence discovered in OpenAPI.", "Journal lines observable", "Ledger/journal observation.", "PASS" if result.ok else "INCONCLUSIVE"))
                if result.ok:
                    evidence = result.response_body
        return self.judge.gl_balance(evidence, steps=state.steps)
