from __future__ import annotations

from decimal import Decimal
from typing import Any

from .models import AssertionResult, ControlResult
from .utils import decimal, find_first, walk


class InvariantJudge:
    @staticmethod
    def received_value(lines: list[dict[str, Any]]) -> Decimal:
        if not lines:
            raise ValueError("at least one observable PO line is required")
        total = Decimal("0")
        for line in lines:
            qty = decimal(
                find_first(line, ["received_quantity", "quantity_received", "received_qty", "received", "quantity"])
            )
            price = decimal(find_first(line, ["agreed_unit_price", "unit_price", "unit_cost", "price"]))
            if qty is None or price is None:
                raise ValueError("received quantity and agreed unit price are required")
            total += qty * price
        return total

    @staticmethod
    def unchanged(before: Any, after: Any) -> AssertionResult:
        observable = before is not None and after is not None
        return AssertionResult(
            name="observable_state_unchanged",
            passed=(before == after) if observable else None,
            expected=before,
            actual=after,
            explanation=(
                "Observable state remained unchanged."
                if observable and before == after
                else "Observable state changed."
                if observable
                else "No before/after state was observable."
            ),
        )

    def rejection_control(
        self,
        rule: str,
        status_code: int | None,
        before: Any,
        after: Any,
        *,
        steps: list[Any] | None = None,
    ) -> ControlResult:
        rejected = status_code is not None and 400 <= status_code < 500
        a_rejected = AssertionResult(
            name="prohibited_operation_rejected",
            passed=rejected if status_code is not None else None,
            expected="HTTP 4xx",
            actual=status_code,
            explanation="The prohibited operation was rejected." if rejected else "The operation was not rejected.",
        )
        a_unchanged = self.unchanged(before, after)
        if status_code is None:
            status = "ERROR"
        elif 200 <= status_code < 300 or a_unchanged.passed is False:
            status = "BREACHED"
        elif rejected and a_unchanged.passed is True:
            status = "HELD"
        else:
            status = "INCONCLUSIVE"
        return ControlResult(
            rule=rule,
            status=status,
            evidence={"status_code": status_code, "before": before, "after": after},
            assertions=[a_rejected, a_unchanged],
            steps=steps or [],
            explanation="A rejection counts as held only with observable non-mutation.",
        )

    def overpayment(
        self,
        received_value: Decimal,
        invoice_value: Decimal,
        approved: bool | None,
        before: Any,
        after: Any,
        *,
        steps: list[Any] | None = None,
    ) -> ControlResult:
        is_attack = invoice_value > received_value
        unchanged = self.unchanged(before, after)
        assertions = [
            AssertionResult(
                name="invoice_exceeds_received_value",
                passed=is_attack,
                expected=f"> {received_value}",
                actual=str(invoice_value),
                explanation="Attack value is above the deterministically calculated received value.",
            ),
            AssertionResult(
                name="invoice_not_approved",
                passed=None if approved is None else not approved,
                expected=False,
                actual=approved,
                explanation="An over-value invoice must not become an approved payable.",
            ),
            unchanged,
        ]
        if not is_attack:
            status = "INCONCLUSIVE"
        elif approved is True or unchanged.passed is False:
            status = "BREACHED"
        elif approved is False and unchanged.passed is True:
            status = "HELD"
        elif approved is None:
            status = "ERROR"
        else:
            status = "INCONCLUSIVE"
        return ControlResult(
            rule="overpayment_protection",
            status=status,
            evidence={
                "received_value": str(received_value),
                "invoice_value": str(invoice_value),
                "approved": approved,
                "state_before": before,
                "state_after": after,
            },
            assertions=assertions,
            steps=steps or [],
        )

    def partial_receipt(
        self,
        ordered: Decimal,
        received: Decimal,
        match_status: int | None,
        response: Any,
        *,
        steps: list[Any] | None = None,
    ) -> ControlResult:
        partial = Decimal("0") <= received < ordered
        marker = find_first(response, ["partial", "is_partial", "has_mismatch"])
        status_value = find_first(response, ["status", "match_status", "result", "detail", "message", "reason"])
        status_text = str(status_value).lower() if status_value is not None else ""
        normalized_status = status_text.replace("-", "_").replace(" ", "_")
        explicit = marker is True or str(marker).lower() in {"true", "1", "yes"} or any(
            word in status_text for word in ("partial", "mismatch", "under", "short", "failed", "exception")
        ) or normalized_status in {"unmatched", "not_matched"}
        matched_marker = find_first(response, ["matched", "is_matched"])
        clean = (
            matched_marker is True
            or str(matched_marker).lower() in {"true", "1", "yes"}
            or normalized_status in {"fully_matched", "full_match", "matched", "passed", "success", "successful"}
        )
        assertions = [
            AssertionResult(
                name="receipt_is_numerically_partial",
                passed=partial,
                expected=f"received < {ordered}",
                actual=str(received),
                explanation="Quantities are compared with Decimal.",
            ),
            AssertionResult(
                name="partial_state_disclosed",
                passed=explicit,
                expected=True,
                actual=explicit,
                explanation="The result must explicitly identify the partial or mismatched state.",
            ),
            AssertionResult(
                name="not_reported_as_clean_full_match",
                passed=not clean,
                expected=True,
                actual=not clean,
                explanation="A numerically partial receipt cannot also be reported as a clean match.",
            ),
        ]
        if match_status is None:
            status = "ERROR"
        elif not partial:
            status = "INCONCLUSIVE"
        elif clean:
            status = "BREACHED"
        elif explicit:
            status = "HELD"
        else:
            status = "INCONCLUSIVE"
        return ControlResult(
            rule="partial_receipt_disclosure",
            status=status,
            evidence={"ordered": str(ordered), "received": str(received), "match_response": response},
            assertions=assertions,
            steps=steps or [],
        )

    def gl_balance(self, evidence: Any, *, steps: list[Any] | None = None) -> ControlResult:
        candidate_lines: list[dict[str, Any]] | None = None
        for _path, value in walk(evidence):
            if isinstance(value, list) and value and all(isinstance(line, dict) for line in value):
                if any(
                    find_first(line, ["debit", "debit_amount", "credit", "credit_amount"]) is not None
                    for line in value
                ):
                    candidate_lines = value
                    break
        if not candidate_lines:
            return ControlResult(
                rule="gl_balance",
                status="INCONCLUSIVE",
                evidence={"journal": evidence},
                assertions=[
                    AssertionResult(
                        name="journal_observable",
                        passed=None,
                        expected=True,
                        actual=False,
                        explanation="No debit/credit journal lines were observable.",
                    )
                ],
                steps=steps or [],
            )
        debits = Decimal("0")
        credits = Decimal("0")
        malformed = False
        for line in candidate_lines:
            debit_raw = find_first(line, ["debit", "debit_amount"])
            credit_raw = find_first(line, ["credit", "credit_amount"])
            debit = decimal(debit_raw)
            credit = decimal(credit_raw)
            if (debit_raw is not None and debit is None) or (credit_raw is not None and credit is None):
                malformed = True
            if debit_raw is None and credit_raw is None:
                malformed = True
            debits += debit or Decimal("0")
            credits += credit or Decimal("0")
        if malformed:
            return ControlResult(
                rule="gl_balance",
                status="INCONCLUSIVE",
                evidence={"lines": candidate_lines},
                assertions=[
                    AssertionResult(
                        name="journal_amounts_numeric",
                        passed=None,
                        expected="numeric debit/credit values",
                        actual=candidate_lines,
                        explanation="At least one journal line had missing or malformed monetary evidence.",
                    )
                ],
                steps=steps or [],
            )
        balanced = debits == credits
        assertion = AssertionResult(
            name="total_debits_equal_total_credits",
            passed=balanced,
            expected=str(debits),
            actual=str(credits),
            explanation="Journal totals were summed with Decimal.",
        )
        return ControlResult(
            rule="gl_balance",
            status="HELD" if balanced else "BREACHED",
            evidence={"total_debits": str(debits), "total_credits": str(credits), "lines": candidate_lines},
            assertions=[assertion],
            steps=steps or [],
        )
