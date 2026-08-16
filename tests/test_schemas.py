"""Unit tests for the initial Phase 0 Pydantic schemas."""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from schemas.audit import (
    AuditResult,
    AuditStatus,
    DateRange,
    MissingnessObservation,
    TableAudit,
)
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.run_state import (
    AgentEvent,
    AgentEventStatus,
    AnalysisRunState,
    Hypothesis,
    HypothesisStatus,
    ToolEvent,
    ToolEventStatus,
)
from schemas.validation import (
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)


def test_finding_and_specialist_result_capture_evidence() -> None:
    finding = Finding(
        id="F017",
        statement="Meta CAC increased quarter over quarter.",
        metric="CAC",
        value=29.1,
        evidence_refs=["Q023"],
        confidence=ConfidenceLevel.HIGH,
        caveats=["Campaign-level creative data is unavailable."],
    )

    result = SpecialistResult(
        objective="Determine why Meta CAC increased.",
        findings=[finding],
        artifacts=["working/queries/meta_cac.sql"],
        methods_used=["cohort comparison"],
    )

    assert result.findings[0].evidence_refs == ["Q023"]
    assert result.model_dump(mode="json")["findings"][0]["confidence"] == "high"


def test_finding_requires_at_least_one_evidence_reference() -> None:
    with pytest.raises(ValidationError):
        Finding(
            id="F001",
            statement="A claim without provenance.",
            evidence_refs=[],
            confidence="low",
        )


def test_audit_validates_date_ranges_and_rates() -> None:
    table = TableAudit(
        table_name="orders",
        row_count=284_182,
        date_range={"start": "2025-01-01", "end": "2025-06-30"},
        duplicate_rate=0.0017,
        missingness=[MissingnessObservation(column="customer_id", rate=0.0002)],
    )
    audit = AuditResult(status=AuditStatus.COMPLETE, tables=[table])

    assert audit.tables[0].date_range == DateRange(
        start=date(2025, 1, 1), end=date(2025, 6, 30)
    )
    assert audit.audited_at.tzinfo is not None

    with pytest.raises(ValidationError):
        DateRange(start=date(2025, 7, 1), end=date(2025, 6, 30))

    with pytest.raises(ValidationError):
        TableAudit(table_name="orders", row_count=1, duplicate_rate=1.1)


def test_validation_result_rejects_pass_with_high_severity_issue() -> None:
    issue = ValidationIssue(
        id="V001",
        severity=ValidationSeverity.HIGH,
        message="The CAC denominator includes returning customers.",
    )

    with pytest.raises(ValidationError):
        ValidationResult(status=ValidationStatus.PASS, issues=[issue])

    result = ValidationResult(
        status=ValidationStatus.REVISE,
        issues=[issue],
        checked_finding_ids=["F017"],
        remediation_cycles=1,
    )
    assert result.status is ValidationStatus.REVISE


def test_hypothesis_defaults_to_open() -> None:
    hypothesis = Hypothesis(id="H001", statement="AOV declined in Q2.")

    assert hypothesis.status is HypothesisStatus.OPEN
    assert hypothesis.evidence_refs == []


def test_tool_event_validates_lifecycle_and_errors() -> None:
    started_at = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
    event = ToolEvent(
        id="T001",
        tool_name="run_sql",
        status=ToolEventStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        output={"row_count": 4},
    )
    assert event.completed_at is not None

    with pytest.raises(ValidationError):
        ToolEvent(
            id="T002",
            tool_name="run_python",
            status=ToolEventStatus.FAILED,
            started_at=started_at,
        )

    with pytest.raises(ValidationError):
        ToolEvent(
            id="T003",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=started_at,
            completed_at=started_at - timedelta(seconds=1),
        )


def test_agent_event_and_usage_metadata_round_trip() -> None:
    timestamp = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
    event = AgentEvent(
        id="agent-001",
        agent_name="Statistician",
        agent_role="statistician",
        status=AgentEventStatus.SUCCEEDED,
        started_at=timestamp,
        completed_at=timestamp + timedelta(seconds=1),
        model="test-model",
        output_type="SpecialistResult",
    )
    state = AnalysisRunState(
        run_id="run-agent-event",
        objective="Assess a cohort difference.",
        agent_events=[event],
        estimated_cost_usd=0.012,
        cost_estimation_note="Configured test rates.",
        created_at=timestamp,
        updated_at=timestamp,
    )

    restored = AnalysisRunState.model_validate_json(state.model_dump_json())

    assert restored.agent_events[0].agent_role == "statistician"
    assert restored.estimated_cost_usd == 0.012


def test_analysis_run_state_round_trips_nested_models() -> None:
    created_at = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
    state = AnalysisRunState(
        run_id="run-001",
        objective="Explain the profitability decline.",
        created_at=created_at,
        updated_at=created_at,
        hypotheses=[
            Hypothesis(
                id="H001",
                statement="Paid-social acquisition became less efficient.",
                status=HypothesisStatus.SUPPORTED,
                evidence_refs=["F017"],
            )
        ],
        findings=[
            Finding(
                id="F017",
                statement="Meta CAC increased 29.1% QoQ.",
                value=29.1,
                evidence_refs=["Q023"],
                confidence=ConfidenceLevel.HIGH,
            )
        ],
    )

    restored = AnalysisRunState.model_validate_json(state.model_dump_json())

    assert restored == state
    assert restored.run_budget.max_sql_executions == 30
    assert restored.run_budget.max_python_executions == 20
    assert restored.run_budget.max_critic_loops == 2
