"""Regression tests for specialist output identity and evidence contracts."""

from datetime import UTC, datetime
from pathlib import Path

from agents import AgentRole, AgentRunConfig, AgentRunContext
from agents.analyst import persist_analyst_result, validate_analyst_result
from agents.statistician import (
    persist_statistician_result,
    validate_statistician_result,
)
from orchestration.ledger import AnalysisLedger
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.run_state import ToolEvent, ToolEventStatus
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
