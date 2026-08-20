"""R24 regressions: one lossless citation-resolution contract everywhere.

Provenance used to be judged by four different implementations. The Lead kept a
private copy of the resolver; the Critic never checked resolution at all; the
runtime asked whether *any* citation resolved while offline scoring asked
whether *all* of them did; and canonicalization silently dropped whatever failed
to resolve, so a fabricated reference vanished from a claim the moment a real
one sat beside it.

These tests pin the single contract: resolution reports what resolved and what
did not, a claim is supported only when every citation resolves, and the four
boundaries reach the same verdict on the same persisted workspace.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from agents import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    LeadEvidenceError,
    validate_candidate_citations,
    validate_lead_result,
)
from agents import critic as critic_module
from agents import evidence as evidence_module
from agents import lead as lead_module
from agents.evidence import (
    executed_references,
    finding_reference_aliases,
    material_claims,
    resolve_citations,
    resolve_material_claims,
    unsupported_claim_ids,
)
from evaluation import primitives as primitives_module
from evaluation.contracts import EvaluationCheckStatus
from evaluation.primitives import evaluate_provenance
from orchestration.ledger import AnalysisLedger
from schemas.findings import ConfidenceLevel, Finding
from schemas.lead import LeadRecommendation, LeadResult
from schemas.metrics import MetricComparison
from schemas.run_state import (
    Hypothesis,
    HypothesisStatus,
    ToolEvent,
    ToolEventStatus,
)
from schemas.validation import CriticCandidate
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
_GOOD_EVENT = "tool-Q001"
_GOOD_PATH = "working/queries/Q001.sql"
_FAILED_EVENT = "tool-Q404"
_FAILED_PATH = "working/queries/Q404.sql"
_FABRICATED = "completed_data_audit"


def _context(tmp_path: Path) -> AgentRunContext:
    inputs_source = tmp_path / "inputs-source"
    inputs_source.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"order_id": ["O1", "O2"]}).to_parquet(
        inputs_source / "orders.parquet"
    )
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-cite",
        inputs_source=inputs_source,
    )
    ledger = AnalysisLedger(workspace, objective="Explain the change.")
    for relative in (_GOOD_PATH, _FAILED_PATH):
        path = workspace.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SELECT count(*) FROM orders;\n", encoding="utf-8")
    ledger.append_tool_event(
        ToolEvent(
            id=_GOOD_EVENT,
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q001", "query_path": _GOOD_PATH},
            artifact_refs=[_GOOD_PATH],
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
    # A uniquely aliased specialist finding, an ambiguous pair, and a cycle.
    ledger.upsert_finding(
        Finding(
            id="analyst:F1",
            statement="Orders fell 12% in the second period.",
            metric="orders",
            value=-0.12,
            evidence_refs=[_GOOD_PATH],
            confidence=ConfidenceLevel.HIGH,
        )
    )
    for namespace in ("analyst", "statistician"):
        ledger.upsert_finding(
            Finding(
                id=f"{namespace}:AMB",
                statement="Two specialists reused the same local label.",
                evidence_refs=[_GOOD_PATH],
                confidence=ConfidenceLevel.MEDIUM,
            )
        )
    ledger.upsert_finding(
        Finding(
            id="analyst:CYCLE_A",
            statement="Cites the other half of the cycle.",
            evidence_refs=["analyst:CYCLE_B"],
            confidence=ConfidenceLevel.LOW,
        )
    )
    ledger.upsert_finding(
        Finding(
            id="analyst:CYCLE_B",
            statement="Cites the first half of the cycle.",
            evidence_refs=["analyst:CYCLE_A"],
            confidence=ConfidenceLevel.LOW,
        )
    )
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-cite",
            agent_role=AgentRole.LEAD,
            model="test-model",
        ),
    )


def _resolution(context: AgentRunContext, references: list[str]):
    return resolve_citations(
        references,
        executed_refs=executed_references(context.ledger),
        aliases=finding_reference_aliases(context.ledger),
    )


# --- resolution is lossless ---------------------------------------------------


def test_resolution_reports_resolved_and_unresolved_explicitly(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    resolution = _resolution(context, [_GOOD_EVENT, _FABRICATED, _FAILED_EVENT])

    assert resolution.references == (_GOOD_EVENT, _FABRICATED, _FAILED_EVENT)
    assert resolution.resolved == (_GOOD_EVENT,)
    assert resolution.unresolved == (_FABRICATED, _FAILED_EVENT)
    assert resolution.is_supported is False


def test_canonical_references_never_drop_an_unresolved_citation(
    tmp_path: Path,
) -> None:
    """The old behaviour hid the fabricated reference behind the valid one."""

    context = _context(tmp_path)

    resolution = _resolution(context, ["analyst:F1", _FABRICATED])

    assert resolution.resolved == (_GOOD_PATH,)
    assert resolution.unresolved == (_FABRICATED,)
    assert resolution.canonical_references == (_GOOD_PATH, _FABRICATED)


def test_a_claim_with_no_citations_is_not_supported(tmp_path: Path) -> None:
    context = _context(tmp_path)

    assert _resolution(context, []).is_supported is False


# --- aliases canonicalize deterministically without changing meaning ---------


@pytest.mark.parametrize("reference", ["analyst:F1", "F1"])
def test_unique_specialist_alias_canonicalizes_to_its_executed_reference(
    tmp_path: Path,
    reference: str,
) -> None:
    context = _context(tmp_path)

    first = _resolution(context, [reference])
    second = _resolution(context, [reference])

    assert first == second
    assert first.resolved == (_GOOD_PATH,)
    assert first.is_supported is True


def test_alias_canonicalization_preserves_the_claim(tmp_path: Path) -> None:
    context = _context(tmp_path)
    candidate = _candidate(["analyst:F1"])

    validated = validate_lead_result(candidate, context.ledger)

    original = candidate.findings[0]
    corrected = validated.findings[0]
    assert corrected.evidence_refs == [_GOOD_PATH]
    assert corrected.model_dump(exclude={"evidence_refs"}) == original.model_dump(
        exclude={"evidence_refs"}
    )
    assert validated.answer == candidate.answer


# --- one contract, four boundaries -------------------------------------------


def test_every_boundary_imports_the_same_resolver() -> None:
    """Four implementations were the defect; one shared function is the fix."""

    assert lead_module.resolve_citations is evidence_module.resolve_citations
    assert (
        lead_module.resolve_material_claims is evidence_module.resolve_material_claims
    )
    assert (
        critic_module.resolve_material_claims is evidence_module.resolve_material_claims
    )
    assert (
        primitives_module.resolve_material_claims
        is evidence_module.resolve_material_claims
    )
    assert primitives_module.resolve_citations is evidence_module.resolve_citations
    assert not hasattr(lead_module, "_canonicalize_evidence_refs")
    assert not hasattr(lead_module, "_resolve_evidence_reference")


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
                value_unit="relative_change_fraction",
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
        metric_comparisons=[
            MetricComparison(
                metric_key="orders",
                baseline_period="Q1 2025",
                comparison_period="Q2 2025",
                comparison_type="relative_change",
                value=-0.12,
                unit="relative_change_fraction",
                evidence_refs=list(refs),
            )
        ],
    )


def _critic_candidate(refs: list[str]) -> CriticCandidate:
    candidate = _candidate(refs)
    return CriticCandidate(
        objective=candidate.objective,
        answer=candidate.answer,
        findings=candidate.findings,
        metric_comparisons=candidate.metric_comparisons,
        hypotheses=candidate.hypotheses,
    )


def _offline_supported(context: AgentRunContext, refs: list[str]) -> bool:
    """Persist the claims directly, then score the workspace offline."""

    candidate = _candidate(refs)
    ledger = context.ledger
    ledger.upsert_finding(candidate.findings[0])
    ledger.upsert_hypothesis(candidate.hypotheses[0])
    ledger.replace_metric_comparisons(candidate.metric_comparisons)
    reloaded = AnalysisLedger(ledger.state_path)
    checks = evaluate_provenance(context.workspace, reloaded.state, "")
    return all(
        check.status is EvaluationCheckStatus.PASS
        for check in checks
        if check.check_id
        in {
            "provenance:finding:L1",
            "provenance:hypothesis:H2",
            "provenance:metric:orders",
        }
    )


def _runtime_supported(context: AgentRunContext, refs: list[str]) -> bool:
    try:
        validate_lead_result(_candidate(refs), context.ledger)
    except LeadEvidenceError:
        return False
    return True


@pytest.mark.parametrize(
    ("refs", "expected", "shape"),
    [
        ([_GOOD_EVENT], True, "direct executed event"),
        ([_GOOD_PATH], True, "direct query path"),
        (["analyst:F1"], True, "uniquely aliased specialist finding"),
        (["F1"], True, "unique local alias"),
        ([_GOOD_EVENT, _FAILED_EVENT], False, "mixed valid and failed"),
        ([_GOOD_EVENT, _FABRICATED], False, "mixed valid and fabricated"),
        ([_GOOD_PATH, "AMB"], False, "mixed valid and ambiguous alias"),
        ([_GOOD_PATH, "analyst:CYCLE_A"], False, "mixed valid and cyclic alias"),
        ([_FAILED_EVENT], False, "failed execution only"),
        ([_FABRICATED], False, "fabricated only"),
        (["AMB"], False, "ambiguous alias only"),
        (["analyst:CYCLE_A"], False, "cyclic alias only"),
        (["outputs/never_written.csv"], False, "unrelated success in the run only"),
    ],
)
def test_runtime_critic_and_offline_agree_on_every_reference_shape(
    tmp_path: Path,
    refs: list[str],
    expected: bool,
    shape: str,
) -> None:
    context = _context(tmp_path)
    # The run does hold a successful execution throughout, so an unsupported
    # verdict is never explained by there being no evidence at all.
    assert _GOOD_EVENT in executed_references(context.ledger)

    runtime = _runtime_supported(context, refs)
    critic = validate_candidate_citations(_critic_candidate(refs), context.ledger)
    offline = _offline_supported(context, refs)

    assert runtime is expected, shape
    assert (critic is None) is expected, shape
    assert offline is expected, shape


def test_unsupported_claim_ids_names_every_failing_claim(tmp_path: Path) -> None:
    context = _context(tmp_path)
    candidate = _candidate([_GOOD_EVENT, _FABRICATED])

    unsupported = unsupported_claim_ids(
        resolve_material_claims(
            material_claims(
                findings=candidate.findings,
                recommendations=candidate.recommendations,
                hypotheses=candidate.hypotheses,
                metric_comparisons=candidate.metric_comparisons,
            ),
            context.ledger,
        )
    )

    assert unsupported == (
        "finding:L1",
        "recommendation:R1",
        "hypothesis:H2",
        "metric_comparison:orders",
    )


def test_open_hypotheses_are_not_material_claims(tmp_path: Path) -> None:
    """An open hypothesis is still being tested and may cite nothing."""

    context = _context(tmp_path)
    open_hypothesis = Hypothesis(
        id="H1",
        statement="Still under investigation.",
        status=HypothesisStatus.OPEN,
        evidence_refs=["not-yet-executed"],
    )

    assert material_claims(hypotheses=[open_hypothesis]) == ()
    validated = validate_lead_result(
        _candidate([_GOOD_EVENT]).model_copy(
            update={"hypotheses": [open_hypothesis]},
        ),
        context.ledger,
    )
    assert validated.hypotheses[0].evidence_refs == ["not-yet-executed"]


# --- source lineage agrees across boundaries too -----------------------------


def test_hard_coded_sql_fails_at_runtime_and_offline(tmp_path: Path) -> None:
    """The runtime rejected VALUES-only provenance; offline scoring now does too."""

    context = _context(tmp_path)
    hardcoded = "working/queries/HARD.sql"
    path = context.workspace.root / hardcoded
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("SELECT * FROM (VALUES (-0.12)) AS t(value);\n", encoding="utf-8")
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-HARD",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "HARD", "query_path": hardcoded},
            artifact_refs=[hardcoded],
        )
    )

    with pytest.raises(LeadEvidenceError, match="source_lineage"):
        validate_lead_result(_candidate([hardcoded]), context.ledger)

    candidate = _candidate([hardcoded])
    context.ledger.upsert_finding(candidate.findings[0])
    context.ledger.replace_metric_comparisons(candidate.metric_comparisons)
    reloaded = AnalysisLedger(context.ledger.state_path)
    checks = {
        check.check_id: check
        for check in evaluate_provenance(context.workspace, reloaded.state, "")
    }
    assert checks["provenance:source_lineage"].status is EvaluationCheckStatus.FAIL
    assert "finding:L1" in checks["provenance:source_lineage"].message


def test_source_derived_sql_passes_at_runtime_and_offline(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    validate_lead_result(_candidate([_GOOD_PATH]), context.ledger)

    assert _offline_supported(context, [_GOOD_PATH]) is True
    reloaded = AnalysisLedger(context.ledger.state_path)
    checks = {
        check.check_id: check
        for check in evaluate_provenance(context.workspace, reloaded.state, "")
    }
    assert checks["provenance:source_lineage"].status is EvaluationCheckStatus.PASS


def test_offline_failure_message_names_the_unresolved_reference(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _offline_supported(context, [_GOOD_EVENT, _FABRICATED])
    reloaded = AnalysisLedger(context.ledger.state_path)

    checks = {
        check.check_id: check
        for check in evaluate_provenance(context.workspace, reloaded.state, "")
    }

    assert _FABRICATED in checks["provenance:finding:L1"].message
    assert (
        _GOOD_EVENT
        not in checks["provenance:finding:L1"].message.split("unresolved:")[1]
    )
