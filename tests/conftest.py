from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest


@pytest.fixture(autouse=True)
def no_live_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


@pytest.fixture
def mock_p2p_transport() -> httpx.MockTransport:
    state: dict[str, Any] = {
        "pos": {},
        "invoices": {},
        "invoice_keys": set(),
        "po_seq": 0,
        "inv_seq": 0,
    }
    openapi = {
        "openapi": "3.1.0",
        "info": {"title": "Mock P2P", "version": "1"},
        "paths": {
            "/vendors": {
                "get": {"operationId": "list_vendors", "responses": {"200": {"description": "ok"}}},
                "post": {
                    "operationId": "create_vendor",
                    "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/VendorCreate"}}}},
                    "responses": {"201": {"description": "created"}},
                },
            },
            "/items": {"get": {"operationId": "list_items", "responses": {"200": {"description": "ok"}}}},
            "/purchase-orders": {
                "get": {"operationId": "list_purchase_orders", "responses": {"200": {"description": "ok"}}},
                "post": {
                    "operationId": "create_purchase_order",
                    "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/POCreate"}}}},
                    "responses": {"201": {"description": "created"}},
                }
            },
            "/purchase-orders/{po_id}/submit": {"post": {"operationId": "submit_purchase_order", "responses": {"200": {"description": "ok"}}}},
            "/purchase-orders/{po_id}/receive": {
                "post": {
                    "operationId": "receive_purchase_order",
                    "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Receipt"}}}},
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/purchase-orders/{po_id}": {"get": {"operationId": "get_purchase_order", "responses": {"200": {"description": "ok"}}}},
            "/invoices": {
                "get": {"operationId": "list_invoices", "responses": {"200": {"description": "ok"}}},
                "post": {
                    "operationId": "create_invoice",
                    "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/InvoiceCreate"}}}},
                    "responses": {"201": {"description": "created"}},
                }
            },
            "/invoices/{invoice_id}/match": {"post": {"operationId": "match_invoice", "responses": {"200": {"description": "ok"}}}},
            "/invoices/{invoice_id}/approve": {"post": {"operationId": "approve_invoice", "responses": {"200": {"description": "ok"}}}},
            "/vendors/{vendor_id}/exposure": {"get": {"operationId": "get_vendor_exposure", "responses": {"200": {"description": "ok"}}}},
        },
        "components": {
            "schemas": {
                "VendorCreate": {
                    "type": "object",
                    "required": ["name", "active"],
                    "properties": {"name": {"type": "string"}, "active": {"type": "boolean"}},
                },
                "POCreate": {
                    "type": "object",
                    "required": ["vendor_id", "lines"],
                    "properties": {
                        "vendor_id": {"type": "string"},
                        "lines": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["sku", "quantity", "unit_price"],
                                "properties": {
                                    "sku": {"type": "string", "enum": ["STONE-1"]},
                                    "quantity": {"type": "number"},
                                    "unit_price": {"type": "number"},
                                },
                            },
                        },
                    },
                },
                "Receipt": {
                    "type": "object",
                    "required": ["lines"],
                    "properties": {
                        "lines": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["sku", "quantity"],
                                "properties": {"sku": {"type": "string"}, "quantity": {"type": "number"}},
                            },
                        }
                    },
                },
                "InvoiceCreate": {
                    "type": "object",
                    "required": ["vendor_id", "po_id", "invoice_number", "amount"],
                    "properties": {
                        "vendor_id": {"type": "string"},
                        "po_id": {"type": "string"},
                        "invoice_number": {"type": "string"},
                        "amount": {"type": "number"},
                    },
                },
            }
        },
    }

    def body(request: httpx.Request) -> dict[str, Any]:
        return json.loads(request.content.decode(), parse_float=Decimal) if request.content else {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if path == "/openapi.json":
            return httpx.Response(200, json=openapi)
        if method == "GET" and path == "/vendors":
            return httpx.Response(200, json=[{"id": "v-active", "active": True}, {"id": "v-inactive", "active": False}])
        if method == "GET" and path == "/items":
            return httpx.Response(200, json=[{"sku": "STONE-1"}])
        if method == "POST" and path == "/vendors":
            payload = body(request)
            return httpx.Response(201, json={"id": "v-new", **payload})
        if method == "POST" and path == "/purchase-orders":
            payload = body(request)
            if payload["vendor_id"] == "v-inactive":
                return httpx.Response(409, json={"detail": "inactive vendor"})
            state["po_seq"] += 1
            po_id = f"po-{state['po_seq']}"
            line = payload["lines"][0]
            state["pos"][po_id] = {
                "id": po_id,
                "vendor_id": payload["vendor_id"],
                "ordered": Decimal(str(line["quantity"])),
                "received": Decimal("0"),
                "price": Decimal(str(line["unit_price"])),
                "sku": line["sku"],
                "status": "draft",
            }
            return httpx.Response(201, json={"id": po_id, "lines": [{"id": f"{po_id}-line", **line}]})
        if method == "GET" and path == "/purchase-orders":
            return httpx.Response(200, json=[
                {"id": po["id"], "vendor_id": po["vendor_id"], "status": po["status"]}
                for po in state["pos"].values()
            ])
        match = __import__("re").match(r"/purchase-orders/([^/]+)(?:/(submit|receive))?$", path)
        if match:
            po = state["pos"].get(match.group(1))
            if not po:
                return httpx.Response(404, json={"detail": "not found"})
            action = match.group(2)
            if method == "GET" and action is None:
                return httpx.Response(200, json={**po, "ordered": str(po["ordered"]), "received": str(po["received"]), "price": str(po["price"])})
            if method == "POST" and action == "submit":
                po["status"] = "submitted"
                return httpx.Response(200, json={"status": "submitted"})
            if method == "POST" and action == "receive":
                qty = Decimal(str(body(request)["lines"][0]["quantity"]))
                if po["received"] + qty > po["ordered"]:
                    return httpx.Response(409, json={"detail": "quantity exceeds ordered"})
                po["received"] += qty
                return httpx.Response(200, json={"received_quantity": str(po["received"])})
        if method == "POST" and path == "/invoices":
            payload = body(request)
            key = (payload["vendor_id"], payload["invoice_number"])
            if key in state["invoice_keys"]:
                return httpx.Response(409, json={"detail": "duplicate invoice"})
            po = state["pos"][payload["po_id"]]
            if Decimal(str(payload["amount"])) > po["received"] * po["price"]:
                return httpx.Response(422, json={"detail": "invoice exceeds received value"})
            state["inv_seq"] += 1
            inv_id = f"inv-{state['inv_seq']}"
            state["invoices"][inv_id] = {**payload, "id": inv_id, "matched": False, "approved": False}
            state["invoice_keys"].add(key)
            return httpx.Response(201, json={"id": inv_id})
        if method == "GET" and path == "/invoices":
            return httpx.Response(200, json=[
                {
                    "id": invoice["id"],
                    "vendor_id": invoice["vendor_id"],
                    "invoice_number": invoice["invoice_number"],
                    "status": "approved" if invoice["approved"] else "created",
                }
                for invoice in state["invoices"].values()
            ])
        match = __import__("re").match(r"/invoices/([^/]+)/(match|approve)$", path)
        if match:
            invoice = state["invoices"][match.group(1)]
            po = state["pos"][invoice["po_id"]]
            if match.group(2) == "match":
                if po["received"] < po["ordered"]:
                    return httpx.Response(200, json={"status": "partial_match", "partial": True})
                invoice["matched"] = True
                return httpx.Response(200, json={"status": "matched"})
            if not invoice["matched"]:
                return httpx.Response(409, json={"detail": "invoice not matched"})
            invoice["approved"] = True
            amount = str(invoice["amount"])
            return httpx.Response(200, json={"status": "approved", "journal": {"lines": [{"debit": amount, "credit": "0"}, {"debit": "0", "credit": amount}]}})
        exposure = __import__("re").match(r"/vendors/([^/]+)/exposure$", path)
        if method == "GET" and exposure:
            total = sum((Decimal(str(i["amount"])) for i in state["invoices"].values() if i["vendor_id"] == exposure.group(1) and i["approved"]), Decimal("0"))
            return httpx.Response(200, json={"exposure": str(total)})
        return httpx.Response(404, json={"detail": "unknown"})

    return httpx.MockTransport(handler)

