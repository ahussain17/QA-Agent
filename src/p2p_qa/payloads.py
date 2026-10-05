from __future__ import annotations

from typing import Any
from decimal import Decimal


class PayloadFactory:
    """Schema-constrained payload synthesis. It emits only properties present in OpenAPI."""

    def build(self, schema: dict[str, Any] | None, context: dict[str, Any]) -> dict[str, Any]:
        if not schema:
            raise ValueError("request schema unavailable; refusing to invent payload fields")
        result = self._value(schema, context, "root")
        if not isinstance(result, dict):
            raise ValueError("top-level request schema is not an object")
        return result

    def repair(
        self,
        schema: dict[str, Any] | None,
        payload: dict[str, Any],
        error: Any,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not schema:
            return None
        fresh = self.build(schema, context)
        # Validation details can identify a missing schema property; all values still come from the schema.
        missing: set[str] = set()
        details = error.get("detail", error) if isinstance(error, dict) else error
        if isinstance(details, list):
            for item in details:
                if isinstance(item, dict) and item.get("type") in {"missing", "value_error.missing"}:
                    loc = item.get("loc", [])
                    if loc:
                        missing.add(str(loc[-1]))
        repaired = dict(payload)
        repaired.update({key: value for key, value in fresh.items() if key in missing or key not in repaired})
        return repaired if repaired != payload else None

    def _value(self, schema: dict[str, Any], context: dict[str, Any], name: str) -> Any:
        if "const" in schema:
            return schema["const"]
        if "example" in schema:
            return schema["example"]
        if "default" in schema:
            return schema["default"]
        if schema.get("enum"):
            return self._enum(schema["enum"], name, context)
        if "oneOf" in schema:
            return self._value(schema["oneOf"][0], context, name)
        if "anyOf" in schema:
            options = [item for item in schema["anyOf"] if item.get("type") != "null"]
            return self._value((options or schema["anyOf"])[0], context, name)
        kind = schema.get("type")
        if kind == "object" or "properties" in schema:
            properties = schema.get("properties", {})
            required = set(schema.get("required", []))
            result = {}
            for key, child in properties.items():
                contextual = self._context_value(key, context)
                if contextual is not None:
                    result[key] = self._coerce_context(child, contextual)
                elif key in required:
                    result[key] = self._value(child, context, key)
            return result
        if kind == "array":
            count = max(int(schema.get("minItems", 1)), 1)
            return [self._value(schema.get("items", {}), context, name) for _ in range(count)]
        contextual = self._context_value(name, context)
        if contextual is not None:
            return contextual
        if kind in {"number", "integer"}:
            minimum = schema.get("minimum", schema.get("exclusiveMinimum", 0))
            value = max(minimum, 1)
            return int(value) if kind == "integer" else Decimal(str(value))
        if kind == "boolean":
            return True
        if schema.get("format") == "email":
            return f"{context.get('run_id', 'qa')}@example.invalid"
        return str(context.get("run_id", "qa"))

    def _coerce_context(self, schema: dict[str, Any], value: Any) -> Any:
        if schema.get("enum"):
            for candidate in schema["enum"]:
                if str(candidate).lower() == str(value).lower():
                    return candidate
            return schema["enum"][0]
        kind = schema.get("type")
        if kind == "integer":
            return int(Decimal(str(value)))
        if kind == "number":
            return Decimal(str(value))
        if kind == "boolean":
            return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "active"}
        return value

    def _context_value(self, name: str, context: dict[str, Any]) -> Any:
        normalized = name.lower().replace("-", "_")
        aliases = {
            "vendor_id": {"vendor_id", "vendor", "supplier_id"},
            "po_id": {"po_id", "purchase_order_id", "purchase_order"},
            "invoice_id": {"invoice_id"},
            "line_id": {"line_id", "po_line_id", "purchase_order_line_id", "item_line_id"},
            "invoice_number": {"invoice_number", "invoice_no", "number"},
            "sku": {"sku", "item_code", "product_code", "material_code", "item_id", "product_id"},
            "quantity": {"quantity", "qty", "ordered_quantity", "received_quantity", "quantity_received"},
            "unit_price": {"unit_price", "price", "agreed_unit_price", "unit_cost"},
            "amount": {"amount", "total", "total_amount", "invoice_amount", "gross_amount"},
            "name": {"name", "vendor_name", "supplier_name"},
            "code": {"code", "vendor_code", "supplier_code"},
            "status": {"status", "vendor_status"},
            "active": {"active", "is_active", "enabled"},
            "email": {"email", "contact_email"},
            "description": {"description", "memo", "note", "notes"},
        }
        for canonical, names in aliases.items():
            if normalized in names and canonical in context:
                return context[canonical]
        return context.get(normalized)

    @staticmethod
    def _enum(values: list[Any], name: str, context: dict[str, Any]) -> Any:
        desired = str(context.get(name, context.get(name.lower(), ""))).lower()
        for value in values:
            if str(value).lower() == desired:
                return value
        return values[0]
