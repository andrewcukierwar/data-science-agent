"""R17 regressions: the preflight must fail on the outcomes Task 10 hit.

The previous preflight was green while every retained pilot failed, because its
smoke assertions checked permissive configuration and artifact presence. These
fixtures drive the production runners to real outcomes — a completed run, an
invalid-JSON run, a run whose usage was lost, and a run whose interruption was
dropped — and assert that only the completed run satisfies the same gate the
opt-in live smoke tests use.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from agents.exceptions import ModelBehaviorError

from benchmark.preflight import (
    PreflightError,
    assert_run_outcome,
    check_run_outcome,
)
from orchestration.generalist_runner import GeneralistRunner
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from schemas.audit import AuditResult, AuditStatus
from schemas.findings import ConfidenceLevel, Finding
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.metrics import MetricComparison, MetricComparisonType
from schemas.run_state import (
    AttemptStatus,
    ModelUsage,
    ToolEvent,
    ToolEventStatus,
)
from schemas.validation import ValidationResult, ValidationStatus
from tools.workspace import Workspace, WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
_MODEL = "gpt-5.6-luna"
_OBJECTIVE = "Explain the observed change."


# --- SDK boundary doubles ---------------------------------------------------


def _usage(requests: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        requests=requests,
        input_tokens=1_000,
        output_tokens=300,
        total_tokens=1_300,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


def _sdk_run(
    final_output: object,
    *,
    raises: BaseException | None = None,
    report_usage: bool = True,
):
    usage = _usage() if report_usage else None

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        hooks = kwargs.get("hooks")
        wrapper = SimpleNamespace(context=context, usage=usage)
        if hooks is not None and usage is not None:
            await hooks.on_llm_end(wrapper, agent, SimpleNamespace(usage=usage))
        if raises is not None:
            raises.run_data = SimpleNamespace(context_wrapper=wrapper)
            raise raises
        return SimpleNamespace(final_output=final_output, context_wrapper=wrapper)

    return fake_run


def _generalist_result() -> GeneralistResult:
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
        audit=AuditResult(status=AuditStatus.COMPLETE, audited_at=_STAMP),
        candidate=LeadResult(
            objective=_OBJECTIVE,
            answer="Revenue increased by 10 percent in the observed comparison.",
            findings=[finding],
            metric_comparisons=[comparison],
        ),
        validation=ValidationResult(
            status=ValidationStatus.PASS,
            checked_finding_ids=["F1"],
            summary="The candidate is supported by the executed evidence.",
        ),
    )


# --- workspace fixtures -----------------------------------------------------


def _workspace(tmp_path: Path, run_id: str) -> Workspace:
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
    manager = WorkspaceManager(tmp_path / "workspaces")
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


def _generalist_run(tmp_path: Path, run_id: str):
    runner = GeneralistRunner(
        workspace_base_dir=tmp_path / "workspaces",
        model=_MODEL,
        model_provider="openai",
    )
    return runner.run_sync(
        run_id,
        _OBJECTIVE,
        workspace=_workspace(tmp_path, run_id),
    )


def _multi_agent_run(tmp_path: Path, run_id: str):
    async def auditor(context, objective, *, agent=None):  # noqa: ANN001
        context.record_sdk_usage(_usage())
        return context.ledger.record_audit(
            AuditResult(status=AuditStatus.COMPLETE, audited_at=_STAMP)
        )

    async def lead(
        context, objective, *, business_context=None, audit=None, agent=None
    ):  # noqa: ANN001
        from agents.lead import persist_lead_result

        context.record_sdk_usage(_usage())
        return persist_lead_result(_generalist_result().candidate, context)

    async def critic(context, candidate, *, agent=None):  # noqa: ANN001
        from agents.critic import persist_validation_result

        context.record_sdk_usage(_usage())
        return persist_validation_result(
            ValidationResult(
                status=ValidationStatus.PASS,
                checked_finding_ids=["F1"],
            ),
            context.ledger,
            allow_issue_updates=True,
        )

    runner = AnalysisRunner(
        workspace_base_dir=tmp_path / "workspaces",
        model=_MODEL,
        model_provider="openai",
        auditor_runner=auditor,
        lead_runner=lead,
        critic_runner=critic,
    )
    return runner.run_sync(
        run_id,
        _OBJECTIVE,
        workspace=_workspace(tmp_path, run_id),
    )


def _failed_check_ids(result, *, architecture: str) -> set[str]:
    return {
        check.check_id
        for check in check_run_outcome(result, architecture=architecture).failures
    }


# --- the positive control ---------------------------------------------------


def test_completed_single_agent_run_satisfies_the_smoke_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result()),
    )
    result = _generalist_run(tmp_path, "run-smoke-ok")
    report = assert_run_outcome(result, architecture="single-agent")

    assert report.passed is True
    assert {check.check_id for check in report.checks} >= {
        "lifecycle:completed",
        "report:persisted",
        "usage:accounted",
        "cost:explicit",
        "attempts:usage_reconciled",
    }


def test_completed_multi_agent_run_satisfies_the_smoke_gate(
    tmp_path: Path,
) -> None:
    result = _multi_agent_run(tmp_path, "run-smoke-ok-multi")
    report = assert_run_outcome(result, architecture="multi-agent")

    assert report.passed is True


# --- invalid JSON -----------------------------------------------------------


def test_invalid_json_run_cannot_satisfy_the_smoke_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact retained-pilot failure must not read as a green preflight."""

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(None, raises=ModelBehaviorError("Invalid JSON in final output")),
    )
    result = _generalist_run(tmp_path, "run-smoke-invalid-json")
    failures = _failed_check_ids(result, architecture="single-agent")

    with pytest.raises(PreflightError, match="lifecycle:completed"):
        assert_run_outcome(result, architecture="single-agent")
    assert "lifecycle:completed" in failures
    assert "lifecycle:no_error" in failures
    assert "report:returned" in failures
    # R14 still keeps the usage that the failed call reported.
    assert "usage:accounted" not in failures
    assert "attempts:recorded" not in failures


