"""Deterministic construction and boundary tests for the Lead manager."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext

from agents import (
    LEAD_OBJECTIVE,
    Agent,
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    LeadEvidenceError,
    ToolOutputText,
    ToolResponse,
    build_lead_agent,
    record_hypothesis,
    record_open_question,
    run_lead,
    update_investigation_plan,
    validate_lead_result,
)
from agents.lead import Runner, _canonical_specialist_output, _NestedSpecialistHooks
from orchestration.budgets import BudgetExhaustedError
from orchestration.ledger import AnalysisLedger
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.lead import LeadRecommendation, LeadResult
from schemas.run_state import (
    Hypothesis,
    HypothesisStatus,
    RunBudget,
    ToolEvent,
    ToolEventStatus,
)
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def test_lead_uses_manager_tools_and_has_no_computational_tools() -> None:
    agent = build_lead_agent(model="test-model")
    names = [tool.name for tool in agent.tools]

    assert agent.name == "Lead Data Scientist"
    assert agent.model == "test-model"
    assert agent.output_type is LeadResult
    assert agent.handoffs == []
    assert names[:6] == [
        "inspect_workspace",
        "read_document",
        "save_artifact",
        "update_investigation_plan",
        "record_hypothesis",
        "record_open_question",
    ]
    assert names[-3:] == [
        "delegate_to_data_auditor",
        "delegate_to_analyst",
        "delegate_to_statistician",
    ]
    assert "run_sql" not in names
    assert "run_python" not in names

    for tool in agent.tools[-3:]:
        assert "objective" in tool.params_json_schema["properties"]
        assert "required_outputs" in tool.params_json_schema["properties"]


def test_lead_requires_lead_context_and_specialists_cannot_delegate() -> None:
    config = AgentRunConfig(
        run_id="run-lead",
        agent_role=AgentRole.ANALYST,
        model="test-model",
    )
    with pytest.raises(ValueError, match="Lead"):
        build_lead_agent(config)

    specialist_with_handoff = Agent(
        name="Unsafe specialist",
        model="test-model",
        handoffs=[Agent(name="Nested", model="test-model")],
    )
    with pytest.raises(ValueError, match="cannot have handoffs"):
        build_lead_agent(model="test-model", analyst=specialist_with_handoff)


def test_lead_instructions_require_plan_hypotheses_follow_up_and_provenance() -> None:
    instructions = build_lead_agent(model="test-model").instructions.lower()
    for term in (
        "investigation plan",
        "hypotheses",
        "delegate",
        "structured",
        "follow-up",
        "evidence_refs",
        "raw computation",
        "no sql",
        "no python",
        "unsupported causal",
    ):
        assert term in instructions


def test_lead_follow_up_decision_requires_rationale() -> None:
    with pytest.raises(ValueError, match="follow_up_rationale"):
        LeadResult(
            objective="Explain the change.",
            answer="More evidence is needed.",
            follow_up_analysis=True,
        )


def _context(tmp_path: Path) -> AgentRunContext:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("run-lead")
    ledger = AnalysisLedger(workspace, objective=LEAD_OBJECTIVE)
    ledger.update_budget(RunBudget(max_specialist_invocations=2))
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-lead",
            agent_role=AgentRole.LEAD,
            model="test-model",
        ),
    )


def _invoke(tool, context: AgentRunContext, arguments: dict) -> ToolResponse:  # noqa: ANN001
    payload = json.dumps(arguments)
    wrapper = ToolContext(
        context,
        tool_name=tool.name,
        tool_call_id=f"call-{tool.name}",
        tool_arguments=payload,
    )
    result = asyncio.run(tool.on_invoke_tool(wrapper, payload))
    assert isinstance(result, ToolOutputText)
    return ToolResponse.model_validate_json(result.text)


def test_lead_state_tools_persist_through_bound_context(tmp_path: Path) -> None:
    context = _context(tmp_path)
    plan = _invoke(
        update_investigation_plan,
        context,
        {"steps": ["Read definitions", "Delegate a bounded decomposition"]},
    )
    assert plan.success is True

    hypothesis = Hypothesis(
        id="H001",
        statement="Paid acquisition efficiency declined.",
    )
    recorded = _invoke(
        record_hypothesis,
        context,
        {"hypothesis": hypothesis.model_dump(mode="json")},
    )
    question = _invoke(
        record_open_question,
        context,
        {"question": "Did the change persist in the next period?"},
    )

    assert recorded.success is True
    assert question.success is True
    reloaded = AnalysisLedger(context.ledger.state_path)
    assert reloaded.state.investigation_plan == [
        "Read definitions",
        "Delegate a bounded decomposition",
    ]
    assert reloaded.hypotheses[0].id == "H001"
    assert reloaded.state.open_questions == [
        "Did the change persist in the next period?"
    ]


def _evidence_context(tmp_path: Path) -> AgentRunContext:
    context = _context(tmp_path)
    timestamp = ToolEvent(
        id="tool-Q001",
        tool_name="run_sql",
        status=ToolEventStatus.SUCCEEDED,
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:00:01Z",
        artifact_refs=["working/queries/Q001.sql"],
    )
    context.ledger.append_tool_event(timestamp)
    return context


def test_lead_result_requires_evidence_for_recommendations_and_resolved_hypotheses(
    tmp_path: Path,
) -> None:
    context = _evidence_context(tmp_path)
    result = LeadResult(
        objective="Explain the change.",
        answer="Acquisition efficiency was the primary observed change.",
        findings=[
            Finding(
                id="F001",
                statement="CAC increased.",
                metric="cac",
                value=1.2,
                evidence_refs=["working/queries/Q001.sql"],
                confidence=ConfidenceLevel.HIGH,
            )
        ],
        recommendations=[
            LeadRecommendation(
                id="R001",
                statement="Investigate the acquisition funnel.",
                evidence_refs=["tool-Q001"],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
        hypotheses=[
            Hypothesis(
                id="H001",
                statement="Acquisition efficiency declined.",
                status=HypothesisStatus.SUPPORTED,
                evidence_refs=["tool-Q001"],
            )
        ],
    )
    assert validate_lead_result(result, context.ledger) == result

    invalid = result.model_copy(
        update={
            "recommendations": [
                LeadRecommendation(
                    id="R002",
                    statement="Increase spend immediately.",
                    evidence_refs=["not-executed"],
                    confidence=ConfidenceLevel.LOW,
                )
            ]
        }
    )
    with pytest.raises(LeadEvidenceError, match="recommendation:R002"):
        validate_lead_result(invalid, context.ledger)


def test_run_lead_consumes_typed_output_and_persists_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _evidence_context(tmp_path)
    result = LeadResult(
        objective="Explain the change.",
        answer="The observed change is supported by the query evidence.",
        findings=[
            Finding(
                id="F001",
                statement="The investigated metric changed.",
                metric="metric",
                value=1.0,
                evidence_refs=["tool-Q001"],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
    )

    async def fake_run(agent, prompt, *, context, **kwargs):  # noqa: ANN001
        assert agent.output_type is LeadResult
        assert prompt == "Explain the change."
        assert context.agent_role is AgentRole.LEAD
        assert all(tool.name not in {"run_sql", "run_python"} for tool in agent.tools)
        return SimpleNamespace(final_output=result)

    monkeypatch.setattr(Runner, "run", fake_run)
    returned = asyncio.run(run_lead(context, "Explain the change."))

    assert returned == result
    reloaded = AnalysisLedger(context.ledger.state_path)
    assert reloaded.findings == result.findings


def test_lead_specialist_tool_channel_returns_canonical_finding_ids() -> None:
    result = SpecialistResult(
        objective="Measure a bounded change.",
        findings=[
            Finding(
                id="F1",
                statement="The measured value changed.",
                metric="change",
                value=0.1,
                evidence_refs=["tool-evidence"],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
    )
    extractor = _canonical_specialist_output(AgentRole.ANALYST)

    returned = asyncio.run(extractor(SimpleNamespace(final_output=result)))
    parsed = SpecialistResult.model_validate_json(returned)

    assert parsed.findings[0].id == "analyst:F1"


def test_nested_specialist_hook_enforces_budget_and_restores_lead_role(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    hooks = _NestedSpecialistHooks(AgentRole.ANALYST)
    hook_context = SimpleNamespace(context=context)

    asyncio.run(hooks.on_agent_start(hook_context, Agent(name="Analyst")))
    assert context.agent_role is AgentRole.ANALYST
    assert context.ledger.budget.specialist_invocations == 1
    asyncio.run(hooks.on_agent_end(hook_context, Agent(name="Analyst"), {}))
    assert context.agent_role is AgentRole.LEAD

    context.ledger.update_budget(
        RunBudget(max_specialist_invocations=1, specialist_invocations=1)
    )
    with pytest.raises(BudgetExhaustedError, match="specialist_invocations"):
        asyncio.run(hooks.on_agent_start(hook_context, Agent(name="Analyst")))


def test_nested_statistician_persists_artifacts_like_standalone_run(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    script = context.workspace.working / "scripts" / "nested_stats.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("print('nested statistical result')\n", encoding="utf-8")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-nested-statistics",
            tool_name="run_python",
            status=ToolEventStatus.SUCCEEDED,
            started_at=timestamp,
            completed_at=timestamp,
            artifact_refs=["working/scripts/nested_stats.py"],
        )
    )
    result = SpecialistResult(
        objective="Assess the nested statistical question.",
        findings=[
            Finding(
                id="S-NESTED",
                statement="The measured difference is supported by the evidence.",
                metric="mean_difference",
                value=0.8,
                evidence_refs=["working/scripts/nested_stats.py"],
                confidence=ConfidenceLevel.HIGH,
            )
        ],
        artifacts=["working/scripts/nested_stats.py"],
    )
    hooks = _NestedSpecialistHooks(AgentRole.STATISTICIAN)
    hook_context = SimpleNamespace(
        context=context,
        tool_input={"objective": "Assess the nested statistical question."},
    )
    agent = Agent(name="Statistician", model="test-model")

    asyncio.run(hooks.on_agent_start(hook_context, agent))
    asyncio.run(hooks.on_agent_end(hook_context, agent, result))

    reloaded = AnalysisLedger(context.ledger.state_path)
    assert context.agent_role is AgentRole.LEAD
    assert reloaded.findings[0].id == "statistician:S-NESTED"
    assert reloaded.findings[0].statement == result.findings[0].statement
    assert len(reloaded.artifacts) == 1
    assert reloaded.artifacts[0].path == "working/scripts/nested_stats.py"
    assert reloaded.specialist_results[0].agent_role == AgentRole.STATISTICIAN
