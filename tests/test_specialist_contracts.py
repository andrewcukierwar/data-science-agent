"""Regression tests for specialist output identity and evidence contracts."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import AgentRole, AgentRunConfig, AgentRunContext
from agents.analyst import persist_analyst_result, validate_analyst_result
from agents.critic import Runner as CriticRunner
from agents.critic import run_critic
from agents.lead import persist_lead_result
from agents.statistician import (
    persist_statistician_result,
    validate_statistician_result,
)
from orchestration.ledger import AnalysisLedger
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.lead import LeadResult
from schemas.metrics import MetricComparison
from schemas.run_state import ToolEvent, ToolEventStatus
from schemas.validation import CriticCandidate, ValidationResult, ValidationStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def test_analyst_and_statistician_local_f1_ids_are_namespaced(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-specialist-contracts"
    )
    ledger = AnalysisLedger(workspace, objective="Test specialist finding identity.")
    evidence_path = workspace.working / "scripts" / "evidence.py"
    evidence_path.write_text("print('evidence')\n", encoding="utf-8")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    ledger.append_tool_event(
        ToolEvent(
            id="tool-evidence",
            tool_name="run_python",
            status=ToolEventStatus.SUCCEEDED,
            started_at=timestamp,
            completed_at=timestamp,
            artifact_refs=["working/scripts/evidence.py"],
        )
    )
    context = AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-specialist-contracts",
            agent_role=AgentRole.ANALYST,
            model="test-model",
        ),
    )
    analyst_result = SpecialistResult(
        objective="Measure the business change.",
        findings=[
            Finding(
                id="F1",
                statement="The business metric changed.",
                metric="metric_change",
                value=0.2,
                evidence_refs=["working/scripts/evidence.py"],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
    )
    statistician_result = analyst_result.model_copy(
        update={"objective": "Assess whether the business change is meaningful."}
    )

    persisted_analyst = persist_analyst_result(analyst_result, context)
    persisted_statistician = persist_statistician_result(
        statistician_result,
        context,
    )
    ledger.record_specialist_result(AgentRole.ANALYST.value, persisted_analyst)
    ledger.record_specialist_result(
        AgentRole.STATISTICIAN.value,
        persisted_statistician,
    )
    reloaded = AnalysisLedger(ledger.state_path)

    assert persisted_analyst.findings[0].id == "analyst:F1"
    assert persisted_statistician.findings[0].id == "statistician:F1"
    assert [finding.id for finding in reloaded.findings] == [
        "analyst:F1",
        "statistician:F1",
    ]
    assert [record.result.findings[0].id for record in reloaded.specialist_results] == [
        "analyst:F1",
        "statistician:F1",
    ]


def test_specialist_metric_comparisons_persist_and_lead_reuses_exact_value(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-specialist-comparisons"
    )
    evidence_path = workspace.working / "scripts" / "comparison.py"
    evidence_path.write_text("print('comparison')\n", encoding="utf-8")
    ledger = AnalysisLedger(workspace, objective="Preserve metric comparisons.")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    ledger.append_tool_event(
        ToolEvent(
            id="tool-comparison",
            tool_name="run_python",
            status=ToolEventStatus.SUCCEEDED,
            started_at=timestamp,
            completed_at=timestamp,
            artifact_refs=["working/scripts/comparison.py"],
        )
    )
    analyst_context = AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-specialist-comparisons",
            agent_role=AgentRole.ANALYST,
            model="test-model",
        ),
    )
    comparison = MetricComparison(
        metric_key="cac",
        dimensions={"channel": "Paid"},
        baseline_period="Q1 2025",
        comparison_period="Q2 2025",
        comparison_type="relative_change",
        value=0.3,
        unit="relative_change_fraction",
        evidence_refs=["working/scripts/comparison.py"],
    )
    specialist_result = SpecialistResult(
        objective="Measure acquisition efficiency.",
        metric_comparisons=[comparison],
    )
    persisted_specialist = persist_analyst_result(specialist_result, analyst_context)
    ledger.record_specialist_result(AgentRole.ANALYST.value, persisted_specialist)

    lead_context = AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-specialist-comparisons",
            agent_role=AgentRole.LEAD,
            model="test-model",
        ),
    )
    reconstructed = comparison.model_copy(update={"value": 0.99})
    lead_result = persist_lead_result(
        LeadResult(
            objective="Explain acquisition efficiency.",
            answer="The specialist comparison supports the conclusion.",
            metric_comparisons=[reconstructed, reconstructed],
        ),
        lead_context,
    )
    reloaded = AnalysisLedger(ledger.state_path)

    assert lead_result.metric_comparisons == [comparison]
    assert reloaded.metric_comparisons == [comparison]
    assert reloaded.specialist_results[0].result.metric_comparisons == [comparison]


def test_acquisition_comparisons_survive_specialists_lead_and_critic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-acquisition-comparisons"
    )
    evidence_path = workspace.working / "scripts" / "funnel.py"
    evidence_path.write_text("print('funnel')\n", encoding="utf-8")
    ledger = AnalysisLedger(workspace, objective="Explain acquisition efficiency.")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    raw_comparisons = [
        MetricComparison(
            metric_key=metric_key,
            dimensions={"acquisition_channel": "Meta"},
            baseline_period="2025 Q1",
            comparison_period="Q2 2025",
            comparison_type="relative_change",
            value=value,
            unit="fraction",
            evidence_refs=["working/scripts/funnel.py"],
        )
        for metric_key, value in (
            ("meta_conversion", -0.18),
            ("meta_new_customers", -0.18),
            ("meta_spend", 0.07),
            ("meta_customer_acquisition_cost", 0.30),
            ("meta_90_day_ltv", 0.0),
        )
    ]
    ledger.append_tool_event(
        ToolEvent(
            id="tool-funnel",
            tool_name="run_python",
            status=ToolEventStatus.SUCCEEDED,
            started_at=timestamp,
            completed_at=timestamp,
            output={
                "metric_comparisons": [
                    comparison.model_dump(mode="json") for comparison in raw_comparisons
                ]
            },
            artifact_refs=["working/scripts/funnel.py"],
        )
    )

    def context(role: AgentRole) -> AgentRunContext:
        return AgentRunContext(
            workspace=workspace,
            ledger=ledger,
            sql_service=DuckDBExecutionService(workspace, ledger),
            python_service=PythonExecutionService(workspace, ledger),
            artifact_manager=ArtifactManager(workspace, ledger),
            run_config=AgentRunConfig(
                run_id="run-acquisition-comparisons",
                agent_role=role,
                model="test-model",
            ),
        )

    analyst_result = persist_analyst_result(
        SpecialistResult(
            objective="Measure the acquisition funnel.",
            metric_comparisons=raw_comparisons[:3],
        ),
        context(AgentRole.ANALYST),
    )
    statistician_result = persist_statistician_result(
        SpecialistResult(
            objective="Assess downstream customer value.",
            metric_comparisons=raw_comparisons[3:],
        ),
        context(AgentRole.STATISTICIAN),
    )
    ledger.record_specialist_result(AgentRole.ANALYST.value, analyst_result)
    ledger.record_specialist_result(
        AgentRole.STATISTICIAN.value,
        statistician_result,
    )

    lead_result = persist_lead_result(
        LeadResult(
            objective="Explain acquisition efficiency.",
            answer="The funnel comparisons support the observed acquisition change.",
            metric_comparisons=[
                comparison.model_copy(update={"value": comparison.value + 0.5})
                for comparison in raw_comparisons
            ],
        ),
        context(AgentRole.LEAD),
    )

    validation = ValidationResult(
        status=ValidationStatus.PASS,
        summary="Structured acquisition comparisons are supported.",
    )

    async def fake_run(agent, prompt, *, context, **kwargs):  # noqa: ANN001
        return SimpleNamespace(final_output=validation)

    monkeypatch.setattr(CriticRunner, "run", fake_run)
    candidate = CriticCandidate(
        objective="Explain acquisition efficiency.",
        answer="The funnel comparisons support the observed acquisition change.",
        metric_comparisons=lead_result.metric_comparisons,
    )
    critic_result = asyncio.run(run_critic(context(AgentRole.CRITIC), candidate))

    assert critic_result.status is ValidationStatus.PASS
    assert [item.metric_key for item in lead_result.metric_comparisons] == [
        "conversion_rate",
        "acquired_customers",
        "marketing_spend",
        "cac",
        "ltv",
    ]
    assert all(
        not item.metric_key.startswith("meta_")
        for item in lead_result.metric_comparisons
    )
    assert [item.value for item in lead_result.metric_comparisons] == [
        comparison.value for comparison in raw_comparisons
    ]


def test_specialist_finding_reference_is_resolved_to_executed_evidence(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-specialist-evidence"
    )
    ledger = AnalysisLedger(workspace, objective="Test specialist evidence reuse.")
    evidence_path = workspace.working / "scripts" / "evidence.py"
    evidence_path.write_text("print('evidence')\n", encoding="utf-8")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    ledger.append_tool_event(
        ToolEvent(
            id="tool-prior-evidence",
            tool_name="run_python",
            status=ToolEventStatus.SUCCEEDED,
            started_at=timestamp,
            completed_at=timestamp,
            artifact_refs=["working/scripts/evidence.py"],
        )
    )
    prior = Finding(
        id="statistician:H1",
        statement="The prior statistical result is supported.",
        metric="effect_size",
        value=0.2,
        evidence_refs=["working/scripts/evidence.py"],
        confidence=ConfidenceLevel.MEDIUM,
    )
    ledger.add_finding(prior)
    candidate = SpecialistResult(
        objective="Follow up on the prior result.",
        findings=[
            prior.model_copy(
                update={
                    "id": "F1",
                    "statement": "The follow-up confirms the prior result.",
                    "evidence_refs": ["statistician:H1"],
                }
            )
        ],
    )

    analyst_validated = validate_analyst_result(candidate, ledger)
    statistician_validated = validate_statistician_result(candidate, ledger)

    assert analyst_validated.findings[0].evidence_refs == [
        "working/scripts/evidence.py"
    ]
    assert statistician_validated.findings[0].evidence_refs == [
        "working/scripts/evidence.py"
    ]
