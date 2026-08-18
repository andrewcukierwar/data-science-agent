"""Schemas for hypotheses, tool events, budgets, and persisted run state."""

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.audit import AuditResult
from schemas.findings import Finding, SpecialistResult
from schemas.hypotheses import (  # noqa: F401
    Hypothesis,
    HypothesisStatus,
    NonEmptyString,
)
from schemas.metrics import MetricComparison
from schemas.statistics import StatisticalAssessment
from schemas.validation import ValidationIssue, ValidationResult


class SpecialistResultRecord(BaseModel):
    """Persisted typed output from one non-manager specialist invocation."""

    model_config = ConfigDict(extra="forbid")

    agent_role: NonEmptyString
    result: SpecialistResult


class ArtifactKind(StrEnum):
    """Kinds of persisted analysis artifacts."""

    QUERY = "query"
    SCRIPT = "script"
    CHART = "chart"
    REPORT = "report"
    OTHER = "other"


class Artifact(BaseModel):
    """A relative workspace path retained as reproducible run evidence."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    path: NonEmptyString
    kind: ArtifactKind = ArtifactKind.OTHER
    media_type: NonEmptyString | None = None
    description: NonEmptyString | None = None
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("path")
    @classmethod
    def path_must_be_relative(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not path.parts
            or path.is_absolute()
            or ".." in path.parts
            or normalized.startswith("./")
            or (len(normalized) > 1 and normalized[1] == ":")
        ):
            raise ValueError("artifact path must be relative to the workspace root")
        return normalized

    @model_validator(mode="after")
    def created_at_is_timezone_aware(self) -> "Artifact":
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must include timezone information")
        return self


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


class AgentEventStatus(StrEnum):
    """Outcome of one observable agent invocation."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentEvent(BaseModel):
    """Concise trace entry for an agent invocation.

    Agent outputs are persisted through their typed domain records. This trace
    deliberately stores lifecycle and identity metadata rather than raw model
    transcripts.
    """

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    agent_name: NonEmptyString
    agent_role: NonEmptyString
    status: AgentEventStatus
    started_at: datetime
    completed_at: datetime | None = None
    model: NonEmptyString | None = None
    objective: NonEmptyString | None = None
    output_type: NonEmptyString | None = None
    error: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "AgentEvent":
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must be on or after started_at")
        if self.status is AgentEventStatus.FAILED and self.error is None:
            raise ValueError("failed agent events must include an error")
        if self.status is AgentEventStatus.SUCCEEDED and self.error is not None:
            raise ValueError("succeeded agent events cannot include an error")
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
    max_sql_executions: int = Field(default=30, ge=0)
    max_python_executions: int = Field(default=20, ge=0)
    max_critic_loops: int = Field(default=2, ge=0)
    max_charts: int = Field(default=4, ge=0)
    specialist_invocations: int = Field(default=0, ge=0)
    sql_executions: int = Field(default=0, ge=0)
    python_executions: int = Field(default=0, ge=0)
    critic_loops: int = Field(default=0, ge=0)
    charts_created: int = Field(default=0, ge=0)


class ModelUsage(BaseModel):
    """Aggregated Agents SDK request and token usage for one run."""

    model_config = ConfigDict(extra="forbid")

    requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


class ModelPricing(BaseModel):
    """Provider pricing expressed in USD per one million tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_per_1m: float = Field(ge=0)
    cached_input_per_1m: float = Field(ge=0)
    output_per_1m: float = Field(ge=0)


class CostBreakdown(BaseModel):
    """Persisted, reproducible estimate of model cost by token category."""

    model_config = ConfigDict(extra="forbid")

    pricing_model: NonEmptyString
    input_per_1m: float = Field(ge=0)
    cached_input_per_1m: float = Field(ge=0)
    output_per_1m: float = Field(ge=0)
    input_tokens: int = Field(ge=0)
    cached_tokens: int = Field(ge=0)
    uncached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    uncached_input_cost_usd: float = Field(ge=0)
    cached_input_cost_usd: float = Field(ge=0)
    output_cost_usd: float = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)

    @model_validator(mode="after")
    def token_counts_are_consistent(self) -> "CostBreakdown":
        if self.cached_tokens > self.input_tokens:
            raise ValueError("cached_tokens cannot exceed input_tokens")
        if self.uncached_input_tokens != self.input_tokens - self.cached_tokens:
            raise ValueError(
                "uncached_input_tokens must equal input_tokens - cached_tokens"
            )
        return self


class AnalysisRunState(BaseModel):
    """Persisted, observable state for one analysis run."""

    model_config = ConfigDict(extra="forbid")

    # Added after the Phase 1 canonical workspaces were created.  The default
    # keeps those workspaces loadable while new persisted state advertises the
    # schema understood by the offline evaluation contracts.
    schema_version: str = "1.0"
    run_id: NonEmptyString
    objective: NonEmptyString
    business_context: NonEmptyString | None = None
    model: NonEmptyString | None = None
    model_provider: NonEmptyString | None = None
    status: RunStatus = RunStatus.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    audit: AuditResult | None = None
    investigation_plan: list[NonEmptyString] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    hypothesis_history: list[Hypothesis] = Field(default_factory=list)
    rejected_hypotheses: list[NonEmptyString] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    metric_comparisons: list[MetricComparison] = Field(default_factory=list)
    statistical_assessments: list[StatisticalAssessment] = Field(default_factory=list)
    open_questions: list[NonEmptyString] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    validation_results: list[ValidationResult] = Field(default_factory=list)
    specialist_results: list[SpecialistResultRecord] = Field(default_factory=list)
    tool_events: list[ToolEvent] = Field(default_factory=list)
    agent_events: list[AgentEvent] = Field(default_factory=list)
    run_budget: RunBudget = Field(default_factory=RunBudget)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    elapsed_seconds: float | None = Field(default=None, ge=0)
    cost_breakdown: CostBreakdown | None = None
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    cost_estimation_note: NonEmptyString | None = None
    final_report: Artifact | None = None
    error: NonEmptyString | None = None

    @model_validator(mode="after")
    def timestamps_are_timezone_aware(self) -> "AnalysisRunState":
        for field_name in ("created_at", "updated_at"):
            timestamp = getattr(self, field_name)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError(f"{field_name} must include timezone information")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be on or after created_at")
        return self


# Backward-compatible name for the typed state document; the persistent store
# is implemented as orchestration.ledger.AnalysisLedger.
AnalysisLedger = AnalysisRunState
