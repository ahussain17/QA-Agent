from __future__ import annotations

import asyncio
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

import httpx

from p2p_demo import create_app


class ASGIClient:
    """Small synchronous facade over HTTPX's maintained async ASGI transport."""

    def __init__(self, application):
        self.application = application

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.application)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _create_received_po(client: ASGIClient, *, quantity: str = "2", received: str = "2") -> str:
    response = client.post(
        "/purchase-orders",
        json={
            "vendor_id": "vendor-active",
            "lines": [{"sku": "STONE-1", "quantity": quantity, "unit_price": "10.00"}],
        },
    )
    assert response.status_code == 201
    po_id = response.json()["id"]
    assert client.post(f"/purchase-orders/{po_id}/submit").status_code == 200
    assert client.post(
        f"/purchase-orders/{po_id}/receive",
        json={"lines": [{"sku": "STONE-1", "quantity": received}]},
    ).status_code == 200
    return po_id


def test_demo_starts_with_linked_purchase_order_and_invoice_data():
    client = ASGIClient(create_app("secure"))

    purchase_orders = client.get("/purchase-orders").json()
    invoices = client.get("/invoices").json()
    journals = client.get("/journals").json()

    assert {po["status"] for po in purchase_orders} == {
        "submitted",
        "partially_received",
        "received",
    }
    assert {invoice["po_id"] for invoice in invoices} <= {
        po["id"] for po in purchase_orders
    }
    assert {invoice["status"] for invoice in invoices} == {"created", "approved"}
    assert {invoice["amount"] for invoice in invoices} == {"131.25", "217.50"}
    assert {journal["invoice_id"] for journal in journals} <= {
        invoice["id"] for invoice in invoices
    }
    assert client.get("/journals/invoice-seed-2002").json() == journals[0]
    assert sum(Decimal(line["debit"]) for line in journals[0]["lines"]) == sum(
        Decimal(line["credit"]) for line in journals[0]["lines"]
    )


def test_secure_happy_path_posts_balanced_journal():
    client = ASGIClient(create_app("secure"))
    po_id = _create_received_po(client)
    invoice = client.post(
        "/invoices",
        json={
            "vendor_id": "vendor-active",
            "po_id": po_id,
            "invoice_number": "INV-HAPPY",
            "amount": "20.00",
        },
    )
    assert invoice.status_code == 201
    invoice_id = invoice.json()["id"]
    assert client.post(f"/invoices/{invoice_id}/match").json()["matched"] is True
    approval = client.post(f"/invoices/{invoice_id}/approve")
    assert approval.status_code == 200
    lines = approval.json()["journal"]["lines"]
    assert sum(Decimal(line["debit"]) for line in lines) == sum(
        Decimal(line["credit"]) for line in lines
    )


def test_secure_financial_controls_reject_invalid_actions():
    client = ASGIClient(create_app("secure"))
    inactive = client.post(
        "/purchase-orders",
        json={
            "vendor_id": "vendor-inactive",
            "lines": [{"sku": "STONE-1", "quantity": "2", "unit_price": "10.00"}],
        },
    )
    assert inactive.status_code == 409

    po_id = _create_received_po(client, received="1")
    over_receipt = client.post(
        f"/purchase-orders/{po_id}/receive",
        json={"lines": [{"sku": "STONE-1", "quantity": "2"}]},
    )
    assert over_receipt.status_code == 409
    overpayment = client.post(
        "/invoices",
        json={
            "vendor_id": "vendor-active",
            "po_id": po_id,
            "invoice_number": "INV-OVER",
            "amount": "10.01",
        },
    )
    assert overpayment.status_code == 422


def test_vulnerable_profiles_are_intentionally_observable():
    overpay_client = ASGIClient(create_app("vulnerable-overpayment"))
    po_id = _create_received_po(overpay_client, received="1")
    invoice = overpay_client.post(
        "/invoices",
        json={
            "vendor_id": "vendor-active",
            "po_id": po_id,
            "invoice_number": "INV-VULN",
            "amount": "10.01",
        },
    )
    assert invoice.status_code == 201
    invoice_id = invoice.json()["id"]
    assert overpay_client.post(f"/invoices/{invoice_id}/match").json()["matched"] is True
    assert overpay_client.post(f"/invoices/{invoice_id}/approve").status_code == 200

    gl_client = ASGIClient(create_app("vulnerable-gl"))
    po_id = _create_received_po(gl_client)
    invoice = gl_client.post(
        "/invoices",
        json={
            "vendor_id": "vendor-active",
            "po_id": po_id,
            "invoice_number": "INV-GL",
            "amount": "20.00",
        },
    )
    invoice_id = invoice.json()["id"]
    gl_client.post(f"/invoices/{invoice_id}/match")
    lines = gl_client.post(f"/invoices/{invoice_id}/approve").json()["journal"]["lines"]
    assert lines[0]["debit"] != lines[1]["credit"]


