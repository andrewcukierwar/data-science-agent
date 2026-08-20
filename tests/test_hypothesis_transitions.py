"""R22 regressions: hypothesis evidence is one rule, checked at the transition.

An invalid resolved hypothesis used to be accepted into the ledger and its
append-only history, and only surfaced when the final Lead response was
validated — after the poisoned state was already persisted and with no chance
for the model to correct it. These tests pin the single rule across the five
places that must agree: the model-visible contract, the agent instructions, the
``record_hypothesis`` state tool, final Lead validation, and offline evaluation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agents.tool_context import ToolContext

from agents import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    LeadEvidenceError,
    ToolOutputText,
    ToolResponse,
    build_audit_evidence_catalog,
    persist_audit_result,
    record_hypothesis,
    validate_lead_result,
)
from agents.generalist import GENERALIST_INSTRUCTIONS
from agents.hypothesis_state import (
    MAX_SUGGESTED_REFERENCES,
    HypothesisEvidenceError,
    validate_hypothesis_transition,
)
from agents.lead import LEAD_INSTRUCTIONS
from evaluation.contracts import EvaluationCheckStatus
from evaluation.primitives import evaluate_provenance
from orchestration.ledger import AnalysisLedger
from schemas.audit import AuditObservation, AuditResult, AuditStatus, TableAudit
from schemas.findings import ConfidenceLevel, Finding
from schemas.lead import LeadResult
from schemas.run_state import (
    Hypothesis,
    HypothesisStatus,
    ToolEvent,
    ToolEventStatus,
    hypothesis_requires_evidence,
)
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
_SQL_EVENT = "tool-Q001"
_SQL_PATH = "working/queries/Q001.sql"
_FAILED_EVENT = "tool-Q404"
_FAILED_PATH = "working/queries/Q404.sql"

_RESOLVED_STATUSES = (
    HypothesisStatus.SUPPORTED,
    HypothesisStatus.REJECTED,
    HypothesisStatus.INCONCLUSIVE,
)


def _context(tmp_path: Path, *, role: AgentRole = AgentRole.LEAD) -> AgentRunContext:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("run-h")
    ledger = AnalysisLedger(workspace, objective="Explain the change.")
    for relative in (_SQL_PATH, _FAILED_PATH):
        path = workspace.root / relative
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
    ledger.append_tool_event(
        ToolEvent(
            id=_FAILED_EVENT,
            tool_name="run_sql",
            status=ToolEventStatus.FAILED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q404", "query_path": _FAILED_PATH},
            artifact_refs=[_FAILED_PATH],
            error="Catalog Error: table does not exist",
        )
    )
    ledger.upsert_finding(
        Finding(
            id="analyst:F1",
            statement="Orders fell in the second period.",
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
        run_config=AgentRunConfig(run_id="run-h", agent_role=role, model="test-model"),
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


def _record(
    context: AgentRunContext,
    hypothesis_id: str,
    status: HypothesisStatus,
    refs: list[str],
) -> ToolResponse:
    return _invoke(
        record_hypothesis,
        context,
        {
            "hypothesis": {
                "id": hypothesis_id,
                "statement": "Acquisition efficiency deteriorated.",
                "status": status.value,
                "evidence_refs": refs,
            }
        },
    )


# --- the contract states the rule --------------------------------------------


@pytest.mark.parametrize(
    ("status", "requires"),
    [
        (HypothesisStatus.OPEN, False),
        (HypothesisStatus.SUPPORTED, True),
        (HypothesisStatus.REJECTED, True),
        (HypothesisStatus.INCONCLUSIVE, True),
    ],
)
def test_shared_predicate_names_which_transitions_need_evidence(
    status: HypothesisStatus,
    requires: bool,
) -> None:
    assert hypothesis_requires_evidence(status) is requires
    assert hypothesis_requires_evidence(status.value) is requires


def test_model_visible_hypothesis_schema_states_the_evidence_rule() -> None:
    schema = Hypothesis.model_json_schema()

    refs = " ".join(
        schema["properties"]["evidence_refs"]["description"].lower().split()
    )
    status = " ".join(schema["properties"]["status"]["description"].lower().split())
    assert "required and non-empty for any status other than open" in refs
    assert "rather than inventing a reference" in refs
    assert "without exact evidence_refs is rejected" in status


@pytest.mark.parametrize(
    "instructions", [LEAD_INSTRUCTIONS, GENERALIST_INSTRUCTIONS], ids=["lead", "gen"]
)
def test_instructions_require_evidence_for_every_resolved_hypothesis(
    instructions: str,
) -> None:
    text = " ".join(instructions.lower().split())

    assert "an open hypothesis needs no evidence_refs" in text
    assert "never invent one" in text
    assert (
        "supported, rejected, or inconclusive only together with exact evidence_refs"
        in text
    )
    assert "qualitative and data-quality" in text
    assert "record_hypothesis refuses a resolution" in text


# --- open hypotheses stay usable ---------------------------------------------


def test_open_hypothesis_is_recorded_without_evidence(tmp_path: Path) -> None:
    context = _context(tmp_path)

    response = _record(context, "H1", HypothesisStatus.OPEN, [])

    assert response.success is True
    assert response.data["evidence_refs"] == []
    assert context.ledger.hypotheses[0].status is HypothesisStatus.OPEN


def test_open_hypothesis_keeps_its_references_untouched(tmp_path: Path) -> None:
    """Canonicalization must not quietly drop a reference from an open claim."""

    context = _context(tmp_path)

    response = _record(context, "H1", HypothesisStatus.OPEN, ["not-yet-executed"])

    assert response.success is True
    assert context.ledger.hypotheses[0].evidence_refs == ["not-yet-executed"]


# --- resolved transitions -----------------------------------------------------


@pytest.mark.parametrize("status", _RESOLVED_STATUSES)
@pytest.mark.parametrize(
    ("refs", "canonical"),
    [
        ([_SQL_EVENT], [_SQL_EVENT]),
        ([_SQL_PATH], [_SQL_PATH]),
        (["analyst:F1"], [_SQL_PATH]),
        (["F1"], [_SQL_PATH]),
    ],
    ids=["direct-event", "direct-path", "canonical-alias", "local-alias"],
)
def test_resolved_transition_with_resolvable_evidence_is_persisted_canonically(
    tmp_path: Path,
    status: HypothesisStatus,
    refs: list[str],
    canonical: list[str],
) -> None:
    context = _context(tmp_path)
    _record(context, "H1", HypothesisStatus.OPEN, [])

    response = _record(context, "H1", status, refs)

    assert response.success is True
    assert response.data["evidence_refs"] == canonical
    persisted = AnalysisLedger(context.ledger.state_path).hypotheses[0]
    assert persisted.status is status
    assert persisted.evidence_refs == canonical


@pytest.mark.parametrize("status", _RESOLVED_STATUSES)
@pytest.mark.parametrize(
    ("refs", "reason"),
    [
        ([], "no references at all"),
        (["completed_data_audit"], "fabricated reference"),
        ([_FAILED_EVENT], "failed tool event"),
        ([_FAILED_PATH], "failed query path"),
        (["working/queries/never_written.sql"], "missing file"),
        (["F9"], "unknown alias"),
    ],
)
def test_resolved_transition_without_executed_evidence_is_refused(
    tmp_path: Path,
    status: HypothesisStatus,
    refs: list[str],
    reason: str,
) -> None:
    context = _context(tmp_path)
    _record(context, "H1", HypothesisStatus.OPEN, [])

    response = _record(context, "H1", status, refs)

    assert response.success is False, reason
    assert response.error is not None
    assert response.error.code == "invalid_hypothesis_transition"


def test_ambiguous_alias_is_refused(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.ledger.upsert_finding(
        Finding(
            id="statistician:F1",
            statement="Two specialists used the same local label.",
            metric="orders",
            value=-0.12,
            evidence_refs=[_SQL_PATH],
            confidence=ConfidenceLevel.HIGH,
        )
    )
    _record(context, "H1", HypothesisStatus.OPEN, [])

    response = _record(context, "H1", HypothesisStatus.SUPPORTED, ["F1"])

    assert response.success is False
    assert response.error is not None
    assert response.error.code == "invalid_hypothesis_transition"


# --- a refused transition mutates nothing ------------------------------------


def test_refused_transition_leaves_state_and_history_unchanged(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _record(context, "H1", HypothesisStatus.OPEN, [])
    history_before = list(context.ledger.hypothesis_history)
    state_before = context.ledger.state_path.read_text(encoding="utf-8")

    refused = _record(context, "H1", HypothesisStatus.SUPPORTED, ["invented"])

    assert refused.success is False
    assert context.ledger.hypotheses[0].status is HypothesisStatus.OPEN
    assert context.ledger.hypothesis_history == history_before
    assert context.ledger.state_path.read_text(encoding="utf-8") == state_before


def test_refused_rejection_does_not_enter_rejected_hypotheses(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _record(context, "H1", HypothesisStatus.OPEN, [])

    refused = _record(context, "H1", HypothesisStatus.REJECTED, [_FAILED_EVENT])

    assert refused.success is False
    assert context.ledger.state.rejected_hypotheses == []


def test_resume_cannot_inherit_a_refused_resolution(tmp_path: Path) -> None:
    """A resumed run reads the ledger from disk; the refusal must reach disk."""

    context = _context(tmp_path)
    _record(context, "H1", HypothesisStatus.OPEN, [])
    _record(context, "H1", HypothesisStatus.SUPPORTED, ["completed_data_audit"])

    resumed = AnalysisLedger(context.ledger.state_path)

    assert [item.status for item in resumed.hypotheses] == [HypothesisStatus.OPEN]
    assert [item.status for item in resumed.hypothesis_history] == [
        HypothesisStatus.OPEN
    ]
    assert resumed.state.rejected_hypotheses == []


# --- the refusal is actionable ------------------------------------------------


def test_refusal_payload_tells_the_model_what_to_do_instead(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    response = _record(
        context,
        "H2",
        HypothesisStatus.SUPPORTED,
        ["completed_data_audit", _SQL_EVENT + "-typo"],
    )

    assert response.success is False
    data = response.data
    assert data["hypothesis_id"] == "H2"
    assert data["requested_status"] == "supported"
    assert data["unresolved_evidence_refs"] == [
        "completed_data_audit",
        _SQL_EVENT + "-typo",
    ]
    assert data["resolved_evidence_refs"] == []
    assert _SQL_EVENT in data["available_evidence_refs"]
    assert "Do not invent a reference" in data["remedy"]


def test_available_reference_suggestions_are_bounded(tmp_path: Path) -> None:
    context = _context(tmp_path)
    for index in range(MAX_SUGGESTED_REFERENCES + 10):
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

    response = _record(context, "H1", HypothesisStatus.SUPPORTED, ["invented"])

    assert len(response.data["available_evidence_refs"]) == MAX_SUGGESTED_REFERENCES


def test_typed_error_is_raised_for_direct_callers(tmp_path: Path) -> None:
    context = _context(tmp_path)

    with pytest.raises(HypothesisEvidenceError) as error:
        validate_hypothesis_transition(
            Hypothesis(
                id="H1",
                statement="Acquisition efficiency deteriorated.",
                status=HypothesisStatus.INCONCLUSIVE,
                evidence_refs=[_FAILED_EVENT],
            ),
            context.ledger,
        )

    assert error.value.hypothesis_id == "H1"
    assert error.value.requested_status is HypothesisStatus.INCONCLUSIVE
    assert error.value.unresolved_refs == (_FAILED_EVENT,)


# --- qualitative audit hypotheses --------------------------------------------


def test_audit_derived_hypothesis_resolves_from_the_evidence_catalog(
    tmp_path: Path,
) -> None:
    """A data-quality resolution is qualitative but still needs provenance."""

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
                    statement="2025-05-04 has no orders.",
                    evidence_refs=[_SQL_EVENT],
                )
            ],
            audited_at=_STAMP,
        ),
        context,
    )
    catalog = build_audit_evidence_catalog(audit, context.ledger)

    refused = _record(
        context, "H2", HypothesisStatus.SUPPORTED, ["completed_data_audit"]
    )
    accepted = _record(
        context,
        "H2",
        HypothesisStatus.SUPPORTED,
        list(catalog.entries[-1].evidence_refs),
    )

    assert refused.success is False
    assert accepted.success is True
    assert accepted.data["evidence_refs"] == [_SQL_EVENT]


# --- final Lead validation shares the rule -----------------------------------


def test_final_lead_validation_applies_the_same_rule(tmp_path: Path) -> None:
    context = _context(tmp_path)
    candidate = LeadResult(
        objective="Explain the change.",
        answer="Acquisition efficiency deteriorated.",
        hypotheses=[
            Hypothesis(
                id="H1",
                statement="Still under investigation.",
                status=HypothesisStatus.OPEN,
            ),
            Hypothesis(
                id="H2",
                statement="Acquisition efficiency deteriorated.",
                status=HypothesisStatus.SUPPORTED,
                evidence_refs=["analyst:F1"],
            ),
        ],
    )

    validated = validate_lead_result(candidate, context.ledger)

    assert validated.hypotheses[0].evidence_refs == []
    assert validated.hypotheses[1].evidence_refs == [_SQL_PATH]

    poisoned = candidate.model_copy(
        update={
            "hypotheses": [
                candidate.hypotheses[1].model_copy(
                    update={"evidence_refs": [_FAILED_EVENT]}
                )
            ]
        }
    )
    with pytest.raises(LeadEvidenceError, match="hypothesis:H2"):
        validate_lead_result(poisoned, context.ledger)


# --- offline evaluation shares the rule --------------------------------------


def _provenance_checks(context: AgentRunContext):
    ledger = AnalysisLedger(context.ledger.state_path)
    return {
        check.check_id: check
        for check in evaluate_provenance(context.workspace, ledger.state, "")
    }


def test_offline_evaluation_accepts_open_and_supported_hypotheses(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _record(context, "H1", HypothesisStatus.OPEN, [])
    _record(context, "H2", HypothesisStatus.OPEN, [])
    _record(context, "H2", HypothesisStatus.SUPPORTED, [_SQL_EVENT])

    checks = _provenance_checks(context)

    assert "provenance:hypothesis:H1" not in checks
    assert checks["provenance:hypothesis:H2"].status is EvaluationCheckStatus.PASS
    assert checks["provenance:hypothesis_history"].status is (
        EvaluationCheckStatus.PASS
    )


def test_offline_evaluation_fails_an_unsupported_resolved_hypothesis(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    # Write directly to the ledger: the state tool would refuse this, and the
    # evaluator must catch it anyway in a workspace it did not produce.
    context.ledger.upsert_hypothesis(
        Hypothesis(
            id="H2",
            statement="Acquisition efficiency deteriorated.",
            status=HypothesisStatus.SUPPORTED,
            evidence_refs=["completed_data_audit"],
        )
    )

    checks = _provenance_checks(context)

    assert checks["provenance:hypothesis:H2"].status is EvaluationCheckStatus.FAIL
    assert checks["provenance:hypothesis_history"].status is (
        EvaluationCheckStatus.FAIL
    )


def test_offline_history_check_sees_an_overwritten_bad_transition(
    tmp_path: Path,
) -> None:
    """Revising a claim must not erase that it was once asserted unsupported."""

    context = _context(tmp_path)
    context.ledger.upsert_hypothesis(
        Hypothesis(
            id="H2",
            statement="Acquisition efficiency deteriorated.",
            status=HypothesisStatus.SUPPORTED,
            evidence_refs=["completed_data_audit"],
        )
    )
    context.ledger.upsert_hypothesis(
        Hypothesis(
            id="H2",
            statement="Acquisition efficiency deteriorated.",
            status=HypothesisStatus.SUPPORTED,
            evidence_refs=[_SQL_EVENT],
        )
    )

    checks = _provenance_checks(context)

    assert checks["provenance:hypothesis:H2"].status is EvaluationCheckStatus.PASS
    assert checks["provenance:hypothesis_history"].status is (
        EvaluationCheckStatus.FAIL
    )
    assert "0:H2" in checks["provenance:hypothesis_history"].message
