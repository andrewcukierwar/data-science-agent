"""R15 regressions for the single-agent attempt lifecycle.

Before R15 the ``GeneralistRunner`` never opened an attempt, so single-agent
benchmark records carried a null attempt identity and an empty attempt history
while the multi-agent runner published a full one. These tests drive the real
runner — real workspace, real ledger, real ``run_generalist``, real usage
accounting — and stub only the SDK boundary, so the lifecycle under test is the
production one rather than a benchmark fake that manages attempts itself.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agents.critic import persist_validation_result
from agents.lead import persist_lead_result
from agents.runtime import AgentRole
from orchestration.generalist_runner import GeneralistRunner
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from schemas.audit import AuditResult, AuditStatus
from schemas.findings import ConfidenceLevel, Finding
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.metrics import MetricComparison, MetricComparisonType
from schemas.run_state import (
    AttemptCostAvailability,
    AttemptRecord,
    AttemptStatus,
    ModelUsage,
    RunStatus,
    ToolEvent,
    ToolEventStatus,
)
from schemas.validation import ValidationResult, ValidationStatus
from tools.workspace import Workspace, WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
_MODEL = "gpt-5.6-luna"
_OBJECTIVE = "Explain the observed change."


# --- SDK boundary doubles ---------------------------------------------------


def _usage(requests: int = 1, input_tokens: int = 800) -> SimpleNamespace:
    return SimpleNamespace(
        requests=requests,
        input_tokens=input_tokens,
        output_tokens=200,
        total_tokens=input_tokens + 200,
        input_tokens_details=SimpleNamespace(cached_tokens=100),
        output_tokens_details=SimpleNamespace(reasoning_tokens=50),
    )


def _expected_usage(usage: SimpleNamespace) -> ModelUsage:
    return ModelUsage(
        requests=usage.requests,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cached_tokens=usage.input_tokens_details.cached_tokens,
        reasoning_tokens=usage.output_tokens_details.reasoning_tokens,
    )


def _sdk_run(
    final_output: object,
    *,
    usage: SimpleNamespace | None = None,
    raises: BaseException | None = None,
    on_call=None,
):
    """Stub ``Runner.run`` while leaving the whole application path real."""

    response_usage = usage or _usage()

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        if on_call is not None:
            on_call(context)
        hooks = kwargs.get("hooks")
        wrapper = SimpleNamespace(context=context, usage=response_usage)
        if hooks is not None:
            await hooks.on_llm_end(
                wrapper,
                agent,
                SimpleNamespace(usage=response_usage),
            )
        if raises is not None:
            raises.run_data = SimpleNamespace(context_wrapper=wrapper)
            raise raises
        return SimpleNamespace(final_output=final_output, context_wrapper=wrapper)

    return fake_run


def _generalist_result(
    *,
    validation_status: ValidationStatus = ValidationStatus.PASS,
    audit_status: AuditStatus = AuditStatus.COMPLETE,
) -> GeneralistResult:
    comparison = MetricComparison(
        metric_key="revenue",
        baseline_period="Q1",
        comparison_period="Q2",
        comparison_type=MetricComparisonType.RELATIVE_CHANGE,
        value=0.1,
        unit="relative_change_fraction",
        evidence_refs=["tool-evidence"],
    )
    finding = Finding(
        id="F1",
        statement="Revenue increased by 10 percent.",
        metric="revenue",
        value=0.1,
        value_unit="relative_change_fraction",
        evidence_refs=["tool-evidence"],
        confidence=ConfidenceLevel.MEDIUM,
    )
    return GeneralistResult(
        audit=AuditResult(status=audit_status, audited_at=_STAMP),
        candidate=LeadResult(
            objective=_OBJECTIVE,
            answer="Revenue increased by 10 percent in the observed comparison.",
            findings=[finding],
            metric_comparisons=[comparison],
        ),
        validation=ValidationResult(
            status=validation_status,
            checked_finding_ids=["F1"],
            summary="The candidate is supported by the executed evidence.",
        ),
    )


# --- workspace fixtures -----------------------------------------------------


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    inputs_source = tmp_path / "inputs"
    docs_source = tmp_path / "docs"
    inputs_source.mkdir(exist_ok=True)
    docs_source.mkdir(exist_ok=True)
    pd.DataFrame({"observed_value": [1]}).to_parquet(
        inputs_source / "customers.parquet",
        index=False,
    )
    (docs_source / "business_definitions.md").write_text(
        "# Definitions\n\nRevenue is the sum of order revenue.\n",
        encoding="utf-8",
    )
    return inputs_source, docs_source


def _runner(tmp_path: Path) -> GeneralistRunner:
    return GeneralistRunner(
        workspace_base_dir=tmp_path / "workspaces",
        model=_MODEL,
        model_provider="openai",
    )


def _workspace(tmp_path: Path, run_id: str) -> Workspace:
    """Create a workspace whose seeded evidence satisfies the run contract.

    The generalist's persisted findings must cite executed evidence, so the
    workspace carries one successful tool event before the run starts. This is
    setup for the lifecycle under test, not part of the attempt protocol.
    """

    inputs_source, docs_source = _sources(tmp_path)
    manager = WorkspaceManager(tmp_path / "workspaces")
    if (tmp_path / "workspaces" / run_id).exists():
        return manager.open_workspace(run_id)
    workspace = manager.create_workspace(
        run_id,
        inputs_source=inputs_source,
        docs_source=docs_source,
    )
    evidence = workspace.working / "queries" / "evidence.sql"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("SELECT COUNT(*) FROM customers;\n", encoding="utf-8")
    ledger = AnalysisLedger(workspace, run_id=run_id, objective=_OBJECTIVE)
    ledger.append_tool_event(
        ToolEvent(
            id="tool-evidence",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            output={"rows": [{"observed_value": 1}]},
            artifact_refs=["working/queries/evidence.sql"],
        )
    )
    return workspace


def _run(
    runner: GeneralistRunner,
    tmp_path: Path,
    run_id: str,
    *,
    workspace: Workspace | None = None,
):
    return runner.run_sync(
        run_id,
        _OBJECTIVE,
        workspace=workspace or _workspace(tmp_path, run_id),
    )


def _persisted(result) -> AnalysisLedger:
    """Reload the workspace from disk so only persisted state is asserted."""

    assert result.ledger is not None
    return AnalysisLedger(result.ledger.state_path)


# --- one attempt opens before the agent runs --------------------------------


def test_single_agent_run_begins_one_attempt_before_agent_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def capture(context) -> None:  # noqa: ANN001
        state = context.ledger.state
        observed["attempt_id"] = state.attempt_id
        observed["history"] = [
            (item.attempt_id, item.status) for item in state.attempt_history
        ]
        observed["config_attempt_id"] = context.run_config.attempt_id
        observed["role"] = context.agent_role

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result(), on_call=capture),
    )
    result = _run(_runner(tmp_path), tmp_path, "run-attempt-open")

    # The attempt exists and is still running while the agent executes.
    assert observed["attempt_id"] is not None
    assert observed["history"] == [(observed["attempt_id"], AttemptStatus.RUNNING)]
    # Agent and tool events are attributable because the config carries it.
    assert observed["config_attempt_id"] == observed["attempt_id"]
    assert observed["role"] is AgentRole.GENERALIST
    assert _persisted(result).state.attempt_number == 1


def test_agent_events_are_attributed_to_the_single_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result()),
    )
    result = _run(_runner(tmp_path), tmp_path, "run-attempt-events")
    ledger = _persisted(result)
    attempt = ledger.attempt_history[-1]

    assert ledger.agent_events
    assert {event.attempt_id for event in ledger.agent_events} == {attempt.attempt_id}
    assert {event.agent_role for event in ledger.agent_events} == {
        AgentRole.GENERALIST.value
    }


# --- terminal outcomes ------------------------------------------------------


def test_completed_exit_finishes_the_attempt_with_full_accounting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = _usage()
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result(), usage=usage),
    )
    result = _run(_runner(tmp_path), tmp_path, "run-attempt-completed")
    ledger = _persisted(result)
    attempt = ledger.attempt_history[-1]

    assert result.status is RunStatus.COMPLETED
    assert attempt.status is AttemptStatus.COMPLETED
    assert attempt.attempt_id == ledger.state.attempt_id
    assert attempt.finished_at is not None
    assert attempt.finished_at >= attempt.started_at
    assert attempt.error is None
    assert attempt.usage_delta == _expected_usage(usage)
    assert attempt.usage_complete is True
    assert attempt.elapsed_seconds > 0
    assert attempt.elapsed_seconds == pytest.approx(ledger.state.elapsed_seconds)
    assert attempt.cost is not None
    assert attempt.cost.availability is AttemptCostAvailability.KNOWN
    assert attempt.cost.estimated_cost_usd == pytest.approx(
        ledger.state.estimated_cost_usd
    )


def test_blocked_exit_finishes_the_attempt_as_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result(validation_status=ValidationStatus.REVISE)),
    )
    result = _run(_runner(tmp_path), tmp_path, "run-attempt-blocked")
    ledger = _persisted(result)
    attempt = ledger.attempt_history[-1]

    assert result.status is RunStatus.BLOCKED
    assert result.constrained is True
    assert attempt.status is AttemptStatus.BLOCKED
    assert attempt.finished_at is not None
    assert attempt.usage_delta.requests == 1
    assert attempt.cost is not None
    assert attempt.cost.availability is AttemptCostAvailability.KNOWN
    # A blocked run still produced a report, so its record stays observable.
    assert result.report is not None


def test_failed_exit_finishes_the_attempt_with_the_lifecycle_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result(audit_status=AuditStatus.BLOCKED)),
    )
    result = _run(_runner(tmp_path), tmp_path, "run-attempt-failed")
    ledger = _persisted(result)
    attempt = ledger.attempt_history[-1]

    assert result.status is RunStatus.FAILED
    assert attempt.status is AttemptStatus.FAILED
    assert attempt.error == result.error
    assert attempt.error == ledger.state.error
    assert attempt.finished_at is not None
    # A failed run keeps the usage the provider already reported (R14).
    assert attempt.usage_delta.requests == 1
    assert attempt.cost is not None


def test_interrupted_exit_finishes_the_attempt_as_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``BaseException`` bypasses the failure handler but not the attempt."""

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(None, raises=KeyboardInterrupt()),
    )
    runner = _runner(tmp_path)

    with pytest.raises(KeyboardInterrupt):
        _run(runner, tmp_path, "run-attempt-interrupted")

    ledger = AnalysisLedger(
        WorkspaceManager(tmp_path / "workspaces").open_workspace(
            "run-attempt-interrupted"
        )
    )
    attempt = ledger.attempt_history[-1]

    assert attempt.status is AttemptStatus.INTERRUPTED
    assert attempt.finished_at is not None
    assert attempt.error is not None
    assert "KeyboardInterrupt" in attempt.error
    # Partial usage recorded before the interruption is retained.
    assert attempt.usage_delta.requests == 1
    assert attempt.cost is not None


