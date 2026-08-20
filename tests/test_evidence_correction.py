"""R23 regressions: one bounded correction for a semantic provenance failure.

The 2026-08-20 multi-agent canary returned well-formed JSON whose hypothesis
cited ``completed_data_audit`` — a valid document making an unsupported claim.
Terminating there threw away an entire run over a citation the model could have
fixed; retrying the whole run would have been resampling until a favourable
output appeared. These tests pin the narrow path between the two: exactly one
correction attempt, no new execution, both calls observable, and an explicit
provenance failure if the second response is still invalid.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agents import (
    CORRECTION_TURN_LIMIT,
    DEFAULT_EVIDENCE_CORRECTION_ATTEMPTS,
    EVIDENCE_CORRECTION_INSTRUCTIONS,
    MAX_EVIDENCE_CORRECTION_ATTEMPTS,
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    LeadEvidenceError,
    build_correction_prompt,
    build_evidence_correction_agent,
    build_evidence_correction_catalog,
    persist_audit_result,
    run_generalist,
    run_lead,
)
from agents.model_usage import Runner
from orchestration.ledger import AnalysisLedger
from schemas.audit import AuditObservation, AuditResult, AuditStatus, TableAudit
from schemas.findings import ConfidenceLevel, Finding
from schemas.generalist import GeneralistResult
from schemas.lead import LeadRecommendation, LeadResult
from schemas.run_state import (
    AgentEventStatus,
    Hypothesis,
    HypothesisStatus,
    RunBudget,
    RunStatus,
    ToolEvent,
    ToolEventStatus,
)
from schemas.validation import ValidationResult, ValidationStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
_SQL_EVENT = "tool-Q001"
_SQL_PATH = "working/queries/Q001.sql"
_BAD_REF = "completed_data_audit"


def _context(
    tmp_path: Path,
    *,
    role: AgentRole = AgentRole.LEAD,
    correction_attempts: int = DEFAULT_EVIDENCE_CORRECTION_ATTEMPTS,
) -> AgentRunContext:
    inputs_source = tmp_path / "inputs-source"
    inputs_source.mkdir(exist_ok=True)
    pd.DataFrame({"order_id": ["O1", "O2"]}).to_parquet(
        inputs_source / "orders.parquet"
    )
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-c",
        inputs_source=inputs_source,
    )
    ledger = AnalysisLedger(workspace, objective="Explain the change.")
    ledger.begin_attempt()
    ledger.update_budget(RunBudget())
    path = workspace.root / _SQL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("SELECT count(*) FROM orders;\n", encoding="utf-8")
    ledger.append_tool_event(
        ToolEvent(
            id=_SQL_EVENT,
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q001", "query_path": _SQL_PATH},
            artifact_refs=[_SQL_PATH],
        )
    )
    ledger.upsert_finding(
        Finding(
            id="analyst:F1",
            statement="Orders fell 12% in the second period.",
            metric="orders",
            value=-0.12,
            evidence_refs=[_SQL_PATH],
            confidence=ConfidenceLevel.HIGH,
        )
    )
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-c",
            agent_role=role,
            model="test-model",
            evidence_correction_attempts=correction_attempts,
        ),
    )


def _candidate(refs: list[str]) -> LeadResult:
    return LeadResult(
        objective="Explain the change.",
        answer="Order volume fell in the second period.",
        findings=[
            Finding(
                id="L1",
                statement="Orders fell 12% in the second period.",
                metric="orders",
                value=-0.12,
                evidence_refs=list(refs),
                confidence=ConfidenceLevel.HIGH,
            )
        ],
        recommendations=[
            LeadRecommendation(
                id="R1",
                statement="Investigate the order decline.",
                evidence_refs=list(refs),
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
        hypotheses=[
            Hypothesis(
                id="H2",
                statement="Order volume drove the change.",
                status=HypothesisStatus.SUPPORTED,
                evidence_refs=list(refs),
            )
        ],
    )


class _ScriptedRunner:
    """Return one queued final output per ``Runner.run`` call."""

    def __init__(self, *outputs: object) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[str, object, int]] = []

    async def __call__(self, agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        self.calls.append((agent.name, agent_input, kwargs.get("max_turns")))
        self.agents = getattr(self, "agents", [])
        self.agents.append(agent)
        if not self.outputs:
            raise AssertionError("Runner.run was called more times than scripted")
        return SimpleNamespace(final_output=self.outputs.pop(0))


def _agent_events(context: AgentRunContext):
    return AnalysisLedger(context.ledger.state_path).agent_events


# --- the correction is bounded by construction --------------------------------


def test_correction_allowance_is_one_and_cannot_be_configured_higher() -> None:
    assert DEFAULT_EVIDENCE_CORRECTION_ATTEMPTS == 1
    assert MAX_EVIDENCE_CORRECTION_ATTEMPTS == 1
    assert CORRECTION_TURN_LIMIT == 1

    with pytest.raises(ValueError, match="less than or equal to 1"):
        AgentRunConfig(
            run_id="run-c",
            agent_role=AgentRole.LEAD,
            evidence_correction_attempts=2,
        )


def test_correction_agent_has_no_tools_and_cannot_delegate() -> None:
    agent = build_evidence_correction_agent(
        LeadResult,
        model="test-model",
        agent_name="Lead Data Scientist (evidence correction)",
    )

    assert agent.tools == []
    assert agent.handoffs == []
    assert agent.output_type.output_type is LeadResult
    assert agent.output_type.is_strict_json_schema() is True
    instructions = " ".join(EVIDENCE_CORRECTION_INSTRUCTIONS.lower().split())
    assert "you have no tools" in instructions
    assert "change only evidence_refs" in instructions
    assert "never invent a reference" in instructions


# --- the correction prompt is specific and bounded ---------------------------


def test_correction_prompt_names_the_invalid_fields_and_available_evidence(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    audit = persist_audit_result(
        AuditResult(
            status=AuditStatus.COMPLETE,
            tables=[
                TableAudit(
                    table_name="orders",
                    row_count=1200,
                    evidence_refs=[_SQL_EVENT],
                )
            ],
            limitations=[
                AuditObservation(
                    statement="Refund reasons are unavailable.",
                    evidence_refs=[_SQL_EVENT],
                )
            ],
            audited_at=_STAMP,
        ),
        context,
    )
    from agents import build_audit_evidence_catalog

    catalog = build_evidence_correction_catalog(
        context.ledger,
        audit_evidence=build_audit_evidence_catalog(audit, context.ledger),
    )
    prompt = build_correction_prompt(
        _candidate([_BAD_REF]),
        invalid_fields=("finding:L1", "hypothesis:H2"),
        reason="lead outputs cite no executed evidence: finding:L1, hypothesis:H2",
        catalog=catalog,
    )

    assert "INVALID_OUTPUT_FIELDS:" in prompt
    assert "- finding:L1" in prompt
    assert "- hypothesis:H2" in prompt
    assert _SQL_EVENT in prompt
    assert _SQL_PATH in prompt
    assert "analyst:F1" in prompt
    assert "PREVIOUS_OUTPUT_JSON:" in prompt
    assert "audit:table:0" in prompt


def test_correction_catalog_is_bounded(tmp_path: Path) -> None:
    context = _context(tmp_path)
    for index in range(120):
        context.ledger.append_tool_event(
            ToolEvent(
                id=f"tool-extra-{index:03d}",
                tool_name="run_sql",
                status=ToolEventStatus.SUCCEEDED,
                started_at=_STAMP,
                completed_at=_STAMP,
                arguments={"query_id": f"X{index:03d}"},
            )
        )

    catalog = build_evidence_correction_catalog(context.ledger)

    assert len(catalog.executed_references) == 80
    assert catalog.truncated is True


def test_correction_catalog_carries_no_evaluator_or_internal_state(
    tmp_path: Path,
) -> None:
    """Only the run's own executed evidence may reach the correction prompt."""

    context = _context(tmp_path)
    payload = build_evidence_correction_catalog(context.ledger).model_dump()

    assert set(payload) == {
        "catalog_version",
        "executed_references",
        "specialist_findings",
        "audit_claims",
        "truncated",
    }
    # The query ID is also citable: it is the reference the run_sql response
    # hands the model directly.
    assert set(payload["executed_references"]) == {"Q001", _SQL_EVENT, _SQL_PATH}
    assert payload["audit_claims"] is None


