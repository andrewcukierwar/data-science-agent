"""R16 regressions for retained and resumable interrupted benchmark cells.

The retained Task 10 `v2` attempt interrupted a cell after real work had been
done. Its workspace kept 34 requests and an interrupted attempt, but the
manifest dropped the cell entirely, so the aborted report observed zero records
and 60 missing cells. An interrupted cell is now materialized as a cancelled
operational record before the manifest is marked aborted, keeps the workspace's
partial accounting, counts in the denominators, and can be retried by an
explicit resume without rewriting prior attempt evidence.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from benchmark import BenchmarkCellResult, BenchmarkRunner
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
from evaluation.engine import load_manifest
from orchestration.generalist_runner import GeneralistRunner
from orchestration.ledger import AnalysisLedger
from schemas.audit import AuditResult, AuditStatus
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.run_state import AttemptStatus, RunStatus
from schemas.validation import ValidationResult, ValidationStatus

FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)
SCENARIO_ID = "meaningful-ab-treatment-effect"


# --- benchmark scaffolding --------------------------------------------------


def _sources(_registration, destination: Path) -> tuple[Path, Path]:
    inputs = destination / "inputs"
    docs = destination / "docs"
    inputs.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"observed_value": [1]}).to_parquet(
        inputs / "customers.parquet",
        index=False,
    )
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
        score_breakdown=ScoreBreakdown(
            dimensions={"quality": 1.0},
            overall_score=1.0,
        ),
        evaluated_at=FIXED_TIME,
    )


def _completed(cell):
    return BenchmarkCellResult(
        lifecycle=LifecycleOutcome(status=LifecycleStatus.COMPLETED),
        evaluator_result=_evaluated(cell),
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
    )


def _runner(tmp_path: Path, executor) -> BenchmarkRunner:
    return BenchmarkRunner(
        tmp_path / "workspaces",
        architecture_executors={"single-agent": executor},
        source_preparer=_sources,
    )


def _plan(runner: BenchmarkRunner, tmp_path: Path, *, repetitions: int = 1) -> Path:
    manifest = runner.build_manifest(
        manifest_id="interruption-manifest",
        scenario_ids=[SCENARIO_ID],
        architectures=("single-agent",),
        repetitions=repetitions,
        model="gpt-5.6-luna",
        model_provider="openai",
        execution_mode=ExecutionMode.DETERMINISTIC,
        repetition_justification="R16 interruption fixture",
    )
    path = tmp_path / "manifest.json"
    runner.persist_plan(manifest, path)
    return path


# --- real single-agent execution driven to an interruption ------------------


def _generalist_result() -> GeneralistResult:
    return GeneralistResult(
        audit=AuditResult(status=AuditStatus.COMPLETE, audited_at=FIXED_TIME),
        candidate=LeadResult(
            objective="Explain the observed change.",
            answer="The observed change is described by the available evidence.",
        ),
        validation=ValidationResult(status=ValidationStatus.PASS),
    )


def _response_usage() -> SimpleNamespace:
    return SimpleNamespace(
        requests=1,
        input_tokens=1_200,
        output_tokens=400,
        total_tokens=1_600,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


def _sdk_run(*, interrupt_after_response: bool):
    """Stub the provider call, optionally interrupting after it returns."""

    usage = _response_usage()

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        hooks = kwargs.get("hooks")
        wrapper = SimpleNamespace(context=context, usage=usage)
        if hooks is not None:
            await hooks.on_llm_end(wrapper, agent, SimpleNamespace(usage=usage))
        if interrupt_after_response:
            raise KeyboardInterrupt("operator stopped the benchmark")
        return SimpleNamespace(
            final_output=_generalist_result(),
            context_wrapper=wrapper,
        )

    return fake_run


def _real_single_agent_executor(tmp_path: Path):
    """Drive the production GeneralistRunner for each benchmark cell."""

    def execute(cell, workspace):
        runner = GeneralistRunner(
            workspace_base_dir=cell.workspace_path.parent,
            model="gpt-5.6-luna",
            model_provider="openai",
        )
        return runner.run_sync(
            cell.run_id,
            cell.scenario.metadata.user_question,
            workspace=workspace,
        )

    return execute


# --- interruption after partial persistence ---------------------------------


def test_interrupted_cell_retains_workspace_usage_cost_and_attempt_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(interrupt_after_response=True),
    )
    runner = _runner(tmp_path, _real_single_agent_executor(tmp_path))
    manifest_path = _plan(runner, tmp_path)

    summary = runner.execute(manifest_path)

    assert summary.interrupted is True
    assert summary.manifest.status is ManifestStatus.ABORTED
    assert len(summary.manifest.run_records) == 1
    record = summary.manifest.run_records[0]

    assert record.lifecycle.status is LifecycleStatus.CANCELLED
    assert record.lifecycle.failure_category is FailureCategory.INTERRUPTED
    assert "interrupted" in record.lifecycle.failure_message
    assert "KeyboardInterrupt" in record.lifecycle.failure_message
    # The workspace and its evidence are retained and referenced.
    assert Path(record.workspace_path).is_dir()
    assert record.attempt_id is not None
    assert len(record.attempt_history) == 1
    attempt = record.attempt_history[0]
    assert attempt.status is AttemptStatus.INTERRUPTED
    assert attempt.attempt_id == record.attempt_id
    # Partial usage, cost availability, and latency survive the interruption.
    assert record.usage.requests == 1
    assert record.usage.total_tokens == 1_600
    assert attempt.usage_delta.requests == 1
    assert record.cost.availability in {
        CostAvailability.KNOWN,
        CostAvailability.UNAVAILABLE,
    }
    assert record.latency.elapsed_seconds > 0
    assert record.evaluator_result.status is EvaluatorStatus.NOT_EVALUATED


def test_interrupted_workspace_status_is_reconciled_with_its_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The workspace must not be left advertising ``running`` forever."""

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(interrupt_after_response=True),
    )
    runner = _runner(tmp_path, _real_single_agent_executor(tmp_path))
    manifest_path = _plan(runner, tmp_path)
    summary = runner.execute(manifest_path)

    ledger = AnalysisLedger(
        Path(summary.manifest.run_records[0].workspace_path) / "state"
    )

    assert ledger.state.status is RunStatus.CANCELLED
    assert ledger.state.error is not None
    assert ledger.attempt_history[-1].status is AttemptStatus.INTERRUPTED


