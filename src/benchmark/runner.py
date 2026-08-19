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
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
from schemas.run_state import RunBudget
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


ArchitectureExecutor = Callable[[BenchmarkCell, Workspace], object]
SourcePreparer = Callable[[ScenarioRegistration, Path], tuple[Path, Path]]
RunIdFactory = Callable[[str, str, str, str, int], str]
RuleMap = Mapping[str | tuple[str, str], ScenarioRules]


def _now() -> datetime:
    return datetime.now(UTC)


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
    """Capture local code identity without requiring a repository or network."""

    try:
        revision_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
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
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        return CodeRevision(
            revision=revision,
            dirty=bool(dirty_result.stdout.strip()),
        )
    except OSError:
        return None


def _default_budgets() -> BudgetConfiguration:
    return BudgetConfiguration(
        resource_limits={
            "specialist_invocations": 12,
            "sql": 30,
            "python": 20,
            "critic_loops": 2,
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


def _failure_category(message: str) -> FailureCategory:
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
    ) -> BenchmarkManifest:
        """Create a frozen matrix declaration without generating data or agents."""

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
            run_configuration=RunConfiguration(
                execution_mode=execution_mode,
                tool_contract_version=TOOL_CONTRACT_VERSION,
                parameters=parameters,
            ),
            budgets=budgets or _default_budgets(),
            aggregation_version=AGGREGATION_VERSION,
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
                pilot.observed_cost_usd is None
                and not manifest.unknown_cost_acknowledged
            ):
                if not unknown_cost:
                    raise BenchmarkError(
                        "pilot cost is unknown; pass the explicit unknown-cost "
                        "acknowledgement before continuing beyond the pilot"
                    )
                manifest = manifest.model_copy(
                    update={
                        "unknown_cost_acknowledged": True,
                        "unknown_cost_pilot_id": pilot.pilot_id,
                        "unknown_cost_pilot_record_digest": pilot.record_digest,
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
            for cell in self._cells(manifest):
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
    ) -> tuple[BenchmarkExecutionSummary, BenchmarkPilotReport]:
        """Run one missing cell and persist a scaled full-matrix cost estimate."""

        path = Path(manifest_path).expanduser().resolve()
        pilot_file = Path(pilot_path).expanduser().resolve() if pilot_path else None
        if pilot_file is None:
            pilot_file = path.with_name(path.stem + ".pilot.json")
        if pilot_file.exists():
            raise BenchmarkError(f"pilot report already exists: {pilot_file}")
        summary = self.execute(
            path,
            resume=True,
            allow_paid=allow_paid,
            environment=environment,
            require_pilot=False,
            max_cells=1,
        )
        if not summary.executed_run_ids:
            raise BenchmarkError("pilot found no missing benchmark cell to execute")
        run_id = summary.executed_run_ids[0]
        record = next(
            record for record in summary.manifest.run_records if record.run_id == run_id
        )
        if record.lifecycle.status is LifecycleStatus.CANCELLED:
            # An interrupted cell is retained as an operational record, but it
            # measured nothing: publishing it as a cost pilot would scale a
            # partial observation across the whole matrix.
            raise BenchmarkError(
                "pilot cell was interrupted before completion; resume the "
                "interrupted cell before publishing a cost pilot"
            )
        total_cells = (
            len(summary.manifest.scenario_references)
            * len(summary.manifest.architectures)
            * summary.manifest.repetitions
        )
        scale = float(total_cells)
        observed_cost = record.cost.estimated_cost_usd
        report = BenchmarkPilotReport(
            pilot_id=f"pilot-{uuid.uuid4().hex}",
            manifest_id=summary.manifest.manifest_id,
            run_id=run_id,
            record_digest=canonical_run_record_digest(record),
            model=summary.manifest.model,
            model_provider=summary.manifest.model_provider,
            run_configuration=summary.manifest.run_configuration,
            budgets=summary.manifest.budgets,
            planned_cells=total_cells,
            observed_requests=record.usage.requests,
            observed_input_tokens=record.usage.input_tokens,
            observed_cached_tokens=record.usage.cached_tokens,
            observed_output_tokens=record.usage.output_tokens,
            observed_reasoning_tokens=record.usage.reasoning_tokens,
            observed_total_tokens=record.usage.total_tokens,
            observed_cost_usd=observed_cost,
            observed_cost=record.cost,
            estimated_full_matrix_cost_usd=(
                observed_cost * scale if observed_cost is not None else None
            ),
            observed_elapsed_seconds=record.latency.elapsed_seconds,
            observed_started_at=record.latency.started_at,
            observed_finished_at=record.latency.finished_at,
            estimated_full_matrix_elapsed_seconds=record.latency.elapsed_seconds
            * scale,
            created_at=_now(),
            methodology=(
                "One declared matrix cell was measured; full-matrix estimates are "
                "linear scaling only and must be reviewed before paid execution."
            ),
        )
        self._persist_text_exclusive(
            pilot_file,
            dump_stable_json(report.model_dump(mode="json")),
        )
        return summary, report

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
                self.code_revision if code_revision is None else code_revision
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
            max_sql_executions=resource_limits.get("sql", 30),
            max_python_executions=resource_limits.get("python", 20),
            max_critic_loops=resource_limits.get("critic_loops", 2),
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
        if status == LifecycleStatus.COMPLETED.value:
            lifecycle = LifecycleOutcome(status=LifecycleStatus.COMPLETED)
        elif status == LifecycleStatus.BLOCKED.value:
            lifecycle = LifecycleOutcome(
                status=LifecycleStatus.BLOCKED,
                failure_category=FailureCategory.BUDGET,
                failure_message=error or "analysis was constrained",
            )
        else:
            message = error or f"analysis returned lifecycle status {status}"
            lifecycle = LifecycleOutcome(
                status=LifecycleStatus.FAILED,
                failure_category=_failure_category(message),
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
            code_revision=self.code_revision,
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
    ) -> BenchmarkPilotReport:
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
            report = BenchmarkPilotReport.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as error:
            raise BenchmarkError(
                f"invalid cost-estimation pilot report: {error}"
            ) from error
        manifest = load_manifest(manifest_path)
        if report.manifest_id != manifest.manifest_id:
            raise BenchmarkError("pilot report belongs to a different manifest")
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
        expected_cells = (
            len(manifest.scenario_references)
            * len(manifest.architectures)
            * manifest.repetitions
        )
        if report.planned_cells != expected_cells:
            raise BenchmarkError("pilot report does not match the declared matrix")
        pilot_record = next(
            (
                record
                for record in manifest.run_records
                if record.run_id == report.run_id
            ),
            None,
        )
        if pilot_record is None:
            raise BenchmarkError("pilot report does not reference a recorded cell")
        if pilot_record.lifecycle.status is not LifecycleStatus.COMPLETED:
            raise BenchmarkError(
                "cost-estimation pilot did not complete successfully; "
                "full matrix execution is blocked"
            )
        expected_digest = canonical_run_record_digest(pilot_record)
        if report.record_digest != expected_digest:
            raise BenchmarkError("pilot report run-record digest does not match")
        if report.observed_requests != pilot_record.usage.requests:
            raise BenchmarkError(
                "pilot report request usage does not match the run record"
            )
        if report.observed_input_tokens != pilot_record.usage.input_tokens:
            raise BenchmarkError(
                "pilot report input-token usage does not match the run record"
            )
        if report.observed_cached_tokens != pilot_record.usage.cached_tokens:
            raise BenchmarkError(
                "pilot report cached-token usage does not match the run record"
            )
        if report.observed_output_tokens != pilot_record.usage.output_tokens:
            raise BenchmarkError(
                "pilot report output-token usage does not match the run record"
            )
        if report.observed_reasoning_tokens != pilot_record.usage.reasoning_tokens:
            raise BenchmarkError(
                "pilot report reasoning-token usage does not match the run record"
            )
        if report.observed_total_tokens != pilot_record.usage.total_tokens:
            raise BenchmarkError(
                "pilot report total-token usage does not match the run record"
            )
        if report.observed_elapsed_seconds != pilot_record.latency.elapsed_seconds:
            raise BenchmarkError("pilot report latency does not match the run record")
        if (
            report.observed_started_at != pilot_record.latency.started_at
            or report.observed_finished_at != pilot_record.latency.finished_at
        ):
            raise BenchmarkError(
                "pilot report latency timestamps do not match the run record"
            )
        if report.observed_cost != pilot_record.cost:
            raise BenchmarkError(
                "pilot report cost breakdown does not match the run record"
            )
        if report.observed_cost_usd != pilot_record.cost.estimated_cost_usd:
            raise BenchmarkError("pilot report cost does not match the run record")
        recorded_cost = pilot_record.cost.estimated_cost_usd
        expected_cost = (
            recorded_cost * expected_cells if recorded_cost is not None else None
        )
        if report.estimated_full_matrix_cost_usd != expected_cost:
            raise BenchmarkError("pilot full-matrix cost estimate is inconsistent")
        expected_elapsed = pilot_record.latency.elapsed_seconds * expected_cells
        if report.estimated_full_matrix_elapsed_seconds != expected_elapsed:
            raise BenchmarkError("pilot full-matrix latency estimate is inconsistent")
        if pilot_record.cost.availability is CostAvailability.KNOWN:
            if (
                pilot_record.cost.estimated_cost_usd is None
                or not pilot_record.cost.pricing_model
            ):
                raise BenchmarkError(
                    "known pilot pricing requires a cost and pricing model"
                )
        if manifest.unknown_cost_acknowledged and (
            report.pilot_id != manifest.unknown_cost_pilot_id
            or report.record_digest != manifest.unknown_cost_pilot_record_digest
        ):
            raise BenchmarkError(
                "unknown-cost acknowledgement is not bound to this pilot report"
            )
        if report.observed_cost_usd is None and not allow_unknown_cost:
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
    "BenchmarkPilotReport",
    "BenchmarkRunner",
    "aggregate_manifest",
    "canonical_run_record_digest",
]
