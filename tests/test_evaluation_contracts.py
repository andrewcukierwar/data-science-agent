"""Deterministic validation tests for Phase 2 evaluation contracts."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.contracts import (
    BenchmarkManifest,
    BenchmarkRunRecord,
    BudgetConfiguration,
    CostAvailability,
    CostSummary,
    EvaluationCheck,
    EvaluationCheckStatus,
    EvaluatorCompatibility,
    EvaluatorResult,
    EvaluatorStatus,
    ExecutionMode,
    LatencySummary,
    LifecycleOutcome,
    LifecycleStatus,
    RunConfiguration,
    ScenarioEvaluationSpec,
    ScenarioMetadata,
    ScenarioReference,
    ScoreBreakdown,
    UsageSummary,
    WorkspaceVersionCompatibilityError,
    check_workspace_version_compatibility,
)
from orchestration.ledger import AnalysisLedger
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from tools.workspace import WorkspaceManager

_START = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _score() -> ScoreBreakdown:
    return ScoreBreakdown(
        dimensions={"numerical_correctness": 1.0, "evidence_grounding": 0.9},
        overall_score=0.95,
    )


def _evaluation(
    *,
    run_id: str = "run-1",
    status: EvaluatorStatus = EvaluatorStatus.PASS,
) -> EvaluatorResult:
    return EvaluatorResult(
        result_id=f"evaluation-{run_id}",
        run_id=run_id,
        scenario_id="scenario-a",
        scenario_version="1.0",
        evaluator_version="1.0",
        status=status,
        checks=(
            EvaluationCheck(
                check_id="lifecycle",
                status=(
                    EvaluationCheckStatus.PASS
                    if status is EvaluatorStatus.PASS
                    else EvaluationCheckStatus.FAIL
                ),
                message="Deterministic check completed.",
            ),
        ),
        score_breakdown=_score() if status is EvaluatorStatus.PASS else None,
        failure_reasons=("evaluation failed",)
        if status is EvaluatorStatus.FAIL
        else (),
        error_message=("evaluation unavailable",)
        if status in {EvaluatorStatus.ERROR, EvaluatorStatus.NOT_EVALUATED}
        else None,
        evaluated_at=_START,
    )


def _record(
    *,
    run_id: str = "run-1",
    lifecycle: LifecycleOutcome | None = None,
    evaluation: EvaluatorResult | None = None,
) -> BenchmarkRunRecord:
    evaluation = evaluation or _evaluation(run_id=run_id)
    return BenchmarkRunRecord(
        run_id=run_id,
        repetition=1,
        scenario_id="scenario-a",
        scenario_version="1.0",
        evaluator_version="1.0",
        architecture="multi-agent",
        model="test-model",
        model_provider="test-provider",
        run_configuration=RunConfiguration(
            execution_mode=ExecutionMode.DETERMINISTIC,
            tool_contract_version="1.0",
        ),
        budgets=BudgetConfiguration(
            resource_limits={"sql": 3, "python": 2},
            turn_limits={"lead": 4},
        ),
        seed=42,
        workspace_path=".runs/benchmark/run-1",
        lifecycle=lifecycle or LifecycleOutcome(status=LifecycleStatus.COMPLETED),
        evaluator_result=evaluation,
        score_breakdown=evaluation.score_breakdown,
        usage=UsageSummary(
            requests=2,
            input_tokens=100,
            cached_tokens=20,
            output_tokens=40,
            reasoning_tokens=10,
            total_tokens=140,
        ),
        cost=CostSummary(
            availability=CostAvailability.KNOWN,
            estimated_cost_usd=0.12,
            pricing_model="test-pricing-v1",
        ),
        latency=LatencySummary(
            elapsed_seconds=2.0,
            started_at=_START,
            finished_at=_START + timedelta(seconds=2),
        ),
    )


def _manifest(*records: BenchmarkRunRecord) -> BenchmarkManifest:
    return BenchmarkManifest(
        manifest_id="manifest-1",
        manifest_version="1.0",
        status="declared",
        created_at=_START,
        scenario_references=(
            ScenarioReference(
                scenario_id="scenario-a",
                scenario_version="1.0",
                evaluator_version="1.0",
                seed=42,
            ),
        ),
        architectures=("multi-agent",),
        repetitions=1,
        model="test-model",
        model_provider="test-provider",
        run_configuration=RunConfiguration(
            execution_mode=ExecutionMode.DETERMINISTIC,
            tool_contract_version="1.0",
        ),
        budgets=BudgetConfiguration(
            resource_limits={"sql": 3, "python": 2},
            turn_limits={"lead": 4},
        ),
        aggregation_version="1.0",
        run_records=records,
    )


def test_model_visible_scenario_projection_excludes_evaluator_only_fields() -> None:
    scenario = ScenarioMetadata(
        scenario_id="scenario-a",
        scenario_version="1.0",
        name="A scenario",
        seed=42,
        generation_config={"rows": 10},
        user_question="What changed?",
        evaluator_version="1.0",
    )

    context = scenario.model_visible_context()
    assert context.model_dump() == {
        "contract_version": "1.0",
        "scenario_id": "scenario-a",
        "scenario_version": "1.0",
        "name": "A scenario",
        "user_question": "What changed?",
    }
    assert "seed" not in context.model_dump()
    assert "evaluator_version" not in context.model_dump()
    assert "ground_truth" not in context.model_dump()


def test_evaluator_spec_carries_explicit_compatibility_metadata() -> None:
    spec = ScenarioEvaluationSpec(
        scenario_id="scenario-a",
        scenario_version="1.0",
        evaluator_version="1.0",
        injected_conditions=CANONICAL_PROFITABILITY_SCENARIO.injected_conditions,
        expected_primary_driver=CANONICAL_PROFITABILITY_SCENARIO.expected_primary_driver,
        expected_secondary_findings=("A secondary finding.",),
        known_non_drivers=("A non-driver.",),
        expected_data_quality_findings=("The data is coherent.",),
        ground_truth=CANONICAL_PROFITABILITY_SCENARIO.ground_truth,
        compatibility=EvaluatorCompatibility(
            evaluator_contract_version="1.0",
            supported_workspace_versions=("legacy", "1.0"),
        ),
    )

    assert spec.compatibility.supported_workspace_versions == ("legacy", "1.0")


def test_canonical_definition_has_an_explicit_safe_model_projection() -> None:
    context = CANONICAL_PROFITABILITY_SCENARIO.model_visible_context()

    assert context.user_question == CANONICAL_PROFITABILITY_SCENARIO.user_question
    assert "ground_truth" not in context.model_dump()
    assert "expected_primary_driver" not in context.model_dump()


def test_evaluator_result_rejects_missing_score_or_failure_reason() -> None:
    with pytest.raises(ValidationError, match="score_breakdown"):
        EvaluatorResult.model_validate(
            _evaluation(status=EvaluatorStatus.PASS).model_dump()
            | {"score_breakdown": None}
        )

    with pytest.raises(ValidationError, match="failed check or reason"):
        EvaluatorResult(
            result_id="evaluation-fail",
            run_id="run-fail",
            scenario_id="scenario-a",
            scenario_version="1.0",
            evaluator_version="1.0",
            status=EvaluatorStatus.FAIL,
            checks=(
                EvaluationCheck(
                    check_id="check",
                    status=EvaluationCheckStatus.PASS,
                    message="No failure was recorded.",
                ),
            ),
            score_breakdown=_score(),
            evaluated_at=_START,
        )


def test_run_record_requires_explicit_unavailable_cost_instead_of_zero() -> None:
    with pytest.raises(ValidationError, match="known cost requires"):
        CostSummary(availability=CostAvailability.KNOWN)

    with pytest.raises(ValidationError, match="unavailable cost must not"):
        CostSummary(
            availability=CostAvailability.UNAVAILABLE,
            estimated_cost_usd=0.0,
            note="Provider did not return billing data.",
        )


def test_run_record_rejects_inconsistent_evaluator_identity() -> None:
    evaluation = _evaluation().model_copy(update={"run_id": "different-run"})
    with pytest.raises(ValidationError, match="must match run_id"):
        _record(evaluation=evaluation)


def test_manifest_rejects_duplicate_benchmark_cells_and_mismatched_configs() -> None:
    first = _record()
    with pytest.raises(ValidationError, match="duplicate"):
        _manifest(first, _record(run_id="run-2"))

    mismatched = first.model_copy(
        update={
            "run_configuration": RunConfiguration(
                execution_mode=ExecutionMode.LIVE,
                tool_contract_version="1.0",
            )
        }
    )
    with pytest.raises(ValidationError, match="run configuration"):
        _manifest(mismatched)


def test_manifest_requires_all_cells_and_aggregates_when_marked_complete() -> None:
    with pytest.raises(ValidationError, match="every declared run cell"):
        values = _manifest().model_dump()
        values["status"] = "complete"
        BenchmarkManifest.model_validate(values)


def test_legacy_workspace_version_is_supported_and_future_version_is_explicit(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("legacy")
    ledger = AnalysisLedger(workspace, run_id="legacy", objective="Inspect data")
    raw = json.loads(ledger.state_path.read_text(encoding="utf-8"))
    raw.pop("schema_version")
    ledger.state_path.write_text(json.dumps(raw), encoding="utf-8")

    assert check_workspace_version_compatibility(workspace.root) == "legacy"

    raw["schema_version"] = "9.0"
    ledger.state_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(WorkspaceVersionCompatibilityError, match="not supported"):
        check_workspace_version_compatibility(workspace.root)