# --- interruption before agent execution ------------------------------------


def test_interruption_before_agent_execution_still_records_the_cell(
    tmp_path: Path,
) -> None:
    def execute(cell, workspace):
        raise KeyboardInterrupt("stopped before the agent ran")

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)
    summary = runner.execute(manifest_path)

    assert summary.interrupted is True
    assert len(summary.manifest.run_records) == 1
    record = summary.manifest.run_records[0]

    assert record.lifecycle.status is LifecycleStatus.CANCELLED
    assert record.lifecycle.failure_category is FailureCategory.INTERRUPTED
    # No model call happened, so there is nothing to overstate.
    assert record.usage.requests == 0
    assert record.usage.total_tokens == 0
    assert record.attempt_history == ()
    assert record.cost.availability is CostAvailability.UNAVAILABLE
    assert record.cost.estimated_cost_usd is None


# --- ordering: record persisted before the manifest is aborted --------------


def test_cancelled_record_is_persisted_before_the_manifest_is_aborted(
    tmp_path: Path,
) -> None:
    persisted_states: list[tuple[ManifestStatus, int]] = []

    def execute(cell, workspace):
        raise KeyboardInterrupt("stopped")

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)
    original = BenchmarkRunner._persist_manifest.__func__

    def spy(cls, path, manifest, **kwargs):  # noqa: ANN001
        persisted_states.append((manifest.status, len(manifest.run_records)))
        return original(cls, path, manifest, **kwargs)

    BenchmarkRunner._persist_manifest = classmethod(spy)
    try:
        runner.execute(manifest_path)
    finally:
        BenchmarkRunner._persist_manifest = classmethod(original)

    # The cancelled record reaches disk while the manifest is still running,
    # so an abort cannot race the evidence it is supposed to preserve.
    assert (ManifestStatus.RUNNING, 1) in persisted_states
    aborted_index = next(
        index
        for index, (status, _) in enumerate(persisted_states)
        if status is ManifestStatus.ABORTED
    )
    running_with_record = next(
        index
        for index, entry in enumerate(persisted_states)
        if entry == (ManifestStatus.RUNNING, 1)
    )
    assert running_with_record < aborted_index
    assert load_manifest(manifest_path).run_records


# --- denominators -----------------------------------------------------------


