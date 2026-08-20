"""Persisted calibration fixtures for the complete offline scenario catalog."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pandas as pd
import pytest

from evaluation.engine import evaluate_workspace
from evaluation.primitives import AnalyticalCapability, CapabilityPolicy
from evaluation.rules import rules_for_scenario
from scenarios import discover_scenarios
from scenarios.generator import SyntheticEcommerceConfig
from schemas.audit import (
    AuditObservation,
    AuditResult,
    DataQualityIssue,
    IssueSeverity,
    TableAudit,
)
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.hypotheses import Hypothesis, HypothesisStatus
from schemas.metrics import MetricComparison, MetricDefinitionContext
from schemas.run_state import (
    AgentEvent,
    AgentEventStatus,
    AnalysisRunState,
    Artifact,
    ArtifactKind,
    ModelUsage,
    RunBudget,
    RunStatus,
    SpecialistResultRecord,
    ToolEvent,
    ToolEventStatus,
)
from schemas.statistics import StatisticalAssessment
from schemas.validation import ValidationResult, ValidationStatus
from tools.workspace import WorkspaceManager

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_EVIDENCE = "working/queries/calibration.sql"
_PYTHON_EVIDENCE = "working/scripts/calibration.py"

_CORRECT_REPORTS = {
    "canonical-q2-profitability": (
        "Meta was the largest material profitability driver. Meta conversion "
        "declined and drove the acquisition decline. Marketing spend increased "
        "while sessions were stable; acquired customers fell and CAC rose. Meta "
        "90-day LTV was stable, and broad COGS and contribution margin did not "
        "drive the decline."
    ),
    "retention-q2-deterioration": (
        "Email retention declined because fewer acquired customers placed a second "
        "order within the 90-day acquisition_date cohort window, reducing cohort "
        "contribution profit. Acquisition volume and CAC were stable; COGS and "
        "margin were stable. The metric uses acquired customers and acquisition_date "
        "through a 90-day window."
    ),
    "cogs-q2-margin-deterioration": (
        "Google COGS rose and reduced contribution margin in the acquired cohort, "
        "driving the profitability change. Google acquisition volume and CAC were "
        "stable; discounts and refunds were stable. The metric uses the "
        "acquisition_date cohort and a 90-day window with COGS divided by net "
        "revenue."
    ),
    "discount-refund-q2-deterioration": (
        "Affiliate discounts and refunds increased and reduced realized net "
        "revenue, driving the margin change. Affiliate acquisition volume and CAC "
        "were stable; COGS and margin were stable. Discount and refund rates use "
        "the acquisition_date cohort, a 90-day window, and gross revenue "
        "denominator."
    ),
    "missing-reporting-day": (
        "Reporting coverage is incomplete: the source has a missing reporting day."
    ),
    "partial-latest-reporting-day": (
        "Reporting coverage is incomplete: the latest reporting day is partial."
    ),
    "meaningful-ab-treatment-effect": (
        "The treatment is statistically significant and practically meaningful. "
        "The confidence interval excludes zero; the effect size exceeds the "
        "practical significance threshold. Assumptions were checked and the "
        "randomized design supports a cautious causal interpretation."
    ),
    "no-effect-ab-experiment": (
        "The treatment is not statistically significant and shows no effect; the "
        "confidence interval includes zero. The effect size is below the practical "
        "significance threshold. Assumptions were checked and the randomized design "
        "supports a cautious causal interpretation."
    ),
    "significant-but-immaterial-ab-effect": (
        "The treatment is statistically significant but immaterial and not practical. "
        "The confidence interval excludes zero while the effect size remains below "
        "the practical significance threshold. Assumptions were checked and the "
        "randomized design supports a cautious causal interpretation."
    ),
    "channel-mix-confounding": (
        "In Q2, the channel mix shifted between Meta and Organic, which explains "
        "the apparent channel movement rather than a total acquisition decline. "
        "Total acquired customers were stable. Meta and Organic attribution is an "
        "association, and no causal inference is supported. Channel share uses all "
        "acquired customers as the denominator."
    ),
}


def _ecommerce_scale() -> SyntheticEcommerceConfig:
    return SyntheticEcommerceConfig(
        seed=42,
        num_customers=1_000,
        num_orders=4_000,
        num_sessions=8_000,
        num_products=4,
        period_days=365,
    )


def _artifact(
    path: Path,
    workspace_root: Path,
    artifact_id: str,
    kind: ArtifactKind,
) -> Artifact:
    content = path.read_bytes()
    return Artifact(
        id=artifact_id,
        path=path.relative_to(workspace_root).as_posix(),
        kind=kind,
        media_type="text/markdown" if kind is ArtifactKind.REPORT else "image/png",
        sha256=sha256(content).hexdigest(),
        size_bytes=len(content),
        created_at=_NOW,
    )


def _comparison(metric, *, evidence_refs: list[str] | None = None) -> MetricComparison:
    return MetricComparison(
        metric_key=metric.metric_key,
        dimensions=metric.dimensions,
        baseline_period=metric.baseline_period,
        comparison_period=metric.comparison_period,
        comparison_type=metric.comparison_type,
        value=metric.expected_relative_change,
        unit=metric.value_unit,
        evidence_refs=evidence_refs or [_EVIDENCE],
        definition_context=metric.definition_context,
    )


def _correct_report(scenario_id: str, report_suffix: str = "") -> str:
    answer = _CORRECT_REPORTS[scenario_id] + report_suffix
    return (
        "# Calibration Report\n\n"
        "## Executive Summary\n\n"
        f"{answer}\n\n"
        "## Findings\n\n"
        f"- {answer} (evidence: {_EVIDENCE})\n\n"
        "## Recommendations\n\n"
        "- Monitor the declared metric and its source scope before changing the "
        f"business process. (evidence: {_EVIDENCE})\n"
    )


def _persist_fixture(
    tmp_path: Path,
    scenario_id: str,
    *,
    architecture: str = "multi-agent",
    tool_names: tuple[str, ...] = ("run_sql", "run_python"),
    statistical_evidence_ref: str = _PYTHON_EVIDENCE,
    report_suffix: str = "",
    metric_updates: dict[str, dict[str, object]] | None = None,
    audit_issue_ids: tuple[str, ...] | None = None,
    finding_text_override: str | None = None,
) -> Path:
    registration = next(
        item for item in discover_scenarios() if item.scenario_id == scenario_id
    )
    rules = registration.evaluator_rules()
    run_id = f"calibration-{scenario_id}"
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(run_id)

    query_path = workspace.root / _EVIDENCE
    script_path = workspace.root / _PYTHON_EVIDENCE
    chart_path = workspace.outputs / "charts" / "calibration.png"
    report_path = workspace.outputs / "calibration-report.md"
    query_path.write_text("SELECT 1;\n", encoding="utf-8")
    script_path.write_text("print('calibration')\n", encoding="utf-8")
    chart_path.write_bytes(b"deterministic calibration chart")
    report_path.write_text(
        _correct_report(scenario_id, report_suffix), encoding="utf-8"
    )

    chart = _artifact(
        chart_path,
        workspace.root,
        "calibration-chart",
        ArtifactKind.CHART,
    )
    report = _artifact(
        report_path,
        workspace.root,
        "calibration-report",
        ArtifactKind.REPORT,
    )
    metrics = []
    for metric in registration.evaluation_spec.ground_truth:
        comparison = _comparison(metric)
        updates = (metric_updates or {}).get(metric.id, {})
        metrics.append(comparison.model_copy(update=updates))

    assessments = []
    expectation = registration.evaluation_spec.statistical_expectation
    if expectation is not None:
        assessments.append(
            StatisticalAssessment(
                metric_key=expectation.metric_key,
                dimensions=expectation.dimensions,
                baseline_period=expectation.baseline_period,
                comparison_period=expectation.comparison_period,
                method="two-proportion z test",
                unit_of_analysis="independently assigned participant",
                conclusion=expectation.expected_conclusion,
                confidence_level=expectation.confidence_level,
                estimate=expectation.expected_estimate,
                confidence_interval=expectation.expected_confidence_interval,
                p_value=expectation.expected_p_value,
                effect_size=expectation.expected_effect_size,
                practical_significance_threshold=(
                    expectation.practical_significance_threshold
                ),
                practically_significant=expectation.expected_practically_significant,
                assumptions_checked=expectation.required_assumptions,
                causal_interpretation=expectation.expected_causal_interpretation,
                evidence_refs=[statistical_evidence_ref],
            )
        )

    issues = [
        DataQualityIssue(
            id=issue_id,
            severity=IssueSeverity.MEDIUM,
            message=f"Fixture audit observed {issue_id}.",
            table_name="marketing_spend",
            evidence_refs=[_EVIDENCE],
        )
        for issue_id in (
            audit_issue_ids
            if audit_issue_ids is not None
            else rules.data_quality_policy.required_issue_ids
        )
    ]
    finding_text = finding_text_override or _CORRECT_REPORTS[scenario_id]
    state = AnalysisRunState(
        schema_version="1.0",
        run_id=run_id,
        objective=registration.metadata.user_question,
        model="calibration-model",
        model_provider="offline",
        status=RunStatus.COMPLETED,
        created_at=_NOW,
        updated_at=_NOW + timedelta(seconds=1),
        audit=AuditResult(
            status="complete",
            # A production-shaped audit states what it profiled and cites the
            # execution behind it, so a clean result is evidence of a performed
            # check rather than only an absence of reported issues.
            tables=[
                TableAudit(
                    table_name="marketing_spend",
                    row_count=180,
                    evidence_refs=[_EVIDENCE],
                )
            ],
            issues=issues,
            limitations=[
                AuditObservation(
                    statement="Refund reasons are not present in the inputs.",
                    evidence_refs=[_EVIDENCE],
                )
            ],
            audited_at=_NOW,
        ),
        investigation_plan=[
            "Audit sources",
            "Measure declared metrics",
            "Review claims",
        ],
        hypotheses=[
            Hypothesis(
                id="H1",
                statement=(
                    "The declared scenario mechanism explains the observed change."
                ),
                status=HypothesisStatus.SUPPORTED,
                evidence_refs=[_EVIDENCE],
                rationale="The typed fixture metrics and report use the same scope.",
            )
        ],
        hypothesis_history=[
            Hypothesis(
                id="H1",
                statement=(
                    "The declared scenario mechanism explains the observed change."
                ),
                status=HypothesisStatus.SUPPORTED,
                evidence_refs=[_EVIDENCE],
                rationale="The typed fixture metrics and report use the same scope.",
            )
        ],
        findings=[
            Finding(
                id="F1",
                statement=finding_text,
                metric=(registration.evaluation_spec.ground_truth[0].metric_key),
                value=registration.evaluation_spec.ground_truth[
                    0
                ].expected_relative_change,
                value_unit=registration.evaluation_spec.ground_truth[0].value_unit,
                evidence_refs=[_EVIDENCE],
                confidence=ConfidenceLevel.HIGH,
            )
        ],
        metric_comparisons=metrics,
        statistical_assessments=(assessments if architecture == "single-agent" else []),
        artifacts=[chart, report],
        validation_results=[
            ValidationResult(
                status=ValidationStatus.PASS,
                checked_finding_ids=["F1"],
                summary="The hand-authored fixture is complete.",
            )
        ],
        specialist_results=(
            [
                SpecialistResultRecord(
                    agent_role="statistician",
                    result=SpecialistResult(
                        objective="Assess the declared metric and assumptions.",
                        statistical_assessments=assessments,
                    ),
                ),
            ]
            if architecture == "multi-agent"
            else []
        ),
        tool_events=[
            ToolEvent(
                id=f"calibration-{tool_name.removeprefix('run-')}",
                tool_name=tool_name,
                status=ToolEventStatus.SUCCEEDED,
                started_at=_NOW,
                completed_at=_NOW,
                artifact_refs=[
                    _EVIDENCE if tool_name == "run_sql" else _PYTHON_EVIDENCE
                ],
            )
            for tool_name in tool_names
        ],
        agent_events=[
            AgentEvent(
                id=f"calibration-agent-{role}",
                agent_name=role.replace("_", " ").title(),
                agent_role=role,
                status=AgentEventStatus.SUCCEEDED,
                started_at=_NOW,
                completed_at=_NOW,
                model="calibration-model",
                objective=registration.metadata.user_question,
                output_type="fixture",
            )
            for role in (
                ("generalist",)
                if architecture == "single-agent"
                else (
                    "data_auditor",
                    "lead",
                    "analyst",
                    "statistician",
                    "critic",
                )
            )
        ],
        run_budget=RunBudget(
            specialist_invocations=1,
            sql_executions=1,
            python_executions=int("run_python" in tool_names),
            critic_loops=1,
            charts_created=1,
        ),
        usage=ModelUsage(
            requests=5,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        ),
        elapsed_seconds=1.0,
        cost_estimation_note="offline fixture cost metadata",
        final_report=report,
    )
    (workspace.state / "analysis_ledger.json").write_text(
        state.model_dump_json(indent=2), encoding="utf-8"
    )
    return workspace.root


@pytest.mark.parametrize(
    "scenario_id",
    tuple(item.scenario_id for item in discover_scenarios().registrations),
)
def test_fully_correct_persisted_fixture_passes_every_catalog_evaluator(
    tmp_path: Path,
    scenario_id: str,
) -> None:
    workspace = _persist_fixture(tmp_path, scenario_id)
    evaluation = evaluate_workspace(workspace, rules_for_scenario(scenario_id, "1.0"))

    assert evaluation.passed, [
        (check.check_id, check.message)
        for check in evaluation.checks
        if check.status.value == "fail"
    ]


def test_semantically_equivalent_architectures_receive_the_same_evaluation_result(
    tmp_path: Path,
) -> None:
    """Role traces must not change the score for equivalent persisted outputs."""

    for scenario_id in (
        item.scenario_id for item in discover_scenarios().registrations
    ):
        multi_workspace = _persist_fixture(
            tmp_path / "multi-agent",
            scenario_id,
            architecture="multi-agent",
        )
        single_workspace = _persist_fixture(
            tmp_path / "single-agent",
            scenario_id,
            architecture="single-agent",
        )
        rules = rules_for_scenario(scenario_id, "1.0")
        multi = evaluate_workspace(multi_workspace, rules)
        single = evaluate_workspace(single_workspace, rules)

        assert multi.result.status is single.result.status
        assert multi.result.score_breakdown == single.result.score_breakdown
        assert [
            (check.check_id, check.status, check.message) for check in multi.checks
        ] == [(check.check_id, check.status, check.message) for check in single.checks]


@pytest.mark.parametrize(
    "tool_names",
    (("run_sql",), ("run_sql", "run_python")),
)
def test_non_statistical_scenario_accepts_valid_tool_mix(
    tmp_path: Path,
    tool_names: tuple[str, ...],
) -> None:
    workspace = _persist_fixture(
        tmp_path,
        "retention-q2-deterioration",
        tool_names=tool_names,
    )
    evaluation = evaluate_workspace(
        workspace,
        rules_for_scenario("retention-q2-deterioration", "1.0"),
    )

    assert evaluation.passed, [
        (check.check_id, check.message)
        for check in evaluation.checks
        if check.status.value == "fail"
    ]
    assert not any(
        check.check_id in {"provenance:sql_execution", "provenance:python_execution"}
        for check in evaluation.checks
    )


def test_unnecessary_python_does_not_change_non_statistical_score(
    tmp_path: Path,
) -> None:
    sql_only = evaluate_workspace(
        _persist_fixture(
            tmp_path / "sql-only",
            "retention-q2-deterioration",
            tool_names=("run_sql",),
        ),
        rules_for_scenario("retention-q2-deterioration", "1.0"),
    )
    sql_and_python = evaluate_workspace(
        _persist_fixture(
            tmp_path / "sql-and-python",
            "retention-q2-deterioration",
            tool_names=("run_sql", "run_python"),
        ),
        rules_for_scenario("retention-q2-deterioration", "1.0"),
    )

    assert sql_only.result.status is sql_and_python.result.status
    assert sql_only.result.score_breakdown == sql_and_python.result.score_breakdown
    assert [
        (check.check_id, check.status, check.message)
        for check in sql_only.checks
        if check.check_id != "lifecycle:budget:python_executions"
    ] == [
        (check.check_id, check.status, check.message)
        for check in sql_and_python.checks
        if check.check_id != "lifecycle:budget:python_executions"
    ]


@pytest.mark.parametrize("architecture", ("multi-agent", "single-agent"))
def test_statistical_capability_is_typed_not_role_or_tool_bound(
    tmp_path: Path,
    architecture: str,
) -> None:
    workspace = _persist_fixture(
        tmp_path,
        "meaningful-ab-treatment-effect",
        architecture=architecture,
        tool_names=("run_sql",),
        statistical_evidence_ref=_EVIDENCE,
    )
    evaluation = evaluate_workspace(
        workspace,
        rules_for_scenario("meaningful-ab-treatment-effect", "1.0"),
    )

    assert evaluation.passed, [
        (check.check_id, check.message)
        for check in evaluation.checks
        if check.status.value == "fail"
    ]
    assert (
        next(
            check
            for check in evaluation.checks
            if check.check_id == "capability:statistical_analysis"
        ).status.value
        == "pass"
    )


def test_missing_declared_capability_fails_named_check(tmp_path: Path) -> None:
    workspace = _persist_fixture(
        tmp_path,
        "retention-q2-deterioration",
        tool_names=("run_sql",),
    )
    base_rules = rules_for_scenario("retention-q2-deterioration", "1.0")
    rules = replace(
        base_rules,
        capability_policy=CapabilityPolicy(
            required=(AnalyticalCapability.STATISTICAL_ANALYSIS,)
        ),
    )

    evaluation = evaluate_workspace(workspace, rules)

    assert (
        next(
            check
            for check in evaluation.checks
            if check.check_id == "capability:statistical_analysis"
        ).status.value
        == "fail"
    )


@pytest.mark.parametrize(
    ("label", "scenario_id", "metric_updates", "expected_check"),
    (
        (
            "wrong denominator",
            "retention-q2-deterioration",
            {
                "email-q2-retention-rate": {
                    "definition_context": MetricDefinitionContext(
                        population="acquired customer cohort",
                        date_basis="acquisition_date cohort and order_date observation",
                        observation_window="90_day",
                        numerator="retained customers with a second order",
                        denominator="orders",
                        definition_ref="retention_rate_90_day",
                    )
                }
            },
            "numeric:email-q2-retention-rate",
        ),
        (
            "grain-multiplying join",
            "canonical-q2-profitability",
            {"meta-q2-cac": {"value": 0.6156}},
            "numeric:meta-q2-cac",
        ),
        (
            "period leakage",
            "cogs-q2-margin-deterioration",
            {"google-q2-cogs-ratio": {"comparison_period": "Q1-Q3 2025"}},
            "numeric:google-q2-cogs-ratio",
        ),
    ),
)
def test_targeted_metric_defects_fail_numeric_scope_or_value_check(
    tmp_path: Path,
    label: str,
    scenario_id: str,
    metric_updates: dict[str, dict[str, object]],
    expected_check: str,
) -> None:
    workspace = _persist_fixture(
        tmp_path,
        scenario_id,
        metric_updates=metric_updates,
    )
    evaluation = evaluate_workspace(workspace, rules_for_scenario(scenario_id, "1.0"))

    failed = {
        check.check_id for check in evaluation.checks if check.status.value == "fail"
    }
    assert expected_check in failed, label


def test_unsupported_causality_fixture_fails_only_claim_language_gate(
    tmp_path: Path,
) -> None:
    workspace = _persist_fixture(
        tmp_path,
        "channel-mix-confounding",
        report_suffix=" Meta caused the company-wide acquisition movement.",
    )
    evaluation = evaluate_workspace(
        workspace,
        rules_for_scenario("channel-mix-confounding", "1.0"),
    )

    failed = {
        check.check_id for check in evaluation.checks if check.status.value == "fail"
    }
    assert failed == {"unsupported_claims:forbidden_language"}


def test_evidence_free_numbers_fail_provenance_not_numeric_correctness(
    tmp_path: Path,
) -> None:
    workspace = _persist_fixture(
        tmp_path,
        "retention-q2-deterioration",
        metric_updates={
            "email-q2-retention-rate": {"evidence_refs": ["invented-number"]}
        },
    )
    evaluation = evaluate_workspace(
        workspace,
        rules_for_scenario("retention-q2-deterioration", "1.0"),
    )

    failed = {
        check.check_id for check in evaluation.checks if check.status.value == "fail"
    }
    assert "provenance:metric:retention_rate" in failed
    assert not any(check_id.startswith("numeric:") for check_id in failed)


def test_incomplete_keyword_rich_fixture_fails_root_cause_semantics(
    tmp_path: Path,
) -> None:
    workspace = _persist_fixture(
        tmp_path,
        "canonical-q2-profitability",
        report_suffix="",
        finding_text_override=(
            "Meta conversion spend sessions acquired customers CAC LTV profit decline."
        ),
    )
    report_path = workspace / "outputs" / "calibration-report.md"
    report_path.write_text(
        "# Calibration Report\n\n"
        "## Executive Summary\n\n"
        "Meta conversion spend sessions acquired customers CAC LTV profit decline.\n\n"
        "## Findings\n\n"
        "- Meta conversion and CAC were reviewed. (evidence: "
        f"{_EVIDENCE})\n\n"
        "## Recommendations\n\n"
        "- Review the acquisition funnel. (evidence: "
        f"{_EVIDENCE})\n",
        encoding="utf-8",
    )
    # The report is intentionally changed after persistence, so the registered
    # report hash is stale; replace only that hash to keep this defect semantic.
    state_path = workspace / "state" / "analysis_ledger.json"
    state = AnalysisRunState.model_validate_json(state_path.read_text(encoding="utf-8"))
    report = state.final_report
    assert report is not None
    report = report.model_copy(
        update={
            "sha256": sha256(report_path.read_bytes()).hexdigest(),
            "size_bytes": report_path.stat().st_size,
        }
    )
    state = state.model_copy(
        update={
            "final_report": report,
            "artifacts": [
                report if artifact.id == report.id else artifact
                for artifact in state.artifacts
            ],
        }
    )
    state_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    evaluation = evaluate_workspace(
        workspace,
        rules_for_scenario("canonical-q2-profitability", "1.0"),
    )

    failed = {
        check.check_id for check in evaluation.checks if check.status.value == "fail"
    }
    assert any(check_id.startswith("root_cause:") for check_id in failed)
    assert not any(check_id.startswith("numeric:") for check_id in failed)


def test_catalog_generation_and_evaluator_lookup_are_regression_covered() -> None:
    catalog = discover_scenarios()
    assert len(catalog) == 10
    assert len({item.key for item in catalog.registrations}) == 10
    for registration in catalog.registrations:
        assert registration.evaluator_rules().scenario_id == registration.scenario_id
        if registration.evaluation_spec.statistical_expectation is None:
            first = registration.generate_validated(_ecommerce_scale())
            second = registration.generate_validated(_ecommerce_scale())
        else:
            first = registration.generate_validated()
            second = registration.generate_validated()
        assert first.dataset.business_definitions == second.dataset.business_definitions
        for name, frame in first.dataset.table_map().items():
            pd.testing.assert_frame_equal(frame, second.dataset.table_map()[name])
