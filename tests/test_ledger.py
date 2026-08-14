"""Unit tests for persistent typed analysis ledger operations."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from orchestration.ledger import AnalysisLedger, LedgerConflictError
from schemas.findings import ConfidenceLevel, Finding
from schemas.run_state import (
    Artifact,
    ArtifactKind,
    Hypothesis,
    HypothesisStatus,
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


def test_rejected_hypotheses_are_kept_in_ledger_state(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    hypothesis = Hypothesis(id="H001", statement="AOV declined in Q2.")
    ledger.add_hypothesis(hypothesis)

    ledger.update_hypothesis(
        hypothesis.model_copy(update={"status": HypothesisStatus.REJECTED})
    )

    assert ledger.state.rejected_hypotheses == ["H001"]


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