# --- lost usage -------------------------------------------------------------


def test_completed_run_with_lost_usage_cannot_satisfy_the_smoke_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed run that silently recorded zero tokens is not acceptable.

    This reproduces the pre-R14 shape: the run reports success and claims its
    usage is complete, but every counter is zero because the provider's
    accounting was dropped.
    """

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result()),
    )
    result = _generalist_run(tmp_path, "run-smoke-lost-usage")
    state = result.ledger.state
    state.usage = ModelUsage()
    state.usage_complete = True
    state.usage_incompleteness_note = None
    state.attempt_history[-1] = state.attempt_history[-1].model_copy(
        update={"usage_delta": ModelUsage(), "usage_complete": True}
    )
    failures = _failed_check_ids(result, architecture="single-agent")

    assert result.status.value == "completed"
    assert result.report is not None
    # Presence-only assertions would have passed this run.
    with pytest.raises(PreflightError, match="usage:accounted"):
        assert_run_outcome(result, architecture="single-agent")
    assert "usage:accounted" in failures
    # The attempt totals still reconcile, so only the accounting check fails.
    assert "attempts:usage_reconciled" not in failures


def test_unreconcilable_usage_is_reported_as_unavailable_not_as_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that reports no usage yields an honest lower bound."""

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result(), report_usage=False),
    )
    result = _generalist_run(tmp_path, "run-smoke-unreconcilable")
    state = result.ledger.state

    assert state.usage_complete is False
    assert state.usage_incompleteness_note is not None
    assert state.estimated_cost_usd is None
    # Explicit unavailability satisfies the gate; a silent zero does not.
    assert check_run_outcome(result, architecture="single-agent").passed is True


