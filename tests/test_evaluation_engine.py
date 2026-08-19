"""Deterministic fixtures for the generic offline evaluation engine."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evaluation.contracts import (
    BenchmarkManifest,
    BudgetConfiguration,
    EvaluationCheckStatus,
    ExecutionMode,
    RunConfiguration,
    ScenarioReference,
    WorkspaceIdentity,
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
from evaluation.workspace_identity import (
    WorkspaceIdentityError,
    persist_workspace_identity,
    source_file_identities,
)
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


def _bound_fixture_workspace(tmp_path: Path):
    source_inputs = tmp_path / "source-inputs"
    source_docs = tmp_path / "source-docs"
    source_inputs.mkdir()
    source_docs.mkdir()
    (source_inputs / "fixture.parquet").write_bytes(b"fixture")
    (source_docs / "README.md").write_text("fixture\n", encoding="utf-8")
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "bound",
        inputs_source=source_inputs,
        docs_source=source_docs,
    )
    AnalysisLedger(workspace, run_id="bound", objective="fixture")
    persist_workspace_identity(
        workspace,
        WorkspaceIdentity(
            benchmark_manifest_id="manifest",
            run_id="bound",
            scenario_id=CANONICAL_PROFITABILITY_SCENARIO.scenario_id,
            scenario_version="1.0",
            evaluator_version="1.1",
            architecture="single-agent",
            repetition=1,
            seed=42,
            source_files=source_file_identities(workspace),
        ),
    )
    return workspace


def test_bound_workspace_refuses_rules_with_different_scenario_or_evaluator(
    tmp_path: Path,
) -> None:
    workspace = _bound_fixture_workspace(tmp_path)
    rules = ScenarioRules(
        scenario_id="different-scenario",
        scenario_version="1.0",
        evaluator_version="1.1",
    )

    with pytest.raises(WorkspaceIdentityError, match="does not match evaluator rules"):
        evaluate_workspace(workspace, rules)


def test_standalone_cli_requires_identity_or_explicit_legacy_diagnostic(
    tmp_path: Path,
) -> None:
    unbound = WorkspaceManager(tmp_path / "workspaces").create_workspace("unbound")
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_workspace.py"

    refused = subprocess.run(
        [sys.executable, str(script), str(unbound.root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert refused.returncode == 2
    assert "workspace identity is missing" in refused.stderr

    legacy_without_selection = subprocess.run(
        [sys.executable, str(script), str(unbound.root), "--legacy-diagnostic"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert legacy_without_selection.returncode == 2
    assert "requires --scenario-id and --scenario-version" in (
        legacy_without_selection.stderr
    )


def test_standalone_cli_rejects_explicit_selection_mismatch(tmp_path: Path) -> None:
    workspace = _bound_fixture_workspace(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_workspace.py"
    derived = subprocess.run(
        [sys.executable, str(script), str(workspace.root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert derived.returncode == 1
    assert "OFFLINE EVALUATION ERROR" not in derived.stderr

    refused = subprocess.run(
        [
            sys.executable,
            str(script),
            str(workspace.root),
            "--scenario-id",
            "meaningful-ab-treatment-effect",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert refused.returncode == 2
    assert "does not match persisted workspace identity" in refused.stderr


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


def test_batch_cli_output_is_exclusive_and_rejects_input_aliases(
    tmp_path: Path,
) -> None:
    manifest = BenchmarkManifest(
        manifest_id="offline-output-manifest",
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
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_manifest.py"
    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)

    output_path = tmp_path / "rescored.json"
    command = [
        sys.executable,
        str(script),
        str(manifest_path),
        "--output",
        str(output_path),
    ]
    first = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    output_before = output_path.read_bytes()
    second = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 2
    assert "refusing to overwrite" in second.stderr
    assert output_path.read_bytes() == output_before
    assert manifest_path.read_text(encoding="utf-8") == original

    same_path = subprocess.run(
        [
            sys.executable,
            str(script),
            str(manifest_path),
            "--output",
            str(tmp_path / "nested" / ".." / "manifest.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert same_path.returncode == 2
    assert "must differ from input" in same_path.stderr
    assert manifest_path.read_text(encoding="utf-8") == original

    symlink_path = tmp_path / "manifest-alias.json"
    symlink_path.symlink_to(manifest_path)
    symlink = subprocess.run(
        [
            sys.executable,
            str(script),
            str(manifest_path),
            "--output",
            str(symlink_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert symlink.returncode == 2
    assert "must differ from input" in symlink.stderr
    assert manifest_path.read_text(encoding="utf-8") == original
