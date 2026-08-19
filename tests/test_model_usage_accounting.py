"""R14 regressions for usage and cost that survive failed model calls.

The retained Task 10 pilots recorded usage only after ``Runner.run()``
returned. An invalid-JSON final output therefore discarded every token the
provider had already reported and billed, and one architecture published a
known ``$0.00`` for a run that had really spent tokens.

These fixtures simulate the SDK contract faithfully: a fake ``Runner.run``
invokes the run hooks once per provider response and then either returns a
result carrying the run's cumulative usage, or raises with the same cumulative
usage attached to ``run_data`` — exactly what the real SDK does for
``ModelBehaviorError`` and ``MaxTurnsExceeded``.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError

from agents import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    build_analyst_agent,
    run_analyst,
    run_critic,
    run_data_auditor,
    run_generalist,
    run_lead,
    run_statistician,
)
from agents.lead import _NestedSpecialistHooks
from agents.model_usage import ModelUsageHooks, run_agent_with_usage
from agents.output_contract import AgentOutputContractError
from orchestration.ledger import AnalysisLedger
from orchestration.pricing import MODEL_PRICING
from schemas.audit import AuditResult, AuditStatus
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.run_state import (
    AttemptCostAvailability,
    ModelUsage,
    ToolEvent,
    ToolEventStatus,
)
from schemas.validation import (
    CriticCandidate,
    ValidationResult,
    ValidationStatus,
)
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)


# --- SDK-shaped test doubles ------------------------------------------------


def _response_usage(
    requests: int = 1,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cached_tokens: int = 10,
    reasoning_tokens: int = 5,
) -> SimpleNamespace:
    """One provider response's usage, shaped like the SDK's Usage object."""

    return SimpleNamespace(
        requests=requests,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )


def _cumulative(responses: list[SimpleNamespace]) -> SimpleNamespace:
    """The run-level accumulator the SDK exposes on its context wrapper."""

    return SimpleNamespace(
        requests=sum(item.requests for item in responses),
        input_tokens=sum(item.input_tokens for item in responses),
        output_tokens=sum(item.output_tokens for item in responses),
        total_tokens=sum(item.total_tokens for item in responses),
        input_tokens_details=SimpleNamespace(
            cached_tokens=sum(
                item.input_tokens_details.cached_tokens for item in responses
            )
        ),
        output_tokens_details=SimpleNamespace(
            reasoning_tokens=sum(
                item.output_tokens_details.reasoning_tokens for item in responses
            )
        ),
    )


def _expected_usage(responses: list[SimpleNamespace]) -> ModelUsage:
    total = _cumulative(responses)
    return ModelUsage(
        requests=total.requests,
        input_tokens=total.input_tokens,
        output_tokens=total.output_tokens,
        total_tokens=total.total_tokens,
        cached_tokens=total.input_tokens_details.cached_tokens,
        reasoning_tokens=total.output_tokens_details.reasoning_tokens,
    )


def _fake_runner(
    responses: list[SimpleNamespace],
    *,
    final_output: object = None,
    raises: Exception | None = None,
    attach_run_data: bool = True,
    hooked_responses: int | None = None,
):
    """Build a ``Runner.run`` double that follows the SDK's usage contract.

    ``hooked_responses`` limits how many responses reach ``on_llm_end``, which
    reproduces a turn that failed before its response hook could fire.
    """

    delivered = hooked_responses if hooked_responses is not None else len(responses)

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        hooks = kwargs.get("hooks")
        wrapper = SimpleNamespace(context=context, usage=_cumulative(responses))
        for response in responses[:delivered]:
            await hooks.on_llm_end(
                wrapper,
                agent,
                SimpleNamespace(usage=response),
            )
        if raises is not None:
            if attach_run_data:
                raises.run_data = SimpleNamespace(context_wrapper=wrapper)
            raise raises
        return SimpleNamespace(final_output=final_output, context_wrapper=wrapper)

    return fake_run


# --- workspace/context fixtures --------------------------------------------


def _context(tmp_path: Path, role: AgentRole, run_id: str) -> AgentRunContext:
    input_source = tmp_path / "input-source"
    input_source.mkdir(exist_ok=True)
    pd.DataFrame({"observed_value": [1]}).to_parquet(
        input_source / "customers.parquet",
        index=False,
    )
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        run_id,
        inputs_source=input_source,
    )
    query = workspace.working / "queries" / "Q001.sql"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.write_text("SELECT 1;\n", encoding="utf-8")
    ledger = AnalysisLedger(workspace, objective="Explain the observed change.")
    ledger.begin_attempt()
    ledger.append_tool_event(
        ToolEvent(
            id="tool-sql",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q001"},
            output={"rows": [{"observed_value": 1}]},
            artifact_refs=["working/queries/Q001.sql"],
        )
    )
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id=run_id,
            agent_role=role,
            model="gpt-5.6-luna",
        ),
    )


def _specialist_result() -> SpecialistResult:
    return SpecialistResult(
        objective="Compare the acquisition periods.",
        findings=[
            Finding(
                id="F1",
                statement="Meta CAC increased.",
                metric="cac",
                value=0.3,
                evidence_refs=["working/queries/Q001.sql"],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
    )


def _lead_result() -> LeadResult:
    return LeadResult(
        objective="Explain the observed change.",
        answer="Acquisition efficiency deteriorated in the observed comparison.",
    )


def _generalist_result() -> GeneralistResult:
    return GeneralistResult(
        audit=AuditResult(status=AuditStatus.COMPLETE, audited_at=_STAMP),
        candidate=_lead_result(),
        validation=ValidationResult(status=ValidationStatus.PASS),
    )


def _assert_totals_match_attempts(ledger: AnalysisLedger) -> None:
    """Attempt deltas must reconstruct the cumulative run usage exactly."""

    for name in ModelUsage.model_fields:
        assert getattr(ledger.usage, name) == sum(
            getattr(item.usage_delta, name) for item in ledger.attempt_history
        ), name


# --- exactly-once accounting -----------------------------------------------


def test_successful_run_records_each_response_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, AgentRole.ANALYST, "run-usage-success")
    responses = [_response_usage(), _response_usage(input_tokens=250)]
    agent = build_analyst_agent(model="gpt-5.6-luna")
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _fake_runner(responses, final_output=_specialist_result()),
    )

    asyncio.run(
        run_agent_with_usage(
            agent,
            "objective",
            context=context,
            max_turns=10,
            hooks=ModelUsageHooks(),
        )
    )

    # Hooks recorded both responses and reconciliation added nothing more.
    assert context.ledger.usage == _expected_usage(responses)
    assert context.ledger.usage_complete is True
    _assert_totals_match_attempts(context.ledger)


def test_response_hook_that_never_fires_is_recovered_by_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, AgentRole.ANALYST, "run-usage-partial-hooks")
    responses = [_response_usage(), _response_usage(input_tokens=400)]
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _fake_runner(
            responses,
            final_output=_specialist_result(),
            hooked_responses=1,
        ),
    )

    asyncio.run(run_analyst(context, "Compare the periods."))

    assert context.ledger.usage == _expected_usage(responses)
    assert context.ledger.usage_complete is True
    _assert_totals_match_attempts(context.ledger)


# --- failure paths retain usage --------------------------------------------


def test_final_output_parsing_failure_retains_the_triggering_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact pilot failure: invalid JSON raised inside ``Runner.run``."""

    context = _context(tmp_path, AgentRole.ANALYST, "run-usage-parse-failure")
    responses = [_response_usage(), _response_usage(output_tokens=900)]
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _fake_runner(
            responses,
            raises=ModelBehaviorError("Invalid JSON in final output"),
            # The failing turn's own response hook never fires.
            hooked_responses=1,
        ),
    )

    with pytest.raises(ModelBehaviorError):
        asyncio.run(run_analyst(context, "Compare the periods."))

    assert context.ledger.usage == _expected_usage(responses)
    assert context.ledger.usage.requests == 2
    assert context.ledger.usage_complete is True
    _assert_totals_match_attempts(context.ledger)


