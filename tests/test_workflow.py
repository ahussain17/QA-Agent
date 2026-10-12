from __future__ import annotations

from decimal import Decimal

import httpx

from p2p_qa.client import ApiClient
from p2p_qa.judge import InvariantJudge
from p2p_qa.runner import execute


def test_happy_path_with_mock_transport(mock_p2p_transport):
    report = execute("http://localhost:8000", mode="happy", transport=mock_p2p_transport)
    assert report.discovery.status == "PASS"
    assert report.happy_path.status == "PASS"
    assert report.happy_path.resources["invoice_id"].startswith("inv-")
    assert report.run.schema_hash not in {"", "unavailable"}


def test_all_controls_with_mock_transport(mock_p2p_transport):
    report = execute("http://localhost:8000", mode="all", transport=mock_p2p_transport, max_actions=150)
    statuses = {item.rule: item.status for item in report.adversarial}
    assert statuses["three_way_match_gate"] == "HELD"
    assert statuses["excess_goods_receipt"] == "HELD"
    assert statuses["overpayment_protection"] == "HELD"
    assert statuses["inactive_vendor_gate"] == "HELD"
    assert statuses["partial_receipt_disclosure"] == "HELD"
    assert statuses["duplicate_invoice_protection"] == "HELD"
    assert statuses["gl_balance"] == "HELD"
    assert report.summary.overall_status == "PASS"


def test_adversarial_only_ignores_the_not_run_happy_path_placeholder(mock_p2p_transport):
    report = execute(
        "http://localhost:8000",
        mode="adversarial",
        transport=mock_p2p_transport,
        max_actions=150,
    )
    assert report.happy_path.status == "INCONCLUSIVE"
    assert report.happy_path.resources["not_run"] is True
    assert report.summary.held == 7
    assert report.summary.overall_status == "PASS"


def test_mock_transport_surfaces_vulnerable_overpayment_and_gl_failures():
    def vulnerable_api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/overpay/approve":
            return httpx.Response(200, json={"approved": True, "exposure": "10.01"})
        if request.url.path == "/journal/unbalanced":
            return httpx.Response(200, json={"lines": [{"debit": "10.01"}, {"credit": "10.00"}]})
        return httpx.Response(200, json={"status": "approved"})

    judge = InvariantJudge()
    with ApiClient("http://localhost:8000", transport=httpx.MockTransport(vulnerable_api)) as client:
        approval = client.request("POST", "/overpay/approve")
        unbalanced = client.request("GET", "/journal/unbalanced")
        missing = client.request("GET", "/journal/missing")

    overpayment = judge.overpayment(
        Decimal("10"), Decimal("10.01"), approval.ok, {"exposure": "0"}, approval.response_body
    )
    assert overpayment.status == "BREACHED"
    assert judge.gl_balance(unbalanced.response_body).status == "BREACHED"
    assert judge.gl_balance(missing.response_body).status == "INCONCLUSIVE"
