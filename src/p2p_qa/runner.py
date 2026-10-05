from __future__ import annotations

import os
import random
import secrets
from datetime import datetime, timezone
from typing import Literal

import httpx

from .agents import AdversarialAgent, ExplorationAgent
from .client import ApiClient
from .llm import make_reasoner
from .models import HappyPathReport, QAReport, RunInfo, SummaryReport
from .reporting import summarize
from .schema import SchemaExplorer


def make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"qa-{stamp}-{secrets.token_hex(3)}"


def execute(
    base_url: str,
    *,
    mode: Literal["happy", "adversarial", "all"] = "all",
    timeout: float = 10.0,
    max_actions: int = 100,
    seed: int | None = None,
    allow_nonlocal: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> QAReport:
    if seed is not None:
        random.seed(seed)
    run_id = make_run_id()
    started = datetime.now(timezone.utc)
    reasoner = make_reasoner()
    with ApiClient(
        base_url,
        timeout=timeout,
        max_actions=max_actions,
        allow_nonlocal=allow_nonlocal,
        transport=transport,
    ) as client:
        explorer = SchemaExplorer(client)
        discovery = explorer.discover()
        if mode in {"happy", "all"}:
            happy = ExplorationAgent(client, explorer, reasoner, run_id).run()
        else:
            happy = HappyPathReport(status="INCONCLUSIVE", resources={"not_run": True})
        if mode in {"adversarial", "all"}:
            adversarial = AdversarialAgent(client, explorer, reasoner, run_id).run()
        else:
            adversarial = []
    report = QAReport(
        run=RunInfo(
            id=run_id,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            base_url=base_url,
            schema_hash=explorer.schema_hash,
            model=os.getenv("OPENAI_MODEL", "deterministic-fallback") or "deterministic-fallback",
            seed=seed,
        ),
        discovery=discovery,
        happy_path=happy,
        adversarial=adversarial,
        summary=SummaryReport(overall_status="INCONCLUSIVE"),
    )
    report.summary = summarize(report)
    return report
