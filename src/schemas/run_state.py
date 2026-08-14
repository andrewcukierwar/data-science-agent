"""Schemas for hypotheses, tool events, budgets, and persisted run state."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.audit import AuditResult
from schemas.findings import Finding
from schemas.validation import ValidationIssue, ValidationResult

NonEmptyString = Annotated[str, Field(min_length=1)]


class HypothesisStatus(StrEnum):
    """Current disposition of an investigation hypothesis."""

    OPEN = "open"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class Hypothesis(BaseModel):
    """An explicit, testable explanation tracked by the Lead."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    statement: NonEmptyString
    status: HypothesisStatus = HypothesisStatus.OPEN
    evidence_refs: list[NonEmptyString] = Field(default_factory=list)
    rationale: NonEmptyString | None = None
    parent_id: NonEmptyString | None = None


class ToolEventStatus(StrEnum):
    """Lifecycle state of a logged tool invocation."""

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolEvent(BaseModel):
    """Observable record of a tool call and its result or error."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    tool_name: NonEmptyString
    status: ToolEventStatus
    started_at: datetime
    completed_at: datetime | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | None = None
    error: NonEmptyString | None = None
    artifact_refs: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ToolEvent":
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must be on or after started_at")
        if self.status is ToolEventStatus.FAILED and self.error is None:
            raise ValueError("failed tool events must include an error")
        if self.status is ToolEventStatus.SUCCEEDED and self.error is not None:
            raise ValueError("succeeded tool events cannot include an error")
        return self


class RunStatus(StrEnum):
    """Lifecycle state of an analysis run."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class RunBudget(BaseModel):
    """Configured limits and observable usage counters for one run."""

    model_config = ConfigDict(extra="forbid")

    max_specialist_invocations: int = Field(default=12, ge=0)
    max_sql_executions: int = Field(default=20, ge=0)
    max_python_executions: int = Field(default=15, ge=0)
    max_critic_loops: int = Field(default=2, ge=0)
    max_charts: int = Field(default=4, ge=0)
    specialist_invocations: int = Field(default=0, ge=0)
    sql_executions: int = Field(default=0, ge=0)
    python_executions: int = Field(default=0, ge=0)
    critic_loops: int = Field(default=0, ge=0)
    charts_created: int = Field(default=0, ge=0)


class AnalysisRunState(BaseModel):
    """Persisted, observable state for one analysis run."""

    model_config = ConfigDict(extra="forbid")

    run_id: NonEmptyString
    objective: NonEmptyString
    business_context: NonEmptyString | None = None
    status: RunStatus = RunStatus.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    audit: AuditResult | None = None
    investigation_plan: list[NonEmptyString] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    rejected_hypotheses: list[NonEmptyString] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    open_questions: list[NonEmptyString] = Field(default_factory=list)
    artifacts: list[NonEmptyString] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    validation_results: list[ValidationResult] = Field(default_factory=list)
    tool_events: list[ToolEvent] = Field(default_factory=list)
    run_budget: RunBudget = Field(default_factory=RunBudget)

    @model_validator(mode="after")
    def timestamps_are_timezone_aware(self) -> "AnalysisRunState":
        for field_name in ("created_at", "updated_at"):
            timestamp = getattr(self, field_name)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError(f"{field_name} must include timezone information")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be on or after created_at")
        return self


# The project plan uses AnalysisLedger for this same persisted run document.
AnalysisLedger = AnalysisRunState
