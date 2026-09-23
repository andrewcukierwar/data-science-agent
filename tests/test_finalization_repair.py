"""Regression fixtures for the P1.2 objective-bound repair contract."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agents.finalization import (
    ORIGINAL_OBJECTIVE_REQUIREMENT,
    ReviewContractError,
    build_validation_catalog,
    validate_review,
)
from agents.runtime import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    PermissionDeniedError,
)
from orchestration.ledger import AnalysisLedger
from schemas.run_state import ToolEvent, ToolEventStatus
from schemas.validation import (
    BlockerCategory,
    CriticCandidate,
    EvidenceAnchorSource,
    LimitationCategory,
    ObjectionEvidence,
    RepairClass,
    ValidationBlocker,
    ValidationLimitation,
    ValidationResult,
    ValidationStatus,
)
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def _context(tmp_path: Path) -> AgentRunContext:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("p12")
    ledger = AnalysisLedger(workspace, objective="Compare North and South revenue.")
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(run_id="p12", agent_role=AgentRole.GENERALIST),
    )


def _blocker(candidate: CriticCandidate, **updates: object) -> ValidationBlocker:
    values: dict[str, object] = {
        "category": BlockerCategory.OBJECTIVE_NOT_ANSWERED,
        "requirement_id": "requirement:evidence",
        "target_id": "target:answer",
        "evidence": [
            ObjectionEvidence(
                source=EvidenceAnchorSource.CANDIDATE,
                pointer="/answer",
                value=candidate.answer,
            )
        ],
        "message": "The denominator uses all accounts.",
        "smallest_feasible_repair": "Select the already-computed active-account rate.",
        "repair_class": RepairClass.SYNTHESIS_SELECTION,
    }
    values.update(updates)
    return ValidationBlocker.model_validate(values)


def test_real_event_id_cannot_launder_a_fabricated_value(tmp_path: Path) -> None:
    context = _context(tmp_path)
    now = datetime.now(UTC)
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-real",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=now,
            completed_at=now,
            output={"rows": [[17]]},
        )
    )
    candidate = CriticCandidate(
        objective="Compare North and South revenue.", answer="North was higher."
    )
    review = ValidationResult(
        status=ValidationStatus.REVISE,
        blockers=[
            _blocker(
                candidate,
                category=BlockerCategory.INCORRECT_NUMERICAL_CLAIM,
                evidence=[
                    ObjectionEvidence(
                        source=EvidenceAnchorSource.TOOL_EVENT,
                        event_id="tool-real",
                        pointer="/output/rows/0/0",
                        value=99,
                    )
                ],
            )
        ],
    )

    with pytest.raises(ReviewContractError, match="does not match retained"):
        validate_review(review, candidate, context.ledger)


def test_blocker_identity_survives_rephrasing_reordering_and_value_change(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    first = CriticCandidate(
        objective="Compare North and South revenue.", answer="North was 21."
    )
    second = first.model_copy(update={"answer": "North was 19."})
    a = validate_review(
        ValidationResult(status="revise", blockers=[_blocker(first)]),
        first,
        context.ledger,
    )
    b = validate_review(
        ValidationResult(
            status="revise",
            blockers=[
                _blocker(
                    second,
                    message="All accounts were used as the denominator.",
                    smallest_feasible_repair="Use the active-account result.",
                    evidence=[
                        ObjectionEvidence(
                            source="candidate", pointer="/answer", value=second.answer
                        )
                    ],
                )
            ],
        ),
        second,
        context.ledger,
    )

    assert a.blockers[0].id == b.blockers[0].id


@pytest.mark.parametrize("category", list(LimitationCategory))
def test_limitation_categories_alone_do_not_block(
    tmp_path: Path, category: LimitationCategory
) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(
        objective=context.ledger.state.objective, answer="Done."
    )
    result = validate_review(
        ValidationResult(
            status="pass",
            limitations=[ValidationLimitation(category=category, message="Disclosed.")],
        ),
        candidate,
        context.ledger,
    )

    assert result.status is ValidationStatus.PASS
    assert result.blockers == []


def test_missing_comparison_must_bind_to_exact_original_objective(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(
        objective=context.ledger.state.objective, answer="Done."
    )
    valid = _blocker(
        candidate,
        category=BlockerCategory.MISSING_REQUESTED_COMPARISON,
        requirement_id=ORIGINAL_OBJECTIVE_REQUIREMENT,
        target_id=f"target:missing:{ORIGINAL_OBJECTIVE_REQUIREMENT}",
        objective_clause="Compare North and South revenue",
    )
    result = validate_review(
        ValidationResult(status="revise", blockers=[valid]),
        candidate,
        context.ledger,
    )
    assert result.status is ValidationStatus.REVISE


def test_synthesis_repair_runtime_denies_computation(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.begin_finalization_repair(RepairClass.SYNTHESIS_SELECTION)
    try:
        for tool in ("run_sql", "run_python", "run_analytical"):
            with pytest.raises(PermissionDeniedError):
                context.require_permission(tool)
    finally:
        context.end_finalization_repair()


def test_catalog_deduplicates_ambiguous_targets(tmp_path: Path) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(
        objective=context.ledger.state.objective, answer="Done."
    )
    catalog = build_validation_catalog(candidate)
    assert len({target.id for target in catalog.targets}) == len(catalog.targets)
