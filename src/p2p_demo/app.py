from __future__ import annotations

import os
from decimal import Decimal
from threading import RLock
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

Currency = Annotated[Decimal, Field(gt=0, decimal_places=2)]


class VendorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120, examples=["QA Building Supply"])
    code: str | None = Field(default=None, max_length=80)
    active: bool = True


class POLine(BaseModel):
    sku: str = Field(examples=["STONE-1"])
    quantity: Decimal = Field(gt=0, examples=["2"])
    unit_price: Currency = Field(examples=["10.00"])


class PurchaseOrderCreate(BaseModel):
    vendor_id: str
    lines: list[POLine] = Field(min_length=1)
    description: str | None = None


class ReceiptLine(BaseModel):
    sku: str = Field(examples=["STONE-1"])
    quantity: Decimal = Field(gt=0, examples=["1"])


class GoodsReceipt(BaseModel):
    lines: list[ReceiptLine] = Field(min_length=1)


class InvoiceCreate(BaseModel):
    vendor_id: str
    po_id: str
    invoice_number: str = Field(min_length=1, max_length=120)
    amount: Currency = Field(examples=["20.00"])


class DemoState:
    def __init__(self) -> None:
        self.lock = RLock()
        self.vendors: dict[str, dict[str, Any]] = {
            "vendor-active": {
                "id": "vendor-active",
                "name": "Acme Building Materials",
                "code": "ACME",
                "active": True,
                "status": "active",
            },
            "vendor-inactive": {
                "id": "vendor-inactive",
                "name": "Dormant Stone Supply",
                "code": "DORMANT",
                "active": False,
                "status": "inactive",
            },
        }
        self.items: dict[str, dict[str, Any]] = {
            "STONE-1": {"sku": "STONE-1", "description": "Cut limestone block", "active": True},
            "CEMENT-50": {"sku": "CEMENT-50", "description": "50 lb cement bag", "active": True},
        }
        # Seed records make the demo useful immediately after startup while still
        # leaving room for the QA agent to create its own isolated transactions.
        self.purchase_orders: dict[str, dict[str, Any]] = {
            "po-seed-1001": {
                "id": "po-seed-1001",
                "vendor_id": "vendor-active",
                "status": "submitted",
                "description": "Limestone for the lobby renovation",
                "lines": [
                    {
                        "id": "line-seed-1001",
                        "sku": "STONE-1",
                        "ordered_quantity": Decimal("12"),
                        "received_quantity": Decimal("0"),
                        "unit_price": Decimal("32.50"),
                    }
                ],
            },
            "po-seed-1002": {
                "id": "po-seed-1002",
                "vendor_id": "vendor-active",
                "status": "partially_received",
                "description": "Cement for the east foundation",
                "lines": [
                    {
                        "id": "line-seed-1002",
                        "sku": "CEMENT-50",
                        "ordered_quantity": Decimal("40"),
                        "received_quantity": Decimal("15"),
                        "unit_price": Decimal("8.75"),
                    }
                ],
            },
            "po-seed-1003": {
                "id": "po-seed-1003",
                "vendor_id": "vendor-active",
                "status": "received",
                "description": "Materials for the courtyard repair",
                "lines": [
                    {
                        "id": "line-seed-1003-a",
                        "sku": "STONE-1",
                        "ordered_quantity": Decimal("4"),
                        "received_quantity": Decimal("4"),
                        "unit_price": Decimal("32.50"),
                    },
                    {
                        "id": "line-seed-1003-b",
                        "sku": "CEMENT-50",
                        "ordered_quantity": Decimal("10"),
                        "received_quantity": Decimal("10"),
                        "unit_price": Decimal("8.75"),
                    },
                ],
            },
        }
        self.invoices: dict[str, dict[str, Any]] = {
            "invoice-seed-2001": {
                "id": "invoice-seed-2001",
                "vendor_id": "vendor-active",
                "po_id": "po-seed-1002",
                "invoice_number": "ACME-2026-2001",
                "amount": Decimal("131.25"),
                "status": "created",
                "matched": False,
            },
            "invoice-seed-2002": {
                "id": "invoice-seed-2002",
                "vendor_id": "vendor-active",
                "po_id": "po-seed-1003",
                "invoice_number": "ACME-2026-2002",
                "amount": Decimal("217.50"),
                "status": "approved",
                "matched": True,
            },
        }
        self.journals: dict[str, dict[str, Any]] = {
            "invoice-seed-2002": {
                "id": "journal-seed-3001",
                "invoice_id": "invoice-seed-2002",
                "lines": [
                    {
                        "account": "Construction Materials Expense",
                        "debit": "217.50",
                        "credit": "0.00",
                    },
                    {
                        "account": "Accounts Payable",
                        "debit": "0.00",
                        "credit": "217.50",
                    },
                ],
            }
        }


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _po_view(po: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": po["id"],
        "vendor_id": po["vendor_id"],
        "status": po["status"],
        "description": po.get("description"),
        "lines": [
            {
                "id": line["id"],
                "sku": line["sku"],
                "ordered_quantity": str(line["ordered_quantity"]),
                "received_quantity": str(line["received_quantity"]),
                "unit_price": _money(line["unit_price"]),
            }
            for line in po["lines"]
        ],
    }


