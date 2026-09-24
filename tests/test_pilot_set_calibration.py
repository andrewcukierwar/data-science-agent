"""R19 regressions for a declared, stratified pilot set.

The retained Task 10 attempts extrapolated the entire 60-cell matrix from one
first cell, so no architecture or workload difference could surface and one
measurement stood in for all sixty. The pilot is now a declared set with at
least one cell per architecture, every record is bound to the immutable
manifest, and the estimate is a stratified sum with an explicit range.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agents.runtime import DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
from benchmark import BenchmarkCellResult, BenchmarkError, BenchmarkRunner
from benchmark.runner import (
    BENCHMARK_RUNNER_VERSION,
    TOOL_CONTRACT_VERSION,
    canonical_manifest_declaration_digest,
    canonical_run_record_digest,
    default_pilot_set,
    output_schema_fingerprint,
)
from evaluation.contracts import (
    CodeRevision,
    CostAvailability,
    EvaluationCheck,
    EvaluationCheckStatus,
    EvaluatorResult,
    EvaluatorStatus,
    ExecutionMode,
    FailureCategory,
    LifecycleOutcome,
    LifecycleStatus,
    PilotSetDeclaration,
    PilotStratumDeclaration,
    ScoreBreakdown,
)
from evaluation.engine import load_manifest
from schemas.run_state import (
    AttemptCost,
    AttemptCostAvailability,
    AttemptRecord,
    AttemptStatus,
    CostBreakdown,
    ModelUsage,
    RunBlockReason,
)

FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)
SCENARIO_IDS = ("meaningful-ab-treatment-effect", "no-effect-ab-experiment")


def _sources(_registration, destination: Path) -> tuple[Path, Path]:
    inputs = destination / "inputs"
    docs = destination / "docs"
    inputs.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)
    (inputs / "fixture.parquet").write_bytes(b"deterministic fixture")
    (docs / "README.md").write_text("question context\n", encoding="utf-8")
    return inputs, docs


def _evaluated(cell):
    return EvaluatorResult(
        result_id=f"{cell.run_id}-fixture",
        run_id=cell.run_id,
        scenario_id=cell.scenario.scenario_id,
        scenario_version=cell.scenario.scenario_version,
        evaluator_version=cell.scenario.metadata.evaluator_version,
        status=EvaluatorStatus.PASS,
        checks=(
            EvaluationCheck(
                check_id="fixture:quality",
                status=EvaluationCheckStatus.PASS,
                message="fixture score",
            ),
        ),
        score_breakdown=ScoreBreakdown(dimensions={"quality": 1.0}, overall_score=1.0),
        evaluated_at=FIXED_TIME,
    )


def _completed(
    cell,
    *,
    cost_usd: float | None = 0.001,
    elapsed: float = 2.0,
    usage_complete: bool = True,
):
    """A completed cell whose cost and latency vary by architecture."""

    usage = ModelUsage(
        requests=1,
        input_tokens=1_000,
        cached_tokens=0,
        output_tokens=200,
        reasoning_tokens=0,
        total_tokens=1_200,
    )
    breakdown = (
        CostBreakdown(
            pricing_model="fixture-pricing-v1",
            input_per_1m=1.0,
            cached_input_per_1m=0.1,
            output_per_1m=2.0,
            input_tokens=usage.input_tokens,
            cached_tokens=usage.cached_tokens,
            uncached_input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            uncached_input_cost_usd=cost_usd,
            cached_input_cost_usd=0.0,
            output_cost_usd=0.0,
            estimated_cost_usd=cost_usd,
        )
        if cost_usd is not None
        else None
    )
    from types import SimpleNamespace

    state = SimpleNamespace(
        run_id=cell.run_id,
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
        elapsed_seconds=elapsed,
        usage=usage,
        cost_breakdown=breakdown,
        usage_complete=usage_complete,
    )
    return BenchmarkCellResult(
        lifecycle=LifecycleOutcome(status=LifecycleStatus.COMPLETED),
        evaluator_result=_evaluated(cell),
        state=state,
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
    )


def _architecture_cost(cell):
    """Single-agent cells are cheaper and faster than multi-agent cells."""

    if cell.architecture == "single-agent":
        return _completed(cell, cost_usd=0.001, elapsed=2.0)
    return _completed(cell, cost_usd=0.004, elapsed=8.0)


def _bounded_timeout(cell):
    """A failed agent-bound timeout with lower-bound usage and no priced cost."""

    elapsed = 300.03875158308074
    result = _completed(
        cell,
        cost_usd=None,
        elapsed=elapsed,
        usage_complete=False,
    )
    finished = FIXED_TIME + timedelta(seconds=elapsed)
    result.state.updated_at = finished
    result.state.cost_estimation_note = (
        "Provider usage is incomplete; cost cannot be estimated."
    )
    result.state.attempt_history = (
        AttemptRecord(
            attempt_number=1,
            attempt_id=f"{cell.run_id}-attempt-1",
            status=AttemptStatus.FAILED,
            started_at=FIXED_TIME,
            finished_at=finished,
            usage_delta=result.state.usage,
            usage_complete=False,
            cost=AttemptCost(
                availability=AttemptCostAvailability.UNAVAILABLE,
                note="Provider usage is incomplete; cost cannot be estimated.",
            ),
            elapsed_seconds=elapsed,
            error="TimeoutError: agent invocation timed out",
            block_reason=RunBlockReason.TIMEOUT,
        ),
    )
    return replace(
        result,
        lifecycle=LifecycleOutcome(
            status=LifecycleStatus.FAILED,
            failure_category=FailureCategory.TIMEOUT,
            failure_message="agent invocation exceeded its declared timeout",
        ),
        evaluator_result=None,
        finished_at=finished,
    )


def _runner(tmp_path: Path, executor=None) -> BenchmarkRunner:
    executor = executor or (lambda cell, _workspace: _architecture_cost(cell))
    return BenchmarkRunner(
        tmp_path / "workspaces",
        architecture_executors={
            "single-agent": executor,
            "multi-agent": executor,
        },
        source_preparer=_sources,
    )


def _plan(
    runner: BenchmarkRunner,
    tmp_path: Path,
    *,
    repetitions: int = 1,
    pilot_set: PilotSetDeclaration | None = None,
    name: str = "manifest.json",
) -> Path:
    manifest = runner.build_manifest(
        manifest_id="pilot-set-manifest",
        scenario_ids=list(SCENARIO_IDS),
        architectures=("single-agent", "multi-agent"),
        repetitions=repetitions,
        model="gpt-5.6-luna",
        model_provider="openai",
        execution_mode=ExecutionMode.DETERMINISTIC,
        repetition_justification="R19 pilot-set fixture",
        pilot_set=pilot_set,
    )
    path = tmp_path / name
    runner.persist_plan(manifest, path)
    return path


# --- the declared pilot set -------------------------------------------------


def test_planning_declares_one_stratum_per_architecture(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    manifest = load_manifest(_plan(runner, tmp_path))

    assert manifest.pilot_set is not None
    assert set(manifest.pilot_set.architectures) == set(manifest.architectures)
    # Strata partition the declared matrix, so the estimate covers all cells.
    assert manifest.pilot_set.planned_cells == (
        len(manifest.scenario_references)
        * len(manifest.architectures)
        * manifest.repetitions
    )


def test_pilot_set_must_cover_every_declared_architecture(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    manifest = load_manifest(_plan(runner, tmp_path))
    incomplete = PilotSetDeclaration(
        strata=(
            PilotStratumDeclaration(
                stratum_id="only-single",
                architecture="single-agent",
                planned_cells=4,
            ),
        )
    )

    with pytest.raises(ValueError, match="every declared architecture"):
        manifest.model_copy(update={"pilot_set": incomplete}).model_validate(
            manifest.model_copy(update={"pilot_set": incomplete}).model_dump(
                mode="json"
            )
        )


def test_workload_strata_can_be_declared_explicitly(tmp_path: Path) -> None:
    """A narrower workload class is named, not inferred."""

    pilot_set = PilotSetDeclaration(
        strata=(
            PilotStratumDeclaration(
                stratum_id="single:effect",
                architecture="single-agent",
                scenario_ids=(SCENARIO_IDS[0],),
                planned_cells=1,
            ),
            PilotStratumDeclaration(
                stratum_id="single:no-effect",
                architecture="single-agent",
                scenario_ids=(SCENARIO_IDS[1],),
                planned_cells=1,
            ),
            PilotStratumDeclaration(
                stratum_id="multi:all",
                architecture="multi-agent",
                planned_cells=2,
            ),
        )
    )
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path, pilot_set=pilot_set)
    _, report = runner.run_pilot(manifest_path, pilot_path=tmp_path / "pilot.json")

    assert {item.stratum_id for item in report.strata} == {
        "single:effect",
        "single:no-effect",
        "multi:all",
    }
    # Each stratum measured a cell from its own workload class.
    by_id = {item.stratum_id: item for item in report.strata}
    assert by_id["single:effect"].observations[0].scenario_id == SCENARIO_IDS[0]
    assert by_id["single:no-effect"].observations[0].scenario_id == SCENARIO_IDS[1]


def test_pilot_strata_cannot_overlap_or_omit_declared_workloads(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    manifest = load_manifest(_plan(runner, tmp_path))
    overlapping = PilotSetDeclaration(
        strata=(
            PilotStratumDeclaration(
                stratum_id="single:all",
                architecture="single-agent",
                planned_cells=2,
            ),
            PilotStratumDeclaration(
                stratum_id="single:effect-overlap",
                architecture="single-agent",
                scenario_ids=(SCENARIO_IDS[0],),
                planned_cells=1,
            ),
            PilotStratumDeclaration(
                stratum_id="multi:no-effect-only",
                architecture="multi-agent",
                scenario_ids=(SCENARIO_IDS[1],),
                planned_cells=1,
            ),
        )
    )

    with pytest.raises(ValueError, match="partition every.*exactly once"):
        manifest.model_copy(update={"pilot_set": overlapping}).model_validate(
            manifest.model_copy(update={"pilot_set": overlapping}).model_dump(
                mode="json"
            )
        )


def test_pilot_stratum_planned_cells_are_derived_from_its_scope(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    manifest = load_manifest(_plan(runner, tmp_path))
    misstated = PilotSetDeclaration(
        strata=(
            PilotStratumDeclaration(
                stratum_id="single:all",
                architecture="single-agent",
                planned_cells=1,
            ),
            PilotStratumDeclaration(
                stratum_id="multi:all",
                architecture="multi-agent",
                planned_cells=3,
            ),
        )
    )

    with pytest.raises(ValueError, match="planned_cells must equal"):
        manifest.model_copy(update={"pilot_set": misstated}).model_validate(
            manifest.model_copy(update={"pilot_set": misstated}).model_dump(mode="json")
        )


# --- stratified estimate ----------------------------------------------------


def test_estimate_is_stratified_and_retains_per_pilot_observations(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    _, report = runner.run_pilot(manifest_path, pilot_path=tmp_path / "pilot.json")

    assert report.scaling_method.value == "stratified_mean"
    assert len(report.strata) == 2
    assert len(report.observations) == 2
    # Per-architecture observations are retained separately.
    by_arch = {item.architecture: item for item in report.strata}
    assert by_arch["single-agent"].mean_cost_usd == pytest.approx(0.001)
    assert by_arch["multi-agent"].mean_cost_usd == pytest.approx(0.004)
    assert by_arch["single-agent"].mean_elapsed_seconds == pytest.approx(2.0)
    assert by_arch["multi-agent"].mean_elapsed_seconds == pytest.approx(8.0)
    # The matrix estimate sums the strata rather than scaling one cell.
    assert report.estimated_full_matrix_cost_usd == pytest.approx(0.001 * 2 + 0.004 * 2)
    assert report.estimated_full_matrix_elapsed_seconds == pytest.approx(
        2.0 * 2 + 8.0 * 2
    )
    # A single-cell linear extrapolation would have produced a different total.
    assert report.estimated_full_matrix_cost_usd != pytest.approx(0.001 * 4)
    assert report.methodology


def test_runner_and_report_versions_preserve_existing_analysis_contracts(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    manifest = load_manifest(manifest_path)
    parameters = manifest.run_configuration.parameters

    assert parameters["benchmark_runner_version"] == BENCHMARK_RUNNER_VERSION == "1.2"
    assert (
        parameters["agent_run_timeout_seconds"]
        == DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
        == 300.0
    )
    assert parameters["evaluator_contract_version"] == "1.3"
    assert manifest.run_configuration.tool_contract_version == TOOL_CONTRACT_VERSION


def test_historical_2_0_pilot_reports_remain_readable(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)

    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["report_version"] = "2.0"
    for stratum in payload["strata"]:
        for observation in stratum["observations"]:
            observation.pop("usage_completeness")
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    summary = runner.execute(
        manifest_path,
        resume=True,
        require_pilot=True,
        pilot_path=pilot_path,
        max_cells=0,
    )

    assert summary.executed_run_ids == ()


def test_estimate_states_an_explicit_range(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    _, report = runner.run_pilot(manifest_path, pilot_path=tmp_path / "pilot.json")

    assert report.estimated_full_matrix_cost_low_usd is not None
    assert report.estimated_full_matrix_cost_high_usd is not None
    assert (
        report.estimated_full_matrix_cost_low_usd
        <= report.estimated_full_matrix_cost_usd
        <= report.estimated_full_matrix_cost_high_usd
    )
    assert (
        report.estimated_full_matrix_elapsed_low_seconds
        <= report.estimated_full_matrix_elapsed_seconds
        <= report.estimated_full_matrix_elapsed_high_seconds
    )


def test_unknown_cost_in_one_stratum_makes_the_matrix_estimate_unavailable(
    tmp_path: Path,
) -> None:
    """An unknown stratum must not be silently treated as zero."""

    def execute(cell, _workspace):
        if cell.architecture == "multi-agent":
            return _completed(cell, cost_usd=None)
        return _completed(cell, cost_usd=0.001)

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)
    _, report = runner.run_pilot(manifest_path, pilot_path=tmp_path / "pilot.json")

    assert report.cost_availability is CostAvailability.UNAVAILABLE
    assert report.estimated_full_matrix_cost_usd is None
    assert report.estimated_full_matrix_cost_low_usd is None
    assert report.unknown_cost_record_digests


def test_incomplete_usage_cannot_be_used_as_pilot_evidence(tmp_path: Path) -> None:
    def execute(cell, _workspace):
        return _completed(cell, cost_usd=None, usage_complete=False)

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)

    with pytest.raises(BenchmarkError, match="pilot usage is incomplete"):
        runner.run_pilot(manifest_path, pilot_path=tmp_path / "pilot.json")


def test_bounded_incomplete_timeout_is_retained_without_replacement(
    tmp_path: Path,
) -> None:
    calls = []

    def execute(cell, _workspace):
        calls.append(cell)
        return _bounded_timeout(cell) if len(calls) == 1 else _architecture_cost(cell)

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)
    manifest = load_manifest(manifest_path)
    assert manifest.pilot_set is not None
    declared_architectures = [item.architecture for item in manifest.pilot_set.strata]
    pilot_path = tmp_path / "pilot.json"

    summary, report = runner.run_pilot(manifest_path, pilot_path=pilot_path)

    assert len(calls) == len(manifest.pilot_set.strata)
    assert [cell.architecture for cell in calls] == declared_architectures
    assert report.report_version == "2.1"
    assert report.output_schema_fingerprint == output_schema_fingerprint()
    assert len(report.observations) == len(manifest.pilot_set.strata)
    timed_out_call = calls[0]
    timeout_record = next(
        record
        for record in summary.manifest.run_records
        if record.run_id == timed_out_call.run_id
    )
    timeout_observation = next(
        observation
        for observation in report.observations
        if observation.run_id == timed_out_call.run_id
    )

    assert timeout_record.lifecycle.status is LifecycleStatus.FAILED
    assert timeout_record.lifecycle.failure_category is FailureCategory.TIMEOUT
    assert timeout_record.evaluator_result.status is EvaluatorStatus.NOT_EVALUATED
    assert timeout_record.score_breakdown is None
    assert timeout_record.usage.complete is False
    assert timeout_record.usage.requests == 1
    assert timeout_record.usage.input_tokens == 1_000
    assert timeout_record.usage.output_tokens == 200
    assert timeout_record.usage.total_tokens == 1_200
    assert timeout_record.cost.availability is CostAvailability.UNAVAILABLE
    assert timeout_record.cost.estimated_cost_usd is None

    assert timeout_observation.usage_completeness == "lower_bound"
    assert timeout_observation.observed_requests == timeout_record.usage.requests
    assert (
        timeout_observation.observed_input_tokens == timeout_record.usage.input_tokens
    )
    assert (
        timeout_observation.observed_output_tokens == timeout_record.usage.output_tokens
    )
    assert (
        timeout_observation.observed_total_tokens == timeout_record.usage.total_tokens
    )
    assert (
        timeout_observation.observed_cost.availability is CostAvailability.UNAVAILABLE
    )
    assert timeout_observation.observed_cost_usd is None
    assert timeout_observation.observed_elapsed_seconds == pytest.approx(
        300.03875158308074
    )
    timeout_digest = canonical_run_record_digest(timeout_record)
    assert timeout_observation.record_digest == timeout_digest
    assert report.unknown_cost_record_digests == (timeout_digest,)

    timeout_stratum = next(
        item
        for item in report.strata
        if item.architecture == timed_out_call.architecture
    )
    assert timeout_stratum.cost_availability is CostAvailability.UNAVAILABLE
    assert timeout_stratum.estimated_cost_usd is None
    assert report.cost_availability is CostAvailability.UNAVAILABLE
    assert report.estimated_full_matrix_cost_usd is None
    assert report.estimated_full_matrix_cost_low_usd is None
    assert report.estimated_full_matrix_cost_high_usd is None
    assert report.estimated_full_matrix_elapsed_seconds >= 300.0
    assert report.estimated_full_matrix_elapsed_low_seconds >= 300.0
    assert report.estimated_full_matrix_elapsed_high_seconds >= 300.0

    timeout_aggregate = next(
        aggregate
        for aggregate in summary.manifest.aggregates
        if aggregate.scenario_id == timeout_record.scenario_id
        and aggregate.architecture == timeout_record.architecture
    )
    assert timeout_aggregate.denominator is not None
    assert timeout_aggregate.denominator.failed_runs == 1
    assert timeout_aggregate.denominator.completed_runs == 0
    assert timeout_aggregate.denominator.evaluated_runs == 0
    assert timeout_aggregate.failure_taxonomy == {
        "evaluator:not_evaluated": 1,
        "lifecycle:timeout": 1,
    }

    # Re-running publication reuses the exact timeout record instead of
    # advancing to another cell in that stratum.
    _, republished = runner.run_pilot(
        manifest_path,
        pilot_path=tmp_path / "pilot-republished.json",
    )
    assert len(calls) == len(manifest.pilot_set.strata)
    assert {observation.run_id for observation in republished.observations} == {
        observation.run_id for observation in report.observations
    }
    assert (
        next(
            item
            for item in republished.observations
            if item.run_id == timed_out_call.run_id
        ).record_digest
        == timeout_digest
    )

    with pytest.raises(BenchmarkError, match="unknown-cost"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
        )

    acknowledged = runner.execute(
        manifest_path,
        resume=True,
        require_pilot=True,
        pilot_path=pilot_path,
        unknown_cost=True,
        max_cells=0,
    )
    assert acknowledged.manifest.unknown_cost_acknowledged is True
    assert acknowledged.manifest.unknown_cost_pilot_id == report.pilot_id
    assert acknowledged.manifest.unknown_cost_pilot_record_digests == (timeout_digest,)
    assert acknowledged.manifest.unknown_cost_pilot_record_digest == timeout_digest

    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    forged_digest = "f" * 64
    manifest_payload["unknown_cost_pilot_record_digest"] = forged_digest
    manifest_payload["unknown_cost_pilot_record_digests"] = [forged_digest]
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="not bound to every unknown-cost"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
            max_cells=0,
        )


@pytest.mark.parametrize(
    "category",
    (
        FailureCategory.OTHER,
        FailureCategory.AGENT,
        FailureCategory.SCHEMA,
        FailureCategory.PROVIDER,
        FailureCategory.TOOL,
        FailureCategory.WORKSPACE,
    ),
)
def test_non_timeout_failed_cells_remain_ineligible_pilot_observations(
    tmp_path: Path,
    category: FailureCategory,
) -> None:
    def execute(cell, _workspace):
        return replace(
            _completed(cell),
            lifecycle=LifecycleOutcome(
                status=LifecycleStatus.FAILED,
                failure_category=category,
                failure_message=f"fixture {category.value} failure",
            ),
            evaluator_result=None,
        )

    runner = _runner(tmp_path / category.value, execute)
    manifest_path = _plan(runner, tmp_path / category.value)
    pilot_path = tmp_path / category.value / "pilot.json"

    with pytest.raises(BenchmarkError, match="ended as failed"):
        runner.run_pilot(manifest_path, pilot_path=pilot_path)

    assert not pilot_path.exists()
    failed = next(
        record
        for record in load_manifest(manifest_path).run_records
        if record.lifecycle.status is LifecycleStatus.FAILED
    )
    assert failed.lifecycle.failure_category is category


def test_timeout_category_without_agent_bound_elapsed_is_ineligible(
    tmp_path: Path,
) -> None:
    def execute(cell, _workspace):
        result = _bounded_timeout(cell)
        result.state.elapsed_seconds = 30.0
        return result

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)

    with pytest.raises(BenchmarkError, match="ended as failed"):
        runner.run_pilot(manifest_path, pilot_path=tmp_path / "pilot.json")


# --- the full-run gate ------------------------------------------------------


def test_full_run_gate_requires_every_declared_stratum(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)

    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["strata"] = payload["strata"][:1]
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="every declared stratum"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
        )


def test_full_run_gate_refuses_an_unreconciled_pilot_observation(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)

    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["strata"][0]["observations"][0]["observed_total_tokens"] += 1
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="does not match the run record"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
        )


def test_full_run_gate_refuses_a_missing_pilot_record(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)

    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["strata"][0]["observations"][0]["run_id"] = "not-a-declared-cell"
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="does not .*reference a recorded cell"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
        )


def test_full_run_gate_refuses_a_tampered_matrix_estimate(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)

    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["estimated_full_matrix_cost_usd"] = 0.0
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="inconsistent with its retained"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
        )


def test_pilot_accepts_a_bounded_blocked_cell_as_a_cost_observation(
    tmp_path: Path,
) -> None:
    """A blocked cell with reconciled usage is a valid cost observation.

    The multi-agent stratum's declared cell legitimately blocks with
    ``validation_revision`` on the harder scenarios. That is a real benchmark
    outcome the failure taxonomy records, and it consumed its declared budgets,
    so it is the conservative cost measurement — not a reason to refuse the
    matrix the benchmark exists to run.
    """

    def execute(cell, _workspace):
        result = _architecture_cost(cell)
        if cell.architecture != "multi-agent":
            return result
        return replace(
            result,
            lifecycle=LifecycleOutcome(
                status=LifecycleStatus.BLOCKED,
                failure_category=FailureCategory.VALIDATION,
                failure_message=(
                    "Critic returned REVISE and the configured maximum of "
                    "3 critic loop(s) was reached."
                ),
            ),
            evaluator_result=None,
        )

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"

    _, report = runner.run_pilot(manifest_path, pilot_path=pilot_path)

    assert pilot_path.exists()
    assert report.estimated_full_matrix_cost_usd is not None
    blocked = [
        record
        for record in load_manifest(manifest_path).run_records
        if record.architecture == "multi-agent"
    ]
    assert blocked
    assert all(record.lifecycle.status is LifecycleStatus.BLOCKED for record in blocked)
    assert all(record.usage.complete for record in blocked)
    # The full-matrix gate accepts the same evidence it was measured from.
    runner.execute(
        manifest_path,
        resume=True,
        require_pilot=True,
        pilot_path=pilot_path,
        max_cells=1,
    )


def test_full_run_gate_refuses_a_failed_pilot_cell(tmp_path: Path) -> None:
    state = {"fail_multi": False}

    def execute(cell, _workspace):
        if cell.architecture == "multi-agent" and not state["fail_multi"]:
            state["fail_multi"] = True
            raise RuntimeError("deterministic pilot failure")
        return _architecture_cost(cell)

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)

    with pytest.raises(BenchmarkError, match="ended as failed"):
        runner.run_pilot(manifest_path, pilot_path=tmp_path / "pilot.json")

    # A second invocation must not skip the failed observation and select a
    # later cell from the same stratum until one happens to succeed.
    with pytest.raises(BenchmarkError, match="instead of replacing failed"):
        runner.run_pilot(manifest_path, pilot_path=tmp_path / "pilot-retry.json")


def test_partial_pilot_publication_reuses_completed_cells(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    manifest = load_manifest(manifest_path)
    first_stratum = manifest.pilot_set.strata[0]
    first_cell, requires_execution = runner._pilot_cell_for_stratum(  # noqa: SLF001
        manifest,
        first_stratum,
    )
    assert requires_execution is True
    first = runner.execute(
        manifest_path,
        resume=True,
        require_pilot=False,
        max_cells=1,
        only_run_ids=(first_cell.run_id,),
    )
    assert first.executed_run_ids == (first_cell.run_id,)

    summary, report = runner.run_pilot(
        manifest_path,
        pilot_path=tmp_path / "pilot-after-partial.json",
    )

    assert first_cell.run_id in {item.run_id for item in report.observations}
    assert first_cell.run_id not in summary.executed_run_ids
    assert len(report.observations) == 2


# --- manifest binding -------------------------------------------------------


def test_changing_turn_budgets_changes_the_declaration_digest(
    tmp_path: Path,
) -> None:
    """Turn budgets may change only in a new manifest version.

    The manifest's own cross-validation rejects a budget change once records
    exist; the declaration digest is the second, independent guard that also
    covers a change made before any cell ran.
    """

    runner = _runner(tmp_path)
    manifest = load_manifest(_plan(runner, tmp_path))
    before = canonical_manifest_declaration_digest(manifest)
    rebudgeted = manifest.model_copy(
        update={
            "budgets": manifest.budgets.model_copy(
                update={"turn_limits": {**manifest.budgets.turn_limits, "lead": 99}}
            )
        }
    )

    assert canonical_manifest_declaration_digest(rebudgeted) != before


def test_manifest_execution_is_bound_to_the_exact_code_revision(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    manifest = load_manifest(manifest_path)

    assert manifest.code_revision == runner.code_revision
    assert manifest.code_revision is not None
    runner.code_revision = CodeRevision(
        revision=manifest.code_revision.revision,
        dirty=True,
        working_tree_digest="0" * 64,
    )

    with pytest.raises(BenchmarkError, match="repository state differs"):
        runner.execute(manifest_path, resume=True, require_pilot=False)


def test_changing_the_pilot_set_invalidates_the_pilot(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)

    manifest = load_manifest(manifest_path)
    replacement = PilotSetDeclaration(
        strata=(
            PilotStratumDeclaration(
                stratum_id="single:renamed",
                architecture="single-agent",
                planned_cells=2,
            ),
            PilotStratumDeclaration(
                stratum_id="multi:renamed",
                architecture="multi-agent",
                planned_cells=2,
            ),
        )
    )
    manifest_path.write_text(
        json.dumps(
            manifest.model_copy(update={"pilot_set": replacement}).model_dump(
                mode="json"
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkError, match="freeze a new manifest version"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
        )


def test_recording_cells_does_not_invalidate_a_valid_pilot(
    tmp_path: Path,
) -> None:
    """Executing the matrix must not invalidate its own pilot evidence."""

    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)
    digest_after_pilot = canonical_manifest_declaration_digest(
        load_manifest(manifest_path)
    )

    full = runner.execute(
        manifest_path,
        resume=True,
        require_pilot=True,
        pilot_path=pilot_path,
    )

    assert full.manifest.status.value == "complete"
    assert canonical_manifest_declaration_digest(full.manifest) == digest_after_pilot


def test_output_schema_change_invalidates_the_pilot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pilot evidence describes one structured-output contract."""

    runner = _runner(tmp_path)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)

    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    assert payload["output_schema_fingerprint"] == output_schema_fingerprint()
    payload["output_schema_fingerprint"] = "0" * 64
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="output schemas changed"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
        )


