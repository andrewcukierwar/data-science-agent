"""Resumable, immutable-workspace benchmark matrix execution.

This module is deliberately separate from the agent runners.  It plans and
records scenario x architecture x repetition cells, while the selected
architecture runner remains responsible for one analysis workspace.  Offline
rescore uses only the persisted workspaces and the deterministic evaluator.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents.runtime import DEFAULT_EVIDENCE_CORRECTION_ATTEMPTS
from benchmark.aggregation import AGGREGATION_VERSION, aggregate_manifest
from evaluation.contracts import (
    BenchmarkManifest,
    BenchmarkRunRecord,
    BudgetConfiguration,
    CodeRevision,
    CostAvailability,
    CostSummary,
    EvaluationCheck,
    EvaluationCheckStatus,
    EvaluatorResult,
    EvaluatorStatus,
    ExecutionMode,
    FailureCategory,
    LatencySummary,
    LifecycleOutcome,
    LifecycleStatus,
    ManifestStatus,
    PilotSetDeclaration,
    PilotStratumDeclaration,
    RunConfiguration,
    ScenarioReference,
    ScoreBreakdown,
    SourceFileIdentity,
    UsageSummary,
    WorkspaceIdentity,
)
from evaluation.engine import (
    ScenarioRules,
    dump_stable_json,
    evaluate_workspace,
    load_manifest,
    rescore_manifest,
)
from evaluation.output import (
    OfflineOutputError,
    canonical_path,
    ensure_distinct_paths,
    ensure_output_is_new,
    write_exclusive_text,
)
from evaluation.workspace_identity import (
    WorkspaceIdentityError,
    persist_workspace_identity,
    source_file_identities_for_roots,
    verify_workspace_identity,
)
from orchestration.ledger import AnalysisLedger
from scenarios import discover_scenarios
from scenarios.catalog import ScenarioCatalog, ScenarioRegistration
from schemas.run_state import RunBlockReason, RunBudget
from tools.workspace import Workspace, WorkspaceManager

BENCHMARK_RUNNER_VERSION = "1.0"
TOOL_CONTRACT_VERSION = "1.0"
DEFAULT_ARCHITECTURES = ("multi-agent", "single-agent")
DEFAULT_REPETITIONS = 3
_RUN_ID_SAFE = re.compile(r"[^A-Za-z0-9_-]+")
_RUN_ID_VALID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


class BenchmarkError(RuntimeError):
    """Raised when a benchmark declaration or lifecycle is unsafe to continue."""


@dataclass(frozen=True, slots=True)
class BenchmarkCell:
    """One immutable matrix cell selected for execution."""

    scenario: ScenarioRegistration
    architecture: str
    repetition: int
    run_id: str
    workspace_path: Path
    inputs_source: Path
    docs_source: Path

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (
            self.scenario.scenario_id,
            self.scenario.scenario_version,
            self.architecture,
            self.repetition,
        )


@dataclass(frozen=True, slots=True)
class BenchmarkCellResult:
    """Normalized output accepted from a real or deterministic fake runner."""

    lifecycle: LifecycleOutcome
    workspace: Workspace | None = None
    state: Any | None = None
    evaluator_result: EvaluatorResult | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkExecutionSummary:
    """Result of one plan execution or resume pass."""

    manifest: BenchmarkManifest
    executed_run_ids: tuple[str, ...]
    skipped_run_ids: tuple[str, ...]
    failed_run_ids: tuple[str, ...]
    interrupted: bool = False


class BenchmarkPilotReport(BaseModel):
    """Persisted one-cell cost-estimation pilot."""

    model_config = ConfigDict(extra="forbid")

    report_version: Literal["1.1"] = "1.1"
    pilot_id: str = Field(min_length=1)
    manifest_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: str = Field(min_length=1)
    model_provider: str = Field(min_length=1)
    run_configuration: RunConfiguration
    budgets: BudgetConfiguration
    planned_cells: int = Field(ge=1)
    observed_requests: int = Field(ge=0)
    observed_input_tokens: int = Field(ge=0)
    observed_cached_tokens: int = Field(ge=0)
    observed_output_tokens: int = Field(ge=0)
    observed_reasoning_tokens: int = Field(ge=0)
    observed_total_tokens: int = Field(ge=0)
    observed_cost_usd: float | None = Field(default=None, ge=0)
    observed_cost: CostSummary
    estimated_full_matrix_cost_usd: float | None = Field(default=None, ge=0)
    observed_elapsed_seconds: float = Field(ge=0)
    observed_started_at: datetime
    observed_finished_at: datetime
    estimated_full_matrix_elapsed_seconds: float = Field(ge=0)
    created_at: datetime
    methodology: str = Field(min_length=1)


class PilotScalingMethod(StrEnum):
    """How per-pilot observations are scaled to the remaining matrix."""

    STRATIFIED_MEAN = "stratified_mean"


class PilotObservation(BaseModel):
    """One measured pilot cell, bound to its immutable run record."""

    model_config = ConfigDict(extra="forbid")

    stratum_id: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    scenario_version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_requests: int = Field(ge=0)
    observed_input_tokens: int = Field(ge=0)
    observed_cached_tokens: int = Field(ge=0)
    observed_output_tokens: int = Field(ge=0)
    observed_reasoning_tokens: int = Field(ge=0)
    observed_total_tokens: int = Field(ge=0)
    observed_cost: CostSummary
    observed_cost_usd: float | None = Field(default=None, ge=0)
    observed_elapsed_seconds: float = Field(ge=0)
    observed_started_at: datetime
    observed_finished_at: datetime


class PilotStratumEstimate(BaseModel):
    """Per-stratum observations and the estimate derived from them."""

    model_config = ConfigDict(extra="forbid")

    stratum_id: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    scenario_ids: tuple[str, ...] = Field(default_factory=tuple)
    planned_cells: int = Field(ge=1)
    observations: tuple[PilotObservation, ...] = Field(min_length=1)
    mean_cost_usd: float | None = Field(default=None, ge=0)
    min_cost_usd: float | None = Field(default=None, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    mean_elapsed_seconds: float = Field(ge=0)
    min_elapsed_seconds: float = Field(ge=0)
    max_elapsed_seconds: float = Field(ge=0)
    estimated_elapsed_seconds: float = Field(ge=0)
    cost_availability: CostAvailability


class BenchmarkPilotSetReport(BaseModel):
    """A declared pilot set and the stratified estimate derived from it.

    One cell is never presented as representative of the whole matrix: each
    stratum keeps its own observations, and the matrix estimate is the sum of
    per-stratum estimates with an explicit range.
    """

    model_config = ConfigDict(extra="forbid")

    report_version: Literal["2.0"] = "2.0"
    pilot_id: str = Field(min_length=1)
    manifest_id: str = Field(min_length=1)
    manifest_version: str = Field(min_length=1)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: str = Field(min_length=1)
    model_provider: str = Field(min_length=1)
    run_configuration: RunConfiguration
    budgets: BudgetConfiguration
    planned_cells: int = Field(ge=1)
    scaling_method: PilotScalingMethod = PilotScalingMethod.STRATIFIED_MEAN
    strata: tuple[PilotStratumEstimate, ...] = Field(min_length=1)
    estimated_full_matrix_cost_usd: float | None = Field(default=None, ge=0)
    estimated_full_matrix_cost_low_usd: float | None = Field(default=None, ge=0)
    estimated_full_matrix_cost_high_usd: float | None = Field(default=None, ge=0)
    estimated_full_matrix_elapsed_seconds: float = Field(ge=0)
    estimated_full_matrix_elapsed_low_seconds: float = Field(ge=0)
    estimated_full_matrix_elapsed_high_seconds: float = Field(ge=0)
    cost_availability: CostAvailability
    unknown_cost_record_digests: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime
    methodology: str = Field(min_length=1)

    @property
    def observations(self) -> tuple[PilotObservation, ...]:
        """Every measured pilot cell across all strata."""

        return tuple(
            observation
            for stratum in self.strata
            for observation in stratum.observations
        )


ArchitectureExecutor = Callable[[BenchmarkCell, Workspace], object]
SourcePreparer = Callable[[ScenarioRegistration, Path], tuple[Path, Path]]
RunIdFactory = Callable[[str, str, str, str, int], str]
RuleMap = Mapping[str | tuple[str, str], ScenarioRules]


def _now() -> datetime:
    return datetime.now(UTC)


def output_schema_fingerprint() -> str:
    """Hash every production agent output schema.

    A pilot measures a specific structured-output contract. If an agent output
    type changes, the pilot's token and cost observations describe a different
    system, so the estimate must not be reused.
    """

    from agents.output_contract import PRODUCTION_AGENT_OUTPUT_TYPES

    payload = {
        role.value: output_type.model_json_schema()
        for role, output_type in sorted(
            PRODUCTION_AGENT_OUTPUT_TYPES.items(),
            key=lambda item: item[0].value,
        )
    }
    return sha256(dump_stable_json(payload).encode("utf-8")).hexdigest()


def canonical_manifest_declaration_digest(manifest: BenchmarkManifest) -> str:
    """Hash the frozen declaration a pilot estimate depends on.

    Records, aggregates, and status are excluded: they change as the matrix
    executes. Model identity, turn budgets, matrix size, and the declared pilot
    set are included, so changing any of them invalidates existing pilot
    evidence and requires a new manifest version.
    """

    payload = manifest.model_dump(mode="json")
    for mutable in (
        "status",
        "run_records",
        "aggregates",
        "architecture_comparisons",
        "unknown_cost_acknowledged",
        "unknown_cost_pilot_id",
        "unknown_cost_pilot_record_digest",
        "unknown_cost_pilot_record_digests",
        "created_at",
    ):
        payload.pop(mutable, None)
    # Source-file identities are bound to each scenario as its cells run, so
    # they are mutable during execution. R8 verifies them per workspace; the
    # declaration digest covers the frozen scenario identity only.
    for reference in payload.get("scenario_references", []):
        reference.pop("source_files", None)
    return sha256(dump_stable_json(payload).encode("utf-8")).hexdigest()


def default_pilot_set(
    architectures: Sequence[str],
    *,
    scenario_count: int,
    repetitions: int,
) -> PilotSetDeclaration:
    """Declare one stratum per architecture covering its share of the matrix."""

    cells_per_architecture = scenario_count * repetitions
    return PilotSetDeclaration(
        strata=tuple(
            PilotStratumDeclaration(
                stratum_id=f"architecture:{architecture}",
                architecture=architecture,
                planned_cells=cells_per_architecture,
            )
            for architecture in architectures
        )
    )


def _declared_cell_count(manifest: BenchmarkManifest) -> int:
    return (
        len(manifest.scenario_references)
        * len(manifest.architectures)
        * manifest.repetitions
    )


def _stratum_estimate(
    stratum: PilotStratumDeclaration,
    observations: tuple[PilotObservation, ...],
) -> PilotStratumEstimate:
    """Scale one stratum's observations to its share of the matrix."""

    costs = [
        observation.observed_cost_usd
        for observation in observations
        if observation.observed_cost_usd is not None
    ]
    known = len(costs) == len(observations)
    elapsed = [observation.observed_elapsed_seconds for observation in observations]
    mean_elapsed = sum(elapsed) / len(elapsed)
    mean_cost = sum(costs) / len(costs) if known and costs else None
    return PilotStratumEstimate(
        stratum_id=stratum.stratum_id,
        architecture=stratum.architecture,
        scenario_ids=stratum.scenario_ids,
        planned_cells=stratum.planned_cells,
        observations=observations,
        mean_cost_usd=mean_cost,
        min_cost_usd=min(costs) if known and costs else None,
        max_cost_usd=max(costs) if known and costs else None,
        estimated_cost_usd=(
            mean_cost * stratum.planned_cells if mean_cost is not None else None
        ),
        mean_elapsed_seconds=mean_elapsed,
        min_elapsed_seconds=min(elapsed),
        max_elapsed_seconds=max(elapsed),
        estimated_elapsed_seconds=mean_elapsed * stratum.planned_cells,
        cost_availability=(
            CostAvailability.KNOWN if known and costs else CostAvailability.UNAVAILABLE
        ),
    )


