from decimal import Decimal

from p2p_qa.judge import InvariantJudge


def test_unmatched_approval_rejection_requires_unchanged_state():
    result = InvariantJudge().rejection_control("three_way_match_gate", 409, {"x": "1"}, {"x": "1"})
    assert result.status == "HELD"


def test_over_receipt_rejection_with_mutation_is_breached():
    result = InvariantJudge().rejection_control("excess_goods_receipt", 409, {"received": "0"}, {"received": "3"})
    assert result.status == "BREACHED"


def test_status_code_without_state_evidence_is_inconclusive():
    result = InvariantJudge().rejection_control("duplicate_invoice_protection", 409, None, None)
    assert result.status == "INCONCLUSIVE"


def test_overpayment_rejection_and_vulnerable_behavior():
    held = InvariantJudge().overpayment(Decimal("10"), Decimal("10.01"), False, "0", "0")
    breached = InvariantJudge().overpayment(Decimal("10"), Decimal("10.01"), True, "0", "10.01")
    assert held.status == "HELD"
    assert breached.status == "BREACHED"


def test_partial_receipt_reporting():
    held = InvariantJudge().partial_receipt(Decimal("2"), Decimal("1"), 200, {"status": "partial_match"})
    breached = InvariantJudge().partial_receipt(Decimal("2"), Decimal("1"), 200, {"status": "fully_matched"})
    assert held.status == "HELD"
    assert breached.status == "BREACHED"


def test_balanced_unbalanced_and_unavailable_gl():
    judge = InvariantJudge()
    balanced = judge.gl_balance({"lines": [{"debit": "10.01", "credit": "0"}, {"debit": "0", "credit": "10.01"}]})
    unbalanced = judge.gl_balance({"lines": [{"debit": "10.01", "credit": "0"}, {"debit": "0", "credit": "10.00"}]})
    missing = judge.gl_balance({"status": "approved"})
    assert balanced.status == "HELD"
    assert unbalanced.status == "BREACHED"
    assert missing.status == "INCONCLUSIVE"


def test_received_value_uses_decimal():
    value = InvariantJudge().received_value([{"received_quantity": "0.1", "unit_price": "0.2"}])
    assert value == Decimal("0.02")