def test_negative_quantities_and_zero_amount_are_rejected_by_schema():
    client = ASGIClient(create_app("secure"))
    negative_po = client.post(
        "/purchase-orders",
        json={
            "vendor_id": "vendor-active",
            "lines": [{"sku": "STONE-1", "quantity": "-1", "unit_price": "10.00"}],
        },
    )
    assert negative_po.status_code == 422

    po_id = _create_received_po(client)
    negative_receipt = client.post(
        f"/purchase-orders/{po_id}/receive",
        json={"lines": [{"sku": "STONE-1", "quantity": "-1"}]},
    )
    assert negative_receipt.status_code == 422
    zero_invoice = client.post(
        "/invoices",
        json={
            "vendor_id": "vendor-active",
            "po_id": po_id,
            "invoice_number": "INV-ZERO",
            "amount": "0.00",
        },
    )
    assert zero_invoice.status_code == 422


def test_repeated_submit_receipt_and_approval_are_idempotency_safe():
    client = ASGIClient(create_app("secure"))
    po = client.post(
        "/purchase-orders",
        json={
            "vendor_id": "vendor-active",
            "lines": [{"sku": "STONE-1", "quantity": "1", "unit_price": "10.00"}],
        },
    ).json()
    po_id = po["id"]
    assert client.post(f"/purchase-orders/{po_id}/submit").status_code == 200
    assert client.post(f"/purchase-orders/{po_id}/submit").status_code == 409
    receipt = {"lines": [{"sku": "STONE-1", "quantity": "1"}]}
    assert client.post(f"/purchase-orders/{po_id}/receive", json=receipt).status_code == 200
    assert client.post(f"/purchase-orders/{po_id}/receive", json=receipt).status_code == 409

    invoice = client.post(
        "/invoices",
        json={
            "vendor_id": "vendor-active",
            "po_id": po_id,
            "invoice_number": "INV-REPEAT",
            "amount": "10.00",
        },
    ).json()
    invoice_id = invoice["id"]
    assert client.post(f"/invoices/{invoice_id}/match").status_code == 200
    assert client.post(f"/invoices/{invoice_id}/approve").status_code == 200
    assert client.post(f"/invoices/{invoice_id}/approve").status_code == 409


def test_decimal_boundary_uses_exact_decimal_arithmetic():
    client = ASGIClient(create_app("secure"))
    response = client.post(
        "/purchase-orders",
        json={
            "vendor_id": "vendor-active",
            "lines": [{"sku": "STONE-1", "quantity": "0.30", "unit_price": "0.10"}],
        },
    )
    po_id = response.json()["id"]
    client.post(f"/purchase-orders/{po_id}/submit")
    client.post(
        f"/purchase-orders/{po_id}/receive",
        json={"lines": [{"sku": "STONE-1", "quantity": "0.30"}]},
    )
    exact = client.post(
        "/invoices",
        json={
            "vendor_id": "vendor-active",
            "po_id": po_id,
            "invoice_number": "INV-DECIMAL-EXACT",
            "amount": "0.03",
        },
    )
    above = client.post(
        "/invoices",
        json={
            "vendor_id": "vendor-active",
            "po_id": po_id,
            "invoice_number": "INV-DECIMAL-ABOVE",
            "amount": "0.04",
        },
    )
    assert exact.status_code == 201
    assert above.status_code == 422
    sub_cent = client.post(
        "/invoices",
        json={
            "vendor_id": "vendor-active",
            "po_id": po_id,
            "invoice_number": "INV-SUB-CENT",
            "amount": "0.031",
        },
    )
    assert sub_cent.status_code == 422


def test_duplicate_sku_lines_are_rejected_to_keep_receipt_accounting_unambiguous():
    client = ASGIClient(create_app("secure"))
    response = client.post(
        "/purchase-orders",
        json={
            "vendor_id": "vendor-active",
            "lines": [
                {"sku": "STONE-1", "quantity": "1", "unit_price": "10.00"},
                {"sku": "STONE-1", "quantity": "1", "unit_price": "10.00"},
            ],
        },
    )
    assert response.status_code == 422


def test_concurrent_duplicate_invoice_requests_create_only_one_invoice():
    application = create_app("secure")
    setup_client = ASGIClient(application)
    po_id = _create_received_po(setup_client)
    payload = {
        "vendor_id": "vendor-active",
        "po_id": po_id,
        "invoice_number": "INV-CONCURRENT",
        "amount": "20.00",
    }

    def create_duplicate() -> int:
        with ASGIClient(application) as client:
            return client.post("/invoices", json=payload).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: create_duplicate(), range(4)))

    assert statuses.count(201) == 1
    assert statuses.count(409) == 3