def _matrix_estimate(strata: Sequence[PilotStratumEstimate]) -> dict[str, object]:
    """Sum per-stratum estimates into an explicit matrix range.

    Cost is published only when every stratum has a known cost; one unknown
    stratum makes the whole matrix estimate unavailable rather than silently
    understated.
    """

    known = all(item.cost_availability is CostAvailability.KNOWN for item in strata)
    return {
        "estimated_full_matrix_cost_usd": (
            sum(item.estimated_cost_usd or 0.0 for item in strata) if known else None
        ),
        "estimated_full_matrix_cost_low_usd": (
            sum((item.min_cost_usd or 0.0) * item.planned_cells for item in strata)
            if known
            else None
        ),
        "estimated_full_matrix_cost_high_usd": (
            sum((item.max_cost_usd or 0.0) * item.planned_cells for item in strata)
            if known
            else None
        ),
        "estimated_full_matrix_elapsed_seconds": sum(
            item.estimated_elapsed_seconds for item in strata
        ),
        "estimated_full_matrix_elapsed_low_seconds": sum(
            item.min_elapsed_seconds * item.planned_cells for item in strata
        ),
        "estimated_full_matrix_elapsed_high_seconds": sum(
            item.max_elapsed_seconds * item.planned_cells for item in strata
        ),
        "cost_availability": (
            CostAvailability.KNOWN if known else CostAvailability.UNAVAILABLE
        ),
    }


def _require_reconciled_observation(
    observation: PilotObservation,
    record: BenchmarkRunRecord,
) -> None:
    """Refuse a pilot observation that no longer matches its run record."""

    if not record.usage.complete:
        raise BenchmarkError(
            f"pilot usage is incomplete for {record.run_id}; the cost gate "
            "requires reconciled provider usage"
        )

    mismatches = [
        name
        for name, observed, recorded in (
            ("request usage", observation.observed_requests, record.usage.requests),
            (
                "input-token usage",
                observation.observed_input_tokens,
                record.usage.input_tokens,
            ),
            (
                "cached-token usage",
                observation.observed_cached_tokens,
                record.usage.cached_tokens,
            ),
            (
                "output-token usage",
                observation.observed_output_tokens,
                record.usage.output_tokens,
            ),
            (
                "reasoning-token usage",
                observation.observed_reasoning_tokens,
                record.usage.reasoning_tokens,
            ),
            (
                "total-token usage",
                observation.observed_total_tokens,
                record.usage.total_tokens,
            ),
            (
                "latency",
                observation.observed_elapsed_seconds,
                record.latency.elapsed_seconds,
            ),
            (
                "latency start",
                observation.observed_started_at,
                record.latency.started_at,
            ),
            (
                "latency finish",
                observation.observed_finished_at,
                record.latency.finished_at,
            ),
            ("cost breakdown", observation.observed_cost, record.cost),
            (
                "cost",
                observation.observed_cost_usd,
                record.cost.estimated_cost_usd,
            ),
        )
        if observed != recorded
    ]
    if mismatches:
        raise BenchmarkError(
            f"pilot report {mismatches[0]} does not match the run record for "
            f"{observation.run_id}"
        )
    if record.cost.availability is CostAvailability.KNOWN and (
        record.cost.estimated_cost_usd is None or not record.cost.pricing_model
    ):
        raise BenchmarkError("known pilot pricing requires a cost and pricing model")


def canonical_run_record_digest(record: BenchmarkRunRecord) -> str:
    """Hash the canonical JSON representation of one immutable run record."""

    payload = dump_stable_json(record.model_dump(mode="json"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _cell_key_string(
    scenario_id: str,
    scenario_version: str,
    architecture: str,
    repetition: int,
) -> str:
    return f"{scenario_id}@{scenario_version}|{architecture}|r{repetition}"


def _default_run_id(
    manifest_id: str,
    scenario_id: str,
    scenario_version: str,
    architecture: str,
    repetition: int,
) -> str:
    values = (
        manifest_id,
        scenario_id,
        scenario_version.replace(".", "_"),
        architecture,
        f"r{repetition}",
    )
    return _RUN_ID_SAFE.sub("-", "-".join(values)).strip("-")


def _capture_code_revision() -> CodeRevision | None:
    """Capture the exact local repository state without network access.

    A commit plus a dirty boolean is not an identity: two different uncommitted
    patches share both values. Hash the binary diff and every untracked,
    non-ignored file so resuming after any working-tree change is refused.
    """

    try:
        root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if root_result.returncode != 0:
            return None
        repository_root = Path(root_result.stdout.strip())
        revision_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if revision_result.returncode != 0:
            return None
        revision = revision_result.stdout.strip()
        if not revision:
            return None
        dirty_result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z"],
            cwd=repository_root,
            capture_output=True,
            check=False,
        )
        if dirty_result.returncode != 0:
            return None
        dirty = bool(dirty_result.stdout)
        working_tree_digest: str | None = None
        if dirty:
            content = sha256()
            diff_result = subprocess.run(
                ["git", "diff", "--binary", "HEAD"],
                cwd=repository_root,
                capture_output=True,
                check=False,
            )
            if diff_result.returncode != 0:
                return None
            content.update(diff_result.stdout)
            untracked_result = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard", "-z"],
                cwd=repository_root,
                capture_output=True,
                check=False,
            )
            if untracked_result.returncode != 0:
                return None
            for encoded_path in sorted(
                path for path in untracked_result.stdout.split(b"\0") if path
            ):
                content.update(b"\0path\0")
                content.update(encoded_path)
                candidate = repository_root / os.fsdecode(encoded_path)
                if candidate.is_file():
                    content.update(b"\0content\0")
                    content.update(candidate.read_bytes())
            working_tree_digest = content.hexdigest()
        return CodeRevision(
            revision=revision,
            dirty=dirty,
            working_tree_digest=working_tree_digest,
        )
    except OSError:
        return None


def _default_budgets() -> BudgetConfiguration:
    return BudgetConfiguration(
        resource_limits={
            "specialist_invocations": 12,
            # Keep bounded headroom for Critic-requested corrections after the
            # initial canonical profitability analysis.
            "sql": 40,
            "python": 20,
            # One initial review plus up to two remediation/re-review cycles.
            "critic_loops": 3,
            "charts": 4,
        },
        turn_limits={
            "lead": 16,
            "generalist": 16,
            "data_auditor": 12,
            "analyst": 10,
            "statistician": 10,
            "critic": 8,
        },
    )


