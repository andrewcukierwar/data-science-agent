"""Known-record tests for benchmark aggregation and uncertainty reporting."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from benchmark.aggregation import aggregate_manifest, build_benchmark_report
from evaluation.contracts import (
    BenchmarkManifest,
    BenchmarkRunRecord,
    BudgetConfiguration,
    CostAvailability,
    CostSummary,
    EvaluationCheck,
    EvaluationCheckStatus,
    EvaluatorResult,
    EvaluatorStatus,
    ExecutionMode,
    LatencySummary,
    LifecycleOutcome,
    LifecycleStatus,
    RunConfiguration,
    ScenarioReference,
    ScoreBreakdown,
    UsageSummary,
)
from evaluation.engine import dump_stable_json

START = datetime(2026, 1, 1, tzinfo=UTC)
SCENARIO_ID = "fixture-scenario"
RUN_CONFIGURATION = RunConfiguration(
    execution_mode=ExecutionMode.DETERMINISTIC,
    tool_contract_version="1.0",
    parameters={"fixture": "known-records"},
)
BUDGETS = BudgetConfiguration(
    resource_limits={"sql": 3},
    turn_limits={"lead": 4},
)


def _evaluation(
    run_id: str,
    *,
    score: float | None,
    status: EvaluatorStatus = EvaluatorStatus.PASS,
) -> EvaluatorResult:
    has_failure = status is not EvaluatorStatus.PASS
    return EvaluatorResult(
        result_id=f"evaluation-{run_id}",
        run_id=run_id,
        scenario_id=SCENARIO_ID,
        scenario_version="1.0",
        evaluator_version="1.0",
        status=status,
        checks=(
            EvaluationCheck(
                check_id="fixture:score",
                status=(
                    EvaluationCheckStatus.FAIL
                    if has_failure
                    else EvaluationCheckStatus.PASS
                ),
                message="known fixture result",
            ),
        ),
        score_breakdown=(
            ScoreBreakdown(
                dimensions={"quality": score},
                overall_score=score,
            )
            if score is not None
            else None
        ),
        failure_reasons=("provider failure",) if has_failure else (),
        error_message="provider failure"
        if status is EvaluatorStatus.NOT_EVALUATED
        else None,
        evaluated_at=START,
    )


def _record(
    architecture: str,
    repetition: int,
    *,
    score: float | None,
    cost: float | None,
    elapsed: float,
    lifecycle: LifecycleStatus = LifecycleStatus.COMPLETED,
) -> BenchmarkRunRecord:
    run_id = f"{architecture}-{repetition}"
    evaluation = _evaluation(
        run_id,
        score=score,
        status=(
            EvaluatorStatus.PASS if score is not None else EvaluatorStatus.NOT_EVALUATED
        ),
    )
    outcome = LifecycleOutcome(
        status=lifecycle,
        failure_category=None if lifecycle is LifecycleStatus.COMPLETED else "provider",
        failure_message=None
        if lifecycle is LifecycleStatus.COMPLETED
        else "provider rate limit",
    )
    return BenchmarkRunRecord(
        run_id=run_id,
        repetition=repetition,
        scenario_id=SCENARIO_ID,
        scenario_version="1.0",
        evaluator_version="1.0",
        architecture=architecture,
        model="fixture-model",
        model_provider="fixture-provider",
        run_configuration=RUN_CONFIGURATION,
        budgets=BUDGETS,
        seed=42,
        workspace_path=f"workspaces/{run_id}",
        lifecycle=outcome,
        evaluator_result=evaluation,
        score_breakdown=evaluation.score_breakdown,
        usage=UsageSummary(
            requests=1,
            input_tokens=10,
            cached_tokens=0,
            output_tokens=5,
            reasoning_tokens=0,
            total_tokens=15,
        ),
        cost=(
            CostSummary(
                availability=CostAvailability.KNOWN,
                estimated_cost_usd=cost,
                pricing_model="fixture-pricing",
            )
            if cost is not None
            else CostSummary(
                availability=CostAvailability.UNAVAILABLE,
                note="provider did not return billing data",
            )
        ),
        latency=LatencySummary(
            elapsed_seconds=elapsed,
            started_at=START,
            finished_at=START + timedelta(seconds=elapsed),
        ),
    )


def _manifest(records: tuple[BenchmarkRunRecord, ...]) -> BenchmarkManifest:
    return BenchmarkManifest(
        manifest_id="known-aggregation",
        manifest_version="1.0",
        status="declared",
        created_at=START,
        scenario_references=(
            ScenarioReference(
                scenario_id=SCENARIO_ID,
                scenario_version="1.0",
                evaluator_version="1.0",
                seed=42,
            ),
        ),
        architectures=("multi-agent", "single-agent"),
        repetitions=3,
        model="fixture-model",
        model_provider="fixture-provider",
        run_configuration=RUN_CONFIGURATION,
        budgets=BUDGETS,
        aggregation_version="1.0",
        run_records=records,
    )


def test_aggregation_retains_denominators_distributions_and_failure_taxonomy():
    records = (
        _record("multi-agent", 1, score=0.5, cost=1.0, elapsed=1.0),
        _record("multi-agent", 2, score=0.7, cost=2.0, elapsed=2.0),
        _record(
            "multi-agent",
            3,
            score=None,
            cost=None,
            elapsed=3.0,
            lifecycle=LifecycleStatus.FAILED,
        ),
        _record("single-agent", 1, score=0.8, cost=1.5, elapsed=1.5),
        _record("single-agent", 2, score=0.9, cost=2.5, elapsed=2.5),
    )
    manifest = aggregate_manifest(_manifest(records))

    multi, single = manifest.aggregates
    assert multi.denominator is not None
    assert multi.denominator.model_dump() == {
        "expected_repetitions": 3,
        "observed_repetitions": 3,
        "missing_repetitions": 0,
        "completed_runs": 2,
        "failed_runs": 1,
        "evaluated_runs": 2,
        "completion_rate": pytest.approx(2 / 3),
        "evaluation_rate": pytest.approx(2 / 3),
    }
    assert multi.failure_taxonomy == {
        "evaluator:not_evaluated": 1,
        "lifecycle:provider": 1,
    }
    assert multi.score_distributions["overall_score"].sample_size == 2
    assert multi.score_distributions["overall_score"].mean == 0.6
    assert multi.score_distributions["overall_score"].uncertainty_status == (
        "estimable"
    )
    assert multi.cost_distribution is not None
    assert multi.cost_distribution.sample_size == 2
    assert multi.latency_distribution is not None
    assert multi.latency_distribution.sample_size == 3

    assert single.denominator is not None
    assert single.denominator.missing_repetitions == 1
    assert single.denominator.completion_rate == pytest.approx(2 / 3)
    assert single.failure_taxonomy == {"missing": 1}
    assert len(manifest.run_records) == len(records)
    assert manifest.aggregation_version == "1.1"


def test_architecture_comparison_separates_descriptive_from_inferential_results():
    records = tuple(
        record
        for repetition, (left, right) in enumerate(
            ((0.5, 0.8), (0.7, 0.9)),
            start=1,
        )
        for record in (
            _record("multi-agent", repetition, score=left, cost=1, elapsed=1),
            _record("single-agent", repetition, score=right, cost=1, elapsed=1),
        )
    )
    manifest = aggregate_manifest(_manifest(records))
    comparison = manifest.architecture_comparisons[0]
    metric = next(
        item for item in comparison.metrics if item.metric_key == "overall_score"
    )

    assert comparison.left_architecture == "multi-agent"
    assert comparison.right_architecture == "single-agent"
    assert metric.difference_definition == "single-agent minus multi-agent"
    assert metric.mean_left == 0.6
    assert metric.mean_right == 0.85
    assert metric.mean_difference == 0.25
    assert metric.paired_sample_size == 2
    assert metric.test_method == "paired_t"
    assert metric.conclusion == "not_supported"
    assert metric.p_value is not None and metric.p_value > 0.05


def test_architecture_comparison_marks_consistent_paired_difference_supported():
    records = tuple(
        record
        for repetition, left in enumerate((0.1, 0.2, 0.3), start=1)
        for record in (
            _record("multi-agent", repetition, score=left, cost=1, elapsed=1),
            _record("single-agent", repetition, score=left + 0.3, cost=1, elapsed=1),
        )
    )
    comparison = aggregate_manifest(_manifest(records)).architecture_comparisons[0]
    metric = next(
        item for item in comparison.metrics if item.metric_key == "overall_score"
    )

    assert metric.paired_sample_size == 3
    assert metric.conclusion == "supported_difference"
    assert metric.p_value == 0.0
    assert metric.paired_difference_distribution.uncertainty is not None
    assert metric.paired_difference_distribution.uncertainty.lower > 0


def test_report_is_stable_and_flat_rows_are_readme_ready():
    records = (
        _record("multi-agent", 1, score=0.5, cost=1.0, elapsed=1.0),
        _record("single-agent", 1, score=0.8, cost=2.0, elapsed=2.0),
    )
    manifest = _manifest(records)
    first = build_benchmark_report(manifest)
    second = build_benchmark_report(manifest)

    assert dump_stable_json(first.model_dump(mode="json")) == dump_stable_json(
        second.model_dump(mode="json")
    )
    assert first.expected_matrix_cells == 6
    assert first.observed_raw_records == 2
    assert first.missing_matrix_cells == 4
    assert len(first.table_rows) == 2
    assert first.table_rows[0].completion_rate == pytest.approx(1 / 3)
    assert first.table_rows[0].overall_score_mean == 0.5
    assert first.table_rows[0].overall_score_ci_lower is None
    assert first.table_rows[0].failure_taxonomy == {"missing": 2}
    assert first.architecture_comparisons[0].metrics[0].conclusion == (
        "insufficient_sample"
    )


def test_report_cli_emits_machine_readable_output_without_overwriting(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    manifest_path.write_text(
        _manifest(
            (_record("multi-agent", 1, score=0.5, cost=1.0, elapsed=1.0),)
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "run_benchmark.py"),
        "report",
        str(manifest_path),
        "--output",
        str(report_path),
    ]
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 2
    assert "refusing to overwrite" in second.stderr
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["table_rows"][0]["scenario_id"] == SCENARIO_ID
    assert payload["missing_matrix_cells"] == 5
