from __future__ import annotations

import json
from datetime import datetime, timezone

from p2p_qa.models import DiscoveryReport, HappyPathReport, QAReport, RunInfo, SummaryReport
from p2p_qa.reporting import ReportWriter, exit_code
from p2p_qa.cli import app
from typer.testing import CliRunner


def report(happy="PASS", overall="PASS", breached=0, inconclusive=0, discovery="PASS"):
    return QAReport(
        run=RunInfo(id="qa-test", started_at=datetime.now(timezone.utc), base_url="http://localhost:8000", schema_hash="abc", model="test"),
        discovery=DiscoveryReport(status=discovery),
        happy_path=HappyPathReport(status=happy),
        adversarial=[],
        summary=SummaryReport(overall_status=overall, breached=breached, inconclusive=inconclusive),
    )


def test_report_serialization_round_trip(tmp_path):
    target = ReportWriter().write(report(), tmp_path / "report.json")
    loaded = QAReport.model_validate(json.loads(target.read_text(encoding="utf-8")))
    assert loaded.run.id == "qa-test"


def test_cli_exit_codes():
    assert exit_code(report()) == 0
    assert exit_code(report(overall="FAIL", breached=1)) == 1
    assert exit_code(report(happy="FAIL", overall="FAIL")) == 1
    assert exit_code(report(overall="INCONCLUSIVE", inconclusive=1), True) == 3
    assert exit_code(report(overall="INCONCLUSIVE", inconclusive=1), False) == 0
    assert exit_code(report(happy="INCONCLUSIVE", overall="ERROR", discovery="ERROR")) == 2


def test_actual_cli_propagates_exit_policy(monkeypatch, tmp_path):
    cli = CliRunner()
    monkeypatch.setattr("p2p_qa.cli.execute", lambda *args, **kwargs: report(overall="FAIL", breached=1))
    result = cli.invoke(app, ["run", "--report", str(tmp_path / "breach.json")])
    assert result.exit_code == 1

    monkeypatch.setattr(
        "p2p_qa.cli.execute",
        lambda *args, **kwargs: report(happy="INCONCLUSIVE", overall="INCONCLUSIVE", inconclusive=1),
    )
    result = cli.invoke(
        app,
        ["run", "--report", str(tmp_path / "inconclusive.json"), "--fail-on-inconclusive"],
    )
    assert result.exit_code == 3