def _check_status(message: str, *, failed: bool = True) -> EvaluationCheck:
    return EvaluationCheck(
        check_id="offline:not_evaluated",
        status=EvaluationCheckStatus.FAIL if failed else EvaluationCheckStatus.WARN,
        message=message,
    )


def _not_evaluated(
    cell: BenchmarkCell,
    message: str,
    *,
    status: EvaluatorStatus = EvaluatorStatus.NOT_EVALUATED,
    evaluator_version: str | None = None,
    evaluated_at: datetime | None = None,
) -> EvaluatorResult:
    resolved_evaluator_version = (
        evaluator_version or cell.scenario.metadata.evaluator_version
    )
    return EvaluatorResult(
        result_id=f"{cell.run_id}-{resolved_evaluator_version}",
        run_id=cell.run_id,
        scenario_id=cell.scenario.scenario_id,
        scenario_version=cell.scenario.scenario_version,
        evaluator_version=resolved_evaluator_version,
        status=status,
        checks=(_check_status(message),),
        error_message=message if status is not EvaluatorStatus.FAIL else None,
        failure_reasons=(message,) if status is EvaluatorStatus.FAIL else (),
        score_breakdown=(
            ScoreBreakdown(dimensions={"offline_evaluation": 0.0}, overall_score=0.0)
            if status is EvaluatorStatus.FAIL
            else None
        ),
        evaluated_at=evaluated_at or _now(),
    )


_BLOCK_REASON_CATEGORIES: dict[RunBlockReason, FailureCategory] = {
    RunBlockReason.BUDGET_EXHAUSTED: FailureCategory.BUDGET,
    RunBlockReason.VALIDATION_REVISION: FailureCategory.VALIDATION,
    RunBlockReason.UNRESOLVED_FOLLOW_UP: FailureCategory.UNRESOLVED_FOLLOW_UP,
    RunBlockReason.AGENT_FAILURE: FailureCategory.AGENT,
    RunBlockReason.SCHEMA_FAILURE: FailureCategory.SCHEMA,
    RunBlockReason.TOOL_FAILURE: FailureCategory.TOOL,
    RunBlockReason.PROVIDER_FAILURE: FailureCategory.PROVIDER,
    RunBlockReason.SANDBOX_FAILURE: FailureCategory.SANDBOX,
    RunBlockReason.WORKSPACE_FAILURE: FailureCategory.WORKSPACE,
    RunBlockReason.DATA_QUALITY: FailureCategory.DATA_QUALITY,
    RunBlockReason.EVIDENCE_PROVENANCE: FailureCategory.EVIDENCE_PROVENANCE,
    RunBlockReason.TIMEOUT: FailureCategory.TIMEOUT,
    RunBlockReason.INTERRUPTED: FailureCategory.INTERRUPTED,
    RunBlockReason.OTHER: FailureCategory.OTHER,
}


def category_for_block_reason(reason: RunBlockReason) -> FailureCategory:
    """Map a persisted orchestration block reason to its benchmark category."""

    return _BLOCK_REASON_CATEGORIES[RunBlockReason(reason)]


def _failure_category(message: str) -> FailureCategory:
    """Infer a category from prose.

    This is the compatibility path for pre-R18 workspaces that persisted no
    machine-readable block reason. Live runs now carry an explicit reason and
    never reach this inference.
    """

    lowered = message.lower()
    if any(term in lowered for term in ("api", "provider", "credential", "rate limit")):
        return FailureCategory.PROVIDER
    if "budget" in lowered:
        return FailureCategory.BUDGET
    if "timeout" in lowered or "timed out" in lowered:
        return FailureCategory.TIMEOUT
    if any(term in lowered for term in ("docker", "sandbox", "container")):
        return FailureCategory.SANDBOX
    if any(term in lowered for term in ("schema", "validation", "pydantic")):
        return FailureCategory.SCHEMA
    if "workspace" in lowered or "directory" in lowered:
        return FailureCategory.WORKSPACE
    return FailureCategory.OTHER


