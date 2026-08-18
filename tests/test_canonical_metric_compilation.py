"""Regression coverage for the canonical final metric/evidence boundary."""

from pathlib import Path

import pandas as pd

from agents import AgentRole, AgentRunConfig, AgentRunContext
from agents.critic import (
    candidate_completeness_validation,
    validate_metric_compilation_conflicts,
)
from agents.lead import _preserve_metric_definitions
from evaluation.canonical import _canonical_numeric_ground_truth_failures
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from schemas.audit import AuditResult, AuditStatus
from schemas.lead import LeadResult
from schemas.metrics import (
    MetricComparison,
    compile_metric_comparisons,
)
from schemas.validation import CriticCandidate, ValidationStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def _comparison(
    metric_key: str,
    value: float,
    *,
    evidence_ref: str,
    dimensions: dict[str, str] | None = None,
    unit: str = "relative_change_fraction",
) -> MetricComparison:
    return MetricComparison(
        metric_key=metric_key,
        dimensions={"channel": "Meta"} if dimensions is None else dimensions,
        baseline_period="Q1 2025",
        comparison_period="Q2 2025",
        comparison_type="relative_change",
        value=value,
        unit=unit,
        evidence_refs=[evidence_ref],
    )


def _acquisition_comparisons(*, include_spend: bool = True) -> list[MetricComparison]:
    comparisons = [
        _comparison("acquisition_sessions", -0.0019, evidence_ref="sql-sessions"),
        _comparison("conversion_rate", -0.18, evidence_ref="sql-funnel"),
        _comparison("acquired_customers", -0.1806, evidence_ref="sql-funnel"),
        _comparison("cac", 0.3057, evidence_ref="sql-funnel"),
        _comparison("ltv", 0.0, evidence_ref="python-ltv"),
    ]
    if include_spend:
        comparisons.append(_comparison("meta_spend", 0.07, evidence_ref="sql-funnel"))
    return comparisons


def _critic_context(tmp_path: Path) -> AgentRunContext:
    source = tmp_path / "source"
    source.mkdir()
    pd.DataFrame({"cogs": [1.0]}).to_parquet(source / "orders.parquet")
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "metric-compilation",
        inputs_source=source,
    )
    ledger = AnalysisLedger(workspace, objective="Why did profitability decline?")
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="metric-compilation",
            agent_role=AgentRole.CRITIC,
            model="test-model",
        ),
    )


def test_four_consistent_cac_estimates_compile_as_corroboration() -> None:
    values = (0.305745, 0.30573684617837354, 0.30573684617837354, 0.3057142577576913)
    compilation = compile_metric_comparisons(
        [
            _comparison("meta_cac", value, evidence_ref=f"evidence-{index}")
            for index, value in enumerate(values)
        ]
    )

    assert compilation.conflicts == []
    assert len(compilation.comparisons) == 1
    assert compilation.comparisons[0].metric_key == "cac"
    assert compilation.comparisons[0].value == values[-1]
    assert compilation.comparisons[0].evidence_refs == [
        "evidence-0",
        "evidence-1",
        "evidence-2",
        "evidence-3",
    ]


def test_four_consistent_cac_estimates_pass_canonical_numeric_evaluation() -> None:
    comparisons: list[MetricComparison] = []
    for expected in CANONICAL_PROFITABILITY_SCENARIO.ground_truth:
        if expected.metric_key == "cac":
            comparisons.extend(
                _comparison("meta_cac", value, evidence_ref=f"cac-{index}")
                for index, value in enumerate(
                    (
                        0.305745,
                        0.30573684617837354,
                        0.30573684617837354,
                        0.3057142577576913,
                    )
                )
            )
            continue
        comparisons.append(
            MetricComparison(
                metric_key=expected.metric_key,
                dimensions=expected.dimensions,
                baseline_period=expected.baseline_period,
                comparison_period=expected.comparison_period,
                comparison_type=expected.comparison_type,
                value=expected.expected_relative_change,
                unit=expected.value_unit,
                evidence_refs=[f"evidence-{expected.metric_key}"],
            )
        )

    assert _canonical_numeric_ground_truth_failures(comparisons) == []


def test_materially_conflicting_cac_estimates_fail_validation() -> None:
    compilation = compile_metric_comparisons(
        [
            _comparison("cac", 0.3057, evidence_ref="sql-cac"),
            _comparison("customer_acquisition_cost", 0.9, evidence_ref="bad-cac"),
        ]
    )
    candidate = CriticCandidate(
        objective="Explain acquisition efficiency.",
        answer="The estimates conflict.",
        metric_comparisons=compilation.comparisons,
        metric_conflicts=compilation.conflicts,
    )

    validation = validate_metric_compilation_conflicts(candidate)

    assert validation is not None
    assert validation.status is ValidationStatus.REVISE
    assert validation.issues[0].category == "structured_metric_conflict"