# --- one attempt, then the run succeeds --------------------------------------


def test_invalid_citations_get_exactly_one_successful_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    runner = _ScriptedRunner(_candidate([_BAD_REF]), _candidate([_SQL_PATH]))
    monkeypatch.setattr(Runner, "run", runner)

    result = asyncio.run(run_lead(context, "Explain the change."))

    assert len(runner.calls) == 2
    assert result.hypotheses[0].evidence_refs == [_SQL_PATH]
    assert result.findings[0].evidence_refs == [_SQL_PATH]
    persisted = AnalysisLedger(context.ledger.state_path)
    assert persisted.hypotheses[0].evidence_refs == [_SQL_PATH]


def test_correction_reuses_existing_executions_and_spends_no_extra_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    before = context.ledger.budget.model_copy()
    tool_events_before = len(context.ledger.tool_events)
    runner = _ScriptedRunner(_candidate([_BAD_REF]), _candidate([_SQL_PATH]))
    monkeypatch.setattr(Runner, "run", runner)

    asyncio.run(run_lead(context, "Explain the change."))

    after = AnalysisLedger(context.ledger.state_path)
    assert after.budget.specialist_invocations == before.specialist_invocations
    assert after.budget.sql_executions == before.sql_executions
    assert after.budget.python_executions == before.python_executions
    assert after.budget.critic_loops == before.critic_loops
    assert after.budget.charts_created == before.charts_created
    assert len(after.tool_events) == tool_events_before

    correction_agent = runner.agents[1]
    assert correction_agent.tools == []
    assert runner.calls[1][2] == CORRECTION_TURN_LIMIT