def _received_value(po: dict[str, Any]) -> Decimal:
    return sum(
        (line["received_quantity"] * line["unit_price"] for line in po["lines"]),
        Decimal("0"),
    )


def create_app(
    profile: Literal[
        "secure", "vulnerable-overpayment", "vulnerable-gl", "vulnerable-all"
    ] = "secure",
) -> FastAPI:
    state = DemoState()
    application = FastAPI(
        title="Purchase-to-Pay Demonstration API",
        version="1.0.0",
        description=(
            "A local, in-memory P2P API for exercising the autonomous QA agent. "
            f"Current behavior profile: {profile}."
        ),
    )
    application.state.demo = state
    application.state.profile = profile

    def vulnerable(control: str) -> bool:
        return profile == "vulnerable-all" or profile == f"vulnerable-{control}"

    def get_vendor(vendor_id: str) -> dict[str, Any]:
        vendor = state.vendors.get(vendor_id)
        if not vendor:
            raise HTTPException(404, "vendor not found")
        return vendor

    def get_po(po_id: str) -> dict[str, Any]:
        po = state.purchase_orders.get(po_id)
        if not po:
            raise HTTPException(404, "purchase order not found")
        return po

    def get_invoice(invoice_id: str) -> dict[str, Any]:
        invoice = state.invoices.get(invoice_id)
        if not invoice:
            raise HTTPException(404, "invoice not found")
        return invoice

    @application.get("/health", tags=["discovery"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "profile": profile}

    @application.get("/items", tags=["discovery"])
    async def list_items() -> list[dict[str, Any]]:
        return list(state.items.values())

    @application.get("/vendors", tags=["vendors"])
    async def list_vendors() -> list[dict[str, Any]]:
        return list(state.vendors.values())

    @application.post("/vendors", status_code=201, tags=["vendors"])
    async def create_vendor(payload: VendorCreate) -> dict[str, Any]:
        with state.lock:
            vendor_id = _id("vendor")
            vendor = {
                "id": vendor_id,
                "name": payload.name,
                "code": payload.code,
                "active": payload.active,
                "status": "active" if payload.active else "inactive",
            }
            state.vendors[vendor_id] = vendor
            return vendor

    @application.get("/vendors/{vendor_id}/exposure", tags=["vendors"])
    async def vendor_exposure(vendor_id: str) -> dict[str, str]:
        get_vendor(vendor_id)
        total = sum(
            (
                invoice["amount"]
                for invoice in state.invoices.values()
                if invoice["vendor_id"] == vendor_id and invoice["status"] == "approved"
            ),
            Decimal("0"),
        )
        return {"vendor_id": vendor_id, "open_ap_liability": _money(total)}

    @application.post("/purchase-orders", status_code=201, tags=["purchase-orders"])
    async def create_purchase_order(payload: PurchaseOrderCreate) -> dict[str, Any]:
        with state.lock:
            vendor = get_vendor(payload.vendor_id)
            if not vendor["active"] and not vulnerable("all"):
                raise HTTPException(409, "purchase orders for inactive vendors are prohibited")
            for line in payload.lines:
                item = state.items.get(line.sku)
                if not item or not item["active"]:
                    raise HTTPException(422, f"unknown or inactive SKU: {line.sku}")
            if len({line.sku for line in payload.lines}) != len(payload.lines):
                raise HTTPException(422, "duplicate SKU lines are not supported; consolidate quantities")
            po_id = _id("po")
            po = {
                "id": po_id,
                "vendor_id": payload.vendor_id,
                "status": "draft",
                "description": payload.description,
                "lines": [
                    {
                        "id": _id("line"),
                        "sku": line.sku,
                        "ordered_quantity": line.quantity,
                        "received_quantity": Decimal("0"),
                        "unit_price": line.unit_price,
                    }
                    for line in payload.lines
                ],
            }
            state.purchase_orders[po_id] = po
            return _po_view(po)

    @application.get("/purchase-orders", tags=["purchase-orders"])
    async def list_purchase_orders() -> list[dict[str, Any]]:
        return [_po_view(po) for po in state.purchase_orders.values()]

    @application.post("/purchase-orders/{po_id}/submit", tags=["purchase-orders"])
    async def submit_purchase_order(po_id: str) -> dict[str, Any]:
        with state.lock:
            po = get_po(po_id)
            if po["status"] != "draft":
                raise HTTPException(409, f"cannot submit PO in {po['status']} status")
            po["status"] = "submitted"
            return _po_view(po)

    @application.post("/purchase-orders/{po_id}/receive", tags=["purchase-orders"])
    async def receive_purchase_order(po_id: str, payload: GoodsReceipt) -> dict[str, Any]:
        with state.lock:
            po = get_po(po_id)
            if po["status"] not in {"submitted", "partially_received", "received"}:
                raise HTTPException(409, f"cannot receive PO in {po['status']} status")
            proposed = {line["sku"]: line["received_quantity"] for line in po["lines"]}
            for receipt in payload.lines:
                line = next((item for item in po["lines"] if item["sku"] == receipt.sku), None)
                if not line:
                    raise HTTPException(422, f"SKU {receipt.sku} is not on the PO")
                proposed[receipt.sku] += receipt.quantity
                if proposed[receipt.sku] > line["ordered_quantity"] and not vulnerable("all"):
                    raise HTTPException(409, f"receipt exceeds ordered quantity for {receipt.sku}")
            for line in po["lines"]:
                line["received_quantity"] = proposed[line["sku"]]
            fully_received = all(
                line["received_quantity"] >= line["ordered_quantity"] for line in po["lines"]
            )
            po["status"] = "received" if fully_received else "partially_received"
            return _po_view(po)

    @application.get("/purchase-orders/{po_id}", tags=["purchase-orders"])
    async def purchase_order_detail(po_id: str) -> dict[str, Any]:
        return _po_view(get_po(po_id))

    @application.post("/invoices", status_code=201, tags=["invoices"])
    async def create_invoice(payload: InvoiceCreate) -> dict[str, Any]:
        with state.lock:
            po = get_po(payload.po_id)
            if po["vendor_id"] != payload.vendor_id:
                raise HTTPException(422, "invoice vendor does not match purchase order vendor")
            duplicate = any(
                invoice["vendor_id"] == payload.vendor_id
                and invoice["invoice_number"] == payload.invoice_number
                for invoice in state.invoices.values()
            )
            if duplicate and not vulnerable("all"):
                raise HTTPException(409, "duplicate invoice number for vendor")
            if payload.amount > _received_value(po) and not vulnerable("overpayment"):
                raise HTTPException(422, "invoice amount exceeds received goods value")
            invoice_id = _id("invoice")
            invoice = {
                "id": invoice_id,
                "vendor_id": payload.vendor_id,
                "po_id": payload.po_id,
                "invoice_number": payload.invoice_number,
                "amount": payload.amount,
                "status": "created",
                "matched": False,
            }
            state.invoices[invoice_id] = invoice
            return {**invoice, "amount": _money(invoice["amount"])}

    @application.get("/invoices", tags=["invoices"])
    async def list_invoices() -> list[dict[str, Any]]:
        return [
            {**invoice, "amount": _money(invoice["amount"])}
            for invoice in state.invoices.values()
        ]

    @application.get("/invoices/{invoice_id}", tags=["invoices"])
    async def invoice_detail(invoice_id: str) -> dict[str, Any]:
        invoice = get_invoice(invoice_id)
        return {**invoice, "amount": _money(invoice["amount"])}

    @application.post("/invoices/{invoice_id}/match", tags=["invoices"])
    async def match_invoice(invoice_id: str) -> dict[str, Any]:
        with state.lock:
            invoice = get_invoice(invoice_id)
            po = get_po(invoice["po_id"])
            received = _received_value(po)
            if invoice["amount"] > received and not vulnerable("overpayment"):
                invoice["status"] = "mismatch"
                return {
                    "status": "amount_mismatch",
                    "matched": False,
                    "received_value": _money(received),
                    "invoice_amount": _money(invoice["amount"]),
                }
            partial = any(
                line["received_quantity"] < line["ordered_quantity"] for line in po["lines"]
            )
            over_value_vulnerability = invoice["amount"] > received and vulnerable("overpayment")
            if partial and not over_value_vulnerability and not vulnerable("all"):
                invoice["status"] = "partial_match"
                return {
                    "status": "partial_match",
                    "matched": False,
                    "partial": True,
                    "message": "PO is only partially received",
                }
            invoice["matched"] = True
            invoice["status"] = "matched"
            return {"status": "matched", "matched": True, "partial": False}

    def post_journal(invoice: dict[str, Any]) -> dict[str, Any]:
        debit = invoice["amount"]
        credit = debit
        if vulnerable("gl") or profile == "vulnerable-all":
            credit = max(Decimal("0"), debit - Decimal("0.01"))
        journal = {
            "id": _id("journal"),
            "invoice_id": invoice["id"],
            "lines": [
                {"account": "Expense", "debit": _money(debit), "credit": "0.00"},
                {"account": "Accounts Payable", "debit": "0.00", "credit": _money(credit)},
            ],
        }
        state.journals[invoice["id"]] = journal
        return journal

    @application.post("/invoices/{invoice_id}/approve", tags=["invoices"])
    async def approve_invoice(invoice_id: str) -> dict[str, Any]:
        with state.lock:
            invoice = get_invoice(invoice_id)
            if not invoice["matched"] and not vulnerable("all"):
                raise HTTPException(409, "invoice must pass three-way matching before approval")
            if invoice["status"] == "approved":
                raise HTTPException(409, "invoice is already approved")
            invoice["status"] = "approved"
            journal = post_journal(invoice)
            return {"status": "approved", "invoice_id": invoice_id, "journal": journal}

    @application.get("/journals", tags=["accounting"])
    async def list_journals() -> list[dict[str, Any]]:
        return list(state.journals.values())

    @application.get("/journals/{invoice_id}", tags=["accounting"])
    async def journal_for_invoice(invoice_id: str) -> dict[str, Any]:
        journal = state.journals.get(invoice_id)
        if not journal:
            raise HTTPException(404, "journal not found")
        return journal

    return application


app = create_app(os.getenv("P2P_DEMO_PROFILE", "secure"))

