"""Versioned, architecture-neutral contracts for offline evaluation.

The contracts in this module are deliberately separate from the agent and
scenario implementation modules.  A benchmark record describes what was
run, what was observed, and how it was scored; it does not prescribe how an
architecture performs the analysis.

Scenario ground truth belongs to :class:`ScenarioEvaluationSpec`.  The only
object intended for a model prompt is :class:`ModelVisibleScenarioContext`.
Keeping those types separate makes accidental evaluator leakage visible at
the boundary instead of relying on prompt discipline.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from scenarios.definitions.models import GroundTruthMetric, InjectedCondition
from schemas.metrics import MetricComparisonType
from schemas.statistics import StatisticalExpectation

EVALUATION_CONTRACT_VERSION = "1.0"
LEGACY_WORKSPACE_VERSION = "legacy"
SUPPORTED_WORKSPACE_VERSIONS = frozenset({LEGACY_WORKSPACE_VERSION, "1.0"})

NonEmptyString = Annotated[str, Field(min_length=1)]
VersionString = Annotated[str, Field(pattern=r"^\d+\.\d+$", min_length=3)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
BoundedScore = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
BoundedRate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
JsonObject = dict[str, JsonValue]


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")


class ContractModel(BaseModel):
    """Common strict configuration for persisted evaluation documents."""

    model_config = ConfigDict(extra="forbid")


class ModelVisibleScenarioContext(ContractModel):
    """Safe scenario context that may be supplied to an analysis model.

    This type intentionally has no injected conditions, expected findings,
    tolerances, evaluator rules, or ground-truth values.
    """

    contract_version: Literal[EVALUATION_CONTRACT_VERSION] = EVALUATION_CONTRACT_VERSION
    scenario_id: NonEmptyString
    scenario_version: VersionString
    name: NonEmptyString
    user_question: NonEmptyString


class ScenarioMetadata(ContractModel):
    """Public and reproducibility metadata for one evaluation scenario."""

    contract_version: Literal[EVALUATION_CONTRACT_VERSION] = EVALUATION_CONTRACT_VERSION
    scenario_id: NonEmptyString
    scenario_version: VersionString
    name: NonEmptyString
    seed: int = Field(ge=0)
    generation_config: JsonObject = Field(default_factory=dict)
    user_question: NonEmptyString
    evaluator_version: VersionString

    def model_visible_context(self) -> ModelVisibleScenarioContext:
        """Project metadata into the allow-listed model-visible contract."""

        return ModelVisibleScenarioContext(
            scenario_id=self.scenario_id,
            scenario_version=self.scenario_version,
            name=self.name,
            user_question=self.user_question,
        )


class EvaluatorCompatibility(ContractModel):
    """Workspace and evaluator versions accepted by a scenario evaluator."""

    evaluator_contract_version: VersionString
    supported_workspace_versions: tuple[NonEmptyString, ...] = Field(min_length=1)


class ScenarioEvaluationSpec(ContractModel):
    """Evaluator-only conditions, expectations, and scoring rules."""

    contract_version: Literal[EVALUATION_CONTRACT_VERSION] = EVALUATION_CONTRACT_VERSION
    scenario_id: NonEmptyString
    scenario_version: VersionString
    evaluator_version: VersionString
    injected_conditions: tuple[InjectedCondition, ...] = Field(min_length=1)
    expected_primary_driver: NonEmptyString
    expected_secondary_findings: tuple[NonEmptyString, ...] = Field(min_length=1)
    known_non_drivers: tuple[NonEmptyString, ...] = Field(min_length=1)
    expected_data_quality_findings: tuple[NonEmptyString, ...] = Field(min_length=1)
    ground_truth: tuple[GroundTruthMetric, ...] = Field(min_length=1)
    statistical_expectation: StatisticalExpectation | None = None
    compatibility: EvaluatorCompatibility = Field(
        default_factory=lambda: EvaluatorCompatibility(
            evaluator_contract_version=EVALUATION_CONTRACT_VERSION,
            supported_workspace_versions=tuple(sorted(SUPPORTED_WORKSPACE_VERSIONS)),
        )
    )

    @model_validator(mode="after")
    def identifiers_are_consistent(self) -> ScenarioEvaluationSpec:
        for metric in self.ground_truth:
            if not isinstance(metric.comparison_type, MetricComparisonType):
                raise ValueError("ground-truth comparison types must be typed")
        return self


class EvaluationCheckStatus(StrEnum):
    """Outcome of one deterministic evaluator check."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class EvaluationCheck(ContractModel):
    """One named, reproducible evaluation check."""

    check_id: NonEmptyString
    status: EvaluationCheckStatus
    message: NonEmptyString


