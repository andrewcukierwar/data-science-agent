"""Deterministic fixtures for the generic offline evaluation engine."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from evaluation.contracts import (
    BenchmarkManifest,
    BudgetConfiguration,
    EvaluationCheckStatus,
    ExecutionMode,
    RunConfiguration,
    ScenarioReference,
)
from evaluation.engine import ScenarioRules, dump_stable_json, evaluate_workspace
from evaluation.primitives import (
    DataQualityPolicy,
    TaskCompletenessPolicy,
    compile_final_metric_set,
    evaluate_numeric_comparisons,
    evaluate_provenance,
    evaluate_root_cause,
    evaluate_unsupported_claims,
    numeric_ground_truth_failures,
)
from evaluation.rules import canonical_rules
from orchestration.ledger import AnalysisLedger
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from schemas.audit import AuditStatus
from schemas.findings import ConfidenceLevel, Finding
from schemas.metrics import MetricComparison
from schemas.run_state import RunStatus, ToolEvent, ToolEventStatus
from tools.workspace import WorkspaceManager


def _comparisons() -> list[MetricComparison]:
    return [
        MetricComparison(
            metric_key=metric.metric_key,
            dimensions=metric.dimensions,
            baseline_period=metric.baseline_period,
            comparison_period=metric.comparison_period,
            comparison_type=metric.comparison_type,
            value=metric.expected_relative_change,
            unit=metric.value_unit,
            evidence_refs=["evidence"],
        )
        for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth
    ]


def test_numeric_primitive_covers_pass_missing_incorrect_conflicting_and_stale() -> (
    None
):
    expected = CANONICAL_PROFITABILITY_SCENARIO.ground_truth
    correct = _comparisons()
    assert numeric_ground_truth_failures(correct, expected) == []

    missing = correct[:-1]
    assert any(
        "missing numeric" in item
        for item in numeric_ground_truth_failures(missing, expected)
    )

    incorrect = [
        item.model_copy(update={"value": 0.9}) if item.metric_key == "cac" else item
        for item in correct
    ]
    assert any(
        "outside" in item for item in numeric_ground_truth_failures(incorrect, expected)
    )

    ltv = next(item for item in correct if item.metric_key == "ltv")
    conflicting = [*correct, ltv.model_copy(update={"value": 0.25})]
    failures = numeric_ground_truth_failures(conflicting, expected)
    assert any("conflicting" in item for item in failures)

    stale_then_corrected = [
        ltv.model_copy(update={"value": 0.25}),
        ltv,
    ]
    failures = numeric_ground_truth_failures(stale_then_corrected, (expected[4],))
    assert any("conflicting" in item for item in failures)


def test_numeric_checks_are_typed_and_deterministically_ordered() -> None:
    checks = evaluate_numeric_comparisons(
        _comparisons(),
        CANONICAL_PROFITABILITY_SCENARIO.ground_truth,
    )

    assert [check.check_id for check in checks] == [
        f"numeric:{metric.id}"
        for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth
    ]
    assert all(check.status.value == "pass" for check in checks)


def test_final_metric_set_is_compiled_once_and_conflicts_are_explicit() -> None:
    final_metrics, checks = compile_final_metric_set(_comparisons())
    assert len(final_metrics) == len(CANONICAL_PROFITABILITY_SCENARIO.ground_truth)
    assert [check.status.value for check in checks] == ["pass"]

    ltv = next(item for item in final_metrics if item.metric_key == "ltv")
    _, conflict_checks = compile_final_metric_set(
        [*final_metrics, ltv.model_copy(update={"value": 0.25})]
    )
    assert any(check.status.value == "fail" for check in conflict_checks)


def test_semantic_primitives_reject_speculation_and_unsupported_claims() -> None:
    speculative = (
        "Meta conversion may be worth investigating as a possible explanation."
    )
    asserted = "Meta conversion declined and drove the acquisition decline."
    root_checks = evaluate_root_cause(
        speculative,
        canonical_rules().root_cause_rules[:2],
    )
    assert any(check.status.value == "fail" for check in root_checks)
    assert all(
        check.status.value == "pass"
        for check in evaluate_root_cause(
            asserted, canonical_rules().root_cause_rules[1:2]
        )
    )

    unsupported = evaluate_unsupported_claims(
        "The analysis proves this channel change caused every downstream outcome."
    )
    assert unsupported[0].status.value == "fail"


def test_repeated_offline_evaluation_is_byte_stable(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("stable")
    ledger = AnalysisLedger(workspace, run_id="stable", objective="Inspect data")
    ledger.set_status(RunStatus.COMPLETED)

    rules = ScenarioRules(
        scenario_id="fixture",
        scenario_version="1.0",
        evaluator_version="1.0",
        data_quality_policy=DataQualityPolicy(
            required_audit_status=AuditStatus.COMPLETE,
            maximum_issue_severity=None,
        ),
        task_policy=TaskCompletenessPolicy(
            require_plan=False,
            require_hypothesis_history=False,
            require_findings=False,
            require_structured_metrics=False,
            require_chart=False,
            require_final_critic_pass=False,
            require_recommendations=False,
        ),
    )
    first = evaluate_workspace(workspace, rules)
    second = evaluate_workspace(workspace, rules)

    assert first.result.model_dump_json() == second.result.model_dump_json()
    assert dump_stable_json(first.as_dict()) == dump_stable_json(second.as_dict())
    assert first.result.evaluated_at == datetime.fromisoformat(
        first.result.evaluated_at.isoformat()
    ).astimezone(UTC)


def test_offline_provenance_rejects_failed_event_even_with_unrelated_success(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "provenance-status"
    )
    ledger = AnalysisLedger(workspace, run_id="provenance-status", objective="Measure")
    stamp = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    ledger.append_tool_event(
        ToolEvent(
            id="tool-failed",
            tool_name="run_sql",
            status=ToolEventStatus.FAILED,
            started_at=stamp,
            completed_at=stamp,
            artifact_refs=["working/queries/failed.sql"],
            error="query failed",
        )
    )
    ledger.append_tool_event(
        ToolEvent(
            id="tool-unrelated",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=stamp,
            completed_at=stamp,
            artifact_refs=["working/queries/unrelated.sql"],
        )
    )
    ledger.add_finding(
        Finding(
            id="F-FAILED",
            statement="The failed query measured the value.",
            metric="value",
            value=1.0,
            evidence_refs=["tool-failed"],
            confidence=ConfidenceLevel.HIGH,
        )
    )

    checks = evaluate_provenance(workspace, ledger.state, "")
    finding_check = next(
        check for check in checks if check.check_id == "provenance:finding:F-FAILED"
    )

    assert finding_check.status is EvaluationCheckStatus.FAIL


def test_batch_cli_is_offline_and_does_not_overwrite_manifest(tmp_path: Path) -> None:
    manifest = BenchmarkManifest(
        manifest_id="offline-manifest",
        manifest_version="1.0",
        status="declared",
        created_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        scenario_references=(
            ScenarioReference(
                scenario_id=CANONICAL_PROFITABILITY_SCENARIO.scenario_id,
                scenario_version="1.0",
                evaluator_version="1.0",
                seed=42,
            ),
        ),
        architectures=("multi-agent",),
        repetitions=1,
        model="offline-fixture",
        model_provider="none",
        run_configuration=RunConfiguration(
            execution_mode=ExecutionMode.DETERMINISTIC,
            tool_contract_version="1.0",
        ),
        budgets=BudgetConfiguration(
            resource_limits={"sql": 1},
            turn_limits={"lead": 1},
        ),
        aggregation_version="1.0",
    )
    manifest_path = tmp_path / "manifest.json"
    original = manifest.model_dump_json(indent=2)
    manifest_path.write_text(original, encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    environment["OPENAI_BASE_URL"] = "http://127.0.0.1:1"
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "evaluate_manifest.py"),
        str(manifest_path),
    ]

    first = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    second = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    assert manifest_path.read_text(encoding="utf-8") == original