def test_the_correction_call_only_sees_the_bounded_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    runner = _ScriptedRunner(_candidate([_BAD_REF]), _candidate([_SQL_PATH]))
    monkeypatch.setattr(Runner, "run", runner)

    asyncio.run(run_lead(context, "Explain the change."))

    _, correction_prompt, _ = runner.calls[1]
    assert correction_prompt.startswith("EVIDENCE_CORRECTION_REQUEST")
    assert "hypothesis:H2" in correction_prompt
    assert _BAD_REF in correction_prompt  # named as the failure, not as evidence
    assert "CITABLE_EVIDENCE_CATALOG_JSON" in correction_prompt


# --- both calls stay observable and attributable -----------------------------


def test_both_model_calls_are_recorded_against_the_active_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    attempt_id = context.ledger.state.attempt_id
    runner = _ScriptedRunner(_candidate([_BAD_REF]), _candidate([_SQL_PATH]))
    monkeypatch.setattr(Runner, "run", runner)

    asyncio.run(run_lead(context, "Explain the change."))

    events = _agent_events(context)
    assert [event.status for event in events] == [
        AgentEventStatus.FAILED,
        AgentEventStatus.SUCCEEDED,
    ]
    assert events[0].agent_name == "Lead Data Scientist"
    assert "lead outputs cite no executed evidence" in events[0].error
    assert events[1].agent_name == "Lead Data Scientist (evidence correction)"
    assert all(event.attempt_id == attempt_id for event in events)
    assert all(event.agent_role == AgentRole.LEAD.value for event in events)
    assert events[1].completed_at >= events[1].started_at


