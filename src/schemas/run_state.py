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
    attempt_id: NonEmptyString | None = None
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
    attempt_id: NonEmptyString | None = None
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
    # An external interruption stopped the run. It is reconciled with an
    # interrupted attempt rather than left as a stale ``running`` workspace.
    CANCELLED = "cancelled"


class RunBlockReason(StrEnum):
    """Machine-readable reason a run did not complete normally.

    Orchestration records the originating condition so downstream operational
    reporting never has to infer it from prose. Not every non-completion is a
    budget problem: a self-critique that still requires revision, an unresolved
    objective-critical follow-up, a schema violation, and an interruption are
    all distinct outcomes.
    """

    BUDGET_EXHAUSTED = "budget_exhausted"
    VALIDATION_REVISION = "validation_revision"
    UNRESOLVED_FOLLOW_UP = "unresolved_follow_up"
    AGENT_FAILURE = "agent_failure"
    SCHEMA_FAILURE = "schema_failure"
    TOOL_FAILURE = "tool_failure"
    PROVIDER_FAILURE = "provider_failure"
    SANDBOX_FAILURE = "sandbox_failure"
    WORKSPACE_FAILURE = "workspace_failure"
    DATA_QUALITY = "data_quality"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"
    OTHER = "other"


class AttemptStatus(StrEnum):
    """Lifecycle state of one append-only execution attempt."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"


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


def model_usage_snapshot(usage: object) -> ModelUsage:
    """Convert one Agents SDK usage object into the typed persisted form.

    The SDK reports usage both per response and cumulatively per run using the
    same attribute names, so one duck-typed adapter serves the response
    boundary and the end-of-run reconciliation.
    """

    if usage is None:
        return ModelUsage()
    if isinstance(usage, ModelUsage):
        return usage

    def _integer(name: str) -> int:
        return int(getattr(usage, name, 0) or 0)

    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return ModelUsage(
        requests=_integer("requests"),
        input_tokens=_integer("input_tokens"),
        output_tokens=_integer("output_tokens"),
        total_tokens=_integer("total_tokens"),
        cached_tokens=int(getattr(input_details, "cached_tokens", 0) or 0),
        reasoning_tokens=int(getattr(output_details, "reasoning_tokens", 0) or 0),
    )


def add_model_usage(first: ModelUsage, second: ModelUsage) -> ModelUsage:
    """Return the field-wise sum of two typed usage snapshots."""

    return ModelUsage(
        **{
            name: getattr(first, name) + getattr(second, name)
            for name in ModelUsage.model_fields
        }
    )


def subtract_model_usage(total: ModelUsage, recorded: ModelUsage) -> ModelUsage:
    """Return the part of ``total`` that has not been recorded yet.

    Fields are clamped at zero so a provider snapshot that is smaller than what
    was already persisted can never remove usage from the totals.
    """

    return ModelUsage(
        **{
            name: max(getattr(total, name) - getattr(recorded, name), 0)
            for name in ModelUsage.model_fields
        }
    )


class AttemptCostAvailability(StrEnum):
    """Whether an attempt-level cost delta was resolved."""

    KNOWN = "known"
    UNAVAILABLE = "unavailable"


class AttemptCost(BaseModel):
    """Explicit attempt-level cost delta; unknown is never encoded as zero."""

    model_config = ConfigDict(extra="forbid")

    availability: AttemptCostAvailability
    estimated_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    pricing_model: NonEmptyString | None = None
    note: NonEmptyString | None = None

    @model_validator(mode="after")
    def cost_is_explicit(self) -> "AttemptCost":
        if self.availability is AttemptCostAvailability.KNOWN:
            if self.estimated_cost_usd is None or self.pricing_model is None:
                raise ValueError(
                    "known attempt cost requires estimate and pricing model"
                )
        elif self.estimated_cost_usd is not None:
            raise ValueError("unavailable attempt cost must not contain an estimate")
        if self.availability is AttemptCostAvailability.UNAVAILABLE and not self.note:
            raise ValueError("unavailable attempt cost requires a note")
        return self


class AttemptRecord(BaseModel):
    """One append-only execution attempt and its observable deltas."""

    model_config = ConfigDict(extra="forbid")

    attempt_number: int = Field(ge=1)
    attempt_id: NonEmptyString
    status: AttemptStatus
    started_at: datetime
    finished_at: datetime | None = None
    usage_delta: ModelUsage = Field(default_factory=ModelUsage)
    # False when at least one model response for this attempt could not be
    # reconciled, so the delta is a lower bound rather than the full usage.
    usage_complete: bool = True
    cost: AttemptCost | None = None
    elapsed_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    error: NonEmptyString | None = None

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> "AttemptRecord":
        for field_name in ("started_at", "finished_at"):
            timestamp = getattr(self, field_name)
            if timestamp is not None and (
                timestamp.tzinfo is None or timestamp.utcoffset() is None
            ):
                raise ValueError(f"{field_name} must include timezone information")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must be on or after started_at")
        if self.status is AttemptStatus.RUNNING:
            if self.finished_at is not None or self.error is not None:
                raise ValueError("running attempts cannot have terminal metadata")
        elif self.finished_at is None:
            raise ValueError("terminal attempts require finished_at")
        if self.status is AttemptStatus.COMPLETED and self.error is not None:
            raise ValueError("completed attempts cannot contain an error")
        if self.status is not AttemptStatus.RUNNING and self.cost is None:
            raise ValueError("terminal attempts require explicit cost availability")
        return self


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
    attempt_number: int = Field(default=0, ge=0)
    attempt_id: NonEmptyString | None = None
    attempt_started_at: datetime | None = None
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
    attempt_history: list[AttemptRecord] = Field(default_factory=list)
    run_budget: RunBudget = Field(default_factory=RunBudget)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    # Defaults to True so pre-R14 workspaces stay loadable. It becomes False
    # only when a model call's usage is known to be missing from the totals.
    usage_complete: bool = True
    usage_incompleteness_note: NonEmptyString | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0)
    cost_breakdown: CostBreakdown | None = None
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    cost_estimation_note: NonEmptyString | None = None
    final_report: Artifact | None = None
    error: NonEmptyString | None = None
    # Explicit, machine-readable non-completion cause. Persisted by
    # orchestration so operational reporting never infers it from prose.
    block_reason: RunBlockReason | None = None
    block_detail: NonEmptyString | None = None

    @model_validator(mode="after")
    def timestamps_are_timezone_aware(self) -> "AnalysisRunState":
        for field_name in ("created_at", "updated_at"):
            timestamp = getattr(self, field_name)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError(f"{field_name} must include timezone information")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be on or after created_at")
        if self.attempt_started_at is not None and (
            self.attempt_started_at.tzinfo is None
            or self.attempt_started_at.utcoffset() is None
        ):
            raise ValueError("attempt_started_at must include timezone information")
        if self.attempt_number == 0 and self.attempt_id is not None:
            raise ValueError("attempt_id requires a positive attempt_number")
        if self.attempt_number > 0 and self.attempt_id is None:
            raise ValueError("attempt_number requires an attempt_id")
        if self.attempt_history:
            attempt_ids = [attempt.attempt_id for attempt in self.attempt_history]
            attempt_numbers = [
                attempt.attempt_number for attempt in self.attempt_history
            ]
            if len(attempt_ids) != len(set(attempt_ids)):
                raise ValueError("attempt history IDs must be unique")
            if len(attempt_numbers) != len(set(attempt_numbers)):
                raise ValueError("attempt history numbers must be unique")
            if self.attempt_number != self.attempt_history[-1].attempt_number:
                raise ValueError("attempt_number must match the latest attempt record")
            if self.attempt_id != self.attempt_history[-1].attempt_id:
                raise ValueError("attempt_id must match the latest attempt record")

            usage_totals = ModelUsage(
                requests=sum(
                    item.usage_delta.requests for item in self.attempt_history
                ),
                input_tokens=sum(
                    item.usage_delta.input_tokens for item in self.attempt_history
                ),
                output_tokens=sum(
                    item.usage_delta.output_tokens for item in self.attempt_history
                ),
                total_tokens=sum(
                    item.usage_delta.total_tokens for item in self.attempt_history
                ),
                cached_tokens=sum(
                    item.usage_delta.cached_tokens for item in self.attempt_history
                ),
                reasoning_tokens=sum(
                    item.usage_delta.reasoning_tokens for item in self.attempt_history
                ),
            )
            has_running_attempt = any(
                item.status is AttemptStatus.RUNNING for item in self.attempt_history
            )
            if not has_running_attempt and self.usage != usage_totals:
                raise ValueError("run usage must equal the sum of attempt deltas")
            if self.usage_complete and any(
                not item.usage_complete for item in self.attempt_history
            ):
                raise ValueError(
                    "run usage cannot be complete while an attempt delta is not"
                )
            elapsed_total = sum(item.elapsed_seconds for item in self.attempt_history)
            if not has_running_attempt and (
                self.elapsed_seconds is not None
                and abs(self.elapsed_seconds - elapsed_total) > 1e-9
            ):
                raise ValueError("run elapsed time must equal attempt elapsed deltas")
            if (
                not has_running_attempt
                and self.elapsed_seconds is None
                and elapsed_total
            ):
                raise ValueError("run elapsed time is missing attempt elapsed deltas")
            terminal_attempts = not has_running_attempt
            if terminal_attempts:
                attempt_costs = [item.cost for item in self.attempt_history]
                unknown_cost = any(
                    cost is None
                    or cost.availability is AttemptCostAvailability.UNAVAILABLE
                    for cost in attempt_costs
                )
                if unknown_cost and self.estimated_cost_usd is not None:
                    raise ValueError(
                        "unknown attempt cost cannot be represented as a known total"
                    )
                if not unknown_cost:
                    known_total = sum(
                        cost.estimated_cost_usd or 0 for cost in attempt_costs
                    )
                    if (
                        self.estimated_cost_usd is None
                        or abs(self.estimated_cost_usd - known_total) > 1e-12
                    ):
                        raise ValueError(
                            "run cost must equal the sum of attempt cost deltas"
                        )
        return self


# Backward-compatible name for the typed state document; the persistent store
# is implemented as orchestration.ledger.AnalysisLedger.
AnalysisLedger = AnalysisRunState