# --- resume appends without rewriting history -------------------------------


def test_resume_appends_a_new_attempt_without_double_counting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result(audit_status=AuditStatus.BLOCKED)),
    )
    runner = _runner(tmp_path)
    first = _run(runner, tmp_path, "run-attempt-resume")
    first_ledger = _persisted(first)
    first_attempt = first_ledger.attempt_history[-1].model_copy(deep=True)
    first_usage = first_ledger.usage.model_copy(deep=True)

    assert first.status is RunStatus.FAILED

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result(), usage=_usage(input_tokens=1_500)),
    )
    workspace = WorkspaceManager(tmp_path / "workspaces").open_workspace(
        "run-attempt-resume"
    )
    second = _run(runner, tmp_path, "run-attempt-resume", workspace=workspace)
    ledger = _persisted(second)

    assert second.status is RunStatus.COMPLETED
    assert len(ledger.attempt_history) == 2
    # The earlier attempt is untouched, including its terminal accounting.
    assert ledger.attempt_history[0] == first_attempt
    assert ledger.attempt_history[1].attempt_id != first_attempt.attempt_id
    assert ledger.attempt_history[1].attempt_number == 2
    assert ledger.attempt_history[1].status is AttemptStatus.COMPLETED
    assert ledger.state.attempt_id == ledger.attempt_history[1].attempt_id
    # Cumulative totals are the sum of the per-attempt deltas, counted once.
    for name in ModelUsage.model_fields:
        assert getattr(ledger.usage, name) == sum(
            getattr(item.usage_delta, name) for item in ledger.attempt_history
        ), name
    assert ledger.usage.requests == first_usage.requests + 1
    assert ledger.attempt_history[1].usage_delta.input_tokens == 1_500