def test_explicitly_unavailable_usage_is_accepted_but_never_a_silent_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R14's honest lower bound passes; an unexplained zero does not."""

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result()),
    )
    result = _generalist_run(tmp_path, "run-smoke-unavailable-usage")
    assert check_run_outcome(result, architecture="single-agent").passed

    result.ledger.mark_usage_incomplete("a failed response was not reconciled")
    report = check_run_outcome(result, architecture="single-agent")
    accounted = next(
        check for check in report.checks if check.check_id == "usage:accounted"
    )
    cost = next(
        check
        for check in report.checks
        if check.check_id == "cost:not_known_over_incomplete_usage"
    )

    assert accounted.passed is True
    assert cost.passed is True
    assert result.ledger.state.estimated_cost_usd is None


# --- dropped interruption ---------------------------------------------------


def test_dropped_interruption_cannot_satisfy_the_smoke_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An attempt left running means an interruption was never closed."""

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result()),
    )
    result = _generalist_run(tmp_path, "run-smoke-dropped-interruption")
    assert check_run_outcome(result, architecture="single-agent").passed

    # Reproduce a pre-R15/R16 workspace: the attempt was never finished.
    state = result.ledger.state
    state.attempt_history[-1] = state.attempt_history[-1].model_copy(
        update={
            "status": AttemptStatus.RUNNING,
            "finished_at": None,
            "error": None,
        }
    )
    failures = _failed_check_ids(result, architecture="single-agent")

    with pytest.raises(PreflightError, match="attempts:terminal"):
        assert_run_outcome(result, architecture="single-agent")
    assert "attempts:terminal" in failures


def test_missing_attempt_history_cannot_satisfy_the_smoke_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-R15 single-agent run published no attempt identity at all."""

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result()),
    )
    result = _generalist_run(tmp_path, "run-smoke-no-attempts")
    result.ledger.state.attempt_history.clear()
    failures = _failed_check_ids(result, architecture="single-agent")

    assert {"attempts:recorded", "attempts:identity"} <= failures


def test_unreconciled_attempt_usage_cannot_satisfy_the_smoke_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result()),
    )
    result = _generalist_run(tmp_path, "run-smoke-unreconciled")
    state = result.ledger.state
    state.attempt_history[-1] = state.attempt_history[-1].model_copy(
        update={
            "usage_delta": state.attempt_history[-1].usage_delta.model_copy(
                update={"requests": 99}
            )
        }
    )
    failures = _failed_check_ids(result, architecture="single-agent")

    assert "attempts:usage_reconciled" in failures


# --- report persistence -----------------------------------------------------