def test_unknown_cost_acknowledgement_binds_every_affected_record(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path, lambda cell, _w: _completed(cell, cost_usd=None))
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    _, report = runner.run_pilot(manifest_path, pilot_path=pilot_path)

    assert len(report.unknown_cost_record_digests) == 2

    with pytest.raises(BenchmarkError, match="unknown-cost"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
        )

    full = runner.execute(
        manifest_path,
        resume=True,
        require_pilot=True,
        pilot_path=pilot_path,
        unknown_cost=True,
    )

    assert full.manifest.unknown_cost_acknowledged is True
    assert full.manifest.unknown_cost_pilot_id == report.pilot_id
    assert set(full.manifest.unknown_cost_pilot_record_digests) == set(
        report.unknown_cost_record_digests
    )
    # Each bound digest is a real record digest from the pilot set.
    records = {
        canonical_run_record_digest(record)
        for record in full.manifest.run_records
        if record.run_id in {item.run_id for item in report.observations}
    }
    assert set(full.manifest.unknown_cost_pilot_record_digests) <= records


def test_default_pilot_set_helper_partitions_by_architecture() -> None:
    declaration = default_pilot_set(
        ("single-agent", "multi-agent"),
        scenario_count=10,
        repetitions=3,
    )

    assert declaration.planned_cells == 60
    assert set(declaration.architectures) == {"single-agent", "multi-agent"}
    assert all(item.planned_cells == 30 for item in declaration.strata)
