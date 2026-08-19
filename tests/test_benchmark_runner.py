"""Deterministic tests for the resumable benchmark matrix runner."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from benchmark import (
    BenchmarkCellResult,
    BenchmarkError,
    BenchmarkRunner,
    canonical_run_record_digest,
)
from evaluation.contracts import (
    CostAvailability,
    EvaluationCheck,
    EvaluationCheckStatus,
    EvaluatorResult,
    EvaluatorStatus,
    ExecutionMode,
    FailureCategory,
    LifecycleOutcome,
    LifecycleStatus,
    ManifestStatus,
    ScoreBreakdown,
)
from evaluation.engine import ScenarioRules, load_manifest
from evaluation.workspace_identity import (
    load_workspace_identity,
    workspace_identity_path,
)
from orchestration.ledger import AnalysisLedger
from orchestration.pricing import MODEL_PRICING
from schemas.run_state import (
    AttemptStatus,
    CostBreakdown,
    ModelUsage,
    ToolEvent,
    ToolEventStatus,
)

FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)
SCENARIO_ID = "meaningful-ab-treatment-effect"


def _sources(_registration, destination: Path) -> tuple[Path, Path]:
    inputs = destination / "inputs"
    docs = destination / "docs"
    inputs.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)
    (inputs / "fixture.parquet").write_bytes(b"deterministic fixture")
    (docs / "README.md").write_text("question context\n", encoding="utf-8")
    return inputs, docs


def _evaluated(cell, *, evaluator_version: str | None = None, score: float = 1.0):
    version = evaluator_version or cell.scenario.metadata.evaluator_version
    breakdown = ScoreBreakdown(
        dimensions={"quality": score},
        overall_score=score,
    )
    return EvaluatorResult(
        result_id=f"{cell.run_id}-{version}",
        run_id=cell.run_id,
        scenario_id=cell.scenario.scenario_id,
        scenario_version=cell.scenario.scenario_version,
        evaluator_version=version,
        status=EvaluatorStatus.PASS if score == 1 else EvaluatorStatus.FAIL,
        checks=(
            EvaluationCheck(
                check_id="fixture:quality",
                status=(
                    EvaluationCheckStatus.PASS
                    if score == 1
                    else EvaluationCheckStatus.FAIL
                ),
                message="fixture score",
            ),
        ),
        score_breakdown=breakdown,
        failure_reasons=() if score == 1 else ("fixture failure",),
        evaluated_at=FIXED_TIME,
    )


def _completed(cell, *, evaluator_version: str | None = None, score: float = 1.0):
    return BenchmarkCellResult(
        lifecycle=LifecycleOutcome(status=LifecycleStatus.COMPLETED),
        evaluator_result=_evaluated(
            cell,
            evaluator_version=evaluator_version,
            score=score,
        ),
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
    )


def _runner(tmp_path, executor):
    return BenchmarkRunner(
        tmp_path / "workspaces",
        architecture_executors={"single-agent": executor},
        source_preparer=_sources,
    )


def _plan(
    runner,
    tmp_path,
    *,
    repetitions=3,
    architectures=("single-agent",),
):
    manifest = runner.build_manifest(
        manifest_id="fixture-manifest",
        scenario_ids=[SCENARIO_ID],
        architectures=architectures,
        repetitions=repetitions,
        model="fixture-model",
        model_provider="fixture-provider",
        execution_mode=ExecutionMode.DETERMINISTIC,
        repetition_justification=(
            "R4 evaluator-error fixture" if repetitions < 3 else None
        ),
    )
    path = tmp_path / "manifest.json"
    runner.persist_plan(manifest, path)
    return path


def test_plan_is_persisted_before_execution_and_resume_skips_completed_cells(
    tmp_path,
):
    calls: list[str] = []
    manifest_path: Path | None = None

    def execute(cell, _workspace):
        assert manifest_path is not None and manifest_path.is_file()
        calls.append(cell.run_id)
        return _completed(cell)

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)

    first = runner.execute(manifest_path, max_cells=1)
    assert first.manifest.status.value == "running"
    assert len(first.executed_run_ids) == 1

    second = runner.execute(manifest_path, resume=True)
    assert second.manifest.status.value == "complete"
    assert len(second.skipped_run_ids) == 1
    assert len(calls) == 3
    assert len(set(calls)) == 3

    with pytest.raises(BenchmarkError, match="already contains run records"):
        runner.execute(manifest_path)


def test_interruption_aborts_without_losing_workspace_and_resume_continues(tmp_path):
    calls: list[str] = []
    interrupted = {"value": False}

    def execute(cell, workspace):
        calls.append(cell.run_id)
        marker = workspace.working / "interrupted.marker"
        marker.write_text("keep", encoding="utf-8")
        if not interrupted["value"]:
            interrupted["value"] = True
            raise KeyboardInterrupt
        assert marker.read_text(encoding="utf-8") == "keep"
        return _completed(cell)

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)

    first = runner.execute(manifest_path)
    assert first.interrupted is True
    assert first.manifest.status.value == "aborted"
    assert len(first.manifest.run_records) == 0

    second = runner.execute(manifest_path, resume=True)
    assert second.manifest.status.value == "complete"
    assert len(calls) == 4
    assert len(second.manifest.run_records) == 3


def test_duplicate_run_ids_are_rejected_at_plan_time(tmp_path):
    runner = BenchmarkRunner(
        tmp_path / "workspaces",
        run_id_factory=lambda *_args: "duplicate-run-id",
    )
    with pytest.raises(BenchmarkError, match="duplicate immutable run ID"):
        runner.build_manifest(
            manifest_id="duplicate-manifest",
            scenario_ids=[SCENARIO_ID],
            architectures=("single-agent",),
            repetitions=3,
            model="fixture-model",
            execution_mode=ExecutionMode.DETERMINISTIC,
        )


def test_benchmark_cli_requires_an_explicit_model_for_planning():
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "run_benchmark.py"),
        "dry-run",
        "--scenario-id",
        SCENARIO_ID,
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    assert result.returncode == 2
    assert "--model" in result.stderr
    assert "required" in result.stderr


def test_failed_cell_isolated_and_recorded_with_frozen_manifest_identity(tmp_path):
    failed_once = {"value": False}

    def execute(cell, _workspace):
        if not failed_once["value"]:
            failed_once["value"] = True
            raise RuntimeError("provider rate limit")
        return _completed(cell)

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)
    summary = runner.execute(manifest_path)

    assert summary.manifest.status.value == "complete"
    assert len(summary.failed_run_ids) == 1
    failed = next(
        record
        for record in summary.manifest.run_records
        if record.run_id == summary.failed_run_ids[0]
    )
    assert failed.model == "fixture-model"
    assert failed.model_provider == "fixture-provider"
    assert failed.lifecycle.failure_category.value == "provider"
    assert failed.evaluator_result.status is EvaluatorStatus.NOT_EVALUATED


def test_duplicate_plan_and_existing_workspace_are_never_overwritten(tmp_path):
    def execute(cell, workspace):
        marker = workspace.working / "sentinel.txt"
        if marker.exists():
            assert marker.read_text(encoding="utf-8") == "original"
        else:
            marker.write_text("original", encoding="utf-8")
        return _completed(cell)

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)
    with pytest.raises(BenchmarkError, match="refusing to overwrite existing file"):
        runner.persist_plan(load_manifest(manifest_path), manifest_path)

    runner.execute(manifest_path, max_cells=1)
    first_run_id = load_manifest(manifest_path).run_records[0].run_id
    marker = tmp_path / "workspaces" / first_run_id / "working" / "sentinel.txt"
    marker.write_text("original", encoding="utf-8")
    runner.execute(manifest_path, resume=True)
    assert marker.read_text(encoding="utf-8") == "original"


def test_workspace_base_is_frozen_in_plan_and_used_on_resume(tmp_path):
    planned_base = tmp_path / "planned-workspaces"
    other_base = tmp_path / "other-workspaces"
    planner = BenchmarkRunner(planned_base, source_preparer=_sources)
    manifest_path = _plan(planner, tmp_path)

    calls: list[str] = []

    def execute(cell, _workspace):
        calls.append(cell.run_id)
        return _completed(cell)

    executor = BenchmarkRunner(
        other_base,
        architecture_executors={"single-agent": execute},
        source_preparer=_sources,
    )
    summary = executor.execute(manifest_path, max_cells=1)

    assert len(calls) == 1
    assert summary.manifest.run_records[0].workspace_path.startswith(
        str(planned_base.resolve())
    )
    assert not other_base.joinpath(calls[0]).exists()


def test_offline_rescore_writes_new_manifest_without_rerunning_agents(
    tmp_path,
    monkeypatch,
):
    calls: list[str] = []

    def execute(cell, _workspace):
        calls.append(cell.run_id)
        return _completed(cell)

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)
    runner.execute(manifest_path)
    assert len(calls) == 3

    current_rules = ScenarioRules(
        scenario_id=SCENARIO_ID,
        scenario_version="1.0",
        evaluator_version="1.1",
    )

    def fake_evaluate(workspace, rules, **_kwargs):
        run_id = Path(workspace).name
        return SimpleNamespace(
            result=_evaluated(
                SimpleNamespace(
                    run_id=run_id,
                    scenario=SimpleNamespace(
                        scenario_id=SCENARIO_ID,
                        scenario_version="1.0",
                        metadata=SimpleNamespace(evaluator_version="1.1"),
                    ),
                ),
                evaluator_version=rules.evaluator_version,
                score=0.75,
            )
        )

    monkeypatch.setattr("benchmark.runner.evaluate_workspace", fake_evaluate)
    output_path = tmp_path / "rescored.json"
    rescored = runner.rescore(
        manifest_path,
        output_path=output_path,
        rules_by_scenario={(SCENARIO_ID, "1.0"): current_rules},
    )

    assert output_path.is_file()
    assert manifest_path.read_bytes() != output_path.read_bytes()
    assert len(calls) == 3
    assert {record.evaluator_version for record in rescored.run_records} == {"1.1"}
    assert {
        record.evaluator_result.evaluator_version for record in rescored.run_records
    } == {"1.1"}
    assert all(record.score_breakdown is not None for record in rescored.run_records)


def test_evaluator_crash_is_recorded_as_error_without_marking_analysis_failed(
    tmp_path,
    monkeypatch,
):
    def execute(cell, _workspace):
        return BenchmarkCellResult(
            lifecycle=LifecycleOutcome(status=LifecycleStatus.COMPLETED),
            started_at=FIXED_TIME,
            finished_at=FIXED_TIME,
        )

    def crash(_workspace, _rules, **_kwargs):
        raise RuntimeError("evaluator crashed")

    monkeypatch.setattr("benchmark.runner.evaluate_workspace", crash)
    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path, repetitions=1)

    summary = runner.execute(manifest_path)
    record = summary.manifest.run_records[0]

    assert record.lifecycle.status is LifecycleStatus.COMPLETED
    assert record.evaluator_result.status is EvaluatorStatus.ERROR
    assert record.evaluator_result.score_breakdown is None
    assert "evaluator crashed" in (record.evaluator_result.error_message or "")
    aggregate = summary.manifest.aggregates[0]
    assert aggregate.completed_runs == 1
    assert aggregate.failed_runs == 0
    assert aggregate.evaluated_runs == 0
    assert aggregate.evaluator_error_runs == 1
    assert aggregate.mean_scores == {}


def test_offline_rescore_evaluator_crash_preserves_completed_run_and_no_zero_score(
    tmp_path,
    monkeypatch,
):
    runner = _runner(tmp_path, lambda cell, _workspace: _completed(cell))
    manifest_path = _plan(runner, tmp_path, repetitions=1)
    runner.execute(manifest_path)

    def crash(_workspace, _rules, **_kwargs):
        raise RuntimeError("rescore evaluator crashed")

    monkeypatch.setattr("benchmark.runner.evaluate_workspace", crash)
    source_before = manifest_path.read_bytes()
    output_path = tmp_path / "rescored.json"
    rescored = runner.rescore(
        manifest_path,
        output_path=output_path,
    )
    record = rescored.run_records[0]

    assert record.lifecycle.status is LifecycleStatus.COMPLETED
    assert record.evaluator_result.status is EvaluatorStatus.ERROR
    assert record.evaluator_result.score_breakdown is None
    assert "rescore evaluator crashed" in (record.evaluator_result.error_message or "")
    aggregate = rescored.aggregates[0]
    assert aggregate.evaluator_error_runs == 1
    assert aggregate.evaluated_runs == 0
    assert aggregate.mean_scores == {}
    assert manifest_path.read_bytes() == source_before
    output_before = output_path.read_bytes()

    with pytest.raises(BenchmarkError, match="refusing to overwrite"):
        runner.rescore(manifest_path, output_path=output_path)
    assert output_path.read_bytes() == output_before

    with pytest.raises(BenchmarkError, match="must differ from input"):
        runner.rescore(manifest_path, output_path=manifest_path)
    assert manifest_path.read_bytes() == source_before


def test_offline_rescore_does_not_analytically_score_non_completed_runs(
    tmp_path,
    monkeypatch,
):
    def execute(_cell, _workspace):
        return BenchmarkCellResult(
            lifecycle=LifecycleOutcome(
                status=LifecycleStatus.FAILED,
                failure_category=FailureCategory.PROVIDER,
                failure_message="fixture provider failure",
            ),
            started_at=FIXED_TIME,
            finished_at=FIXED_TIME,
        )

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path, repetitions=1)
    runner.execute(manifest_path)

    def should_not_evaluate(*_args, **_kwargs):
        raise AssertionError("non-completed run was sent to the analytical evaluator")

    monkeypatch.setattr("benchmark.runner.evaluate_workspace", should_not_evaluate)
    rescored = runner.rescore(
        manifest_path,
        output_path=tmp_path / "rescored.json",
    )
    record = rescored.run_records[0]

    assert record.lifecycle.status is LifecycleStatus.FAILED
    assert record.evaluator_result.status is EvaluatorStatus.NOT_EVALUATED
    assert record.score_breakdown is None
    aggregate = rescored.aggregates[0]
    assert aggregate.failed_runs == 1
    assert aggregate.evaluated_runs == 0
    assert aggregate.evaluator_error_runs == 0
    assert aggregate.failure_taxonomy == {
        "evaluator:not_evaluated": 1,
        "lifecycle:provider": 1,
    }


def test_canonical_rescore_isolates_crashes_and_rebuilds_aggregates(
    tmp_path,
    monkeypatch,
):
    def execute(cell, _workspace):
        if cell.architecture == "single-agent" and cell.repetition == 2:
            return BenchmarkCellResult(
                lifecycle=LifecycleOutcome(
                    status=LifecycleStatus.FAILED,
                    failure_category=FailureCategory.PROVIDER,
                    failure_message="fixture provider failure",
                ),
                started_at=FIXED_TIME,
                finished_at=FIXED_TIME,
            )
        return _completed(cell)

    runner = BenchmarkRunner(
        tmp_path / "workspaces",
        architecture_executors={
            "multi-agent": execute,
            "single-agent": execute,
        },
        source_preparer=_sources,
    )
    manifest_path = _plan(
        runner,
        tmp_path,
        repetitions=2,
        architectures=("multi-agent", "single-agent"),
    )
    runner.execute(manifest_path)
    before = load_manifest(manifest_path)
    before_aggregates = before.aggregates
    before_comparisons = before.architecture_comparisons

    def fake_evaluate(workspace, rules, **_kwargs):
        run_id = Path(workspace).name
        if run_id.endswith("-multi-agent-r1") or run_id.endswith("-single-agent-r2"):
            raise RuntimeError("fixture evaluator crash")
        return SimpleNamespace(
            result=_evaluated(
                SimpleNamespace(
                    run_id=run_id,
                    scenario=SimpleNamespace(
                        scenario_id=SCENARIO_ID,
                        scenario_version="1.0",
                        metadata=SimpleNamespace(evaluator_version="1.1"),
                    ),
                ),
                evaluator_version=rules.evaluator_version,
                score=0.25,
            )
        )

    monkeypatch.setattr("benchmark.runner.evaluate_workspace", fake_evaluate)
    rescored = runner.rescore(
        manifest_path,
        output_path=tmp_path / "rescored.json",
    )

    records = {record.run_id: record for record in rescored.run_records}
    crashed_completed = next(
        record
        for record in records.values()
        if record.run_id.endswith("-multi-agent-r1")
    )
    crashed_failed = next(
        record
        for record in records.values()
        if record.run_id.endswith("-single-agent-r2")
    )
    assert crashed_completed.lifecycle.status is LifecycleStatus.COMPLETED
    assert crashed_completed.evaluator_result.status is EvaluatorStatus.ERROR
    assert crashed_completed.score_breakdown is None
    assert crashed_failed.lifecycle.status is LifecycleStatus.FAILED
    assert crashed_failed.evaluator_result.status is EvaluatorStatus.NOT_EVALUATED
    assert crashed_failed.score_breakdown is None

    multi = next(
        item for item in rescored.aggregates if item.architecture == "multi-agent"
    )
    single = next(
        item for item in rescored.aggregates if item.architecture == "single-agent"
    )
    assert multi.evaluated_runs == 1
    assert multi.evaluator_error_runs == 1
    assert multi.mean_scores["overall_score"] == 0.25
    assert single.evaluated_runs == 1
    assert single.evaluator_error_runs == 0
    assert single.failure_taxonomy == {
        "evaluator:fail": 1,
        "evaluator:not_evaluated": 1,
        "lifecycle:provider": 1,
    }
    comparison = rescored.architecture_comparisons[0]
    overall = next(
        metric for metric in comparison.metrics if metric.metric_key == "overall_score"
    )
    assert overall.paired_sample_size == 0
    assert rescored.aggregates != before_aggregates
    assert rescored.architecture_comparisons != before_comparisons


def test_live_execution_requires_opt_in_and_credentials_without_loading_dotenv(
    tmp_path,
):
    runner = BenchmarkRunner(tmp_path / "workspaces", source_preparer=_sources)
    manifest = runner.build_manifest(
        manifest_id="live-manifest",
        scenario_ids=[SCENARIO_ID],
        architectures=("single-agent",),
        repetitions=3,
        model="configured-model",
        execution_mode=ExecutionMode.LIVE,
    )
    path = tmp_path / "live.json"
    runner.persist_plan(manifest, path)

    with pytest.raises(BenchmarkError, match="allow_paid"):
        runner.execute(path)
    with pytest.raises(BenchmarkError, match="OPENAI_API_KEY"):
        runner.execute(
            path,
            allow_paid=True,
            environment={"OPENAI_DEFAULT_MODEL": "configured-model"},
        )


def test_cost_pilot_is_persisted_and_required_before_full_resume(tmp_path):
    calls: list[str] = []

    def execute(cell, _workspace):
        calls.append(cell.run_id)
        return _completed(cell)

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"

    with pytest.raises(BenchmarkError, match="cost-estimation pilot"):
        runner.execute(manifest_path, resume=True, require_pilot=True)

    pilot_summary, pilot = runner.run_pilot(
        manifest_path,
        pilot_path=pilot_path,
    )
    assert len(pilot_summary.executed_run_ids) == 1
    assert pilot_path.is_file()
    assert pilot.planned_cells == 3

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
    assert full.manifest.status.value == "complete"
    assert full.manifest.unknown_cost_acknowledged is True
    assert full.manifest.unknown_cost_pilot_id == pilot.pilot_id
    pilot_record = next(
        record
        for record in pilot_summary.manifest.run_records
        if record.run_id == pilot.run_id
    )
    assert full.manifest.unknown_cost_pilot_record_digest == (
        canonical_run_record_digest(pilot_record)
    )
    assert len(full.skipped_run_ids) == 1
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        ("cost", lambda payload: payload.update(observed_cost_usd=0.0)),
        ("usage", lambda payload: payload.update(observed_requests=99)),
        ("latency", lambda payload: payload.update(observed_elapsed_seconds=99.0)),
        ("run_id", lambda payload: payload.update(run_id="tampered-run")),
        ("model", lambda payload: payload.update(model="tampered-model")),
        ("matrix", lambda payload: payload.update(planned_cells=999)),
        (
            "record_digest",
            lambda payload: payload.update(record_digest="0" * 64),
        ),
    ],
)
def test_full_run_refuses_tampered_cost_pilot_binding(tmp_path, field, mutate):
    runner = _runner(tmp_path, lambda cell, _workspace: _completed(cell))
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)
    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    mutate(payload)
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="pilot"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
        )


def test_unknown_cost_acknowledgement_is_bound_to_the_pilot_record(tmp_path):
    runner = _runner(tmp_path, lambda cell, _workspace: _completed(cell))
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)
    runner.execute(
        manifest_path,
        resume=True,
        require_pilot=True,
        unknown_cost=True,
        pilot_path=pilot_path,
        max_cells=1,
    )

    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["pilot_id"] = "tampered-pilot"
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="acknowledgement"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
        )


def _completed_with_known_cost(cell, _workspace):
    usage = ModelUsage(
        requests=1,
        input_tokens=100,
        cached_tokens=0,
        output_tokens=20,
        reasoning_tokens=0,
        total_tokens=120,
    )
    cost_breakdown = CostBreakdown(
        pricing_model="fixture-pricing-v1",
        input_per_1m=1.0,
        cached_input_per_1m=0.1,
        output_per_1m=2.0,
        input_tokens=usage.input_tokens,
        cached_tokens=usage.cached_tokens,
        uncached_input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        uncached_input_cost_usd=0.0001,
        cached_input_cost_usd=0.0,
        output_cost_usd=0.00004,
        estimated_cost_usd=0.00014,
    )
    state = SimpleNamespace(
        run_id=cell.run_id,
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
        elapsed_seconds=2.5,
        usage=usage,
        cost_breakdown=cost_breakdown,
    )
    return BenchmarkCellResult(
        lifecycle=LifecycleOutcome(status=LifecycleStatus.COMPLETED),
        evaluator_result=_evaluated(cell),
        state=state,
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
    )


def test_known_cost_pilot_can_pass_with_recorded_pricing(tmp_path):
    runner = _runner(tmp_path, _completed_with_known_cost)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)

    summary = runner.execute(
        manifest_path,
        resume=True,
        require_pilot=True,
        pilot_path=pilot_path,
    )

    assert summary.manifest.status is ManifestStatus.COMPLETE
    assert summary.manifest.unknown_cost_acknowledged is False


def test_known_cost_pilot_rejects_pricing_tamper(tmp_path):
    runner = _runner(tmp_path, _completed_with_known_cost)
    manifest_path = _plan(runner, tmp_path)
    pilot_path = tmp_path / "pilot.json"
    runner.run_pilot(manifest_path, pilot_path=pilot_path)
    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["observed_cost"]["pricing_model"] = "tampered-pricing"
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="cost"):
        runner.execute(
            manifest_path,
            resume=True,
            require_pilot=True,
            pilot_path=pilot_path,
        )


def test_benchmark_record_exposes_reconciled_attempt_history(tmp_path):
    def execute(cell, workspace):
        ledger = AnalysisLedger(
            workspace,
            run_id=cell.run_id,
            objective=cell.scenario.metadata.user_question,
        )
        ledger.begin_attempt()
        ledger.record_model_usage(
            ModelUsage(
                requests=1,
                input_tokens=10,
                output_tokens=4,
                total_tokens=14,
            )
        )
        ledger.record_elapsed(1.0)
        ledger.append_tool_event(
            ToolEvent(
                id=f"tool-{cell.run_id}",
                tool_name="run_sql",
                status=ToolEventStatus.SUCCEEDED,
                started_at=FIXED_TIME,
                completed_at=FIXED_TIME,
            )
        )
        ledger.finish_attempt(AttemptStatus.COMPLETED)
        return BenchmarkCellResult(
            lifecycle=LifecycleOutcome(status=LifecycleStatus.COMPLETED),
            workspace=workspace,
            state=ledger.state,
            evaluator_result=_evaluated(cell),
            started_at=FIXED_TIME,
            finished_at=FIXED_TIME,
        )

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path, repetitions=1)
    summary = runner.execute(manifest_path)
    record = summary.manifest.run_records[0]

    assert len(record.attempt_history) == 1
    assert record.attempt_history[0].attempt_id.startswith(f"{record.run_id}-attempt-")
    assert record.attempt_history[0].status is AttemptStatus.COMPLETED
    assert record.attempt_history[0].usage_delta.requests == record.usage.requests
    assert record.attempt_history[0].elapsed_seconds == record.latency.elapsed_seconds


def test_benchmark_record_marks_incomplete_usage_and_refuses_known_cost(tmp_path):
    """R14: a lost model call cannot be published as a complete $0.00 cell."""

    def execute(cell, workspace):
        ledger = AnalysisLedger(
            workspace,
            run_id=cell.run_id,
            objective=cell.scenario.metadata.user_question,
        )
        ledger.begin_attempt()
        ledger.record_usage_delta(
            ModelUsage(
                requests=1,
                input_tokens=1_000,
                output_tokens=400,
                total_tokens=1_400,
            )
        )
        ledger.mark_usage_incomplete(
            "A failed model response was not reconciled into the totals."
        )
        ledger.record_elapsed(1.0)
        ledger.record_cost_estimate(
            pricing=MODEL_PRICING["gpt-5.6-luna"],
            pricing_model="gpt-5.6-luna",
        )
        ledger.finish_attempt(AttemptStatus.FAILED, error="invalid final output")
        return BenchmarkCellResult(
            lifecycle=LifecycleOutcome(
                status=LifecycleStatus.FAILED,
                failure_category=FailureCategory.SCHEMA,
                failure_message="invalid final output",
            ),
            workspace=workspace,
            state=ledger.state,
            started_at=FIXED_TIME,
            finished_at=FIXED_TIME,
        )

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path, repetitions=1)
    summary = runner.execute(manifest_path)
    record = summary.manifest.run_records[0]

    # The usage that was recorded is retained, but explicitly as a lower bound.
    assert record.usage.requests == 1
    assert record.usage.total_tokens == 1_400
    assert record.usage.complete is False
    # Known pricing over incomplete usage must not become a confident total.
    assert record.cost.availability is CostAvailability.UNAVAILABLE
    assert record.cost.estimated_cost_usd is None
    assert record.cost.note is not None
    assert record.attempt_history[0].usage_complete is False


def test_benchmark_workspace_persists_manifest_bound_source_identity(tmp_path):
    runner = _runner(tmp_path, lambda cell, _workspace: _completed(cell))
    manifest_path = _plan(runner, tmp_path)

    summary = runner.execute(manifest_path, max_cells=1)
    record = summary.manifest.run_records[0]
    reference = summary.manifest.scenario_references[0]
    identity = load_workspace_identity(record.workspace_path)

    assert reference.source_files
    assert identity.source_files == reference.source_files
    assert identity.benchmark_manifest_id == summary.manifest.manifest_id
    assert identity.run_id == record.run_id
    assert identity.scenario_id == record.scenario_id
    assert identity.seed == record.seed
    assert workspace_identity_path(record.workspace_path).is_file()


def test_offline_rescore_refuses_tampered_workspace_source(tmp_path):
    runner = _runner(tmp_path, lambda cell, _workspace: _completed(cell))
    manifest_path = _plan(runner, tmp_path)
    runner.execute(manifest_path, max_cells=1)
    record = load_manifest(manifest_path).run_records[0]
    source_path = Path(record.workspace_path) / "inputs" / "fixture.parquet"
    source_path.chmod(0o644)
    source_path.write_bytes(b"tampered fixture")

    with pytest.raises(BenchmarkError, match="offline rescore refused"):
        runner.rescore(manifest_path, output_path=tmp_path / "rescored.json")


def test_offline_rescore_refuses_tampered_workspace_metadata(tmp_path):
    runner = _runner(tmp_path, lambda cell, _workspace: _completed(cell))
    manifest_path = _plan(runner, tmp_path)
    runner.execute(manifest_path, max_cells=1)
    record = load_manifest(manifest_path).run_records[0]
    identity_path = workspace_identity_path(record.workspace_path)
    payload = json.loads(identity_path.read_text(encoding="utf-8"))
    payload["scenario_id"] = "tampered-scenario"
    identity_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="offline rescore refused"):
        runner.rescore(manifest_path, output_path=tmp_path / "rescored.json")


@pytest.mark.parametrize("identity_state", ["missing", "corrupt"])
def test_offline_rescore_refuses_unbound_noncompleted_record(
    tmp_path,
    identity_state,
):
    def execute(cell, _workspace):
        return BenchmarkCellResult(
            lifecycle=LifecycleOutcome(
                status=LifecycleStatus.FAILED,
                failure_category=FailureCategory.AGENT,
                failure_message="fixture failed before completion",
            ),
            evaluator_result=_evaluated(cell),
            started_at=FIXED_TIME,
            finished_at=FIXED_TIME,
        )

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path, repetitions=1)
    runner.execute(manifest_path)
    record = load_manifest(manifest_path).run_records[0]
    identity_path = workspace_identity_path(record.workspace_path)
    if identity_state == "missing":
        identity_path.unlink()
    else:
        identity_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(BenchmarkError, match="offline rescore refused"):
        runner.rescore(manifest_path, output_path=tmp_path / "rescored.json")


def test_offline_rescore_refuses_record_seed_tampered_against_manifest_identity(
    tmp_path,
):
    runner = _runner(tmp_path, lambda cell, _workspace: _completed(cell))
    manifest_path = _plan(runner, tmp_path, repetitions=1)
    runner.execute(manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["run_records"][0]["seed"] += 1
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        ValidationError,
        match="run seed differs from scenario reference",
    ):
        runner.rescore(manifest_path, output_path=tmp_path / "rescored.json")
