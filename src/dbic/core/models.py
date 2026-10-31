from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

UtcTime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))]
DatabaseType = Literal["mysql", "redis", "mongodb", "postgresql", "doris"]
EvidenceStatus = Literal["ok", "empty", "unavailable", "timeout", "forbidden", "error"]
EvidenceKind = Literal["metric", "log", "database_state", "historical_incident", "derived"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeWindow(Model):
    start: UtcTime
    end: UtcTime

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("window.end must be after window.start")
        return self


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Instance(Model):
    id: str = Field(min_length=1)
    type: DatabaseType
    environment: Literal["lab"] = "lab"
    version: str
    connection_ref: str = Field(pattern=r"^env:[A-Z][A-Z0-9_]*$")
    enabled: bool = True


class DiagnosticTask(Model):
    task_id: UUID = Field(default_factory=uuid4)
    requested_by: str = Field(min_length=1)
    database_type: DatabaseType
    instance_id: str = Field(min_length=1)
    problem_type: str = Field(min_length=1)
    window: TimeWindow | None = None
    status: TaskStatus = TaskStatus.QUEUED
    playbook_id: str | None = None
    created_at: UtcTime = Field(default_factory=utc_now)
    started_at: UtcTime | None = None
    finished_at: UtcTime | None = None
    error_code: str | None = None
    parent_task_id: UUID | None = None
    request_id: UUID | None = None

    @model_validator(mode="after")
    def lifecycle(self) -> Self:
        if self.parent_task_id == self.task_id:
            raise ValueError("task cannot be its own parent")
        if self.status == TaskStatus.QUEUED:
            if self.started_at or self.finished_at:
                raise ValueError("queued task cannot have execution timestamps")
        elif self.started_at is None:
            raise ValueError("started_at is required after queued")
        if self.status == TaskStatus.RUNNING and self.finished_at:
            raise ValueError("running task cannot have finished_at")
        if self.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING) and not self.finished_at:
            raise ValueError("terminal task requires finished_at")
        if self.started_at and self.started_at < self.created_at:
            raise ValueError("started_at precedes created_at")
        if self.finished_at and self.started_at and self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        return self


class ToolResult(Model):
    status: EvidenceStatus
    observed_at: UtcTime
    source: str = Field(min_length=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    duration_ms: float = Field(ge=0)
    kind: EvidenceKind = "database_state"
    window: TimeWindow | None = None


class Evidence(Model):
    evidence_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    kind: EvidenceKind
    source: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    observed_at: UtcTime
    collected_at: UtcTime = Field(default_factory=utc_now)
    window: TimeWindow | None = None
    quality: Literal["observed", "derived", "inferred"]
    status: EvidenceStatus
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    redacted: bool = False
    tool_version: str | None = None
    parameters_summary: str | None = None
    payload_hash: str | None = None


class Finding(Model):
    finding_id: UUID = Field(default_factory=uuid4)
    statement: str = Field(min_length=1)
    certainty: Literal[
        "observed", "supported_hypothesis", "unverified_hypothesis", "confirmed_root_cause"
    ]
    evidence_ids: list[UUID] = Field(min_length=1)
    rule_id: str | None = None
    confirmed_by: Literal["rule", "human"] | None = None

    @model_validator(mode="after")
    def confirmation(self) -> Self:
        if self.certainty == "confirmed_root_cause" and self.confirmed_by is None:
            raise ValueError("confirmed root cause requires rule or human confirmation")
        return self


class AgentSuggestion(Model):
    tool_id: Literal["mysql.health", "mysql.lock_chain", "mysql.slow_query_summary"]
    instance_id: Literal["mysql-lab-01"]
    reason: Literal["refresh_snapshot", "check_missing_evidence", "compare_current_state"]
    evidence_ids: list[UUID] = Field(min_length=1, max_length=8)


class AgentAdvice(Model):
    suggestions: list[AgentSuggestion] = Field(max_length=3)


class Report(Model):
    task_id: UUID
    status: Literal["observed", "inconclusive", "unavailable"]
    summary: str = Field(min_length=1)
    timeline: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    generated_at: UtcTime = Field(default_factory=utc_now)
    generator_version: str = "0.1.0"
    agent_status: Literal["disabled", "ok", "unavailable", "rejected"] = "disabled"
    agent_reason: Literal["backend_failure", "invalid_advice", "budget_exceeded"] | None = None
    agent_suggestions: list[AgentSuggestion] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def references(self) -> Self:
        known = set(self.evidence_ids)
        if any(not set(f.evidence_ids) <= known for f in self.findings):
            raise ValueError("finding references evidence absent from report")
        if any(not set(s.evidence_ids) <= known for s in self.agent_suggestions):
            raise ValueError("agent suggestion references evidence absent from report")
        if self.agent_suggestions and self.agent_status != "ok":
            raise ValueError("suggestions require validated agent status")
        if self.status == "observed" and (not known or not self.findings):
            raise ValueError("observed report requires evidence and findings")
        return self


class DiagnosticStep(Model):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    step_key: str
    tool_id: str
    status: EvidenceStatus
    elapsed_ms: float = Field(ge=0)
    error_code: str | None = None
    started_at: UtcTime
    finished_at: UtcTime


class Feedback(Model):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    verdict: Literal["correct", "partial", "incorrect"]
    confirmed_root_cause: str | None = Field(default=None, max_length=1000)
    comment: str | None = Field(default=None, max_length=2000)
    created_at: UtcTime = Field(default_factory=utc_now)


class TaskDetail(Model):
    task: DiagnosticTask
    steps: list[DiagnosticStep] = Field(default_factory=list)
    report: Report | None = None
    feedback: list[Feedback] = Field(default_factory=list)