class ScoreBreakdown(ContractModel):
    """Named analytical scores with an explicit overall score."""

    dimensions: dict[NonEmptyString, BoundedScore] = Field(min_length=1)
    overall_score: BoundedScore

    @model_validator(mode="after")
    def score_is_not_ambiguous(self) -> ScoreBreakdown:
        if "overall" in self.dimensions:
            raise ValueError(
                "use overall_score for the overall value, not an 'overall' dimension"
            )
        return self


class EvaluatorStatus(StrEnum):
    """Whether offline scoring produced a usable result."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    NOT_EVALUATED = "not_evaluated"


class EvaluatorResult(ContractModel):
    """Versioned offline evaluator output for one persisted run."""

    contract_version: Literal[EVALUATION_CONTRACT_VERSION] = EVALUATION_CONTRACT_VERSION
    result_id: NonEmptyString
    run_id: NonEmptyString
    scenario_id: NonEmptyString
    scenario_version: VersionString
    evaluator_version: VersionString
    status: EvaluatorStatus
    checks: tuple[EvaluationCheck, ...] = Field(min_length=1)
    score_breakdown: ScoreBreakdown | None = None
    failure_reasons: tuple[NonEmptyString, ...] = Field(default_factory=tuple)
    error_message: NonEmptyString | None = None
    evaluated_at: datetime

    @model_validator(mode="after")
    def result_has_explicit_outcome(self) -> EvaluatorResult:
        _require_timezone_aware(self.evaluated_at, "evaluated_at")
        failed_checks = tuple(
            check.check_id
            for check in self.checks
            if check.status is EvaluationCheckStatus.FAIL
        )
        if self.status in {EvaluatorStatus.PASS, EvaluatorStatus.FAIL}:
            if self.score_breakdown is None:
                raise ValueError(
                    "pass/fail evaluator results require a score_breakdown"
                )
            if self.status is EvaluatorStatus.PASS and (
                failed_checks or self.failure_reasons
            ):
                raise ValueError("passing evaluator results cannot contain failures")
            if self.status is EvaluatorStatus.FAIL and not (
                failed_checks or self.failure_reasons
            ):
                raise ValueError(
                    "failing evaluator results require a failed check or reason"
                )
            if self.error_message is not None:
                raise ValueError("pass/fail evaluator results cannot contain errors")
        else:
            if self.score_breakdown is not None:
                raise ValueError(
                    "non-scoring evaluator results cannot contain a score_breakdown"
                )
            if not self.error_message and not self.failure_reasons:
                raise ValueError(
                    "non-scoring evaluator results require an explicit reason"
                )
        return self


class ExecutionMode(StrEnum):
    """How the recorded architecture was executed."""

    LIVE = "live"
    DETERMINISTIC = "deterministic"
    REPLAY = "replay"


class RunConfiguration(ContractModel):
    """Architecture-neutral execution configuration frozen for a run."""

    execution_mode: ExecutionMode
    tool_contract_version: VersionString
    parameters: JsonObject = Field(default_factory=dict)


class BudgetConfiguration(ContractModel):
    """Hard resource and per-role turn limits used by a benchmark cell."""

    resource_limits: dict[NonEmptyString, int] = Field(min_length=1)
    turn_limits: dict[NonEmptyString, int] = Field(min_length=1)

    @model_validator(mode="after")
    def limits_are_non_negative(self) -> BudgetConfiguration:
        if any(limit < 0 for limit in self.resource_limits.values()):
            raise ValueError("resource limits must be non-negative")
        if any(limit < 1 for limit in self.turn_limits.values()):
            raise ValueError("turn limits must be positive")
        return self


class UsageSummary(ContractModel):
    """Observable model usage retained independently from analytical scores."""

    requests: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    cached_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def usage_is_consistent(self) -> UsageSummary:
        if self.cached_tokens > self.input_tokens:
            raise ValueError("cached_tokens cannot exceed input_tokens")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self


class CostAvailability(StrEnum):
    KNOWN = "known"
    UNAVAILABLE = "unavailable"


class CostSummary(ContractModel):
    """Explicit cost metadata; unavailable cost is never represented as zero."""

    availability: CostAvailability
    currency: NonEmptyString = "USD"
    estimated_cost_usd: FiniteFloat | None = Field(default=None, ge=0)
    pricing_model: NonEmptyString | None = None
    note: NonEmptyString | None = None

    @model_validator(mode="after")
    def cost_availability_is_explicit(self) -> CostSummary:
        if self.availability is CostAvailability.KNOWN:
            if self.estimated_cost_usd is None or self.pricing_model is None:
                raise ValueError("known cost requires estimated_cost and pricing_model")
        elif self.estimated_cost_usd is not None:
            raise ValueError("unavailable cost must not contain estimated_cost_usd")
        if self.availability is CostAvailability.UNAVAILABLE and not self.note:
            raise ValueError("unavailable cost requires a note")
        return self


class LatencySummary(ContractModel):
    """Wall-clock latency for the recorded run."""

    elapsed_seconds: FiniteFloat = Field(ge=0)
    started_at: datetime
    finished_at: datetime

    @model_validator(mode="after")
    def timestamps_match_latency_contract(self) -> LatencySummary:
        _require_timezone_aware(self.started_at, "started_at")
        _require_timezone_aware(self.finished_at, "finished_at")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be on or after started_at")
        return self


class LifecycleStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class FailureCategory(StrEnum):
    AGENT = "agent"
    TOOL = "tool"
    SCHEMA = "schema"
    BUDGET = "budget"
    TIMEOUT = "timeout"
    SANDBOX = "sandbox"
    PROVIDER = "provider"
    WORKSPACE = "workspace"
    OTHER = "other"


class LifecycleOutcome(ContractModel):
    """Operational outcome with a required taxonomy for non-successes."""

    status: LifecycleStatus
    failure_category: FailureCategory | None = None
    failure_message: NonEmptyString | None = None

    @model_validator(mode="after")
    def failures_are_classified(self) -> LifecycleOutcome:
        if self.status is LifecycleStatus.COMPLETED:
            if self.failure_category is not None or self.failure_message is not None:
                raise ValueError("completed lifecycle outcomes cannot contain failure")
        elif self.failure_category is None or self.failure_message is None:
            raise ValueError(
                "non-completed lifecycle outcomes require category and message"
            )
        return self


class CodeRevision(ContractModel):
    """Code identity captured when the repository exposes one."""

    revision: NonEmptyString
    dirty: bool = False


class SourceFileIdentity(ContractModel):
    """Hash and path identity for one immutable workspace source file."""

    path: NonEmptyString
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def path_is_an_approved_workspace_source(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not path.parts
            or path.is_absolute()
            or ".." in path.parts
            or normalized.startswith("./")
            or (len(normalized) > 1 and normalized[1] == ":")
            or path.parts[0] not in {"inputs", "docs"}
            or len(path.parts) < 2
        ):
            raise ValueError(
                "workspace source identity path must be a file under inputs/ or docs/"
            )
        return path.as_posix()


class WorkspaceIdentity(ContractModel):
    """Cryptographic identity binding a benchmark workspace to its generator."""

    contract_version: Literal[EVALUATION_CONTRACT_VERSION] = EVALUATION_CONTRACT_VERSION
    identity_version: Literal["1.0"] = "1.0"
    benchmark_manifest_id: NonEmptyString
    run_id: NonEmptyString
    scenario_id: NonEmptyString
    scenario_version: VersionString
    evaluator_version: VersionString
    architecture: NonEmptyString
    repetition: int = Field(ge=1)
    seed: int = Field(ge=0)
    source_files: tuple[SourceFileIdentity, ...] = Field(min_length=1)
    code_revision: CodeRevision | None = None

    @model_validator(mode="after")
    def source_paths_are_unique(self) -> WorkspaceIdentity:
        paths = [item.path for item in self.source_files]
        if len(paths) != len(set(paths)):
            raise ValueError("workspace source identity paths must be unique")
        return self


class BenchmarkRunRecord(ContractModel):
    """Immutable raw record for one scenario/architecture repetition."""

    contract_version: Literal[EVALUATION_CONTRACT_VERSION] = EVALUATION_CONTRACT_VERSION
    run_id: NonEmptyString
    repetition: int = Field(ge=1)
    scenario_id: NonEmptyString
    scenario_version: VersionString
    evaluator_version: VersionString
    architecture: NonEmptyString
    model: NonEmptyString
    model_provider: NonEmptyString
    run_configuration: RunConfiguration
    budgets: BudgetConfiguration
    code_revision: CodeRevision | None = None
    seed: int = Field(ge=0)
    workspace_path: NonEmptyString
    lifecycle: LifecycleOutcome
    evaluator_result: EvaluatorResult
    score_breakdown: ScoreBreakdown | None
    usage: UsageSummary
    cost: CostSummary
    latency: LatencySummary

    @model_validator(mode="after")
    def record_is_unambiguous(self) -> BenchmarkRunRecord:
        if self.evaluator_result.run_id != self.run_id:
            raise ValueError("evaluator_result.run_id must match run_id")
        if (
            self.evaluator_result.scenario_id != self.scenario_id
            or self.evaluator_result.scenario_version != self.scenario_version
            or self.evaluator_result.evaluator_version != self.evaluator_version
        ):
            raise ValueError(
                "evaluator result scenario and evaluator versions must match record"
            )
        result_score = self.evaluator_result.score_breakdown
        if (self.score_breakdown is None) != (result_score is None):
            raise ValueError(
                "record score_breakdown must agree with evaluator_result score"
            )
        if self.score_breakdown is not None and self.score_breakdown != result_score:
            raise ValueError(
                "record score_breakdown must equal evaluator_result score_breakdown"
            )
        if (
            self.lifecycle.status is LifecycleStatus.COMPLETED
            and self.evaluator_result.status is EvaluatorStatus.NOT_EVALUATED
        ):
            raise ValueError(
                "completed runs require a scored, failed, or explicitly errored "
                "offline evaluation"
            )
        return self


class ScenarioReference(ContractModel):
    """Scenario identity frozen into a benchmark declaration."""

    scenario_id: NonEmptyString
    scenario_version: VersionString
    evaluator_version: VersionString
    seed: int = Field(ge=0)
    source_files: tuple[SourceFileIdentity, ...] = Field(default_factory=tuple)


class UncertaintyInterval(ContractModel):
    """A confidence interval with an explicit method and sample size."""

    confidence_level: float = Field(gt=0, lt=1, allow_inf_nan=False)
    lower: FiniteFloat
    upper: FiniteFloat
    sample_size: int = Field(ge=2)
    method: Literal["student_t"] = "student_t"

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> UncertaintyInterval:
        if self.lower > self.upper:
            raise ValueError("uncertainty interval lower bound exceeds upper bound")
        return self


class DistributionSummary(ContractModel):
    """Deterministic descriptive statistics for one observed metric."""

    sample_size: int = Field(ge=0)
    mean: FiniteFloat | None = None
    stddev: FiniteFloat | None = Field(default=None, ge=0)
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None
    quantiles: dict[NonEmptyString, FiniteFloat] = Field(default_factory=dict)
    uncertainty: UncertaintyInterval | None = None
    uncertainty_status: Literal["estimable", "insufficient_sample", "no_observations"]

    @model_validator(mode="after")
    def statistics_match_sample_size(self) -> DistributionSummary:
        if self.sample_size == 0:
            if any(
                value is not None
                for value in (self.mean, self.stddev, self.minimum, self.maximum)
            ):
                raise ValueError("empty distributions cannot contain statistics")
            if self.quantiles or self.uncertainty is not None:
                raise ValueError("empty distributions cannot contain estimates")
            if self.uncertainty_status != "no_observations":
                raise ValueError("empty distributions require no_observations status")
        elif self.sample_size == 1:
            if any(value is None for value in (self.mean, self.minimum, self.maximum)):
                raise ValueError(
                    "one-observation distributions require location statistics"
                )
            if self.stddev is not None or self.uncertainty is not None:
                raise ValueError(
                    "one-observation distributions cannot estimate variance or "
                    "uncertainty"
                )
            if self.uncertainty_status != "insufficient_sample":
                raise ValueError(
                    "one-observation distributions require insufficient_sample status"
                )
        else:
            if any(
                value is None
                for value in (
                    self.mean,
                    self.stddev,
                    self.minimum,
                    self.maximum,
                )
            ):
                raise ValueError("multi-observation distributions require statistics")
            if self.uncertainty is None:
                raise ValueError(
                    "multi-observation distributions require an uncertainty interval"
                )
            if self.uncertainty.sample_size != self.sample_size:
                raise ValueError("uncertainty sample size must match distribution")
            if self.uncertainty_status != "estimable":
                raise ValueError(
                    "multi-observation distributions require estimable status"
                )
        if self.sample_size > 0 and not {"p25", "p50", "p75"}.issubset(self.quantiles):
            raise ValueError(
                "observed distributions require p25, p50, and p75 quantiles"
            )
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("distribution minimum exceeds maximum")
        if self.quantiles and self.minimum is not None and self.maximum is not None:
            if any(
                value < self.minimum or value > self.maximum
                for value in self.quantiles.values()
            ):
                raise ValueError("quantiles must lie within distribution bounds")
        return self


class AggregateDenominator(ContractModel):
    """Counts and rates whose denominator is the declared matrix cell count."""

    expected_repetitions: int = Field(ge=1)
    observed_repetitions: int = Field(ge=0)
    missing_repetitions: int = Field(ge=0)
    completed_runs: int = Field(ge=0)
    failed_runs: int = Field(ge=0)
    evaluated_runs: int = Field(ge=0)
    evaluator_error_runs: int = Field(default=0, ge=0)
    completion_rate: BoundedRate
    evaluation_rate: BoundedRate

    @model_validator(mode="after")
    def counts_use_declared_denominator(self) -> AggregateDenominator:
        if (
            self.observed_repetitions + self.missing_repetitions
            != self.expected_repetitions
        ):
            raise ValueError("observed and missing repetitions must equal expected")
        if self.completed_runs + self.failed_runs != self.observed_repetitions:
            raise ValueError(
                "completed and failed runs must equal observed repetitions"
            )
        if self.evaluated_runs > self.completed_runs:
            raise ValueError("evaluated runs cannot exceed completed runs")
        if self.evaluator_error_runs > self.observed_repetitions:
            raise ValueError("evaluator error runs cannot exceed observed repetitions")
        if self.evaluated_runs + self.evaluator_error_runs > self.observed_repetitions:
            raise ValueError(
                "evaluated and evaluator-error runs cannot exceed observed repetitions"
            )
        expected_completion = self.completed_runs / self.expected_repetitions
        expected_evaluation = self.evaluated_runs / self.expected_repetitions
        if abs(self.completion_rate - expected_completion) > 1e-12:
            raise ValueError(
                "completion_rate must use expected_repetitions denominator"
            )
        if abs(self.evaluation_rate - expected_evaluation) > 1e-12:
            raise ValueError(
                "evaluation_rate must use expected_repetitions denominator"
            )
        return self


class ArchitectureMetricComparison(ContractModel):
    """Descriptive and paired statistical comparison for one metric."""

    metric_key: NonEmptyString
    left_architecture: NonEmptyString
    right_architecture: NonEmptyString
    difference_definition: NonEmptyString
    left_sample_size: int = Field(ge=0)
    right_sample_size: int = Field(ge=0)
    paired_sample_size: int = Field(ge=0)
    paired_repetitions: tuple[int, ...] = Field(default_factory=tuple)
    mean_left: FiniteFloat | None = None
    mean_right: FiniteFloat | None = None
    mean_difference: FiniteFloat | None = None
    paired_difference_distribution: DistributionSummary
    alpha: float = Field(gt=0, lt=1, allow_inf_nan=False)
    p_value: FiniteFloat | None = Field(default=None, ge=0, le=1)
    test_method: Literal["paired_t", "not_estimable"]
    conclusion: Literal["supported_difference", "not_supported", "insufficient_sample"]

    @model_validator(mode="after")
    def comparison_is_consistent(self) -> ArchitectureMetricComparison:
        if self.paired_sample_size != len(self.paired_repetitions):
            raise ValueError("paired sample size must match paired repetitions")
        if self.paired_sample_size > min(self.left_sample_size, self.right_sample_size):
            raise ValueError("paired sample size cannot exceed either side")
        if self.paired_difference_distribution.sample_size != self.paired_sample_size:
            raise ValueError("paired distribution size must match paired sample size")
        if self.mean_difference != self.paired_difference_distribution.mean:
            raise ValueError("mean_difference must match paired distribution mean")
        if self.paired_sample_size < 2:
            if self.test_method != "not_estimable" or self.p_value is not None:
                raise ValueError("small paired samples cannot have a statistical test")
            if self.conclusion != "insufficient_sample":
                raise ValueError("small paired samples require insufficient_sample")
        elif self.test_method != "paired_t" or self.p_value is None:
            raise ValueError("estimable paired samples require a paired_t result")
        return self


class ArchitectureComparison(ContractModel):
    """Scenario comparison with descriptive and inferential fields separated."""

    scenario_id: NonEmptyString
    scenario_version: VersionString
    left_architecture: NonEmptyString
    right_architecture: NonEmptyString
    pairing_definition: NonEmptyString
    metrics: tuple[ArchitectureMetricComparison, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def architectures_are_distinct(self) -> ArchitectureComparison:
        if self.left_architecture == self.right_architecture:
            raise ValueError(
                "architecture comparisons require two distinct architectures"
            )
        return self


class BenchmarkTableRow(ContractModel):
    """Flat machine-readable row suitable for README/table generation."""

    scenario_id: NonEmptyString
    scenario_version: VersionString
    architecture: NonEmptyString
    expected_repetitions: int = Field(ge=1)
    observed_repetitions: int = Field(ge=0)
    missing_repetitions: int = Field(ge=0)
    completed_runs: int = Field(ge=0)
    failed_runs: int = Field(ge=0)
    evaluated_runs: int = Field(ge=0)
    evaluator_error_runs: int = Field(default=0, ge=0)
    completion_rate: BoundedRate
    evaluation_rate: BoundedRate
    overall_score_mean: FiniteFloat | None = None
    overall_score_ci_lower: FiniteFloat | None = None
    overall_score_ci_upper: FiniteFloat | None = None
    mean_estimated_cost: FiniteFloat | None = Field(default=None, ge=0)
    mean_elapsed_seconds: FiniteFloat | None = Field(default=None, ge=0)
    failure_taxonomy: dict[NonEmptyString, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def row_counts_are_consistent(self) -> BenchmarkTableRow:
        if (
            self.observed_repetitions + self.missing_repetitions
            != self.expected_repetitions
        ):
            raise ValueError(
                "table row observed and missing counts must equal expected"
            )
        if self.completed_runs + self.failed_runs != self.observed_repetitions:
            raise ValueError(
                "table row completed and failed counts must equal observed"
            )
        if (
            abs(self.completion_rate - self.completed_runs / self.expected_repetitions)
            > 1e-12
        ):
            raise ValueError("table row completion rate has the wrong denominator")
        if (
            abs(self.evaluation_rate - self.evaluated_runs / self.expected_repetitions)
            > 1e-12
        ):
            raise ValueError("table row evaluation rate has the wrong denominator")
        if self.evaluator_error_runs > self.observed_repetitions:
            raise ValueError("evaluator error runs cannot exceed observed repetitions")
        if self.evaluated_runs + self.evaluator_error_runs > self.observed_repetitions:
            raise ValueError(
                "evaluated and evaluator-error runs cannot exceed observed repetitions"
            )
        return self


class AggregateBenchmarkResult(ContractModel):
    """Deterministic aggregate for one scenario and architecture cell."""

    scenario_id: NonEmptyString
    scenario_version: VersionString
    architecture: NonEmptyString
    expected_repetitions: int = Field(ge=1)
    observed_repetitions: int = Field(ge=0)
    completed_runs: int = Field(ge=0)
    failed_runs: int = Field(ge=0)
    evaluated_runs: int = Field(ge=0)
    evaluator_error_runs: int = Field(default=0, ge=0)
    mean_scores: dict[NonEmptyString, BoundedScore] = Field(default_factory=dict)
    mean_estimated_cost: FiniteFloat | None = Field(default=None, ge=0)
    mean_elapsed_seconds: FiniteFloat = Field(ge=0)
    denominator: AggregateDenominator | None = None
    score_distributions: dict[NonEmptyString, DistributionSummary] = Field(
        default_factory=dict
    )
    cost_distribution: DistributionSummary | None = None
    latency_distribution: DistributionSummary | None = None
    failure_taxonomy: dict[NonEmptyString, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def aggregate_counts_are_consistent(self) -> AggregateBenchmarkResult:
        if self.observed_repetitions > self.expected_repetitions:
            raise ValueError("observed_repetitions cannot exceed expected_repetitions")
        if self.completed_runs + self.failed_runs > self.observed_repetitions:
            raise ValueError("completed and failed runs exceed observed repetitions")
        if self.evaluated_runs > self.completed_runs:
            raise ValueError("evaluated_runs cannot exceed completed_runs")
        if self.evaluator_error_runs > self.observed_repetitions:
            raise ValueError("evaluator error runs cannot exceed observed repetitions")
        if self.evaluated_runs + self.evaluator_error_runs > self.observed_repetitions:
            raise ValueError(
                "evaluated and evaluator-error runs cannot exceed observed repetitions"
            )
        if self.evaluated_runs and not self.mean_scores:
            raise ValueError("evaluated aggregates require mean_scores")
        if self.denominator is not None:
            if self.denominator.expected_repetitions != self.expected_repetitions:
                raise ValueError(
                    "aggregate denominator does not match expected repetitions"
                )
            if self.denominator.observed_repetitions != self.observed_repetitions:
                raise ValueError(
                    "aggregate denominator does not match observed repetitions"
                )
            if self.denominator.completed_runs != self.completed_runs:
                raise ValueError("aggregate denominator does not match completed runs")
            if self.denominator.failed_runs != self.failed_runs:
                raise ValueError("aggregate denominator does not match failed runs")
            if self.denominator.evaluated_runs != self.evaluated_runs:
                raise ValueError("aggregate denominator does not match evaluated runs")
            if self.denominator.evaluator_error_runs != self.evaluator_error_runs:
                raise ValueError(
                    "aggregate denominator does not match evaluator error runs"
                )
        if self.score_distributions:
            distribution_means = {
                key: summary.mean
                for key, summary in self.score_distributions.items()
                if summary.mean is not None
            }
            if distribution_means != self.mean_scores:
                raise ValueError("mean_scores must match score distribution means")
        return self


class ManifestStatus(StrEnum):
    DECLARED = "declared"
    RUNNING = "running"
    COMPLETE = "complete"
    ABORTED = "aborted"


class BenchmarkReport(ContractModel):
    """Portable aggregate report; raw records remain in the source manifest."""

    report_version: Literal["1.0"] = "1.0"
    manifest_id: NonEmptyString
    manifest_status: ManifestStatus
    aggregation_version: VersionString
    expected_matrix_cells: int = Field(ge=1)
    observed_raw_records: int = Field(ge=0)
    missing_matrix_cells: int = Field(ge=0)
    aggregates: tuple[AggregateBenchmarkResult, ...] = Field(min_length=1)
    architecture_comparisons: tuple[ArchitectureComparison, ...] = Field(
        default_factory=tuple
    )
    table_rows: tuple[BenchmarkTableRow, ...] = Field(min_length=1)


class BenchmarkManifest(ContractModel):
    """Frozen benchmark declaration plus raw records and aggregate results."""

    contract_version: Literal[EVALUATION_CONTRACT_VERSION] = EVALUATION_CONTRACT_VERSION
    manifest_id: NonEmptyString
    manifest_version: VersionString
    status: ManifestStatus
    created_at: datetime
    scenario_references: tuple[ScenarioReference, ...] = Field(min_length=1)
    architectures: tuple[NonEmptyString, ...] = Field(min_length=1)
    repetitions: int = Field(ge=1)
    model: NonEmptyString
    model_provider: NonEmptyString
    run_configuration: RunConfiguration
    budgets: BudgetConfiguration
    aggregation_version: VersionString
    run_records: tuple[BenchmarkRunRecord, ...] = Field(default_factory=tuple)
    aggregates: tuple[AggregateBenchmarkResult, ...] = Field(default_factory=tuple)
    architecture_comparisons: tuple[ArchitectureComparison, ...] = Field(
        default_factory=tuple
    )

    @model_validator(mode="after")
    def manifest_has_unique_and_matching_cells(self) -> BenchmarkManifest:
        _require_timezone_aware(self.created_at, "created_at")
        scenario_keys = {
            (item.scenario_id, item.scenario_version)
            for item in self.scenario_references
        }
        if len(scenario_keys) != len(self.scenario_references):
            raise ValueError("scenario_references must have unique scenario identities")
        if len(set(self.architectures)) != len(self.architectures):
            raise ValueError("architectures must be unique")

        run_ids = [record.run_id for record in self.run_records]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("run_records must have unique run_id values")
        cells = [
            (
                record.scenario_id,
                record.scenario_version,
                record.architecture,
                record.repetition,
            )
            for record in self.run_records
        ]
        if len(set(cells)) != len(cells):
            raise ValueError(
                "manifest cannot contain duplicate scenario/architecture repetitions"
            )

        for record in self.run_records:
            scenario = (record.scenario_id, record.scenario_version)
            if scenario not in scenario_keys:
                raise ValueError("run record references an undeclared scenario")
            if record.architecture not in self.architectures:
                raise ValueError("run record references an undeclared architecture")
            reference = next(
                item
                for item in self.scenario_references
                if (item.scenario_id, item.scenario_version) == scenario
            )
            if record.evaluator_version != reference.evaluator_version:
                raise ValueError(
                    "run evaluator version differs from scenario reference"
                )
            if record.seed != reference.seed:
                raise ValueError("run seed differs from scenario reference")
            if (
                record.model != self.model
                or record.model_provider != self.model_provider
            ):
                raise ValueError("run model identity differs from manifest")
            if record.run_configuration != self.run_configuration:
                raise ValueError("run configuration differs from manifest")
            if record.budgets != self.budgets:
                raise ValueError("run budgets differ from manifest")
            if record.repetition > self.repetitions:
                raise ValueError("run repetition exceeds manifest repetitions")

        aggregate_keys = [
            (item.scenario_id, item.scenario_version, item.architecture)
            for item in self.aggregates
        ]
        if len(set(aggregate_keys)) != len(aggregate_keys):
            raise ValueError("aggregates must have unique scenario/architecture cells")
        allowed_aggregate_keys = {
            (reference.scenario_id, reference.scenario_version, architecture)
            for reference in self.scenario_references
            for architecture in self.architectures
        }
        if not set(aggregate_keys).issubset(allowed_aggregate_keys):
            raise ValueError("aggregate references an undeclared benchmark cell")

        comparison_keys = [
            (
                item.scenario_id,
                item.scenario_version,
                item.left_architecture,
                item.right_architecture,
            )
            for item in self.architecture_comparisons
        ]
        if len(set(comparison_keys)) != len(comparison_keys):
            raise ValueError("architecture comparisons must be unique")
        allowed_comparison_scenarios = {
            (reference.scenario_id, reference.scenario_version)
            for reference in self.scenario_references
        }
        for comparison in self.architecture_comparisons:
            if (
                comparison.scenario_id,
                comparison.scenario_version,
            ) not in allowed_comparison_scenarios:
                raise ValueError(
                    "architecture comparison references an undeclared scenario"
                )
            if (
                comparison.left_architecture not in self.architectures
                or comparison.right_architecture not in self.architectures
            ):
                raise ValueError(
                    "architecture comparison references an undeclared architecture"
                )
        if self.status is ManifestStatus.COMPLETE:
            expected_cells = {
                (
                    reference.scenario_id,
                    reference.scenario_version,
                    architecture,
                    repetition,
                )
                for reference in self.scenario_references
                for architecture in self.architectures
                for repetition in range(1, self.repetitions + 1)
            }
            if set(cells) != expected_cells:
                raise ValueError(
                    "complete manifests must retain every declared run cell"
                )
            if set(aggregate_keys) != allowed_aggregate_keys:
                raise ValueError(
                    "complete manifests must retain one aggregate per benchmark cell"
                )
        return self


class WorkspaceVersionCompatibilityError(ValueError):
    """Raised when persisted workspace state is too new for this evaluator."""


def workspace_state_path(workspace_path: str | Path) -> Path:
    """Resolve a workspace directory or direct state file to its ledger path."""

    path = Path(workspace_path).expanduser()
    if path.name == "analysis_ledger.json":
        return path
    return path / "state" / "analysis_ledger.json"


def check_workspace_version_compatibility(workspace_path: str | Path) -> str:
    """Return the persisted state version, accepting the pre-versioned format.

    The Phase 1 canonical workspaces predate this field.  They are explicitly
    treated as the supported ``legacy`` format so offline evaluation continues
    to work.  A future/unknown version fails with an actionable compatibility
    message rather than a generic JSON validation error.
    """

    state_path = workspace_state_path(workspace_path)
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceVersionCompatibilityError(
            f"persisted workspace state is missing: {state_path}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceVersionCompatibilityError(
            f"persisted workspace state cannot be read as JSON: {state_path}"
        ) from exc

    version = raw.get("schema_version", LEGACY_WORKSPACE_VERSION)
    if not isinstance(version, str) or version not in SUPPORTED_WORKSPACE_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_WORKSPACE_VERSIONS))
        raise WorkspaceVersionCompatibilityError(
            "persisted workspace schema version "
            f"{version!r} is not supported by this evaluator; supported versions: "
            f"{supported}. Migrate or re-run the workspace with a compatible "
            "evaluator."
        )
    return version


__all__ = [
    "AggregateDenominator",
    "AggregateBenchmarkResult",
    "ArchitectureComparison",
    "ArchitectureMetricComparison",
    "BenchmarkReport",
    "BenchmarkTableRow",
    "BenchmarkManifest",
    "BenchmarkRunRecord",
    "BudgetConfiguration",
    "CodeRevision",
    "CostAvailability",
    "CostSummary",
    "DistributionSummary",
    "EVALUATION_CONTRACT_VERSION",
    "EvaluationCheck",
    "EvaluationCheckStatus",
    "EvaluatorCompatibility",
    "EvaluatorResult",
    "EvaluatorStatus",
    "ExecutionMode",
    "FailureCategory",
    "LEGACY_WORKSPACE_VERSION",
    "LatencySummary",
    "LifecycleOutcome",
    "LifecycleStatus",
    "ModelVisibleScenarioContext",
    "RunConfiguration",
    "ScenarioEvaluationSpec",
    "ScenarioMetadata",
    "ScenarioReference",
    "ScoreBreakdown",
    "SourceFileIdentity",
    "SUPPORTED_WORKSPACE_VERSIONS",
    "UncertaintyInterval",
    "UsageSummary",
    "WorkspaceIdentity",
    "WorkspaceVersionCompatibilityError",
    "check_workspace_version_compatibility",
    "workspace_state_path",
]
