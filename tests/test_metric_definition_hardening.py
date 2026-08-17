"""Regression tests for metric scope, provenance, synthesis, and completeness."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from agents import AgentRole, AgentRunConfig, AgentRunContext
from agents.analyst import persist_analyst_result
from agents.critic import (
    candidate_completeness_validation,
    run_critic,
    validate_candidate_evidence_provenance,
)
from agents.lead import persist_lead_result
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from schemas.audit import AuditResult, AuditStatus
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.lead import LeadResult
from schemas.metrics import (
    MetricComparison,
    MetricDefinitionContext,
    metric_comparison_identity,
    metric_comparison_scope_identity,
)
from schemas.run_state import (
    ArtifactKind,
    RunBudget,
    RunStatus,
    ToolEvent,
    ToolEventStatus,
)
from schemas.validation import CriticCandidate, ValidationResult, ValidationStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def _context(
    tmp_path: Path,
    role: AgentRole,
    *,
    inputs_source: Path | None = None,
) -> AgentRunContext:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "metric-hardening",
        inputs_source=inputs_source,
    )
    ledger = AnalysisLedger(workspace, objective="Why did profitability change?")
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="metric-hardening",
            agent_role=role,
            model="test-model",
        ),
    )


def _comparison(
    *,
    value: float,
    evidence_ref: str = "working/queries/profit.sql",
    context: MetricDefinitionContext | None = None,
    metric_key: str = "reporting_contribution_profit",
) -> MetricComparison:
    return MetricComparison(
        metric_key=metric_key,
        dimensions={"channel": "Paid"},
        baseline_period="Q1 2025",
        comparison_period="Q2 2025",
        comparison_type="relative_change",
        value=value,
        unit="fraction",
        evidence_refs=[evidence_ref],
        definition_context=context,
    )


def _cohort_context() -> MetricDefinitionContext:
    return MetricDefinitionContext(
        population="customers acquired in the reporting period",
        date_basis="acquisition_date",
        observation_window="acquisition_date through acquisition_date + 90 days",
        numerator=(
            "sum(net_revenue - cogs) for observed cohort orders minus marketing spend"
        ),
        denominator="reporting-period acquisition cohort",
        definition_ref="docs/business_definitions.md#profitability",
    )


def _calendar_context() -> MetricDefinitionContext:
    return MetricDefinitionContext(
        population="all orders occurring in the calendar period",
        date_basis="order_date",
        observation_window="calendar quarter",
        numerator="sum(net_revenue - cogs) for calendar-period orders",
        denominator="calendar-period orders",
        definition_ref="docs/business_definitions.md#calendar-profit",
    )


def test_metric_scope_is_part_of_identity() -> None:
    cohort = _comparison(value=-0.2, context=_cohort_context())
    calendar = cohort.model_copy(update={"definition_context": _calendar_context()})

    assert metric_comparison_scope_identity(cohort) == metric_comparison_scope_identity(
        calendar
    )
    assert metric_comparison_identity(cohort) != metric_comparison_identity(calendar)


def test_corrected_comparison_replaces_stale_same_scope_version(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, AgentRole.LEAD)
    stale = _comparison(value=-0.2, evidence_ref="tool-stale")
    corrected = _comparison(value=-0.3, evidence_ref="tool-corrected")

    context.ledger.upsert_metric_comparison(stale)
    context.ledger.upsert_metric_comparison(corrected)

    assert context.ledger.metric_comparisons == [
        corrected.model_copy(update={"unit": "relative_change_fraction"})
    ]


def test_equivalent_metric_aliases_share_the_corrected_identity(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, AgentRole.LEAD)
    stale = MetricComparison(
        metric_key="meta_spend",
        dimensions={"acquisition_channel": "Meta"},
        baseline_period="2025 Q1",
        comparison_period="Q2/2025",
        comparison_type="relative_change",
        value=0.2,
        unit="fraction",
        evidence_refs=["tool-stale"],
    )
    corrected = MetricComparison(
        metric_key="spend",
        dimensions={"channel": "Meta"},
        baseline_period="Q1 2025",
        comparison_period="Q2 2025",
        comparison_type="relative_change",
        value=0.07,
        unit="relative_change_fraction",
        evidence_refs=["tool-corrected"],
    )

    context.ledger.upsert_metric_comparison(stale)
    context.ledger.upsert_metric_comparison(corrected)

    assert len(context.ledger.metric_comparisons) == 1
    assert context.ledger.metric_comparisons[0].metric_key == "marketing_spend"
    assert context.ledger.metric_comparisons[0].value == 0.07


def test_remediation_preserves_original_estimand_and_keeps_new_scope_distinct(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, AgentRole.LEAD)
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-profit",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            artifact_refs=["working/queries/profit.sql"],
        )
    )
    prior = LeadResult(
        objective="Explain profitability.",
        answer="The cohort metric declined.",
        metric_comparisons=[_comparison(value=-0.2, context=_cohort_context())],
    )
    # A remediation that omits context is repaired to the original estimand.
    repaired = persist_lead_result(
        prior.model_copy(
            update={
                "answer": "The cohort metric was rechecked.",
                "metric_comparisons": [_comparison(value=-0.21)],
            }
        ),
        context,
        prior_result=prior,
    )
    assert repaired.metric_comparisons[0].definition_context == _cohort_context()

    distinct = persist_lead_result(
        LeadResult(
            objective="Explain profitability.",
            answer="A calendar comparison is also shown separately.",
            metric_comparisons=[_comparison(value=-0.1, context=_calendar_context())],
        ),
        context,
        prior_result=repaired,
    )
    assert {
        item.definition_context.date_basis
        for item in distinct.metric_comparisons
        if item.definition_context is not None
    } == {"acquisition_date", "order_date"}


def test_critic_reports_metric_scope_mismatch_instead_of_generic_conflict(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, AgentRole.CRITIC)
    evidence = _comparison(
        value=-0.1, context=_calendar_context(), evidence_ref="tool-profit"
    )
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-profit",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            output={"metric_comparisons": [evidence.model_dump(mode="json")]},
        )
    )
    candidate = CriticCandidate(
        objective="Explain profitability.",
        answer="The documented cohort metric declined.",
        metric_comparisons=[
            _comparison(
                value=-0.2, context=_cohort_context(), evidence_ref="tool-profit"
            )
        ],
    )

    result = asyncio.run(run_critic(context, candidate))

    assert result.status is ValidationStatus.REVISE
    assert result.issues[0].category == "metric_definition"
    assert "scope mismatch" in result.issues[0].message


def test_values_only_sql_cannot_be_sole_material_provenance(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pd.DataFrame({"customer_id": ["C1"]}).to_parquet(
        source / "customers.parquet", index=False
    )
    context = _context(tmp_path, AgentRole.CRITIC, inputs_source=source)
    query_path = context.workspace.working / "queries" / "hardcoded.sql"
    query_path.write_text(
        "SELECT * FROM (VALUES ('C1', 123.0)) AS values_only(customer_id, value)",
        encoding="utf-8",
    )
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-hardcoded",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            arguments={"query_path": "working/queries/hardcoded.sql"},
            artifact_refs=["working/queries/hardcoded.sql"],
        )
    )
    candidate = CriticCandidate(
        objective="Assess the measured value.",
        answer="The value is 123.",
        findings=[
            Finding(
                id="F1",
                statement="The measured value is 123.",
                metric="value",
                value=123.0,
                evidence_refs=["working/queries/hardcoded.sql"],
                confidence=ConfidenceLevel.HIGH,
            )
        ],
    )

    validation = validate_candidate_evidence_provenance(candidate, context.ledger)

    assert validation is not None
    assert validation.status is ValidationStatus.REVISE
    assert validation.issues[0].category == "evidence_provenance"


def test_values_only_cte_cannot_claim_an_approved_relation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pd.DataFrame({"customer_id": ["C1"]}).to_parquet(
        source / "customers.parquet", index=False
    )
    context = _context(tmp_path, AgentRole.CRITIC, inputs_source=source)
    query_path = context.workspace.working / "queries" / "cte.sql"
    query_path.write_text(
        "WITH customers AS (VALUES ('C1', 123.0)) SELECT * FROM customers",
        encoding="utf-8",
    )
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-cte",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            arguments={"query_path": "working/queries/cte.sql"},
            artifact_refs=["working/queries/cte.sql"],
        )
    )

    assert not validate_candidate_evidence_provenance(
        CriticCandidate(
            objective="Assess the measured value.",
            answer="The value is source-derived.",
            findings=[
                Finding(
                    id="F-CTE",
                    statement="The measured value is 123.",
                    metric="value",
                    value=123.0,
                    evidence_refs=["working/queries/cte.sql"],
                    confidence=ConfidenceLevel.HIGH,
                )
            ],
        ),
        context.ledger,
    )


def test_unrelated_registered_artifact_cannot_be_sole_material_provenance(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, AgentRole.CRITIC)
    artifact_path = context.workspace.working / "summary.csv"
    artifact_path.write_text("metric,value\ncac,0.3\n", encoding="utf-8")
    artifact = context.artifact_manager.register(
        "working/summary.csv",
        artifact_id="unrelated-summary",
    )

    validation = validate_candidate_evidence_provenance(
        CriticCandidate(
            objective="Assess the measured value.",
            answer="The value is supported by the summary.",
            findings=[
                Finding(
                    id="F-UNRELATED",
                    statement="CAC changed materially.",
                    metric="cac",
                    value=0.3,
                    evidence_refs=[artifact.id],
                    confidence=ConfidenceLevel.HIGH,
                )
            ],
        ),
        context.ledger,
    )

    assert validation is not None
    assert validation.issues[0].category == "evidence_provenance"


def test_source_derived_registered_artifact_retains_lineage(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pd.DataFrame({"customer_id": ["C1"]}).to_parquet(
        source / "customers.parquet", index=False
    )
    context = _context(tmp_path, AgentRole.CRITIC, inputs_source=source)
    script_path = context.workspace.working / "scripts" / "summary.py"
    script_path.write_text(
        "import pandas as pd\n"
        "pd.read_parquet('/workspace/inputs/customers.parquet').to_csv(\n"
        "    '/workspace/working/summary.csv', index=False\n"
        ")\n",
        encoding="utf-8",
    )
    summary_path = context.workspace.working / "summary.csv"
    summary_path.write_text("customer_id\nC1\n", encoding="utf-8")
    artifact = context.artifact_manager.register(
        "working/summary.csv",
        artifact_id="source-derived-summary",
    )
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-summary",
            tool_name="run_python",
            status=ToolEventStatus.SUCCEEDED,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            arguments={"script_path": "working/scripts/summary.py"},
            artifact_refs=["working/scripts/summary.py", "working/summary.csv"],
        )
    )

    assert (
        validate_candidate_evidence_provenance(
            CriticCandidate(
                objective="Assess the measured value.",
                answer="The source-derived value is supported.",
                findings=[
                    Finding(
                        id="F-DERIVED",
                        statement="The summary is source-derived.",
                        metric="customer_count",
                        value=1.0,
                        evidence_refs=[artifact.id],
                        confidence=ConfidenceLevel.HIGH,
                    )
                ],
            ),
            context.ledger,
        )
        is None
    )


def test_lead_gets_one_bounded_completion_pass_before_critic(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pd.DataFrame({"customer_id": ["C1"]}).to_parquet(
        source / "customers.parquet", index=False
    )
    pd.DataFrame({"session_id": ["S1"]}).to_parquet(
        source / "sessions.parquet", index=False
    )
    events: list[str] = []
    lead_calls = 0
    evidence_ref = "tool-funnel"
    comparison_values = {
        "marketing_spend": 0.07,
        "sessions": 0.01,
        "conversion_rate": -0.18,
        "acquired_customers": -0.18,
        "cac": 0.30,
        "ltv": 0.0,
    }

    def append_funnel_event(context: AgentRunContext) -> None:
        query_path = context.workspace.working / "queries" / "funnel.sql"
        query_path.parent.mkdir(parents=True, exist_ok=True)
        query_path.write_text("SELECT COUNT(*) FROM customers", encoding="utf-8")
        context.ledger.append_tool_event(
            ToolEvent(
                id=evidence_ref,
                tool_name="run_sql",
                status=ToolEventStatus.SUCCEEDED,
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
                completed_at=datetime(2026, 1, 1, tzinfo=UTC),
                arguments={"query_path": "working/queries/funnel.sql"},
                artifact_refs=["working/queries/funnel.sql"],
            )
        )

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        events.append("audit")
        return AuditResult(status=AuditStatus.COMPLETE)

    async def fake_lead(
        context,
        objective,
        *,
        business_context,
        audit,
        agent,
    ):  # noqa: ANN001
        nonlocal lead_calls
        lead_calls += 1
        events.append("lead")
        if lead_calls == 1:
            append_funnel_event(context)
        else:
            assert "Complete the candidate" in objective
            chart_path = context.workspace.outputs / "funnel.png"
            chart_path.write_bytes(b"chart")
            context.artifact_manager.register(
                "outputs/funnel.png",
                artifact_id="funnel-chart",
                kind=ArtifactKind.CHART,
                media_type="image/png",
                description="Acquisition funnel comparison.",
            )
        comparisons = (
            []
            if lead_calls == 1
            else [
                MetricComparison(
                    metric_key=metric,
                    dimensions={"channel": "Paid"},
                    baseline_period="Q1 2025",
                    comparison_period="Q2 2025",
                    comparison_type="relative_change",
                    value=value,
                    unit="fraction",
                    evidence_refs=[evidence_ref],
                )
                for metric, value in comparison_values.items()
            ]
        )
        return LeadResult(
            objective="Explain acquisition efficiency.",
            answer=(
                "Marketing spend, sessions, conversion, acquired customers, CAC, "
                "and downstream LTV/value were compared. The observed funnel "
                "mechanism is supported without an unsupported upstream causal claim."
            ),
            findings=[
                Finding(
                    id="F-CAC",
                    statement="CAC changed materially.",
                    metric="cac",
                    value=0.30,
                    evidence_refs=[evidence_ref],
                    confidence=ConfidenceLevel.HIGH,
                )
            ],
            metric_comparisons=comparisons,
            artifacts=[] if lead_calls == 1 else ["funnel-chart"],
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        events.append("critic")
        assert candidate.metric_comparisons
        assert candidate.artifacts == ["funnel-chart"]
        context.consume_budget("critic_loops")
        return ValidationResult(status=ValidationStatus.PASS)

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        budget=RunBudget(max_specialist_invocations=0, max_critic_loops=1),
        auditor_runner=fake_auditor,
        lead_runner=fake_lead,
        critic_runner=fake_critic,
    )
    result = asyncio.run(
        runner.run(
            "run-completion-pass",
            "Explain acquisition profitability.",
            inputs_source=source,
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert events == ["audit", "lead", "lead", "critic"]
    assert lead_calls == 2
    assert result.ledger is not None
    assert result.ledger.get_artifact("funnel-chart") is not None


def test_source_relation_sql_has_material_lineage(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pd.DataFrame({"customer_id": ["C1"]}).to_parquet(
        source / "customers.parquet", index=False
    )
    workspace = WorkspaceManager(tmp_path / "source-workspaces").create_workspace(
        "source-lineage",
        inputs_source=source,
    )
    ledger = AnalysisLedger(workspace, objective="Assess the measured value.")
    query_path = workspace.working / "queries" / "source.sql"
    query_path.write_text('SELECT COUNT(*) FROM "customers"', encoding="utf-8")
    ledger.append_tool_event(
        ToolEvent(
            id="tool-source",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            arguments={"query_path": "working/queries/source.sql"},
            artifact_refs=["working/queries/source.sql"],
        )
    )
    candidate = CriticCandidate(
        objective="Assess the measured value.",
        answer="The source-derived value is supported.",
        findings=[
            Finding(
                id="F1",
                statement="The source-derived value is one.",
                metric="value",
                value=1.0,
                evidence_refs=["working/queries/source.sql"],
                confidence=ConfidenceLevel.HIGH,
            )
        ],
    )

    assert validate_candidate_evidence_provenance(candidate, ledger) is None


def test_selected_specialist_comparisons_carry_into_lead_result(
    tmp_path: Path,
) -> None:
    analyst_context = _context(tmp_path, AgentRole.ANALYST)
    evidence = "working/scripts/funnel.py"
    (analyst_context.workspace.working / "scripts" / "funnel.py").write_text(
        "print('source-derived')\n",
        encoding="utf-8",
    )
    analyst_context.ledger.append_tool_event(
        ToolEvent(
            id="tool-funnel",
            tool_name="run_python",
            status=ToolEventStatus.SUCCEEDED,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            artifact_refs=[evidence],
        )
    )
    comparisons = [
        MetricComparison(
            metric_key=metric,
            dimensions={"channel": "Paid"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type="relative_change",
            value=value,
            unit="fraction",
            evidence_refs=[evidence],
        )
        for metric, value in (
            ("marketing_spend", 0.07),
            ("sessions", 0.01),
            ("conversion_rate", -0.18),
            ("acquired_customers", -0.18),
            ("cac", 0.30),
            ("ltv", 0.0),
        )
    ]
    persisted = persist_analyst_result(
        SpecialistResult(
            objective="Measure the acquisition funnel.", metric_comparisons=comparisons
        ),
        analyst_context,
    )
    analyst_context.ledger.record_specialist_result("analyst", persisted)

    lead = persist_lead_result(
        LeadResult(
            objective="Explain acquisition efficiency.",
            answer=(
                "The selected acquisition finding is supported by the funnel evidence."
            ),
            findings=[
                Finding(
                    id=f"F-{metric}",
                    statement=f"{metric} changed.",
                    metric=metric,
                    value=value,
                    evidence_refs=[evidence],
                    confidence=ConfidenceLevel.HIGH,
                )
                for metric, value in (
                    ("marketing_spend", 0.07),
                    ("sessions", 0.01),
                    ("conversion_rate", -0.18),
                    ("acquired_customers", -0.18),
                    ("cac", 0.30),
                    ("ltv", 0.0),
                )
            ],
        ),
        _context_from_ledger(analyst_context, AgentRole.LEAD),
    )

    assert {item.metric_key for item in lead.metric_comparisons} == {
        "marketing_spend",
        "sessions",
        "conversion_rate",
        "acquired_customers",
        "cac",
        "ltv",
    }
    assert [item.value for item in lead.metric_comparisons] == [
        item.value for item in comparisons
    ]


def _context_from_ledger(
    original: AgentRunContext,
    role: AgentRole,
) -> AgentRunContext:
    return AgentRunContext(
        workspace=original.workspace,
        ledger=original.ledger,
        sql_service=DuckDBExecutionService(original.workspace, original.ledger),
        python_service=PythonExecutionService(original.workspace, original.ledger),
        artifact_manager=ArtifactManager(original.workspace, original.ledger),
        run_config=AgentRunConfig(
            run_id=original.ledger.state.run_id,
            agent_role=role,
            model="test-model",
        ),
    )


def test_chart_completeness_is_generic_and_budget_aware(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pd.DataFrame({"customer_id": ["C1"]}).to_parquet(
        source / "customers.parquet", index=False
    )
    pd.DataFrame({"session_id": ["S1"]}).to_parquet(
        source / "sessions.parquet", index=False
    )
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "chart-completeness",
        inputs_source=source,
    )
    ledger = AnalysisLedger(workspace, objective="Explain acquisition efficiency.")
    context = _context_from_ledger(
        AgentRunContext(
            workspace=workspace,
            ledger=ledger,
            sql_service=DuckDBExecutionService(workspace, ledger),
            python_service=PythonExecutionService(workspace, ledger),
            artifact_manager=ArtifactManager(workspace, ledger),
            run_config=AgentRunConfig(
                run_id="chart-completeness",
                agent_role=AgentRole.CRITIC,
                model="test-model",
            ),
        ),
        AgentRole.CRITIC,
    )
    candidate = CriticCandidate(
        objective="Explain acquisition efficiency.",
        answer=(
            "Spend moved through sessions, conversion, acquired customers, CAC, "
            "and LTV."
        ),
        structured_metrics_required=True,
        visualization_requested=True,
        metric_comparisons=[
            MetricComparison(
                metric_key=metric,
                dimensions={"channel": "Paid"},
                baseline_period="Q1 2025",
                comparison_period="Q2 2025",
                comparison_type="relative_change",
                value=0.0,
                unit="fraction",
                evidence_refs=["tool-funnel"],
            )
            for metric in (
                "marketing_spend",
                "sessions",
                "conversion_rate",
                "acquired_customers",
                "cac",
                "ltv",
            )
        ],
    )

    validation = candidate_completeness_validation(candidate, context=context)

    assert validation is not None
    assert validation.status is ValidationStatus.REVISE
    assert any(issue.category == "chart_completeness" for issue in validation.issues)