def test_remediation_correction_supersedes_carried_stale_value() -> None:
    stale = _comparison("cac", 0.9, evidence_ref="stale")
    corrected = _comparison("cac", 0.3057, evidence_ref="corrected")
    prior = LeadResult(
        objective="Explain acquisition efficiency.",
        answer="The initial estimate needs correction.",
        metric_comparisons=[stale],
    )

    preserved = _preserve_metric_definitions([stale, corrected], prior)
    compilation = compile_metric_comparisons(preserved)

    assert compilation.conflicts == []
    assert compilation.comparisons == [
        corrected.model_copy(update={"unit": "relative_change_fraction"})
    ]


def test_latest_report_margin_conclusion_satisfies_completeness(
    tmp_path: Path,
) -> None:
    context = _critic_context(tmp_path)
    metrics = [
        _comparison("net_revenue", -0.019, evidence_ref="sql-profit", dimensions={}),
        _comparison("cogs", -0.018, evidence_ref="sql-profit", dimensions={}),
        _comparison(
            "contribution_before_marketing",
            -0.025,
            evidence_ref="sql-profit",
            dimensions={},
        ),
        _comparison(
            "contribution_margin",
            -0.0005,
            evidence_ref="sql-profit",
            dimensions={},
        ),
    ]
    candidate = CriticCandidate(
        objective="Why did profitability decline?",
        answer=(
            "Net revenue and COGS declined together, contribution before marketing "
            "fell modestly, and contribution margin was effectively stable; broad "
            "margin deterioration was not a material driver."
        ),
        metric_comparisons=metrics,
    )

    validation = candidate_completeness_validation(candidate, context=context)

    assert validation is None or all(
        issue.id != "V-COMPLETENESS-MARGIN" for issue in validation.issues
    )


def test_acquisition_sessions_alias_satisfies_meta_traffic_requirement() -> None:
    candidate = CriticCandidate(
        objective="Explain acquisition efficiency.",
        answer=(
            "Marketing spend, sessions, conversion, acquired customers, CAC, and "
            "downstream LTV close the acquisition path."
        ),
        metric_comparisons=_acquisition_comparisons(include_spend=False),
        structured_metrics_required=True,
    )

    validation = candidate_completeness_validation(candidate)

    assert validation is not None
    issue = next(
        item
        for item in validation.issues
        if item.id == "V-COMPLETENESS-STRUCTURED-METRICS"
    )
    assert "marketing spend (channel=meta)" in issue.message
    assert "sessions/traffic" not in issue.message


def test_valid_meta_spend_comparison_satisfies_numeric_evaluator() -> None:
    comparisons = []
    for expected in CANONICAL_PROFITABILITY_SCENARIO.ground_truth:
        comparisons.append(
            MetricComparison(
                metric_key=expected.metric_key,
                dimensions=expected.dimensions,
                baseline_period=expected.baseline_period,
                comparison_period=expected.comparison_period,
                comparison_type=expected.comparison_type,
                value=expected.expected_relative_change,
                unit=expected.value_unit,
                evidence_refs=[f"evidence-{expected.metric_key}"],
            )
        )

    without_spend = [
        item for item in comparisons if item.metric_key != "marketing_spend"
    ]
    assert any(
        "meta-q2-spend" in failure
        for failure in _canonical_numeric_ground_truth_failures(without_spend)
    )
    assert _canonical_numeric_ground_truth_failures(comparisons) == []


def test_runner_candidate_report_and_ledger_use_one_canonical_metric_set(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("shared")
    ledger = AnalysisLedger(workspace, objective="Explain acquisition efficiency.")
    compilation = compile_metric_comparisons(
        [
            _comparison("meta_cac", 0.305745, evidence_ref="sql-one"),
            _comparison("cac", 0.305714, evidence_ref="sql-two"),
        ]
    )
    lead = LeadResult(
        objective="Explain acquisition efficiency.",
        answer="Meta CAC increased.",
        metric_comparisons=compilation.comparisons,
    )
    ledger.replace_metric_comparisons(lead.metric_comparisons)

    candidate = AnalysisRunner._candidate(lead.objective, lead)
    report = AnalysisRunner._render_report(
        lead.objective,
        AuditResult(status=AuditStatus.COMPLETE),
        lead,
        None,
        constrained=False,
        constraint_reason=None,
        ledger=ledger,
    )

    assert lead.metric_comparisons == candidate.metric_comparisons
    assert lead.metric_comparisons == ledger.metric_comparisons
    assert report.count("**cac:**") == 1