def test_max_turn_failure_retains_every_completed_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, AgentRole.LEAD, "run-usage-max-turns")
    responses = [_response_usage() for _ in range(3)]
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _fake_runner(responses, raises=MaxTurnsExceeded("Max turns exceeded")),
    )

    with pytest.raises(MaxTurnsExceeded):
        asyncio.run(run_lead(context, "Explain the observed change."))

    assert context.ledger.usage.requests == 3
    assert context.ledger.usage == _expected_usage(responses)
    assert context.ledger.usage_complete is True
    _assert_totals_match_attempts(context.ledger)


def test_post_return_contract_failure_retains_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A strict-output violation raised after the run still keeps its usage."""

    context = _context(tmp_path, AgentRole.GENERALIST, "run-usage-contract-failure")
    responses = [_response_usage()]
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _fake_runner(responses, final_output="}{ truncated"),
    )

    with pytest.raises(AgentOutputContractError):
        asyncio.run(run_generalist(context, "Explain the observed change."))

    assert context.ledger.usage == _expected_usage(responses)
    assert context.ledger.usage_complete is True
    _assert_totals_match_attempts(context.ledger)


@pytest.mark.parametrize(
    ("role", "run_id", "runner", "final_output"),
    [
        (
            AgentRole.GENERALIST,
            "run-usage-arch-generalist",
            run_generalist,
            _generalist_result(),
        ),
        (AgentRole.LEAD, "run-usage-arch-lead", run_lead, _lead_result()),
        (
            AgentRole.ANALYST,
            "run-usage-arch-analyst",
            run_analyst,
            _specialist_result(),
        ),
        (
            AgentRole.STATISTICIAN,
            "run-usage-arch-statistician",
            run_statistician,
            _specialist_result(),
        ),
    ],
    ids=("generalist", "lead", "analyst", "statistician"),
)
def test_attempt_totals_equal_recorded_response_deltas_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: AgentRole,
    run_id: str,
    runner: object,
    final_output: object,
) -> None:
    """Every invocation kind keeps failed-call usage in its attempt delta."""

    context = _context(tmp_path, role, run_id)
    responses = [_response_usage(), _response_usage(input_tokens=333)]
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _fake_runner(
            responses,
            raises=ModelBehaviorError("Invalid JSON in final output"),
            hooked_responses=1,
        ),
    )

    with pytest.raises(ModelBehaviorError):
        asyncio.run(runner(context, "Explain the observed change."))

    attempt = context.ledger.attempt_history[-1]
    assert attempt.usage_delta == _expected_usage(responses)
    assert attempt.usage_complete is True
    _assert_totals_match_attempts(context.ledger)


def test_auditor_and_critic_failures_also_retain_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The remaining two production agents share the same accounting path."""

    responses = [_response_usage(), _response_usage(output_tokens=77)]
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _fake_runner(
            responses,
            raises=ModelBehaviorError("Invalid JSON in final output"),
            hooked_responses=1,
        ),
    )

    auditor_context = _context(tmp_path, AgentRole.DATA_AUDITOR, "run-usage-auditor")
    with pytest.raises(ModelBehaviorError):
        asyncio.run(run_data_auditor(auditor_context, "Audit the inputs."))

    critic_context = _context(tmp_path, AgentRole.CRITIC, "run-usage-critic")
    with pytest.raises(ModelBehaviorError):
        asyncio.run(
            run_critic(
                critic_context,
                CriticCandidate(
                    objective="Explain the observed change.",
                    answer="Acquisition efficiency deteriorated.",
                ),
            )
        )

    for context in (auditor_context, critic_context):
        assert context.ledger.usage == _expected_usage(responses)
        assert context.ledger.usage_complete is True
        _assert_totals_match_attempts(context.ledger)