def test_missing_report_file_cannot_satisfy_the_smoke_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifact metadata alone is not evidence that a report exists."""

    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result()),
    )
    result = _generalist_run(tmp_path, "run-smoke-missing-report")
    report_file = Path(result.workspace.root) / result.report.path
    report_file.unlink()
    failures = _failed_check_ids(result, architecture="single-agent")

    assert "report:readable" in failures
    assert "report:returned" not in failures


# --- architecture boundaries ------------------------------------------------


def test_single_agent_gate_rejects_specialist_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(_generalist_result()),
    )
    result = _generalist_run(tmp_path, "run-smoke-roles")
    assert check_run_outcome(result, architecture="single-agent").passed

    # The same run judged against the multi-agent contract must not pass.
    failures = _failed_check_ids(result, architecture="multi-agent")

    assert "architecture:multi_agent_roles" in failures


def test_gate_reports_every_failure_rather_than_stopping_at_the_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(None, raises=ModelBehaviorError("Invalid JSON in final output")),
    )
    result = _generalist_run(tmp_path, "run-smoke-multi-failure")
    report = check_run_outcome(result, architecture="single-agent")

    assert len(report.failures) > 1
    with pytest.raises(PreflightError) as raised:
        report.raise_for_failures()
    message = str(raised.value)
    assert all(check.check_id in message for check in report.failures)


def test_gate_refuses_a_run_without_a_ledger() -> None:
    result = SimpleNamespace(status=None, ledger=None, report=None, error=None)
    report = check_run_outcome(result, architecture="single-agent")

    assert report.passed is False
    assert report.failures[0].check_id == "lifecycle:ledger"


# --- the live preflight keeps one bounded canary per architecture -----------


def test_live_preflight_declares_one_canary_per_architecture() -> None:
    """Deleting a canary must fail in normal CI, not only during a paid run.

    The canaries themselves are opt-in and skipped without credentials, so this
    deterministic check keeps their coverage from silently regressing before a
    matrix pilot is attempted.
    """

    source = (Path(__file__).parent / "test_strict_output_canary_live.py").read_text(
        encoding="utf-8"
    )

    assert "def test_multi_agent_live_strict_output_canary(" in source
    assert "def test_single_agent_live_strict_output_canary(" in source
    # Each canary asserts the same outcome gate this module proves rejects
    # invalid JSON, lost usage, and dropped interruptions.
    assert source.count("assert_run_outcome(") == 2
    assert 'architecture="multi-agent"' in source
    assert 'architecture="single-agent"' in source


def test_live_smoke_tests_use_the_outcome_gate_rather_than_presence() -> None:
    """Both architecture smoke tests must assert outcomes, not artifacts."""

    for name in ("test_generalist_live.py", "test_runner_live.py"):
        source = (Path(__file__).parent / name).read_text(encoding="utf-8")
        assert "assert_run_outcome(" in source, name


# --- calibration against the real retained failures -------------------------


_RETAINED_PILOTS = Path(__file__).parents[1] / ".runs" / "phase2-task10-20260819"


@pytest.mark.skipif(
    not _RETAINED_PILOTS.is_dir(),
    reason="retained Task 10 pilot workspaces are not present",
)
def test_gate_rejects_every_retained_task10_pilot_workspace() -> None:
    """The gate is calibrated against the failures it exists to catch.

    All four retained pilot workspaces failed for real reasons — invalid JSON,
    lost usage, a dropped interruption — while the preflight of the time was
    green. None of them may satisfy the replacement gate.
    """

    workspaces = sorted(
        path
        for path in _RETAINED_PILOTS.glob("workspaces*/*")
        if (path / "state").is_dir()
    )
    assert workspaces, "expected retained pilot workspaces to inspect"

    for workspace_dir in workspaces:
        ledger = AnalysisLedger(workspace_dir / "state")
        architecture = (
            "multi-agent" if "multi-agent" in workspace_dir.name else "single-agent"
        )
        result = SimpleNamespace(
            status=ledger.state.status,
            error=ledger.state.error,
            report=ledger.state.final_report,
            ledger=ledger,
            workspace=SimpleNamespace(root=workspace_dir),
        )
        report = check_run_outcome(result, architecture=architecture)
        failures = {check.check_id for check in report.failures}

        assert report.passed is False, workspace_dir.name
        # Every retained pilot failed to complete and to publish a report.
        assert "lifecycle:completed" in failures, workspace_dir.name
        assert "report:readable" in failures, workspace_dir.name

    # The two single-agent pilots additionally lost their usage and published
    # no attempt history at all, which R14 and R15 fixed.
    single_agent = [path for path in workspaces if "multi-agent" not in path.name]
    assert single_agent
    for workspace_dir in single_agent:
        ledger = AnalysisLedger(workspace_dir / "state")
        result = SimpleNamespace(
            status=ledger.state.status,
            error=ledger.state.error,
            report=ledger.state.final_report,
            ledger=ledger,
            workspace=SimpleNamespace(root=workspace_dir),
        )
        failures = _failed_check_ids(result, architecture="single-agent")
        assert "usage:accounted" in failures, workspace_dir.name
        assert "attempts:recorded" in failures, workspace_dir.name
