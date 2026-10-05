from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .models import QAReport, SummaryReport


def summarize(report: QAReport) -> SummaryReport:
    statuses = [item.status for item in report.adversarial]
    held = statuses.count("HELD")
    breached = statuses.count("BREACHED")
    inconclusive = statuses.count("INCONCLUSIVE")
    errors = statuses.count("ERROR")
    risks = [item.rule for item in report.adversarial if item.status == "BREACHED"]
    if report.happy_path.status == "FAIL" or breached:
        overall = "FAIL"
    elif report.discovery.status == "ERROR" or report.happy_path.status == "ERROR" or errors:
        overall = "ERROR"
    elif report.happy_path.status == "INCONCLUSIVE" or inconclusive:
        overall = "INCONCLUSIVE"
    else:
        overall = "PASS"
    return SummaryReport(
        overall_status=overall,
        held=held,
        breached=breached,
        inconclusive=inconclusive,
        errors=errors,
        highest_risk_findings=risks,
    )


class ReportWriter:
    def write(self, report: QAReport, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Round-trip through the model before persistence.
        payload = report.model_dump(mode="json")
        QAReport.model_validate(payload)
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return target

    def terminal_summary(self, report: QAReport, console: Console | None = None) -> None:
        console = console or Console()
        table = Table(title=f"P2P QA {report.run.id}")
        table.add_column("Area")
        table.add_column("Result")
        table.add_row("Discovery", report.discovery.status)
        table.add_row("Happy path", report.happy_path.status)
        for result in report.adversarial:
            table.add_row(result.rule, result.status)
        table.add_row("Overall", report.summary.overall_status)
        console.print(table)


def exit_code(report: QAReport, fail_on_inconclusive: bool = False) -> int:
    if report.happy_path.status == "FAIL" or report.summary.breached:
        return 1
    if report.happy_path.status == "ERROR" or report.summary.errors:
        return 2
    if fail_on_inconclusive and (
        report.happy_path.status == "INCONCLUSIVE" or report.summary.inconclusive
    ):
        return 3
    return 0
