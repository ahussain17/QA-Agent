from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ControlStatus = Literal["HELD", "BREACHED", "INCONCLUSIVE", "ERROR"]
HappyStatus = Literal["PASS", "FAIL", "INCONCLUSIVE", "ERROR"]
DiscoveryStatus = Literal["PASS", "INCONCLUSIVE", "ERROR"]
OverallStatus = Literal["PASS", "FAIL", "INCONCLUSIVE", "ERROR"]


class AssertionResult(BaseModel):
    name: str
    passed: bool | None
    expected: Any = None
    actual: Any = None
    explanation: str


class StepEvidence(BaseModel):
    step_name: str
    timestamp: datetime
    purpose: str
    method: str
    url: str
    request_body: Any = None
    status_code: int | None = None
    response_body: Any = None
    duration_ms: float
    expected_outcome: str
    observed_outcome: str
    interpretation: str
    deterministic_assertions: list[AssertionResult] = Field(default_factory=list)
    verdict: Literal["PASS", "FAIL", "HELD", "BREACHED", "INCONCLUSIVE", "ERROR"]
    attempt: int = 1


class RunInfo(BaseModel):
    id: str
    started_at: datetime
    base_url: str
    schema_hash: str
    model: str
    finished_at: datetime | None = None
    seed: int | None = None


class OperationInfo(BaseModel):
    method: str
    path: str
    operation_id: str | None = None
    summary: str | None = None
    required_fields: list[str] = Field(default_factory=list)


class DiscoveryReport(BaseModel):
    status: DiscoveryStatus
    operations: list[OperationInfo] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    inspected_for_sku: list[str] = Field(default_factory=list)
    sku_candidates: list[str] = Field(default_factory=list)
    evidence: list[StepEvidence] = Field(default_factory=list)


class HappyPathReport(BaseModel):
    status: HappyStatus
    steps: list[StepEvidence] = Field(default_factory=list)
    plan: list[dict[str, Any]] = Field(default_factory=list)
    resources: dict[str, Any] = Field(default_factory=dict)


class ControlResult(BaseModel):
    rule: str
    status: ControlStatus
    evidence: dict[str, Any] = Field(default_factory=dict)
    assertions: list[AssertionResult] = Field(default_factory=list)
    steps: list[StepEvidence] = Field(default_factory=list)
    explanation: str = ""


class SummaryReport(BaseModel):
    overall_status: OverallStatus
    held: int = 0
    breached: int = 0
    inconclusive: int = 0
    errors: int = 0
    highest_risk_findings: list[str] = Field(default_factory=list)


class QAReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    run: RunInfo
    discovery: DiscoveryReport
    happy_path: HappyPathReport
    adversarial: list[ControlResult]
    summary: SummaryReport