def test_usage_from_both_calls_is_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    outputs = [_candidate([_BAD_REF]), _candidate([_SQL_PATH])]

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        context.ledger.record_usage_delta(
            type(context.ledger.usage)(
                requests=1,
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
            )
        )
        return SimpleNamespace(final_output=outputs.pop(0))

    monkeypatch.setattr(Runner, "run", fake_run)
    asyncio.run(run_lead(context, "Explain the change."))

    usage = AnalysisLedger(context.ledger.state_path).usage
    assert usage.requests == 2
    assert usage.total_tokens == 300


# --- a second invalid response terminates ------------------------------------


def test_a_second_invalid_response_fails_with_the_provenance_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    runner = _ScriptedRunner(_candidate([_BAD_REF]), _candidate(["still-invented"]))
    monkeypatch.setattr(Runner, "run", runner)

    with pytest.raises(LeadEvidenceError, match="hypothesis:H2"):
        asyncio.run(run_lead(context, "Explain the change."))

    assert len(runner.calls) == 2
    events = _agent_events(context)
    assert [event.status for event in events] == [
        AgentEventStatus.FAILED,
        AgentEventStatus.FAILED,
    ]
    # Nothing from either invalid response reached the ledger; the only
    # persisted finding is the pre-existing specialist one.
    persisted = AnalysisLedger(context.ledger.state_path)
    assert persisted.state.hypotheses == []
    assert [finding.id for finding in persisted.findings] == ["analyst:F1"]


def test_no_correction_is_attempted_when_the_allowance_is_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, correction_attempts=0)
    runner = _ScriptedRunner(_candidate([_BAD_REF]))
    monkeypatch.setattr(Runner, "run", runner)

    with pytest.raises(LeadEvidenceError):
        asyncio.run(run_lead(context, "Explain the change."))

    assert len(runner.calls) == 1
    assert [event.status for event in _agent_events(context)] == [
        AgentEventStatus.FAILED
    ]


def test_valid_first_response_spends_no_correction_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    runner = _ScriptedRunner(_candidate([_SQL_PATH]))
    monkeypatch.setattr(Runner, "run", runner)

    asyncio.run(run_lead(context, "Explain the change."))

    assert len(runner.calls) == 1
    assert _agent_events(context) == []


def test_provenance_validation_is_not_weakened_by_the_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrected response is held to exactly the same boundary as the first."""

    context = _context(tmp_path)
    failed_ref = "tool-Q404"
    context.ledger.append_tool_event(
        ToolEvent(
            id=failed_ref,
            tool_name="run_sql",
            status=ToolEventStatus.FAILED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q404"},
            error="Catalog Error: table does not exist",
        )
    )
    runner = _ScriptedRunner(_candidate([_BAD_REF]), _candidate([failed_ref]))
    monkeypatch.setattr(Runner, "run", runner)

    with pytest.raises(LeadEvidenceError):
        asyncio.run(run_lead(context, "Explain the change."))


# --- architecture parity ------------------------------------------------------


def _generalist_result(refs: list[str]) -> GeneralistResult:
    return GeneralistResult(
        audit=AuditResult(
            status=AuditStatus.COMPLETE,
            tables=[
                TableAudit(
                    table_name="orders",
                    row_count=1200,
                    evidence_refs=[_SQL_EVENT],
                )
            ],
            audited_at=_STAMP,
        ),
        candidate=_candidate(refs),
        validation=ValidationResult(
            status=ValidationStatus.PASS,
            summary="The candidate is supported by executed evidence.",
        ),
    )


def test_the_single_agent_baseline_gets_the_same_bounded_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Giving only one architecture a second attempt would bias the benchmark."""

    context = _context(tmp_path, role=AgentRole.GENERALIST)
    runner = _ScriptedRunner(
        _generalist_result([_BAD_REF]),
        _generalist_result([_SQL_PATH]),
    )
    monkeypatch.setattr(Runner, "run", runner)

    result = asyncio.run(run_generalist(context, "Explain the change."))

    assert len(runner.calls) == 2
    assert result.candidate.hypotheses[0].evidence_refs == [_SQL_PATH]
    events = _agent_events(context)
    assert [event.agent_role for event in events] == [
        AgentRole.GENERALIST.value,
        AgentRole.GENERALIST.value,
    ]
    assert events[1].agent_name.endswith("(evidence correction)")
    assert runner.agents[1].output_type.output_type is GeneralistResult


