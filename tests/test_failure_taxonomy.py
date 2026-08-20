"""R18 regressions for explicit block reasons and accurate failure taxonomy.

Before R18 every blocked analysis was recorded as a budget failure, and every
other non-completion had its category guessed by matching substrings in an error
message. A self-critique that still required revision, an unresolved follow-up,
a schema violation, and an interruption were all published as "budget", which
would have made the Task 10 failure taxonomy actively misleading.

These fixtures drive both production architectures to each major block path and
assert the persisted reason, the benchmark category, and that the aggregate
taxonomy reproduces the per-record categories exactly.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError

from agents.output_contract import AgentOutputContractError
from benchmark import BenchmarkCellResult, BenchmarkRunner
from benchmark.runner import category_for_block_reason
from evaluation.contracts import (
    EvaluatorStatus,
    ExecutionMode,
    FailureCategory,
    LifecycleStatus,
)
from orchestration.block_reasons import classify_exception
from orchestration.budgets import (
    BudgetExhaustedError,
    BudgetResource,
    BudgetSnapshot,
)
from orchestration.generalist_runner import GeneralistRunner
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from schemas.audit import AuditResult, AuditStatus
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.metrics import MetricComparison, MetricComparisonType
from schemas.run_state import (
    RunBlockReason,
    RunBudget,
    RunStatus,
    ToolEvent,
    ToolEventStatus,
)
from schemas.validation import ValidationResult, ValidationStatus
from tools.workspace import Workspace, WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
_MODEL = "gpt-5.6-luna"
_OBJECTIVE = "Explain the observed change."
SCENARIO_ID = "meaningful-ab-treatment-effect"


# --- shared fixtures --------------------------------------------------------


def _budget_snapshot() -> BudgetSnapshot:
    return BudgetSnapshot(
        resource=BudgetResource.SQL_EXECUTIONS,
        used=30,
        limit=30,
        remaining=0,
    )


def _usage() -> SimpleNamespace:
    return SimpleNamespace(
        requests=1,
        input_tokens=1_000,
        output_tokens=300,
        total_tokens=1_300,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


def _candidate(*, follow_up: bool = False) -> LeadResult:
    return LeadResult(
        objective=_OBJECTIVE,
        answer="Revenue increased by 10 percent in the observed comparison.",
        findings=[
            Finding(
                id="F1",
                statement="Revenue increased by 10 percent.",
                metric="revenue",
                value=0.1,
                value_unit="relative_change_fraction",
                evidence_refs=["tool-evidence"],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
        metric_comparisons=[
            MetricComparison(
                metric_key="revenue",
                baseline_period="Q1",
                comparison_period="Q2",
                comparison_type=MetricComparisonType.RELATIVE_CHANGE,
                value=0.1,
                unit="relative_change_fraction",
                evidence_refs=["tool-evidence"],
            )
        ],
        follow_up_analysis=follow_up,
        follow_up_rationale=(
            "An objective-critical driver remains unmeasured." if follow_up else None
        ),
    )


def _generalist_result(
    *,
    validation_status: ValidationStatus = ValidationStatus.PASS,
    audit_status: AuditStatus = AuditStatus.COMPLETE,
    follow_up: bool = False,
) -> GeneralistResult:
    return GeneralistResult(
        audit=AuditResult(status=audit_status, audited_at=_STAMP),
        candidate=_candidate(follow_up=follow_up),
        validation=ValidationResult(
            status=validation_status,
            checked_finding_ids=["F1"],
            summary="Self-critique summary.",
        ),
    )


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


def _sdk_run(final_output: object, *, raises: BaseException | None = None):
    usage = _usage()

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        hooks = kwargs.get("hooks")
        wrapper = SimpleNamespace(context=context, usage=usage)
        if hooks is not None:
            await hooks.on_llm_end(wrapper, agent, SimpleNamespace(usage=usage))
        if raises is not None:
            raises.run_data = SimpleNamespace(context_wrapper=wrapper)
            raise raises
        return SimpleNamespace(final_output=final_output, context_wrapper=wrapper)

    return fake_run


def _generalist_run(tmp_path: Path, run_id: str):
    runner = GeneralistRunner(
        workspace_base_dir=tmp_path / "workspaces",
        model=_MODEL,
        model_provider="openai",
    )
    return runner.run_sync(run_id, _OBJECTIVE, workspace=_workspace(tmp_path, run_id))


def _multi_agent_run(
    tmp_path: Path,
    run_id: str,
    *,
    validation_status: ValidationStatus = ValidationStatus.PASS,
    audit_status: AuditStatus = AuditStatus.COMPLETE,
    lead_follow_up: bool = False,
    lead_error: BaseException | None = None,
    critic_error: BaseException | None = None,
    budget: RunBudget | None = None,
):
    async def auditor(context, objective, *, agent=None):  # noqa: ANN001
        context.record_sdk_usage(_usage())
        return context.ledger.record_audit(
            AuditResult(status=audit_status, audited_at=_STAMP)
        )

    async def lead(
        context, objective, *, business_context=None, audit=None, agent=None
    ):  # noqa: ANN001
        from agents.lead import persist_lead_result

        context.record_sdk_usage(_usage())
        if lead_error is not None:
            raise lead_error
        return persist_lead_result(
            _candidate(follow_up=lead_follow_up),
            context,
        )

    async def critic(context, candidate, *, agent=None):  # noqa: ANN001
        from agents.critic import persist_validation_result

        context.record_sdk_usage(_usage())
        if critic_error is not None:
            raise critic_error
        return persist_validation_result(
            ValidationResult(
                status=validation_status,
                checked_finding_ids=["F1"],
                summary="Critic summary.",
            ),
            context.ledger,
            allow_issue_updates=True,
        )

    runner = AnalysisRunner(
        workspace_base_dir=tmp_path / "workspaces",
        model=_MODEL,
        model_provider="openai",
        budget=budget or RunBudget(max_critic_loops=1),
        auditor_runner=auditor,
        lead_runner=lead,
        critic_runner=critic,
    )
    return runner.run_sync(run_id, _OBJECTIVE, workspace=_workspace(tmp_path, run_id))


# --- every non-completed run names its originating condition ----------------


def test_exception_classification_separates_budget_from_other_stops() -> None:
    """Only genuine run-budget exhaustion is a budget outcome."""

    assert (
        classify_exception(BudgetExhaustedError(_budget_snapshot()))
        is RunBlockReason.BUDGET_EXHAUSTED
    )
    # A turn limit is an agent bound, and a structured-output violation is a
    # schema failure. Neither may be reported as budget exhaustion.
    assert classify_exception(MaxTurnsExceeded("limit")) is RunBlockReason.AGENT_FAILURE
    assert (
        classify_exception(ModelBehaviorError("bad json"))
        is RunBlockReason.SCHEMA_FAILURE
    )
    assert (
        classify_exception(AgentOutputContractError("Analyst", SpecialistResult, "{}"))
        is RunBlockReason.SCHEMA_FAILURE
    )
    assert classify_exception(KeyboardInterrupt()) is RunBlockReason.INTERRUPTED
    assert classify_exception(RuntimeError("boom")) is RunBlockReason.OTHER


def test_every_block_reason_maps_to_a_distinct_benchmark_category() -> None:
    """The taxonomy must not collapse distinct conditions into one bucket."""

    mapped = {reason: category_for_block_reason(reason) for reason in RunBlockReason}

    assert mapped[RunBlockReason.BUDGET_EXHAUSTED] is FailureCategory.BUDGET
    for reason in (
        RunBlockReason.VALIDATION_REVISION,
        RunBlockReason.UNRESOLVED_FOLLOW_UP,
        RunBlockReason.SCHEMA_FAILURE,
        RunBlockReason.AGENT_FAILURE,
        RunBlockReason.INTERRUPTED,
        RunBlockReason.DATA_QUALITY,
    ):
        assert mapped[reason] is not FailureCategory.BUDGET, reason
    # Every reason is mapped explicitly rather than defaulting.
    assert set(mapped) == set(RunBlockReason)


# --- single-agent block paths -----------------------------------------------


@pytest.mark.parametrize(
    ("label", "output", "raises", "status", "reason"),
    [
        (
            "validation_revision",
            _generalist_result(validation_status=ValidationStatus.REVISE),
            None,
            RunStatus.BLOCKED,
            RunBlockReason.VALIDATION_REVISION,
        ),
        (
            "unresolved_follow_up",
            _generalist_result(follow_up=True),
            None,
            RunStatus.BLOCKED,
            RunBlockReason.UNRESOLVED_FOLLOW_UP,
        ),
        (
            "blocked_audit",
            _generalist_result(audit_status=AuditStatus.BLOCKED),
            None,
            RunStatus.FAILED,
            RunBlockReason.DATA_QUALITY,
        ),
        (
            "schema_failure",
            None,
            ModelBehaviorError("Invalid JSON in final output"),
            RunStatus.FAILED,
            RunBlockReason.SCHEMA_FAILURE,
        ),
    ],
    ids=("validation", "follow_up", "audit", "schema"),
)
def test_single_agent_block_paths_persist_their_originating_condition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    output: object,
    raises: BaseException | None,
    status: RunStatus,
    reason: RunBlockReason,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(output, raises=raises),
    )
    result = _generalist_run(tmp_path, f"run-taxonomy-single-{label}")
    persisted = AnalysisLedger(result.ledger.state_path).state

    assert result.status is status
    assert result.block_reason is reason
    assert persisted.block_reason is reason
    # A human-readable explanation accompanies every machine-readable reason.
    assert persisted.block_detail
    assert result.block_detail == persisted.block_detail
    # None of these is a budget failure.
    assert persisted.block_reason is not RunBlockReason.BUDGET_EXHAUSTED


def test_single_agent_interruption_is_categorized_as_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(None, raises=KeyboardInterrupt("operator stopped the run")),
    )
    with pytest.raises(KeyboardInterrupt):
        _generalist_run(tmp_path, "run-taxonomy-single-interrupt")

    ledger = AnalysisLedger(
        WorkspaceManager(tmp_path / "workspaces").open_workspace(
            "run-taxonomy-single-interrupt"
        )
    )

    assert ledger.state.status is RunStatus.CANCELLED
    assert ledger.state.block_reason is RunBlockReason.INTERRUPTED
    assert ledger.state.block_detail


# --- multi-agent block paths ------------------------------------------------


def test_multi_agent_blocked_audit_is_categorized_as_data_quality(
    tmp_path: Path,
) -> None:
    result = _multi_agent_run(
        tmp_path,
        "run-taxonomy-multi-audit",
        audit_status=AuditStatus.BLOCKED,
    )

    assert result.status is RunStatus.FAILED
    assert result.block_reason is RunBlockReason.DATA_QUALITY
    assert result.ledger.state.block_reason is RunBlockReason.DATA_QUALITY
    assert result.block_detail


def test_multi_agent_validation_revision_is_not_a_budget_failure(
    tmp_path: Path,
) -> None:
    result = _multi_agent_run(
        tmp_path,
        "run-taxonomy-multi-validation",
        validation_status=ValidationStatus.REVISE,
    )

    assert result.status is RunStatus.BLOCKED
    assert result.constrained is True
    assert result.block_reason is RunBlockReason.VALIDATION_REVISION
    assert result.block_reason is not RunBlockReason.BUDGET_EXHAUSTED
    assert "REVISE" in result.block_detail


def test_multi_agent_unresolved_follow_up_is_not_a_budget_failure(
    tmp_path: Path,
) -> None:
    result = _multi_agent_run(
        tmp_path,
        "run-taxonomy-multi-follow-up",
        lead_follow_up=True,
    )

    assert result.status is RunStatus.BLOCKED
    assert result.block_reason is RunBlockReason.UNRESOLVED_FOLLOW_UP
    assert result.block_detail


def test_multi_agent_budget_exhaustion_is_categorized_as_budget(
    tmp_path: Path,
) -> None:
    """The one condition that genuinely is a budget failure."""

    result = _multi_agent_run(
        tmp_path,
        "run-taxonomy-multi-budget",
        lead_error=BudgetExhaustedError(_budget_snapshot()),
    )

    assert result.status is RunStatus.FAILED
    assert result.block_reason is RunBlockReason.BUDGET_EXHAUSTED
    assert result.ledger.state.block_reason is RunBlockReason.BUDGET_EXHAUSTED


def test_multi_agent_schema_failure_is_not_a_budget_failure(
    tmp_path: Path,
) -> None:
    result = _multi_agent_run(
        tmp_path,
        "run-taxonomy-multi-schema",
        lead_error=AgentOutputContractError(
            "Lead Data Scientist",
            LeadResult,
            "}{",
        ),
    )

    assert result.status is RunStatus.FAILED
    assert result.block_reason is RunBlockReason.SCHEMA_FAILURE
    assert result.block_reason is not RunBlockReason.BUDGET_EXHAUSTED


# --- benchmark records and aggregation --------------------------------------


def _sources(_registration, destination: Path) -> tuple[Path, Path]:
    inputs = destination / "inputs"
    docs = destination / "docs"
    inputs.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"observed_value": [1]}).to_parquet(
        inputs / "customers.parquet",
        index=False,
    )
    (docs / "README.md").write_text("question context\n", encoding="utf-8")
    return inputs, docs


def _benchmark(tmp_path: Path, executor, *, repetitions: int = 1) -> Path:
    runner = BenchmarkRunner(
        tmp_path / "workspaces",
        architecture_executors={"single-agent": executor},
        source_preparer=_sources,
    )
    manifest = runner.build_manifest(
        manifest_id="taxonomy-manifest",
        scenario_ids=[SCENARIO_ID],
        architectures=("single-agent",),
        repetitions=repetitions,
        model=_MODEL,
        model_provider="openai",
        execution_mode=ExecutionMode.DETERMINISTIC,
        repetition_justification="R18 taxonomy fixture",
    )
    path = tmp_path / "manifest.json"
    runner.persist_plan(manifest, path)
    return runner, path


def test_blocked_record_is_not_published_as_a_budget_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The benchmark builds its own workspace, so use a candidate that cites no
    # evidence and isolates the block path under test.
    revise_result = GeneralistResult(
        audit=AuditResult(status=AuditStatus.COMPLETE, audited_at=_STAMP),
        candidate=LeadResult(
            objective=_OBJECTIVE,
            answer="The observed change is described by the available evidence.",
        ),
        validation=ValidationResult(
            status=ValidationStatus.REVISE,
            summary="A material issue remains.",
        ),
    )
    monkeypatch.setattr(
        "agents.model_usage.Runner.run",
        _sdk_run(revise_result),
    )

    def execute(cell, workspace):
        runner = GeneralistRunner(
            workspace_base_dir=cell.workspace_path.parent,
            model=_MODEL,
            model_provider="openai",
        )
        return runner.run_sync(
            cell.run_id,
            cell.scenario.metadata.user_question,
            workspace=workspace,
        )

    runner, manifest_path = _benchmark(tmp_path, execute)
    summary = runner.execute(manifest_path)
    record = summary.manifest.run_records[0]

    assert record.lifecycle.status is LifecycleStatus.BLOCKED
    assert record.lifecycle.failure_category is FailureCategory.VALIDATION
    assert record.lifecycle.failure_category is not FailureCategory.BUDGET
    assert record.lifecycle.failure_message
    # A blocked run stays an operational observation, not an analytical failure.
    assert record.evaluator_result.status is EvaluatorStatus.NOT_EVALUATED
    assert record.evaluator_result.status is not EvaluatorStatus.FAIL


def test_blocked_and_cancelled_runs_stay_in_operational_denominators(
    tmp_path: Path,
) -> None:
    outcomes = iter(
        [
            RunBlockReason.VALIDATION_REVISION,
            RunBlockReason.UNRESOLVED_FOLLOW_UP,
            RunBlockReason.BUDGET_EXHAUSTED,
        ]
    )

    def execute(cell, workspace):
        reason = next(outcomes)
        ledger = AnalysisLedger(
            workspace,
            run_id=cell.run_id,
            objective=cell.scenario.metadata.user_question,
        )
        ledger.begin_attempt()
        ledger.record_elapsed(1.0)
        ledger.mark_blocked(reason, f"blocked by {reason.value}")
        ledger.finish_attempt("blocked", error=f"blocked by {reason.value}")
        return SimpleNamespace(
            status=RunStatus.BLOCKED,
            workspace=workspace,
            state=ledger.state,
            error=None,
            block_reason=reason,
            block_detail=f"blocked by {reason.value}",
        )

    runner, manifest_path = _benchmark(tmp_path, execute, repetitions=3)
    summary = runner.execute(manifest_path)
    aggregate = summary.manifest.aggregates[0]
    denominator = aggregate.denominator

    assert denominator is not None
    assert denominator.observed_repetitions == 3
    assert denominator.missing_repetitions == 0
    assert denominator.completed_runs == 0
    assert denominator.failed_runs == 3
    assert denominator.evaluated_runs == 0
    # The three distinct conditions are reported distinctly.
    assert aggregate.failure_taxonomy["lifecycle:validation"] == 1
    assert aggregate.failure_taxonomy["lifecycle:unresolved_follow_up"] == 1
    assert aggregate.failure_taxonomy["lifecycle:budget"] == 1
    assert "missing" not in aggregate.failure_taxonomy


def test_aggregate_taxonomy_reproduces_the_per_record_categories_exactly(
    tmp_path: Path,
) -> None:
    reasons = [
        RunBlockReason.VALIDATION_REVISION,
        RunBlockReason.SCHEMA_FAILURE,
        RunBlockReason.SCHEMA_FAILURE,
    ]
    outcomes = iter(reasons)

    def execute(cell, workspace):
        reason = next(outcomes)
        ledger = AnalysisLedger(
            workspace,
            run_id=cell.run_id,
            objective=cell.scenario.metadata.user_question,
        )
        ledger.begin_attempt()
        ledger.record_elapsed(1.0)
        ledger.mark_blocked(reason, f"blocked by {reason.value}")
        ledger.finish_attempt("blocked", error=f"blocked by {reason.value}")
        return SimpleNamespace(
            status=RunStatus.BLOCKED,
            workspace=workspace,
            state=ledger.state,
            error=None,
            block_reason=reason,
            block_detail=f"blocked by {reason.value}",
        )

    runner, manifest_path = _benchmark(tmp_path, execute, repetitions=3)
    summary = runner.execute(manifest_path)
    aggregate = summary.manifest.aggregates[0]

    expected: dict[str, int] = {}
    for record in summary.manifest.run_records:
        category = record.lifecycle.failure_category
        assert category is not None
        key = f"lifecycle:{category.value}"
        expected[key] = expected.get(key, 0) + 1

    lifecycle_counts = {
        key: value
        for key, value in aggregate.failure_taxonomy.items()
        if key.startswith("lifecycle:")
    }

    assert lifecycle_counts == expected
    assert lifecycle_counts == {
        "lifecycle:validation": 1,
        "lifecycle:schema": 2,
    }


def test_legacy_workspace_without_a_block_reason_still_classifies(
    tmp_path: Path,
) -> None:
    """Pre-R18 evidence has no persisted reason and falls back to inference."""

    def execute(cell, workspace):
        return SimpleNamespace(
            status=RunStatus.BLOCKED,
            workspace=workspace,
            state=None,
            error="analysis stopped by budget exhaustion",
        )

    runner, manifest_path = _benchmark(tmp_path, execute)
    summary = runner.execute(manifest_path)
    record = summary.manifest.run_records[0]

    assert record.lifecycle.status is LifecycleStatus.BLOCKED
    assert record.lifecycle.failure_category is FailureCategory.BUDGET
    assert record.lifecycle.failure_message


def test_benchmark_cell_result_block_reason_is_honoured(tmp_path: Path) -> None:
    """An injected executor may state the condition directly."""

    def execute(cell, workspace):
        return SimpleNamespace(
            status=RunStatus.BLOCKED,
            workspace=workspace,
            state=None,
            error="the analysis was constrained",
            block_reason=RunBlockReason.UNRESOLVED_FOLLOW_UP,
            block_detail="an objective-critical question remained unanswered",
        )

    runner, manifest_path = _benchmark(tmp_path, execute)
    summary = runner.execute(manifest_path)
    record = summary.manifest.run_records[0]

    assert record.lifecycle.failure_category is FailureCategory.UNRESOLVED_FOLLOW_UP
    assert (
        record.lifecycle.failure_message
        == "an objective-critical question remained unanswered"
    )


def test_cell_result_dataclass_still_accepted_alongside_typed_results(
    tmp_path: Path,
) -> None:
    """The explicit BenchmarkCellResult path keeps its stated category."""

    from evaluation.contracts import LifecycleOutcome

    def execute(cell, workspace):
        return BenchmarkCellResult(
            lifecycle=LifecycleOutcome(
                status=LifecycleStatus.BLOCKED,
                failure_category=FailureCategory.VALIDATION,
                failure_message="self-critique required revision",
            ),
            workspace=workspace,
            started_at=_STAMP,
            finished_at=_STAMP,
        )

    runner, manifest_path = _benchmark(tmp_path, execute)
    summary = runner.execute(manifest_path)
    record = summary.manifest.run_records[0]

    assert record.lifecycle.failure_category is FailureCategory.VALIDATION
