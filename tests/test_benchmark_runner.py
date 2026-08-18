"""Deterministic tests for the resumable benchmark matrix runner."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark import BenchmarkCellResult, BenchmarkError, BenchmarkRunner
from evaluation.contracts import (
    EvaluationCheck,
    EvaluationCheckStatus,
    EvaluatorResult,
    EvaluatorStatus,
    ExecutionMode,
    LifecycleOutcome,
    LifecycleStatus,
    ScoreBreakdown,
)
from evaluation.engine import ScenarioRules, load_manifest
from evaluation.workspace_identity import (
    load_workspace_identity,
    workspace_identity_path,
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


def _plan(runner, tmp_path, *, repetitions=3):
    manifest = runner.build_manifest(
        manifest_id="fixture-manifest",
        scenario_ids=[SCENARIO_ID],
        architectures=("single-agent",),
        repetitions=repetitions,
        model="fixture-model",
        model_provider="fixture-provider",
        execution_mode=ExecutionMode.DETERMINISTIC,
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
            execution_mode=ExecutionMode.DETERMINISTIC,
        )


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

    def fake_evaluate(workspace, rules):
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


def test_live_execution_requires_opt_in_and_credentials_without_loading_dotenv(
    tmp_path,
):
    runner = BenchmarkRunner(tmp_path / "workspaces", source_preparer=_sources)
    manifest = runner.build_manifest(
        manifest_id="live-manifest",
        scenario_ids=[SCENARIO_ID],
        architectures=("single-agent",),
        repetitions=3,
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

    full = runner.execute(
        manifest_path,
        resume=True,
        require_pilot=True,
        pilot_path=pilot_path,
    )
    assert full.manifest.status.value == "complete"
    assert len(full.skipped_run_ids) == 1
    assert len(calls) == 3


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