def test_resume_after_an_interrupted_attempt_closes_it_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(None, raises=KeyboardInterrupt()),
    )
    runner = _runner(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        _run(runner, tmp_path, "run-attempt-resume-interrupted")

    manager = WorkspaceManager(tmp_path / "workspaces")
    interrupted = (
        AnalysisLedger(manager.open_workspace("run-attempt-resume-interrupted"))
        .attempt_history[-1]
        .model_copy(deep=True)
    )

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result()),
    )
    second = _run(
        runner,
        tmp_path,
        "run-attempt-resume-interrupted",
        workspace=manager.open_workspace("run-attempt-resume-interrupted"),
    )
    ledger = _persisted(second)

    assert len(ledger.attempt_history) == 2
    assert ledger.attempt_history[0] == interrupted
    assert ledger.attempt_history[0].status is AttemptStatus.INTERRUPTED
    assert ledger.attempt_history[1].status is AttemptStatus.COMPLETED
    for name in ModelUsage.model_fields:
        assert getattr(ledger.usage, name) == sum(
            getattr(item.usage_delta, name) for item in ledger.attempt_history
        ), name


# --- both architectures publish the same attempt protocol -------------------


def test_single_agent_attempt_protocol_matches_the_multi_agent_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Benchmark fairness: neither architecture may publish a richer record."""

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result()),
    )
    single = _run(_runner(tmp_path), tmp_path, "run-protocol-single")
    single_ledger = _persisted(single)

    multi_workspace = _workspace(tmp_path, "run-protocol-multi")
    multi = AnalysisRunner(
        workspace_base_dir=tmp_path / "workspaces",
        model=_MODEL,
        model_provider="openai",
        auditor_runner=_fake_auditor,
        lead_runner=_fake_lead,
        critic_runner=_fake_critic,
    ).run_sync(
        "run-protocol-multi",
        _OBJECTIVE,
        workspace=multi_workspace,
    )
    multi_ledger = _persisted(multi)

    assert single.status is multi.status is RunStatus.COMPLETED
    single_attempt = single_ledger.attempt_history[-1]
    multi_attempt = multi_ledger.attempt_history[-1]
    # The same fields are populated, whatever their run-specific values are.
    assert [
        field
        for field in AttemptRecord.model_fields
        if getattr(single_attempt, field) is None
    ] == [
        field
        for field in AttemptRecord.model_fields
        if getattr(multi_attempt, field) is None
    ]
    assert single_attempt.status is multi_attempt.status is AttemptStatus.COMPLETED
    assert single_ledger.state.attempt_id == single_attempt.attempt_id
    assert multi_ledger.state.attempt_id == multi_attempt.attempt_id
    assert single_attempt.cost is not None and multi_attempt.cost is not None
    assert single_attempt.cost.availability is multi_attempt.cost.availability


async def _fake_auditor(context, objective, *, agent=None):  # noqa: ANN001
    return context.ledger.record_audit(
        AuditResult(status=AuditStatus.COMPLETE, audited_at=_STAMP)
    )


async def _fake_lead(
    context, objective, *, business_context=None, audit=None, agent=None
):  # noqa: ANN001
    return persist_lead_result(_generalist_result().candidate, context)


async def _fake_critic(context, candidate, *, agent=None):  # noqa: ANN001
    return persist_validation_result(
        ValidationResult(status=ValidationStatus.PASS, checked_finding_ids=["F1"]),
        context.ledger,
        allow_issue_updates=True,
    )