def test_the_single_agent_baseline_also_terminates_after_one_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, role=AgentRole.GENERALIST)
    runner = _ScriptedRunner(
        _generalist_result([_BAD_REF]),
        _generalist_result(["still-invented"]),
    )
    monkeypatch.setattr(Runner, "run", runner)

    with pytest.raises(LeadEvidenceError):
        asyncio.run(run_generalist(context, "Explain the change."))

    assert len(runner.calls) == 2


# --- the correction survives the full application lifecycle ------------------


def test_correction_works_inside_the_multi_agent_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner re-validates the candidate; a corrected one must pass that too."""

    from orchestration.runner import AnalysisRunner

    inputs_source = tmp_path / "lifecycle-inputs"
    inputs_source.mkdir()
    pd.DataFrame({"order_id": ["O1", "O2"]}).to_parquet(
        inputs_source / "orders.parquet"
    )
    audit_reference: dict[str, str] = {}

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        inspection = context.sql_service.inspect_relations()
        audit_reference["ref"] = inspection.tool_event_id
        return persist_audit_result(
            AuditResult(
                status=AuditStatus.COMPLETE,
                tables=[
                    TableAudit(
                        table_name="orders",
                        row_count=2,
                        evidence_refs=[inspection.tool_event_id],
                    )
                ],
                audited_at=_STAMP,
            ),
            context,
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        context.consume_budget("critic_loops")
        return ValidationResult(
            status=ValidationStatus.PASS,
            summary="The candidate cites executed evidence.",
        )

    def lifecycle_candidate(refs: list[str]) -> LeadResult:
        return LeadResult(
            objective="Explain the change.",
            answer="The order table was profiled before analysis.",
            hypotheses=[
                Hypothesis(
                    id="H2",
                    statement="Order coverage is complete.",
                    status=HypothesisStatus.SUPPORTED,
                    evidence_refs=list(refs),
                )
            ],
        )

    calls: list[str] = []

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        calls.append(agent.name)
        refs = [_BAD_REF] if len(calls) == 1 else [audit_reference["ref"]]
        return SimpleNamespace(final_output=lifecycle_candidate(refs))

    monkeypatch.setattr(Runner, "run", fake_run)

    result = asyncio.run(
        AnalysisRunner(
            workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
            auditor_runner=fake_auditor,
            critic_runner=fake_critic,
        ).run(
            "run-lifecycle",
            "Explain the change.",
            inputs_source=inputs_source,
        )
    )

    assert result.status is RunStatus.COMPLETED, result.error
    assert calls == [
        "Lead Data Scientist",
        "Lead Data Scientist (evidence correction)",
    ]
    ledger = AnalysisLedger(result.workspace)
    assert ledger.hypotheses[0].evidence_refs == [audit_reference["ref"]]
    attempt_ids = {event.attempt_id for event in ledger.agent_events}
    assert attempt_ids == {ledger.state.attempt_id}
    lead_events = [
        (event.agent_name, event.status)
        for event in ledger.agent_events
        if "Lead" in event.agent_name
    ]
    assert ("Lead Data Scientist", AgentEventStatus.FAILED) in lead_events
    assert (
        "Lead Data Scientist (evidence correction)",
        AgentEventStatus.SUCCEEDED,
    ) in lead_events