def test_interrupted_cell_counts_as_an_observed_operational_failure(
    tmp_path: Path,
) -> None:
    def execute(cell, workspace):
        raise KeyboardInterrupt("stopped")

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path, repetitions=3)
    summary = runner.execute(manifest_path)
    aggregate = summary.manifest.aggregates[0]
    denominator = aggregate.denominator

    assert denominator is not None
    assert denominator.expected_repetitions == 3
    # One cell was observed and failed operationally; only two are missing.
    assert denominator.observed_repetitions == 1
    assert denominator.missing_repetitions == 2
    assert denominator.completed_runs == 0
    assert denominator.failed_runs == 1
    assert aggregate.failure_taxonomy["lifecycle:interrupted"] == 1
    assert aggregate.failure_taxonomy.get("missing") == 2


# --- resume -----------------------------------------------------------------


def test_resume_retries_the_interrupted_cell_and_appends_a_new_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(interrupt_after_response=True),
    )
    runner = _runner(tmp_path, _real_single_agent_executor(tmp_path))
    manifest_path = _plan(runner, tmp_path)
    first = runner.execute(manifest_path)
    interrupted_record = first.manifest.run_records[0]
    prior_attempt = interrupted_record.attempt_history[0].model_copy(deep=True)

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(interrupt_after_response=False),
    )
    second = runner.execute(manifest_path, resume=True)

    assert second.interrupted is False
    assert second.manifest.status is ManifestStatus.COMPLETE
    assert len(second.manifest.run_records) == 1
    record = second.manifest.run_records[0]

    # The immutable cell identity is unchanged; the outcome is now real.
    assert record.run_id == interrupted_record.run_id
    assert record.workspace_path == interrupted_record.workspace_path
    assert record.lifecycle.status is LifecycleStatus.COMPLETED
    # The interrupted attempt is preserved verbatim alongside the new one.
    assert len(record.attempt_history) == 2
    assert record.attempt_history[0] == prior_attempt
    assert record.attempt_history[0].status is AttemptStatus.INTERRUPTED
    assert record.attempt_history[1].status is AttemptStatus.COMPLETED
    assert record.attempt_id == record.attempt_history[1].attempt_id
    # Totals are the sum of both attempts, counted exactly once.
    assert record.usage.requests == 2
    assert record.usage.requests == sum(
        item.usage_delta.requests for item in record.attempt_history
    )


def test_resume_does_not_re_execute_completed_or_failed_cells(
    tmp_path: Path,
) -> None:
    """Only interrupted cells are retried; real observations are preserved."""

    calls: list[str] = []
    state = {"interrupt": True}

    def execute(cell, workspace):
        calls.append(cell.run_id)
        if state["interrupt"]:
            state["interrupt"] = False
            raise KeyboardInterrupt("stopped")
        if len(calls) == 2:
            raise RuntimeError("deterministic cell failure")
        return _completed(cell)

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path, repetitions=3)
    runner.execute(manifest_path)
    second = runner.execute(manifest_path, resume=True)
    statuses = {
        record.run_id: record.lifecycle.status for record in second.manifest.run_records
    }
    third = runner.execute(manifest_path, resume=True)

    assert len(second.manifest.run_records) == 3
    assert LifecycleStatus.FAILED in statuses.values()
    # The third resume has nothing left to retry: no cell is interrupted.
    assert third.executed_run_ids == ()
    assert len(third.skipped_run_ids) == 3
    assert {
        record.run_id: record.lifecycle.status for record in third.manifest.run_records
    } == statuses


def test_interrupted_cell_is_not_retried_without_an_explicit_resume(
    tmp_path: Path,
) -> None:
    def execute(cell, workspace):
        raise KeyboardInterrupt("stopped")

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path)
    runner.execute(manifest_path)

    # Without resume the runner refuses to touch a manifest that has records.
    with pytest.raises(Exception, match="resume"):
        runner.execute(manifest_path)


def test_interrupted_pilot_cell_cannot_be_published_as_a_cost_pilot(
    tmp_path: Path,
) -> None:
    """An interrupted cell measured nothing and must not be scaled."""

    def execute(cell, workspace):
        raise KeyboardInterrupt("stopped")

    runner = _runner(tmp_path, execute)
    manifest_path = _plan(runner, tmp_path, repetitions=3)
    pilot_path = tmp_path / "pilot.json"

    with pytest.raises(Exception, match="interrupted"):
        runner.run_pilot(manifest_path, pilot_path=pilot_path)

    assert not pilot_path.exists()
    # The interrupted cell is still retained as an observed operational record.
    manifest = load_manifest(manifest_path)
    assert len(manifest.run_records) == 1
    assert manifest.run_records[0].lifecycle.status is LifecycleStatus.CANCELLED
