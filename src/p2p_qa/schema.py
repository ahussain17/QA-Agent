from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .client import ApiClient
from .models import DiscoveryReport, OperationInfo, StepEvidence
from .utils import stable_json, walk

FALLBACK_ENDPOINTS = {
    ("GET", "/vendors"),
    ("POST", "/vendors"),
    ("POST", "/purchase-orders"),
    ("POST", "/purchase-orders/{id}/submit"),
    ("POST", "/purchase-orders/{id}/receive"),
    ("GET", "/purchase-orders/{id}"),
    ("POST", "/invoices"),
    ("POST", "/invoices/{id}/match"),
    ("POST", "/invoices/{id}/approve"),
    ("GET", "/vendors/{id}/exposure"),
}


@dataclass
class Operation:
    method: str
    path: str
    operation_id: str | None
    summary: str | None
    request_schema: dict[str, Any] | None
    responses: dict[str, Any]
    parameters: list[dict[str, Any]] = field(default_factory=list)


class SchemaExplorer:
    def __init__(self, client: ApiClient) -> None:
        self.client = client
        self.document: dict[str, Any] = {}
        self.schema_hash = ""
        self.operations: dict[tuple[str, str], Operation] = {}
        self.safe_data: dict[str, Any] = {}
        self.limitations: list[str] = []
        self.inspected_for_sku: list[str] = []
        self.sku_candidates: list[str] = []
        self.evidence: list[StepEvidence] = []

    def discover(self) -> DiscoveryReport:
        result = self.client.request("GET", "/openapi.json")
        self._record_discovery(
            result,
            "fetch_openapi",
            "Discover the API contract before constructing payloads.",
            "OpenAPI document returned",
        )
        if result.ok and isinstance(result.response_body, dict):
            self.document = result.response_body
            self.schema_hash = hashlib.sha256(stable_json(self.document).encode()).hexdigest()
            self._parse_operations()
        else:
            self.limitations.append(
                "OpenAPI unavailable; limited to conservatively probing documented GET endpoints."
            )
            self.operations = {
                key: Operation(key[0], key[1], None, None, None, {})
                for key in FALLBACK_ENDPOINTS
            }
            self.schema_hash = "unavailable"
        self._probe_safe_gets()
        self._discover_skus()
        status = "PASS" if self.document else ("INCONCLUSIVE" if result.status_code else "ERROR")
        return DiscoveryReport(
            status=status,
            operations=[
                OperationInfo(
                    method=op.method,
                    path=op.path,
                    operation_id=op.operation_id,
                    summary=op.summary,
                    required_fields=list((op.request_schema or {}).get("required", [])),
                )
                for op in sorted(self.operations.values(), key=lambda item: (item.path, item.method))
            ],
            data_sources=sorted(self.safe_data),
            limitations=self.limitations.copy(),
            inspected_for_sku=self.inspected_for_sku.copy(),
            sku_candidates=self.sku_candidates.copy(),
            evidence=self.evidence.copy(),
        )

    def _record_discovery(self, result: Any, name: str, purpose: str, expected: str) -> None:
        if result.error:
            verdict = "ERROR"
            interpretation = "The discovery request failed at the transport layer."
        elif result.ok:
            verdict = "PASS"
            interpretation = "The read-only discovery request succeeded."
        else:
            verdict = "INCONCLUSIVE"
            interpretation = "The read-only resource was unavailable or rejected."
        self.evidence.append(
            StepEvidence(
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
                observed_outcome=result.error or f"HTTP {result.status_code}",
                interpretation=interpretation,
                verdict=verdict,
            )
        )

    def resolve(self, value: Any, seen: set[str] | None = None) -> Any:
        seen = set() if seen is None else seen
        if isinstance(value, dict) and "$ref" in value:
            ref = value["$ref"]
            if not ref.startswith("#/") or ref in seen:
                return value
            node: Any = self.document
            for part in ref[2:].split("/"):
                node = node[part.replace("~1", "/").replace("~0", "~")]
            merged = copy.deepcopy(node)
            merged.update({k: v for k, v in value.items() if k != "$ref"})
            return self.resolve(merged, seen | {ref})
        if isinstance(value, dict):
            resolved = {k: self.resolve(v, seen) for k, v in value.items()}
            if isinstance(resolved.get("allOf"), list):
                merged: dict[str, Any] = {k: v for k, v in resolved.items() if k != "allOf"}
                properties: dict[str, Any] = dict(merged.get("properties", {}))
                required = list(merged.get("required", []))
                for part in resolved["allOf"]:
                    if isinstance(part, dict):
                        properties.update(part.get("properties", {}))
                        required.extend(part.get("required", []))
                        for key, item in part.items():
                            if key not in {"properties", "required"}:
                                merged.setdefault(key, item)
                if properties:
                    merged["properties"] = properties
                if required:
                    merged["required"] = list(dict.fromkeys(required))
                return merged
            return resolved
        if isinstance(value, list):
            return [self.resolve(v, seen) for v in value]
        return value

    def _parse_operations(self) -> None:
        for path, path_item in self.document.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for method in ("get", "post", "put", "patch", "delete"):
                raw = path_item.get(method)
                if not isinstance(raw, dict):
                    continue
                content = raw.get("requestBody", {}).get("content", {})
                media = content.get("application/json") or next(iter(content.values()), {})
                schema = self.resolve(media.get("schema")) if media.get("schema") else None
                self.operations[(method.upper(), path)] = Operation(
                    method=method.upper(),
                    path=path,
                    operation_id=raw.get("operationId"),
                    summary=raw.get("summary") or raw.get("description"),
                    request_schema=schema,
                    responses=self.resolve(raw.get("responses", {})),
                    parameters=self.resolve(path_item.get("parameters", []) + raw.get("parameters", [])),
                )

    def _probe_safe_gets(self) -> None:
        attempted: set[str] = set()
        for (method, path), _operation in sorted(self.operations.items()):
            if method != "GET" or "{" in path:
                continue
            attempted.add(path)
            result = self.client.request("GET", path)
            self._record_discovery(
                result,
                f"discover_{path.strip('/').replace('/', '_') or 'root'}",
                "Inspect a parameter-free read-only resource for usable seed data.",
                "Safe resource returned",
            )
            self.inspected_for_sku.append(f"GET {path}")
            if result.ok:
                self.safe_data[path] = result.response_body
        if "/vendors" not in attempted and ("GET", "/vendors") in self.operations:
            result = self.client.request("GET", "/vendors")
            self._record_discovery(
                result,
                "discover_vendors",
                "Inspect documented vendors for usable seed data.",
                "Vendor list returned",
            )
            self.inspected_for_sku.append("GET /vendors")
            if result.ok:
                self.safe_data["/vendors"] = result.response_body

    def _discover_skus(self) -> None:
        candidates: list[str] = []
        source = {"openapi": self.document, "safe_gets": self.safe_data}
        key_pattern = re.compile(r"(?i)(^|_)(sku|item_code|product_code|material_code|item_id|product_id)$")
        for path, value in walk(source):
            if path and key_pattern.search(path[-1]) and isinstance(value, (str, int)):
                text = str(value)
                if text and text not in candidates:
                    candidates.append(text)
            if path and path[-1] in {"enum", "examples"} and isinstance(value, list):
                joined = ".".join(path).lower()
                if any(word in joined for word in ("sku", "item", "product", "material")):
                    for item in value:
                        if isinstance(item, (str, int)) and str(item) not in candidates:
                            candidates.append(str(item))
        self.inspected_for_sku.extend(["OpenAPI schemas/examples/enums", "successful safe GET responses"])
        self.sku_candidates = candidates[:50]
        if not candidates:
            self.limitations.append(
                "No SKU/item identifier found in OpenAPI schemas, examples, enums, or safe GET data."
            )

    def operation(self, method: str, path: str) -> Operation | None:
        exact = self.operations.get((method.upper(), path))
        if exact:
            return exact
        for (candidate_method, candidate_path), operation in self.operations.items():
            regex = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", candidate_path) + "$"
            if candidate_method == method.upper() and re.match(regex, path):
                return operation
        return None

    def find_operation(self, method: str, *terms: str) -> Operation | None:
        matches = []
        for operation in self.operations.values():
            haystack = " ".join(
                filter(None, [operation.path, operation.operation_id, operation.summary])
            ).lower()
            if operation.method == method.upper() and all(term.lower() in haystack for term in terms):
                matches.append(operation)
        return min(matches, key=lambda item: len(item.path), default=None)

    def summary_for_agent(self) -> dict[str, Any]:
        return {
            "operations": [
                {
                    "method": op.method,
                    "path": op.path,
                    "operation_id": op.operation_id,
                    "summary": op.summary,
                    "request_schema": op.request_schema,
                }
                for op in self.operations.values()
            ],
            "safe_data": self.safe_data,
            "sku_candidates": self.sku_candidates,
            "limitations": self.limitations,
        }
