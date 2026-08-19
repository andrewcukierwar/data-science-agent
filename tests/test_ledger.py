"""Unit tests for persistent typed analysis ledger operations."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from orchestration.ledger import AnalysisLedger, LedgerConflictError
from schemas.findings import ConfidenceLevel, Finding
from schemas.metrics import MetricComparison, MetricDimension
from schemas.run_state import (
    AgentEvent,
    AgentEventStatus,
    Artifact,
    ArtifactKind,
    AttemptStatus,
    Hypothesis,
    HypothesisStatus,
    ModelPricing,
    ModelUsage,
    RunBudget,
    ToolEvent,
    ToolEventStatus,
)
from schemas.validation import ValidationIssue, ValidationSeverity
from tools.workspace import WorkspaceManager


def _ledger(tmp_path: Path, run_id: str = "run-ledger") -> AnalysisLedger:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(run_id)
    return AnalysisLedger(workspace, objective="Explain the profitability decline.")


def test_ledger_persists_typed_work_products_and_reloads(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    created_at = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)

    ledger.add_hypothesis(
        Hypothesis(
            id="H001",
            statement="Paid-social acquisition became less efficient.",
            status=HypothesisStatus.SUPPORTED,
            evidence_refs=["F001"],
        )
    )
    ledger.add_finding(
        Finding(
            id="F001",
            statement="Meta CAC increased 29.1% QoQ.",
            value=29.1,
            evidence_refs=["Q001"],
            confidence=ConfidenceLevel.HIGH,
        )
    )
    ledger.add_artifact(
        Artifact(
            id="A001",
            path="working/queries/Q001.sql",
            kind=ArtifactKind.QUERY,
            sha256="0" * 64,
            size_bytes=0,
            created_at=created_at,
        )
    )
    ledger.append_tool_event(
        ToolEvent(
            id="T001",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=created_at,
            completed_at=created_at,
            artifact_refs=["working/queries/Q001.sql"],
        )
    )
    ledger.add_validation_issue(
        ValidationIssue(
            id="V001",
            severity=ValidationSeverity.LOW,
            message="Campaign-level creative data is unavailable.",
        )
    )
    ledger.update_budget(RunBudget(max_sql_executions=10, sql_executions=1))

    reloaded = AnalysisLedger(ledger.state_path)

    assert reloaded.state.run_id == "run-ledger"
    assert reloaded.state.objective == "Explain the profitability decline."
    assert isinstance(reloaded.hypotheses[0], Hypothesis)
    assert isinstance(reloaded.findings[0], Finding)
    assert isinstance(reloaded.artifacts[0], Artifact)
    assert isinstance(reloaded.tool_events[0], ToolEvent)
    assert isinstance(reloaded.validation_issues[0], ValidationIssue)
    assert reloaded.budget.max_sql_executions == 10
    assert reloaded.budget.sql_executions == 1


def test_resumed_attempts_are_identified_and_elapsed_time_is_cumulative(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path, "run-resume")

    first_attempt = ledger.begin_attempt()
    ledger.record_elapsed(1.25)
    reloaded = AnalysisLedger(ledger.state_path)
    second_attempt = reloaded.begin_attempt()
    reloaded.record_elapsed(2.5)

    assert first_attempt != second_attempt
    assert reloaded.state.attempt_number == 2
    assert reloaded.state.attempt_id == second_attempt
    assert reloaded.state.elapsed_seconds == pytest.approx(3.75)


def test_attempt_history_reconciles_usage_cost_elapsed_and_event_provenance(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path, "run-attempt-history")
    timestamp = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
    first_id = ledger.begin_attempt()
    ledger.record_model_usage(
        ModelUsage(
            requests=1,
            input_tokens=10,
            output_tokens=4,
            total_tokens=14,
            cached_tokens=2,
            reasoning_tokens=1,
        )
    )
    ledger.record_elapsed(1.25)
    ledger.record_cost_estimate(
        pricing=ModelPricing(
            input_per_1m=1.0,
            cached_input_per_1m=0.1,
            output_per_1m=2.0,
        ),
        pricing_model="fixture-pricing",
    )
    ledger.append_tool_event(
        ToolEvent(
            id="tool-attempt-1",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=timestamp,
            completed_at=timestamp,
        )
    )
    first = ledger.finish_attempt(AttemptStatus.COMPLETED)
    assert first is not None

    second_id = ledger.begin_attempt()
    ledger.record_model_usage(
        ModelUsage(
            requests=2,
            input_tokens=20,
            output_tokens=8,
            total_tokens=28,
            cached_tokens=4,
            reasoning_tokens=2,
        )
    )
    ledger.record_elapsed(2.5)
    ledger.record_cost_estimate(
        pricing=ModelPricing(
            input_per_1m=1.0,
            cached_input_per_1m=0.1,
            output_per_1m=2.0,
        ),
        pricing_model="fixture-pricing",
    )
    ledger.append_agent_event(
        AgentEvent(
            id="agent-attempt-2",
            agent_name="Lead",
            agent_role="lead",
            status=AgentEventStatus.SUCCEEDED,
            started_at=timestamp,
            completed_at=timestamp,
        )
    )
    second = ledger.finish_attempt(AttemptStatus.COMPLETED)
    assert second is not None

    reloaded = AnalysisLedger(ledger.state_path)
    assert [item.attempt_id for item in reloaded.attempt_history] == [
        first_id,
        second_id,
    ]
    assert reloaded.attempt_history[0] == first
    assert reloaded.attempt_history[0].status is AttemptStatus.COMPLETED
    assert reloaded.attempt_history[1].status is AttemptStatus.COMPLETED
    assert reloaded.state.usage.requests == sum(
        item.usage_delta.requests for item in reloaded.attempt_history
    )
    assert reloaded.state.elapsed_seconds == pytest.approx(3.75)
    assert reloaded.state.estimated_cost_usd == pytest.approx(
        sum(item.cost.estimated_cost_usd for item in reloaded.attempt_history)
    )
    assert reloaded.tool_events[0].attempt_id == first_id
    assert reloaded.agent_events[0].attempt_id == second_id


def test_interrupted_before_record_is_closed_without_double_counting(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path, "run-interrupted-before-record")
    first_id = ledger.begin_attempt()
    resumed = AnalysisLedger(ledger.state_path)
    second_id = resumed.begin_attempt()
    resumed.finish_attempt(AttemptStatus.COMPLETED)

    restored = AnalysisLedger(resumed.state_path)
    assert [item.attempt_id for item in restored.attempt_history] == [
        first_id,
        second_id,
    ]
    assert restored.attempt_history[0].status is AttemptStatus.INTERRUPTED
    assert restored.attempt_history[0].usage_delta.requests == 0
    assert restored.state.usage.requests == 0


def test_interrupted_after_partial_write_reconciles_cumulative_totals(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path, "run-interrupted-partial-write")
    attempt_id = ledger.begin_attempt()
    ledger.record_model_usage(
        ModelUsage(
            requests=1,
            input_tokens=10,
            output_tokens=4,
            total_tokens=14,
        )
    )
    ledger.record_elapsed(1.5)

    # Simulate a crash after cumulative totals were persisted but before the
    # corresponding attempt delta made it to the durable state document.
    partial_state = ledger.state.model_copy(
        update={
            "attempt_history": [
                ledger.attempt_history[0].model_copy(
                    update={"usage_delta": ModelUsage()}
                )
            ],
            "elapsed_seconds": None,
        }
    )
    ledger.state_path.write_text(partial_state.model_dump_json(indent=2))

    resumed = AnalysisLedger(ledger.state_path)
    resumed.begin_attempt()
    resumed.finish_attempt(AttemptStatus.COMPLETED)
    restored = AnalysisLedger(resumed.state_path)

    assert restored.attempt_history[0].attempt_id == attempt_id
    assert restored.attempt_history[0].status is AttemptStatus.INTERRUPTED
    assert restored.attempt_history[0].usage_delta.requests == 1
    assert restored.attempt_history[0].elapsed_seconds == pytest.approx(1.5)
    assert restored.state.usage.requests == 1
    assert restored.state.elapsed_seconds == pytest.approx(1.5)
    assert restored.state.usage.requests == sum(
        item.usage_delta.requests for item in restored.attempt_history
    )


def test_unknown_attempt_cost_is_explicit_not_zero(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "run-unknown-attempt-cost")
    ledger.begin_attempt()
    ledger.record_model_usage(
        ModelUsage(
            requests=1,
            input_tokens=10,
            output_tokens=4,
            total_tokens=14,
        )
    )
    ledger.finish_attempt(AttemptStatus.INTERRUPTED, error="interrupted")

    ledger.begin_attempt()
    ledger.record_cost_estimate(
        pricing=ModelPricing(
            input_per_1m=1.0,
            cached_input_per_1m=0.1,
            output_per_1m=2.0,
        ),
        pricing_model="fixture-pricing",
    )
    ledger.finish_attempt(AttemptStatus.COMPLETED)

    restored = AnalysisLedger(ledger.state_path)
    attempt = restored.attempt_history[0]
    assert attempt.cost is not None
    assert attempt.cost.availability.value == "unavailable"
    assert attempt.cost.estimated_cost_usd is None
    assert restored.attempt_history[1].cost is not None
    assert restored.attempt_history[1].cost.availability.value == "known"
    assert restored.state.estimated_cost_usd is None


def test_rejected_hypotheses_are_kept_in_ledger_state(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    hypothesis = Hypothesis(id="H001", statement="AOV declined in Q2.")
    ledger.add_hypothesis(hypothesis)

    ledger.update_hypothesis(
        hypothesis.model_copy(update={"status": HypothesisStatus.REJECTED})
    )

    assert ledger.state.rejected_hypotheses == ["H001"]


def test_identical_hypothesis_updates_are_idempotent(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    hypothesis = Hypothesis(id="H001", statement="AOV declined in Q2.")
    ledger.add_hypothesis(hypothesis)
    history_length = len(ledger.hypothesis_history)

    assert ledger.update_hypothesis(hypothesis) == hypothesis
    assert ledger.upsert_hypothesis(hypothesis) == hypothesis
    assert len(ledger.hypothesis_history) == history_length
    assert len(AnalysisLedger(ledger.state_path).hypothesis_history) == 1


def test_ledger_persists_and_upserts_metric_comparisons(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    comparison = MetricComparison(
        metric_key="cac",
        dimensions={"channel": "Meta"},
        baseline_period="Q1 2025",
        comparison_period="Q2 2025",
        comparison_type="relative_change",
        value=0.30,
        unit="relative_change_fraction",
        evidence_refs=["tool-sql"],
    )

    ledger.upsert_metric_comparison(comparison)
    ledger.upsert_metric_comparison(comparison.model_copy(update={"value": 0.31}))
    reloaded = AnalysisLedger(ledger.state_path)

    assert len(reloaded.metric_comparisons) == 1
    assert reloaded.metric_comparisons[0].value == 0.31


def test_ledger_replaces_stale_alias_with_corrected_comparison(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    stale = MetricComparison(
        metric_key="meta_cac",
        dimensions={"acquisition_channel": "Meta"},
        baseline_period="2025 Q1",
        comparison_period="Q2 2025",
        comparison_type="relative_change",
        value=0.90,
        unit="fraction",
        evidence_refs=["stale"],
    )
    corrected = stale.model_copy(
        update={
            "metric_key": "cac",
            "dimensions": {"channel": "Meta"},
            "value": 0.30,
            "unit": "relative_change_fraction",
            "evidence_refs": ["corrected"],
        }
    )

    ledger.upsert_metric_comparison(stale)
    ledger.upsert_metric_comparison(corrected)
    reloaded = AnalysisLedger(ledger.state_path)

    assert len(reloaded.metric_comparisons) == 1
    assert reloaded.metric_comparisons[0].metric_key == "cac"
    assert reloaded.metric_comparisons[0].dimensions == [
        MetricDimension(name="channel", value="Meta")
    ]
    assert reloaded.metric_comparisons[0].value == 0.30


def test_duplicate_ids_are_rejected_without_overwriting_disk_state(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    finding = Finding(
        id="F001",
        statement="Revenue was stable.",
        evidence_refs=["Q001"],
        confidence=ConfidenceLevel.MEDIUM,
    )
    ledger.add_finding(finding)

    with pytest.raises(LedgerConflictError):
        ledger.add_finding(finding)

    assert len(AnalysisLedger(ledger.state_path).findings) == 1


def test_upsert_finding_replaces_latest_version_without_duplicate_history(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    original = Finding(
        id="analyst:F1",
        statement="Conversion declined.",
        evidence_refs=["tool-1"],
        confidence=ConfidenceLevel.MEDIUM,
    )
    revised = original.model_copy(
        update={
            "statement": "Meta conversion declined materially.",
            "confidence": ConfidenceLevel.HIGH,
        }
    )

    assert ledger.upsert_finding(original) == original
    assert ledger.upsert_finding(original) == original
    assert ledger.upsert_finding(revised) == revised

    reloaded = AnalysisLedger(ledger.state_path)
    assert reloaded.findings == [revised]


def test_budget_usage_increments_preserve_limits(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.update_budget(RunBudget(max_sql_executions=5))

    ledger.increment_budget(sql_executions=2, critic_loops=1)

    assert ledger.budget.max_sql_executions == 5
    assert ledger.budget.sql_executions == 2
    assert ledger.budget.critic_loops == 1

    with pytest.raises(ValueError):
        ledger.increment_budget(sql_executions=-1)
    with pytest.raises(ValueError):
        ledger.increment_budget(max_sql_executions=1)


def test_artifact_paths_must_stay_relative_to_workspace(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    with pytest.raises(ValueError):
        ledger.add_artifact(Artifact(id="A001", path="../outside.txt"))

    with pytest.raises(ValueError):
        ledger.add_artifact(Artifact(id="A002", path="/absolute/path.txt"))


def test_ledgers_in_separate_workspaces_are_isolated(tmp_path: Path) -> None:
    first = _ledger(tmp_path, "run-first")
    second = _ledger(tmp_path, "run-second")
    first.add_hypothesis(Hypothesis(id="H001", statement="First run."))

    assert [item.id for item in first.hypotheses] == ["H001"]
    assert second.hypotheses == []
    assert first.state_path != second.state_path