def test_nested_specialist_responses_are_recorded_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Specialist hooks record their own responses without double counting."""

    context = _context(tmp_path, AgentRole.LEAD, "run-usage-nested")
    responses = [_response_usage(), _response_usage(input_tokens=700)]
    hooks = _NestedSpecialistHooks(AgentRole.ANALYST)
    agent = build_analyst_agent(model="gpt-5.6-luna")
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _fake_runner(responses, final_output=_specialist_result()),
    )

    # The Lead owns the outer run; the nested hooks see the specialist
    # responses through the same recorder.
    asyncio.run(
        run_agent_with_usage(
            agent,
            "objective",
            context=context,
            max_turns=10,
            hooks=hooks,
        )
    )

    assert context.ledger.usage == _expected_usage(responses)
    _assert_totals_match_attempts(context.ledger)


# --- cost is never a confident zero over incomplete usage -------------------


def test_unreconcilable_failure_marks_usage_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, AgentRole.ANALYST, "run-usage-unreconcilable")
    responses = [_response_usage()]
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _fake_runner(
            responses,
            raises=RuntimeError("provider connection dropped"),
            attach_run_data=False,
        ),
    )

    with pytest.raises(RuntimeError):
        asyncio.run(run_analyst(context, "Compare the periods."))

    ledger = context.ledger
    # What the hooks saw is retained; the run is explicitly a lower bound.
    assert ledger.usage.requests == 1
    assert ledger.usage_complete is False
    assert ledger.attempt_history[-1].usage_complete is False
    assert ledger.state.usage_incompleteness_note is not None
    _assert_totals_match_attempts(ledger)


def test_incomplete_usage_cannot_publish_a_known_zero_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, AgentRole.ANALYST, "run-usage-incomplete-cost")
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _fake_runner(
            [],
            raises=RuntimeError("provider connection dropped"),
            attach_run_data=False,
        ),
    )

    with pytest.raises(RuntimeError):
        asyncio.run(run_analyst(context, "Compare the periods."))

    ledger = context.ledger
    ledger.record_cost_estimate(
        pricing=MODEL_PRICING["gpt-5.6-luna"],
        pricing_model="gpt-5.6-luna",
    )

    assert ledger.usage_complete is False
    # Known pricing over incomplete usage would otherwise produce $0.00.
    assert ledger.state.estimated_cost_usd is None
    assert ledger.state.cost_breakdown is None
    assert ledger.state.cost_estimation_note is not None
    attempt_cost = ledger.attempt_history[-1].cost
    assert attempt_cost is not None
    assert attempt_cost.availability is AttemptCostAvailability.UNAVAILABLE
    assert attempt_cost.estimated_cost_usd is None


def test_complete_usage_still_produces_a_reconciled_known_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The positive control: a reconciled failure keeps a real cost."""

    context = _context(tmp_path, AgentRole.ANALYST, "run-usage-complete-cost")
    responses = [_response_usage(input_tokens=1_000, output_tokens=500)]
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _fake_runner(responses, raises=ModelBehaviorError("Invalid JSON")),
    )

    with pytest.raises(ModelBehaviorError):
        asyncio.run(run_analyst(context, "Compare the periods."))

    ledger = context.ledger
    ledger.record_cost_estimate(
        pricing=MODEL_PRICING["gpt-5.6-luna"],
        pricing_model="gpt-5.6-luna",
    )

    assert ledger.usage_complete is True
    assert ledger.state.estimated_cost_usd is not None
    assert ledger.state.estimated_cost_usd > 0
    attempt_cost = ledger.attempt_history[-1].cost
    assert attempt_cost is not None
    assert attempt_cost.availability is AttemptCostAvailability.KNOWN
    assert attempt_cost.estimated_cost_usd == pytest.approx(
        ledger.state.estimated_cost_usd
    )


def test_reconciliation_never_removes_already_recorded_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cumulative snapshot smaller than the hook total cannot subtract."""

    context = _context(tmp_path, AgentRole.ANALYST, "run-usage-clamped")
    hooked = [_response_usage(), _response_usage()]

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        hooks = kwargs["hooks"]
        wrapper = SimpleNamespace(
            context=context,
            # Understated cumulative total, as if the provider reported one
            # response late.
            usage=_cumulative(hooked[:1]),
        )
        for response in hooked:
            await hooks.on_llm_end(wrapper, agent, SimpleNamespace(usage=response))
        return SimpleNamespace(
            final_output=_specialist_result(),
            context_wrapper=wrapper,
        )

    monkeypatch.setattr("agents.model_usage.Runner.run", fake_run)
    asyncio.run(run_analyst(context, "Compare the periods."))

    assert context.ledger.usage == _expected_usage(hooked)
    _assert_totals_match_attempts(context.ledger)
