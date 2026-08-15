"""Deterministic construction and persistence tests for the Data Auditor."""

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import (
    DATA_AUDITOR_OBJECTIVE,
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    build_data_auditor_agent,
    run_data_auditor,
)
from agents.auditor import Runner
from orchestration.ledger import AnalysisLedger
from schemas.audit import AuditResult, AuditStatus, DateRange, TableAudit
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def test_data_auditor_is_structured_and_cannot_delegate() -> None:
    agent = build_data_auditor_agent(model="test-model")

    assert agent.name == "Data Auditor"
    assert agent.model == "test-model"
    assert agent.output_type is AuditResult
    assert agent.handoffs == []
    assert [tool.name for tool in agent.tools] == [
        "inspect_workspace",
        "read_document",
        "run_sql",
        "run_python",
    ]


def test_data_auditor_rejects_non_auditor_run_configuration() -> None:
    config = AgentRunConfig(
        run_id="run-auditor",
        agent_role=AgentRole.ANALYST,
        model="test-model",
    )

    with pytest.raises(ValueError, match="Data Auditor"):
        build_data_auditor_agent(config)


def test_data_auditor_instructions_cover_preflight_checks() -> None:
    agent = build_data_auditor_agent(model="test-model")
    instructions = agent.instructions.lower()

    for term in (
        "schema",
        "types",
        "row counts",
        "date coverage",
        "missingness",
        "duplicate",
        "relationships",
        "temporal gaps",
        "anomalies",
        "business definitions",
        "delegate",
        "user-facing report",
    ):
        assert term in instructions


def _context(tmp_path: Path) -> AgentRunContext:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-auditor"
    )
    ledger = AnalysisLedger(workspace, objective=DATA_AUDITOR_OBJECTIVE)
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-auditor",
            agent_role=AgentRole.DATA_AUDITOR,
            model="test-model",
        ),
    )


def test_data_auditor_persists_typed_result_in_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    audited_at = datetime(2026, 1, 1, tzinfo=UTC)
    audit = AuditResult(
        status=AuditStatus.COMPLETE,
        tables=[
            TableAudit(
                table_name="orders",
                row_count=10,
                date_range=DateRange(
                    start=date(2025, 1, 1),
                    end=date(2025, 1, 10),
                ),
            )
        ],
        audited_at=audited_at,
    )

    async def fake_run(agent, objective, *, context, **kwargs):  # noqa: ANN001
        assert agent.output_type is AuditResult
        assert objective == DATA_AUDITOR_OBJECTIVE
        assert context is not None
        return SimpleNamespace(final_output=audit)

    monkeypatch.setattr(Runner, "run", fake_run)

    result = asyncio.run(run_data_auditor(context))
    reloaded = AnalysisLedger(context.ledger.state_path)

    assert result == audit
    assert context.ledger.audit == audit
    assert reloaded.audit == audit
    assert context.ledger.budget.specialist_invocations == 1