class BenchmarkRunner:
    """Plan, execute, resume, pilot, and rescore a benchmark matrix."""

    def __init__(
        self,
        workspace_base_dir: str | Path,
        *,
        catalog: ScenarioCatalog | None = None,
        architecture_executors: Mapping[str, ArchitectureExecutor] | None = None,
        source_preparer: SourcePreparer | None = None,
        run_id_factory: RunIdFactory | None = None,
        docker_image: str = "data-science-agent-python:latest",
        runner_options: Mapping[str, object] | None = None,
    ) -> None:
        self.workspace_base_dir = Path(workspace_base_dir).expanduser().resolve()
        self.catalog = catalog or discover_scenarios()
        self.architecture_executors = dict(architecture_executors or {})
        self.source_preparer = source_preparer or self._prepare_generated_sources
        self.run_id_factory = run_id_factory or _default_run_id
        self.docker_image = docker_image
        self.runner_options = dict(runner_options or {})
        frozen_option_names = {
            "workspace_manager",
            "workspace_base_dir",
            "model",
            "model_provider",
            "docker_image",
            "budget",
            "agent_turn_limits",
        }
        conflicts = frozen_option_names.intersection(self.runner_options)
        if conflicts:
            names = ", ".join(sorted(conflicts))
            raise BenchmarkError(
                f"runner options cannot override manifest-frozen settings: {names}"
            )
        self.code_revision = _capture_code_revision()

    def build_manifest(
        self,
        *,
        manifest_id: str | None = None,
        scenario_ids: Sequence[str] | None = None,
        scenario_versions: Mapping[str, str] | None = None,
        architectures: Sequence[str] = DEFAULT_ARCHITECTURES,
        repetitions: int = DEFAULT_REPETITIONS,
        model: str,
        model_provider: str = "openai",
        execution_mode: ExecutionMode = ExecutionMode.LIVE,
        budgets: BudgetConfiguration | None = None,
        repetition_justification: str | None = None,
        pilot_set: PilotSetDeclaration | None = None,
    ) -> BenchmarkManifest:
        """Create a frozen matrix declaration without generating data or agents."""

        if self.code_revision is None:
            raise BenchmarkError(
                "benchmark planning requires a resolvable repository revision"
            )
        if repetitions < 3 and not repetition_justification:
            raise BenchmarkError(
                "the first declared benchmark requires at least three repetitions; "
                "provide a documented repetition_justification for a smaller pilot"
            )
        selected_ids = tuple(
            scenario_ids
            or [registration.scenario_id for registration in self.catalog.registrations]
        )
        if not selected_ids:
            raise BenchmarkError("at least one scenario must be declared")
        selected_architectures = tuple(dict.fromkeys(architectures))
        unknown_architectures = set(selected_architectures) - set(DEFAULT_ARCHITECTURES)
        if not selected_architectures or unknown_architectures:
            raise BenchmarkError(
                "architectures must be a non-empty subset of "
                + ", ".join(DEFAULT_ARCHITECTURES)
            )
        versions = scenario_versions or {}
        registrations = tuple(
            self.catalog.resolve(scenario_id, versions.get(scenario_id))
            for scenario_id in selected_ids
        )
        if len({registration.key for registration in registrations}) != len(
            registrations
        ):
            raise BenchmarkError("scenario declarations must resolve uniquely")
        actual_manifest_id = manifest_id or f"bench-{uuid.uuid4().hex}"
        references = tuple(
            ScenarioReference(
                scenario_id=registration.scenario_id,
                scenario_version=registration.scenario_version,
                evaluator_version=registration.metadata.evaluator_version,
                seed=registration.metadata.seed,
            )
            for registration in sorted(registrations, key=lambda item: item.key)
        )
        cell_ids: dict[str, str] = {}
        for reference in references:
            for architecture in selected_architectures:
                for repetition in range(1, repetitions + 1):
                    key = _cell_key_string(
                        reference.scenario_id,
                        reference.scenario_version,
                        architecture,
                        repetition,
                    )
                    run_id = self.run_id_factory(
                        actual_manifest_id,
                        reference.scenario_id,
                        reference.scenario_version,
                        architecture,
                        repetition,
                    )
                    if not isinstance(run_id, str) or not run_id.strip():
                        raise BenchmarkError(f"run ID for {key} is empty")
                    if _RUN_ID_VALID.fullmatch(run_id) is None:
                        raise BenchmarkError(
                            f"run ID for {key} contains unsafe path characters: "
                            f"{run_id!r}"
                        )
                    if run_id in cell_ids.values():
                        raise BenchmarkError(f"duplicate immutable run ID: {run_id}")
                    cell_ids[key] = run_id
        parameters: dict[str, object] = {
            "benchmark_runner_version": BENCHMARK_RUNNER_VERSION,
            "cost_pilot_required": True,
            "workspace_base_dir": str(self.workspace_base_dir),
            # The bounded evidence-correction allowance changes how many model
            # calls a cell can make, so it is frozen in the declaration digest
            # and changing it requires a new manifest version.
            "evidence_correction_attempts": DEFAULT_EVIDENCE_CORRECTION_ATTEMPTS,
            "cell_run_ids": cell_ids,
        }
        if repetition_justification:
            parameters["repetition_justification"] = repetition_justification
        return BenchmarkManifest(
            manifest_id=actual_manifest_id,
            manifest_version="1.0",
            status=ManifestStatus.DECLARED,
            created_at=_now(),
            scenario_references=references,
            architectures=selected_architectures,
            repetitions=repetitions,
            model=model,
            model_provider=model_provider,
            code_revision=self.code_revision,
            run_configuration=RunConfiguration(
                execution_mode=execution_mode,
                tool_contract_version=TOOL_CONTRACT_VERSION,
                parameters=parameters,
            ),
            budgets=budgets or _default_budgets(),
            aggregation_version=AGGREGATION_VERSION,
            pilot_set=pilot_set
            or default_pilot_set(
                selected_architectures,
                scenario_count=len(references),
                repetitions=repetitions,
            ),
        )

    def persist_plan(
        self,
        manifest: BenchmarkManifest,
        path: str | Path,
    ) -> Path:
        """Persist a new plan exclusively; existing declarations are immutable."""

        manifest_path = Path(path).expanduser().resolve()
        self._persist_manifest(manifest_path, manifest, overwrite=False)
        return manifest_path

    def execute(
        self,
        manifest_path: str | Path,
        *,
        resume: bool = False,
        allow_paid: bool = False,
        environment: Mapping[str, str] | None = None,
        require_pilot: bool | None = None,
        pilot_path: str | Path | None = None,
        unknown_cost: bool = False,
        max_cells: int | None = None,
        only_run_ids: Sequence[str] | None = None,
    ) -> BenchmarkExecutionSummary:
        """Execute missing cells, preserving every existing run record."""

        path = Path(manifest_path).expanduser().resolve()
        if not path.is_file():
            raise BenchmarkError(
                f"benchmark plan is missing: {path}; run the plan command first"
            )
        if not resume and load_manifest(path).run_records:
            raise BenchmarkError(
                "manifest already contains run records; pass resume=True to continue"
            )
        manifest = load_manifest(path)
        if manifest.code_revision is None:
            raise BenchmarkError(
                "benchmark manifest is not bound to a code revision; freeze a "
                "new manifest before execution"
            )
        if self.code_revision != manifest.code_revision:
            raise BenchmarkError(
                "repository state differs from the code revision frozen in the "
                "manifest; freeze a new manifest version before execution"
            )
        if manifest.status is ManifestStatus.COMPLETE:
            return BenchmarkExecutionSummary(
                manifest=manifest,
                executed_run_ids=(),
                skipped_run_ids=tuple(record.run_id for record in manifest.run_records),
                failed_run_ids=tuple(
                    record.run_id
                    for record in manifest.run_records
                    if record.lifecycle.status is not LifecycleStatus.COMPLETED
                ),
            )
        if require_pilot is None:
            require_pilot = (
                manifest.run_configuration.execution_mode is ExecutionMode.LIVE
            )
        if manifest.run_configuration.execution_mode is ExecutionMode.LIVE:
            self._require_paid_access(
                manifest,
                allow_paid=allow_paid,
                environment=environment,
            )
        if require_pilot:
            pilot = self._require_pilot(
                path,
                pilot_path,
                allow_unknown_cost=(unknown_cost or manifest.unknown_cost_acknowledged),
            )
            if (
                pilot.unknown_cost_record_digests
                and not manifest.unknown_cost_acknowledged
            ):
                if not unknown_cost:
                    raise BenchmarkError(
                        "pilot cost is unknown; pass the explicit unknown-cost "
                        "acknowledgement before continuing beyond the pilot"
                    )
                # Bind the acknowledgement to every affected pilot record, not
                # just one, so a later record cannot be swapped in silently.
                manifest = manifest.model_copy(
                    update={
                        "unknown_cost_acknowledged": True,
                        "unknown_cost_pilot_id": pilot.pilot_id,
                        "unknown_cost_pilot_record_digest": (
                            pilot.unknown_cost_record_digests[0]
                        ),
                        "unknown_cost_pilot_record_digests": (
                            pilot.unknown_cost_record_digests
                        ),
                    }
                )

        manifest = self._replace_manifest(manifest, status=ManifestStatus.RUNNING)
        self._persist_manifest(path, manifest, overwrite=True)
        existing_by_key = {
            (
                record.scenario_id,
                record.scenario_version,
                record.architecture,
                record.repetition,
            ): record
            for record in manifest.run_records
        }
        executed: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        interrupted = False
        processed = 0
        try:
            selected = set(only_run_ids) if only_run_ids is not None else None
            for cell in self._cells(manifest):
                if selected is not None and cell.run_id not in selected:
                    continue
                if cell.key in existing_by_key and not self._is_resumable_cell(
                    existing_by_key[cell.key],
                    resume=resume,
                ):
                    skipped.append(cell.run_id)
                    if (
                        existing_by_key[cell.key].lifecycle.status
                        is not LifecycleStatus.COMPLETED
                    ):
                        failed.append(cell.run_id)
                    continue
                if max_cells is not None and processed >= max_cells:
                    break
                try:
                    cell, manifest = self._prepare_cell_sources(cell, manifest)
                    record = self._execute_cell(cell, manifest, sources_prepared=True)
                except KeyboardInterrupt as error:
                    # Retain the interrupted cell as an observed operational
                    # outcome before the manifest is marked aborted. Dropping it
                    # would understate the denominator as a missing repetition
                    # and lose the workspace's partial usage, cost, latency, and
                    # attempt evidence.
                    interrupted = True
                    record = self._interrupted_record(cell, error, manifest)
                    existing_by_key[cell.key] = record
                    executed.append(record.run_id)
                    failed.append(record.run_id)
                    manifest = self._replace_manifest(
                        manifest,
                        run_records=tuple(existing_by_key.values()),
                        status=ManifestStatus.RUNNING,
                    )
                    manifest = aggregate_manifest(manifest)
                    self._persist_manifest(path, manifest, overwrite=True)
                    break
                except WorkspaceIdentityError:
                    raise
                except Exception as error:  # noqa: BLE001
                    record = self._failure_record(cell, error, manifest)
                existing_by_key[cell.key] = record
                executed.append(record.run_id)
                if record.lifecycle.status is not LifecycleStatus.COMPLETED:
                    failed.append(record.run_id)
                processed += 1
                manifest = self._replace_manifest(
                    manifest,
                    run_records=tuple(existing_by_key.values()),
                    status=ManifestStatus.RUNNING,
                )
                manifest = aggregate_manifest(manifest)
                self._persist_manifest(path, manifest, overwrite=True)
        except KeyboardInterrupt:
            interrupted = True
        if interrupted:
            manifest = self._replace_manifest(
                manifest,
                status=ManifestStatus.ABORTED,
                run_records=tuple(existing_by_key.values()),
            )
            manifest = aggregate_manifest(manifest)
        else:
            all_cells_recorded = len(existing_by_key) == len(self._cells(manifest))
            manifest = self._replace_manifest(
                manifest,
                status=ManifestStatus.COMPLETE
                if all_cells_recorded
                else ManifestStatus.RUNNING,
                run_records=tuple(existing_by_key.values()),
            )
            manifest = aggregate_manifest(manifest)
        self._persist_manifest(path, manifest, overwrite=True)
        return BenchmarkExecutionSummary(
            manifest=manifest,
            executed_run_ids=tuple(executed),
            skipped_run_ids=tuple(skipped),
            failed_run_ids=tuple(failed),
            interrupted=interrupted,
        )

    def run_pilot(
        self,
        manifest_path: str | Path,
        *,
        allow_paid: bool = False,
        environment: Mapping[str, str] | None = None,
        pilot_path: str | Path | None = None,
    ) -> tuple[BenchmarkExecutionSummary, BenchmarkPilotSetReport]:
        """Run the declared pilot set and persist a stratified estimate.

        One cell per declared stratum is measured, so architecture and workload
        differences are observable instead of being hidden behind a single
        first-cell extrapolation.
        """

        path = Path(manifest_path).expanduser().resolve()
        pilot_file = Path(pilot_path).expanduser().resolve() if pilot_path else None
        if pilot_file is None:
            pilot_file = path.with_name(path.stem + ".pilot.json")
        if pilot_file.exists():
            raise BenchmarkError(f"pilot report already exists: {pilot_file}")

        manifest = load_manifest(path)
        pilot_set = manifest.pilot_set
        if pilot_set is None:
            raise BenchmarkError(
                "the manifest declares no pilot set; re-plan the benchmark so "
                "every architecture has at least one declared pilot stratum"
            )

        summary: BenchmarkExecutionSummary | None = None
        selected_run_ids: list[str] = []
        reused_run_ids: list[str] = []
        for stratum in pilot_set.strata:
            manifest = load_manifest(path)
            target, requires_execution = self._pilot_cell_for_stratum(
                manifest,
                stratum,
            )
            if requires_execution:
                summary = self.execute(
                    path,
                    resume=True,
                    allow_paid=allow_paid,
                    environment=environment,
                    require_pilot=False,
                    max_cells=1,
                    only_run_ids=(target.run_id,),
                )
                if target.run_id not in summary.executed_run_ids:
                    raise BenchmarkError(
                        f"pilot stratum {stratum.stratum_id} did not execute its "
                        f"declared cell {target.run_id}"
                    )
                observed = next(
                    record
                    for record in summary.manifest.run_records
                    if record.run_id == target.run_id
                )
                if observed.lifecycle.status is not LifecycleStatus.COMPLETED:
                    raise BenchmarkError(
                        f"pilot cell {target.run_id} did not complete "
                        f"({observed.lifecycle.status.value}); no later pilot "
                        "strata were executed"
                    )
            else:
                reused_run_ids.append(target.run_id)
            selected_run_ids.append(target.run_id)

        manifest = load_manifest(path)
        if summary is None:
            # A prior process may have completed every declared pilot cell and
            # stopped before atomically publishing the report. Rebuild the
            # derived report from those exact immutable records without
            # selecting replacement cells.
            summary = BenchmarkExecutionSummary(
                manifest=manifest,
                executed_run_ids=(),
                skipped_run_ids=tuple(reused_run_ids),
                failed_run_ids=(),
            )
        report = self._build_pilot_set_report(
            manifest,
            pilot_set,
            selected_run_ids,
        )
        self._persist_text_exclusive(
            pilot_file,
            dump_stable_json(report.model_dump(mode="json")),
        )
        return summary, report

    @staticmethod
    def _stratum_matches(
        stratum: PilotStratumDeclaration,
        *,
        architecture: str,
        scenario_id: str,
    ) -> bool:
        """Return whether one declared cell belongs to a pilot stratum."""

        if architecture != stratum.architecture:
            return False
        return not stratum.scenario_ids or scenario_id in stratum.scenario_ids

    def _pilot_cell_for_stratum(
        self,
        manifest: BenchmarkManifest,
        stratum: PilotStratumDeclaration,
    ) -> tuple[BenchmarkCell, bool]:
        """Select one stable pilot cell, refusing success-based replacement.

        A failed or blocked pilot is benchmark evidence, not permission to try
        the next cell until one succeeds. A cancelled pilot may resume its same
        immutable cell, and a completed cell may be reused if publication was
        interrupted after execution.
        """

        candidates = [
            cell
            for cell in self._cells(manifest)
            if self._stratum_matches(
                stratum,
                architecture=cell.architecture,
                scenario_id=cell.scenario.scenario_id,
            )
        ]
        if not candidates:
            raise BenchmarkError(
                f"pilot stratum {stratum.stratum_id} has no declared cell"
            )
        records = {record.run_id: record for record in manifest.run_records}
        observed = [
            (cell, records[cell.run_id])
            for cell in candidates
            if cell.run_id in records
        ]
        if len(observed) > 1:
            raise BenchmarkError(
                f"pilot stratum {stratum.stratum_id} already contains multiple "
                "observed cells; freeze a new manifest instead of selecting "
                "among prior outcomes"
            )
        if observed:
            cell, record = observed[0]
            if record.lifecycle.status is LifecycleStatus.COMPLETED:
                return cell, False
            if record.lifecycle.status is LifecycleStatus.CANCELLED:
                return cell, True
            raise BenchmarkError(
                f"pilot cell {cell.run_id} did not complete "
                f"({record.lifecycle.status.value}); freeze a new manifest "
                "instead of replacing failed pilot evidence"
            )
        return candidates[0], True

    def _build_pilot_set_report(
        self,
        manifest: BenchmarkManifest,
        pilot_set: PilotSetDeclaration,
        executed_run_ids: Sequence[str],
    ) -> BenchmarkPilotSetReport:
        """Derive the stratified estimate from the measured pilot cells."""

        records = {record.run_id: record for record in manifest.run_records}
        strata: list[PilotStratumEstimate] = []
        unknown_digests: list[str] = []
        for stratum in pilot_set.strata:
            observations: list[PilotObservation] = []
            for run_id in executed_run_ids:
                record = records.get(run_id)
                if record is None:
                    continue
                if not self._stratum_matches(
                    stratum,
                    architecture=record.architecture,
                    scenario_id=record.scenario_id,
                ):
                    continue
                if record.lifecycle.status is not LifecycleStatus.COMPLETED:
                    raise BenchmarkError(
                        f"pilot cell {run_id} did not complete "
                        f"({record.lifecycle.status.value}); resolve it before "
                        "publishing a cost pilot"
                    )
                if not record.usage.complete:
                    raise BenchmarkError(
                        f"pilot usage is incomplete for {run_id}; freeze a new "
                        "manifest after usage accounting is repaired"
                    )
                digest = canonical_run_record_digest(record)
                if record.cost.estimated_cost_usd is None:
                    unknown_digests.append(digest)
                observations.append(
                    PilotObservation(
                        stratum_id=stratum.stratum_id,
                        architecture=record.architecture,
                        scenario_id=record.scenario_id,
                        scenario_version=record.scenario_version,
                        run_id=record.run_id,
                        record_digest=digest,
                        observed_requests=record.usage.requests,
                        observed_input_tokens=record.usage.input_tokens,
                        observed_cached_tokens=record.usage.cached_tokens,
                        observed_output_tokens=record.usage.output_tokens,
                        observed_reasoning_tokens=record.usage.reasoning_tokens,
                        observed_total_tokens=record.usage.total_tokens,
                        observed_cost=record.cost,
                        observed_cost_usd=record.cost.estimated_cost_usd,
                        observed_elapsed_seconds=record.latency.elapsed_seconds,
                        observed_started_at=record.latency.started_at,
                        observed_finished_at=record.latency.finished_at,
                    )
                )
            if not observations:
                raise BenchmarkError(
                    f"pilot stratum {stratum.stratum_id} produced no observation"
                )
            strata.append(_stratum_estimate(stratum, tuple(observations)))

        return BenchmarkPilotSetReport(
            pilot_id=f"pilot-{uuid.uuid4().hex}",
            manifest_id=manifest.manifest_id,
            manifest_version=manifest.manifest_version,
            manifest_digest=canonical_manifest_declaration_digest(manifest),
            output_schema_fingerprint=output_schema_fingerprint(),
            model=manifest.model,
            model_provider=manifest.model_provider,
            run_configuration=manifest.run_configuration,
            budgets=manifest.budgets,
            planned_cells=_declared_cell_count(manifest),
            scaling_method=PilotScalingMethod.STRATIFIED_MEAN,
            strata=tuple(strata),
            **_matrix_estimate(strata),
            unknown_cost_record_digests=tuple(sorted(set(unknown_digests))),
            created_at=_now(),
            methodology=(
                "Each declared pilot stratum contributed at least one measured "
                "cell. Full-matrix cost and latency are the sum of per-stratum "
                "mean-per-cell estimates, with a low/high range from the "
                "observed per-stratum minimum and maximum. Per-pilot "
                "observations are retained; no single cell is treated as "
                "representative of the matrix."
            ),
        )

    def rescore(
        self,
        manifest_path: str | Path,
        *,
        output_path: str | Path | None = None,
        workspace_base_dir: str | Path | None = None,
        rules_by_scenario: RuleMap | None = None,
    ) -> BenchmarkManifest:
        """Rescore persisted workspaces into a new manifest without agents."""

        input_path = canonical_path(manifest_path)
        if output_path is not None:
            try:
                _, output = ensure_distinct_paths(input_path, output_path)
                ensure_output_is_new(output_path)
            except OfflineOutputError as error:
                message = str(error)
                if "must differ from input" in message:
                    message = "offline rescore output must differ from input manifest"
                raise BenchmarkError(message) from error
        else:
            output = input_path.with_name(input_path.stem + ".rescored.json")
            try:
                ensure_output_is_new(output)
            except OfflineOutputError as error:
                raise BenchmarkError(str(error)) from error
        manifest = load_manifest(input_path)
        base_dir = (
            Path(workspace_base_dir).expanduser().resolve()
            if workspace_base_dir is not None
            else self._manifest_workspace_base(manifest, input_path.parent)
        )
        rules = rules_by_scenario or {
            (reference.scenario_id, reference.scenario_version): self.catalog.resolve(
                reference.scenario_id, reference.scenario_version
            ).evaluator_rules()
            for reference in manifest.scenario_references
        }
        try:
            rescored, _ = rescore_manifest(
                manifest,
                rules,
                workspace_base_dir=base_dir,
                evaluator=evaluate_workspace,
            )
        except WorkspaceIdentityError as error:
            raise BenchmarkError(f"offline rescore refused: {error}") from error
        except ValueError as error:
            raise BenchmarkError(f"offline rescore refused: {error}") from error
        self._persist_manifest(output, rescored, overwrite=False)
        return rescored

    def planned_cells(self, manifest: BenchmarkManifest) -> tuple[BenchmarkCell, ...]:
        """Expose the stable matrix for dry-run and test callers."""

        return self._cells(manifest)

    @staticmethod
    def _bind_source_files(
        manifest: BenchmarkManifest,
        cell: BenchmarkCell,
        source_files: tuple[SourceFileIdentity, ...],
    ) -> BenchmarkManifest:
        """Freeze one scenario's generated source hashes into the manifest."""

        references: list[ScenarioReference] = []
        found = False
        for reference in manifest.scenario_references:
            if (
                reference.scenario_id == cell.scenario.scenario_id
                and reference.scenario_version == cell.scenario.scenario_version
            ):
                found = True
                if reference.source_files and reference.source_files != source_files:
                    raise WorkspaceIdentityError(
                        "generated scenario sources do not match the manifest"
                    )
                references.append(
                    reference.model_copy(update={"source_files": source_files})
                )
            else:
                references.append(reference)
        if not found:
            raise WorkspaceIdentityError(
                f"manifest does not declare {cell.scenario.scenario_id}"
            )
        values = manifest.model_dump(mode="json")
        values["scenario_references"] = [
            reference.model_dump(mode="json") for reference in references
        ]
        return BenchmarkManifest.model_validate(values)

    def _workspace_identity(
        self,
        manifest: BenchmarkManifest,
        cell: BenchmarkCell,
        *,
        code_revision: CodeRevision | None = None,
    ) -> WorkspaceIdentity:
        """Build the immutable identity expected for one benchmark workspace."""

        reference = next(
            (
                item
                for item in manifest.scenario_references
                if item.scenario_id == cell.scenario.scenario_id
                and item.scenario_version == cell.scenario.scenario_version
            ),
            None,
        )
        if reference is None or not reference.source_files:
            raise WorkspaceIdentityError(
                "manifest is missing generated source identity for "
                f"{cell.scenario.scenario_id}@{cell.scenario.scenario_version}"
            )
        return WorkspaceIdentity(
            benchmark_manifest_id=manifest.manifest_id,
            run_id=cell.run_id,
            scenario_id=cell.scenario.scenario_id,
            scenario_version=cell.scenario.scenario_version,
            evaluator_version=reference.evaluator_version,
            architecture=cell.architecture,
            repetition=cell.repetition,
            seed=reference.seed,
            source_files=reference.source_files,
            code_revision=(
                manifest.code_revision if code_revision is None else code_revision
            ),
        )

    def _cells(self, manifest: BenchmarkManifest) -> tuple[BenchmarkCell, ...]:
        configured_ids = manifest.run_configuration.parameters.get("cell_run_ids", {})
        cell_ids = configured_ids if isinstance(configured_ids, dict) else {}
        workspace_base = self._manifest_workspace_base(
            manifest,
            self.workspace_base_dir,
        )
        cells: list[BenchmarkCell] = []
        for reference in manifest.scenario_references:
            registration = self.catalog.resolve(
                reference.scenario_id,
                reference.scenario_version,
            )
            for architecture in manifest.architectures:
                for repetition in range(1, manifest.repetitions + 1):
                    key = _cell_key_string(
                        reference.scenario_id,
                        reference.scenario_version,
                        architecture,
                        repetition,
                    )
                    run_id = cell_ids.get(key)
                    if not isinstance(run_id, str):
                        run_id = self.run_id_factory(
                            manifest.manifest_id,
                            reference.scenario_id,
                            reference.scenario_version,
                            architecture,
                            repetition,
                        )
                    source_root = (
                        workspace_base
                        / "_sources"
                        / (
                            f"{registration.scenario_id}-{registration.scenario_version}"
                        )
                    )
                    cells.append(
                        BenchmarkCell(
                            scenario=registration,
                            architecture=architecture,
                            repetition=repetition,
                            run_id=run_id,
                            workspace_path=workspace_base / run_id,
                            inputs_source=source_root / "inputs",
                            docs_source=source_root / "docs",
                        )
                    )
        run_ids = [cell.run_id for cell in cells]
        if len(run_ids) != len(set(run_ids)):
            raise BenchmarkError("manifest resolves duplicate immutable run IDs")
        return tuple(cells)

    def _prepare_cell_sources(
        self,
        cell: BenchmarkCell,
        manifest: BenchmarkManifest,
    ) -> tuple[BenchmarkCell, BenchmarkManifest]:
        """Prepare deterministic sources and freeze their hashes in the manifest."""

        inputs_source, docs_source = self.source_preparer(
            cell.scenario,
            cell.inputs_source.parent,
        )
        prepared = replace(cell, inputs_source=inputs_source, docs_source=docs_source)
        source_files = source_file_identities_for_roots(inputs_source, docs_source)
        return prepared, self._bind_source_files(manifest, prepared, source_files)

    def _execute_cell(
        self,
        cell: BenchmarkCell,
        manifest: BenchmarkManifest,
        *,
        sources_prepared: bool = False,
    ) -> BenchmarkRunRecord:
        if not sources_prepared:
            cell, manifest = self._prepare_cell_sources(cell, manifest)
        workspace_manager = WorkspaceManager(cell.workspace_path.parent)
        workspace_exists = cell.workspace_path.exists()
        if cell.workspace_path.exists():
            workspace = workspace_manager.open_workspace(cell.run_id)
        else:
            workspace = workspace_manager.create_workspace(
                cell.run_id,
                inputs_source=cell.inputs_source,
                docs_source=cell.docs_source,
            )
        identity = self._workspace_identity(manifest, cell)
        if workspace_exists:
            verify_workspace_identity(workspace, identity)
        else:
            persist_workspace_identity(workspace, identity)
        raw_result = self._call_architecture(cell, workspace, manifest)
        outcome = self._coerce_result(raw_result, workspace)
        if outcome.lifecycle.status is LifecycleStatus.COMPLETED:
            if outcome.evaluator_result is None:
                try:
                    outcome = replace(
                        outcome,
                        evaluator_result=evaluate_workspace(
                            workspace,
                            cell.scenario.evaluator_rules(),
                        ).result,
                    )
                except Exception as error:  # noqa: BLE001
                    outcome = replace(
                        outcome,
                        evaluator_result=_not_evaluated(
                            cell,
                            "offline evaluator failed: "
                            f"{type(error).__name__}: {error}",
                            status=EvaluatorStatus.ERROR,
                            evaluated_at=outcome.finished_at,
                        ),
                    )
        elif outcome.evaluator_result is None:
            outcome = replace(
                outcome,
                evaluator_result=_not_evaluated(
                    cell,
                    outcome.lifecycle.failure_message or "analysis did not complete",
                    evaluated_at=outcome.finished_at,
                ),
            )
        return self._record_from_outcome(cell, outcome, manifest)

    def _call_architecture(
        self,
        cell: BenchmarkCell,
        workspace: Workspace,
        manifest: BenchmarkManifest,
    ) -> object:
        injected = self.architecture_executors.get(cell.architecture)
        if injected is not None:
            return injected(cell, workspace)
        if manifest.run_configuration.execution_mode is not ExecutionMode.LIVE:
            raise BenchmarkError(
                "non-live benchmark execution requires an injected deterministic "
                "or replay architecture executor"
            )
        if cell.architecture == "multi-agent":
            from orchestration.runner import AnalysisRunner

            runner_class = AnalysisRunner
        elif cell.architecture == "single-agent":
            from orchestration.generalist_runner import GeneralistRunner

            runner_class = GeneralistRunner
        else:
            raise BenchmarkError(f"unsupported architecture: {cell.architecture}")
        runner_options = {
            "workspace_manager": WorkspaceManager(cell.workspace_path.parent),
            "model": manifest.model,
            "model_provider": manifest.model_provider,
            "docker_image": self.docker_image,
            "budget": self._run_budget(manifest),
            "agent_turn_limits": {
                role: limit
                for role, limit in manifest.budgets.turn_limits.items()
                if role
                in {
                    "lead",
                    "generalist",
                    "data_auditor",
                    "analyst",
                    "statistician",
                    "critic",
                }
            },
        }
        runner_options.update(self.runner_options)
        runner = runner_class(**runner_options)
        return runner.run_sync(
            cell.run_id,
            cell.scenario.metadata.user_question,
            workspace=workspace,
        )

    @staticmethod
    def _run_budget(manifest: BenchmarkManifest) -> RunBudget:
        resource_limits = manifest.budgets.resource_limits
        return RunBudget(
            max_specialist_invocations=resource_limits.get(
                "specialist_invocations", 12
            ),
            max_sql_executions=resource_limits.get("sql", 40),
            max_python_executions=resource_limits.get("python", 20),
            max_critic_loops=resource_limits.get("critic_loops", 3),
            max_charts=resource_limits.get("charts", 4),
        )

    @staticmethod
    def _coerce_result(raw_result: object, workspace: Workspace) -> BenchmarkCellResult:
        if isinstance(raw_result, BenchmarkCellResult):
            if raw_result.workspace is None:
                return replace(raw_result, workspace=workspace)
            return raw_result
        status = getattr(getattr(raw_result, "status", None), "value", None) or str(
            getattr(raw_result, "status", "failed")
        )
        state = getattr(raw_result, "state", None)
        error = getattr(raw_result, "error", None) or getattr(state, "error", None)
        # Orchestration persists the originating condition, so the category is
        # read rather than guessed. Not every blocked analysis is a budget
        # failure: a self-critique revision, an unresolved follow-up, a schema
        # violation, and an interruption are distinct outcomes.
        block_reason = getattr(raw_result, "block_reason", None) or getattr(
            state,
            "block_reason",
            None,
        )
        block_detail = getattr(raw_result, "block_detail", None) or getattr(
            state,
            "block_detail",
            None,
        )
        if status == LifecycleStatus.COMPLETED.value:
            lifecycle = LifecycleOutcome(status=LifecycleStatus.COMPLETED)
        elif status == LifecycleStatus.CANCELLED.value:
            message = block_detail or error or "analysis was interrupted"
            lifecycle = LifecycleOutcome(
                status=LifecycleStatus.CANCELLED,
                failure_category=(
                    category_for_block_reason(block_reason)
                    if block_reason is not None
                    else FailureCategory.INTERRUPTED
                ),
                failure_message=message,
            )
        elif status == LifecycleStatus.BLOCKED.value:
            message = block_detail or error or "analysis was constrained"
            lifecycle = LifecycleOutcome(
                status=LifecycleStatus.BLOCKED,
                failure_category=(
                    category_for_block_reason(block_reason)
                    if block_reason is not None
                    else _failure_category(message)
                ),
                failure_message=message,
            )
        else:
            message = (
                error
                or block_detail
                or (f"analysis returned lifecycle status {status}")
            )
            lifecycle = LifecycleOutcome(
                status=LifecycleStatus.FAILED,
                failure_category=(
                    category_for_block_reason(block_reason)
                    if block_reason is not None
                    else _failure_category(message)
                ),
                failure_message=message,
            )
        return BenchmarkCellResult(
            lifecycle=lifecycle,
            workspace=getattr(raw_result, "workspace", None) or workspace,
            state=state,
            started_at=getattr(state, "created_at", None),
            finished_at=getattr(state, "updated_at", None),
        )

    def _record_from_outcome(
        self,
        cell: BenchmarkCell,
        outcome: BenchmarkCellResult,
        manifest: BenchmarkManifest,
    ) -> BenchmarkRunRecord:
        state = outcome.state
        if state is not None and getattr(state, "run_id", cell.run_id) != cell.run_id:
            raise BenchmarkError(
                f"workspace state run ID does not match immutable cell {cell.run_id}"
            )
        finished = outcome.finished_at or getattr(state, "updated_at", None) or _now()
        started = outcome.started_at or getattr(state, "created_at", None) or finished
        if finished < started:
            finished = started
        usage_state = getattr(state, "usage", None)
        usage = UsageSummary(
            requests=getattr(usage_state, "requests", 0),
            input_tokens=getattr(usage_state, "input_tokens", 0),
            cached_tokens=getattr(usage_state, "cached_tokens", 0),
            output_tokens=getattr(usage_state, "output_tokens", 0),
            reasoning_tokens=getattr(usage_state, "reasoning_tokens", 0),
            total_tokens=getattr(usage_state, "total_tokens", 0),
            complete=bool(getattr(state, "usage_complete", True)),
        )
        cost_breakdown = getattr(state, "cost_breakdown", None)
        cost = (
            CostSummary(
                availability=CostAvailability.KNOWN,
                estimated_cost_usd=cost_breakdown.estimated_cost_usd,
                pricing_model=cost_breakdown.pricing_model,
            )
            if cost_breakdown is not None
            else CostSummary(
                availability=CostAvailability.UNAVAILABLE,
                # Prefer the ledger's own reason so an incomplete-usage run is
                # not reported as a missing-pricing run.
                note=getattr(state, "cost_estimation_note", None)
                or "No pricing breakdown was persisted for this run.",
            )
        )
        elapsed = getattr(state, "elapsed_seconds", None)
        if elapsed is None:
            elapsed = (finished - started).total_seconds()
        evaluation = outcome.evaluator_result or _not_evaluated(
            cell,
            "no offline evaluation was produced",
            evaluated_at=finished,
        )
        return BenchmarkRunRecord(
            run_id=cell.run_id,
            repetition=cell.repetition,
            scenario_id=cell.scenario.scenario_id,
            scenario_version=cell.scenario.scenario_version,
            evaluator_version=cell.scenario.metadata.evaluator_version,
            architecture=cell.architecture,
            model=manifest.model,
            model_provider=manifest.model_provider,
            run_configuration=manifest.run_configuration,
            budgets=manifest.budgets,
            code_revision=manifest.code_revision,
            attempt_id=getattr(state, "attempt_id", None),
            seed=cell.scenario.metadata.seed,
            workspace_path=str(cell.workspace_path),
            lifecycle=outcome.lifecycle,
            evaluator_result=evaluation,
            score_breakdown=evaluation.score_breakdown,
            usage=usage,
            cost=cost,
            latency=LatencySummary(
                elapsed_seconds=max(float(elapsed), 0.0),
                started_at=started,
                finished_at=finished,
            ),
            attempt_history=tuple(getattr(state, "attempt_history", ())),
        )

    @staticmethod
    def _is_resumable_cell(
        record: BenchmarkRunRecord,
        *,
        resume: bool,
    ) -> bool:
        """Return whether an explicit resume may retry this recorded cell.

        Only an interrupted cell is retried. A completed, failed, or blocked
        record is a real observation of the system under test, and re-running it
        would silently replace evidence the benchmark is supposed to report.
        """

        return resume and record.lifecycle.status is LifecycleStatus.CANCELLED

    def _interrupted_workspace_state(self, cell: BenchmarkCell) -> object | None:
        """Load the persisted state an interrupted cell left behind, if any."""

        state_dir = cell.workspace_path / "state"
        if not state_dir.exists():
            return None
        try:
            ledger = AnalysisLedger(state_dir)
        except Exception:  # noqa: BLE001
            # A workspace interrupted mid-write must not mask the interruption
            # itself; the record is still published without persisted totals.
            return None
        if ledger.state.run_id != cell.run_id:
            return None
        return ledger.state

    def _interrupted_record(
        self,
        cell: BenchmarkCell,
        error: BaseException,
        manifest: BenchmarkManifest,
    ) -> BenchmarkRunRecord:
        """Materialize the cancelled record for an interrupted declared cell."""

        message = (
            f"{type(error).__name__}: {error}".strip().rstrip(":").strip()
            or type(error).__name__
        )
        reason = f"benchmark cell was interrupted before completion ({message})"
        state = self._interrupted_workspace_state(cell)
        now = _now()
        outcome = BenchmarkCellResult(
            lifecycle=LifecycleOutcome(
                status=LifecycleStatus.CANCELLED,
                failure_category=FailureCategory.INTERRUPTED,
                failure_message=reason,
            ),
            state=state,
            evaluator_result=_not_evaluated(cell, reason, evaluated_at=now),
            started_at=getattr(state, "created_at", None) or now,
            finished_at=getattr(state, "updated_at", None) or now,
        )
        return self._record_from_outcome(cell, outcome, manifest)

    def _failure_record(
        self,
        cell: BenchmarkCell,
        error: Exception,
        manifest: BenchmarkManifest,
    ) -> BenchmarkRunRecord:
        message = f"{type(error).__name__}: {error}"
        now = _now()
        outcome = BenchmarkCellResult(
            lifecycle=LifecycleOutcome(
                status=LifecycleStatus.FAILED,
                failure_category=_failure_category(message),
                failure_message=message,
            ),
            evaluator_result=_not_evaluated(cell, message, evaluated_at=now),
            started_at=now,
            finished_at=now,
        )
        return self._record_from_outcome(cell, outcome, manifest)

    @staticmethod
    def _replace_manifest(
        manifest: BenchmarkManifest,
        *,
        status: ManifestStatus | None = None,
        run_records: Sequence[BenchmarkRunRecord] | None = None,
    ) -> BenchmarkManifest:
        values = manifest.model_dump(mode="json")
        if status is not None:
            values["status"] = status.value
        if run_records is not None:
            values["run_records"] = [
                item.model_dump(mode="json") for item in run_records
            ]
        return BenchmarkManifest.model_validate(values)

    @staticmethod
    def _persist_text_exclusive(path: Path, text: str) -> None:
        try:
            write_exclusive_text(path, text)
        except OfflineOutputError as error:
            raise BenchmarkError(
                f"refusing to overwrite existing file: {canonical_path(path)}"
            ) from error

    @classmethod
    def _persist_manifest(
        cls,
        path: Path,
        manifest: BenchmarkManifest,
        *,
        overwrite: bool,
    ) -> None:
        text = dump_stable_json(manifest.model_dump(mode="json"))
        if not overwrite:
            cls._persist_text_exclusive(path, text)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)

    def _prepare_generated_sources(
        self,
        registration: ScenarioRegistration,
        destination: Path,
    ) -> tuple[Path, Path]:
        """Generate one deterministic source bundle per scenario version."""

        inputs = destination / "inputs"
        docs = destination / "docs"
        expected_inputs = tuple(inputs.glob("*.parquet")) if inputs.exists() else ()
        expected_docs = tuple(docs.glob("*.md")) if docs.exists() else ()
        if inputs.is_dir() and docs.is_dir() and expected_inputs and expected_docs:
            return inputs, docs
        if destination.exists() and any(destination.iterdir()):
            raise BenchmarkError(
                f"scenario source cache is incomplete and will not be overwritten: "
                f"{destination}"
            )
        with tempfile.TemporaryDirectory(prefix="benchmark-sources-") as temp_dir:
            generated = registration.generate_validated()
            generated_paths = generated.dataset.write(Path(temp_dir))
            inputs.mkdir(parents=True, exist_ok=True)
            docs.mkdir(parents=True, exist_ok=True)
            for path in generated_paths.values():
                if path.suffix == ".parquet":
                    shutil.copy2(path, inputs / path.name)
                elif path.suffix == ".md":
                    shutil.copy2(path, docs / path.name)
        if not tuple(inputs.glob("*.parquet")) or not tuple(docs.glob("*.md")):
            raise BenchmarkError(
                f"generated source bundle is incomplete: {destination}"
            )
        return inputs, docs

    @staticmethod
    def _rules_for(
        rules: RuleMap,
        scenario_id: str,
        scenario_version: str,
    ) -> ScenarioRules:
        try:
            return rules.get((scenario_id, scenario_version)) or rules[scenario_id]
        except KeyError as error:
            raise BenchmarkError(
                f"no evaluator rules for {scenario_id}@{scenario_version}"
            ) from error

    def _cell_from_record(
        self,
        manifest: BenchmarkManifest,
        record: BenchmarkRunRecord,
        workspace_path: Path,
    ) -> BenchmarkCell:
        registration = self.catalog.resolve(record.scenario_id, record.scenario_version)
        workspace_root = self._manifest_workspace_base(
            manifest,
            self.workspace_base_dir,
        )
        source_root = (
            workspace_root
            / "_sources"
            / (f"{record.scenario_id}-{record.scenario_version}")
        )
        return BenchmarkCell(
            scenario=registration,
            architecture=record.architecture,
            repetition=record.repetition,
            run_id=record.run_id,
            workspace_path=workspace_path,
            inputs_source=source_root / "inputs",
            docs_source=source_root / "docs",
        )

    @staticmethod
    def _manifest_workspace_base(
        manifest: BenchmarkManifest,
        fallback: str | Path,
    ) -> Path:
        configured = manifest.run_configuration.parameters.get("workspace_base_dir")
        if isinstance(configured, str) and configured.strip():
            return Path(configured).expanduser().resolve()
        return Path(fallback).expanduser().resolve()

    @staticmethod
    def _require_paid_access(
        manifest: BenchmarkManifest,
        *,
        allow_paid: bool,
        environment: Mapping[str, str] | None,
    ) -> None:
        if not allow_paid:
            raise BenchmarkError(
                "paid benchmark execution is disabled; pass the explicit "
                "allow_paid/--allow-paid opt-in"
            )
        env = environment or os.environ
        if not env.get("OPENAI_API_KEY"):
            raise BenchmarkError(
                "OPENAI_API_KEY is required for paid benchmark execution"
            )
        configured_model = env.get("OPENAI_DEFAULT_MODEL")
        if not configured_model:
            raise BenchmarkError(
                "OPENAI_DEFAULT_MODEL is required for paid benchmark execution"
            )
        if configured_model != manifest.model:
            raise BenchmarkError(
                "OPENAI_DEFAULT_MODEL does not match the model frozen in the "
                f"manifest ({manifest.model!r})"
            )

    @staticmethod
    def _require_pilot(
        manifest_path: Path,
        pilot_path: str | Path | None,
        *,
        allow_unknown_cost: bool = False,
    ) -> BenchmarkPilotSetReport:
        """Verify every declared pilot record before the full matrix runs.

        The gate refuses a pilot set that is missing a stratum, references a
        record that did not complete, or whose observations no longer reconcile
        with the immutable run records or the frozen manifest declaration.
        """

        path = (
            Path(pilot_path).expanduser().resolve()
            if pilot_path is not None
            else manifest_path.with_name(manifest_path.stem + ".pilot.json")
        )
        if not path.is_file():
            raise BenchmarkError(
                f"cost-estimation pilot is required before the full matrix: {path}"
            )
        try:
            report = BenchmarkPilotSetReport.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as error:
            raise BenchmarkError(
                f"invalid cost-estimation pilot report: {error}"
            ) from error

        manifest = load_manifest(manifest_path)
        pilot_set = manifest.pilot_set
        if pilot_set is None:
            raise BenchmarkError("the manifest declares no pilot set")
        if report.manifest_id != manifest.manifest_id:
            raise BenchmarkError("pilot report belongs to a different manifest")
        if report.manifest_version != manifest.manifest_version:
            raise BenchmarkError(
                "pilot report was produced for a different manifest version"
            )
        # Any model, budget, turn-limit, matrix-size, or pilot-set change alters
        # the frozen declaration digest and requires a new manifest version.
        if report.manifest_digest != canonical_manifest_declaration_digest(manifest):
            raise BenchmarkError(
                "the manifest declaration changed after the pilot was measured; "
                "freeze a new manifest version before paid execution"
            )
        if report.output_schema_fingerprint != output_schema_fingerprint():
            raise BenchmarkError(
                "agent output schemas changed after the pilot was measured; "
                "freeze a new manifest version and re-run the pilot set"
            )
        if (
            report.model != manifest.model
            or report.model_provider != manifest.model_provider
        ):
            raise BenchmarkError(
                "pilot report model identity does not match the manifest"
            )
        if report.run_configuration != manifest.run_configuration:
            raise BenchmarkError(
                "pilot report run configuration does not match the manifest"
            )
        if report.budgets != manifest.budgets:
            raise BenchmarkError("pilot report budgets do not match the manifest")
        if report.planned_cells != _declared_cell_count(manifest):
            raise BenchmarkError("pilot report does not match the declared matrix")

        declared_strata = {item.stratum_id: item for item in pilot_set.strata}
        reported_strata = {item.stratum_id: item for item in report.strata}
        if set(declared_strata) != set(reported_strata):
            missing = sorted(set(declared_strata) - set(reported_strata))
            raise BenchmarkError(
                "pilot report does not cover every declared stratum; missing: "
                + (", ".join(missing) or "none")
            )
        covered = {item.architecture for item in report.strata}
        if covered != set(manifest.architectures):
            raise BenchmarkError(
                "the pilot set must measure at least one cell per architecture"
            )

        records = {record.run_id: record for record in manifest.run_records}
        unknown_digests: list[str] = []
        for stratum_id, estimate in sorted(reported_strata.items()):
            declared = declared_strata[stratum_id]
            if estimate.planned_cells != declared.planned_cells:
                raise BenchmarkError(
                    f"pilot stratum {stratum_id} planned cells do not match the "
                    "declaration"
                )
            if estimate.architecture != declared.architecture:
                raise BenchmarkError(
                    f"pilot stratum {stratum_id} architecture does not match the "
                    "declaration"
                )
            for observation in estimate.observations:
                record = records.get(observation.run_id)
                if record is None:
                    raise BenchmarkError(
                        f"pilot observation {observation.run_id} does not "
                        "reference a recorded cell"
                    )
                if record.lifecycle.status is not LifecycleStatus.COMPLETED:
                    raise BenchmarkError(
                        f"pilot cell {observation.run_id} did not complete "
                        "successfully; full matrix execution is blocked"
                    )
                if not BenchmarkRunner._stratum_matches(
                    declared,
                    architecture=record.architecture,
                    scenario_id=record.scenario_id,
                ):
                    raise BenchmarkError(
                        f"pilot cell {observation.run_id} does not belong to "
                        f"stratum {stratum_id}"
                    )
                if observation.record_digest != canonical_run_record_digest(record):
                    raise BenchmarkError(
                        "pilot report run-record digest does not match for "
                        f"{observation.run_id}"
                    )
                _require_reconciled_observation(observation, record)
                if observation.observed_cost_usd is None:
                    unknown_digests.append(observation.record_digest)

        expected = _matrix_estimate(list(report.strata))
        for field_name, value in expected.items():
            if getattr(report, field_name) != value:
                raise BenchmarkError(
                    f"pilot {field_name.replace('_', ' ')} is inconsistent with "
                    "its retained observations"
                )
        if tuple(sorted(set(unknown_digests))) != report.unknown_cost_record_digests:
            raise BenchmarkError(
                "pilot report unknown-cost digests do not match its observations"
            )

        if manifest.unknown_cost_acknowledged:
            if report.pilot_id != manifest.unknown_cost_pilot_id:
                raise BenchmarkError(
                    "unknown-cost acknowledgement is not bound to this pilot report"
                )
            acknowledged = set(manifest.unknown_cost_pilot_record_digests) or {
                manifest.unknown_cost_pilot_record_digest
            }
            if acknowledged != set(report.unknown_cost_record_digests):
                raise BenchmarkError(
                    "unknown-cost acknowledgement is not bound to every "
                    "unknown-cost pilot record"
                )
        if report.unknown_cost_record_digests and not allow_unknown_cost:
            raise BenchmarkError(
                "pilot cost is unknown; pass the explicit unknown-cost "
                "acknowledgement before continuing beyond the pilot"
            )
        return report


__all__ = [
    "BenchmarkCell",
    "BenchmarkCellResult",
    "BenchmarkError",
    "BenchmarkExecutionSummary",
    "BenchmarkPilotSetReport",
    "BenchmarkRunner",
    "PilotObservation",
    "PilotScalingMethod",
    "PilotStratumEstimate",
    "aggregate_manifest",
    "canonical_run_record_digest",
]
