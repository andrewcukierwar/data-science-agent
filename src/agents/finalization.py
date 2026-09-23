"""Shared objective-bound finalization policy for both architectures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from orchestration.ledger import AnalysisLedger
from schemas.metrics import normalize_metric_comparison
from schemas.run_state import ToolEventStatus
from schemas.validation import (
    VALIDATION_CONTRACT_VERSION,
    BlockerCategory,
    CatalogRequirement,
    CatalogTarget,
    CriticCandidate,
    EvidenceAnchorSource,
    LimitationCategory,
    ObjectionEvidence,
    RepairClass,
    ValidationBlocker,
    ValidationCatalog,
    ValidationIssue,
    ValidationLimitation,
    ValidationResult,
    ValidationStatus,
)

ORIGINAL_OBJECTIVE_REQUIREMENT = "requirement:original-objective"
COMPLETION_REQUIREMENTS = (
    CatalogRequirement(
        id="requirement:follow-up",
        kind="completion",
        text="Resolve objective-critical follow-up.",
    ),
    CatalogRequirement(
        id="requirement:margin",
        kind="completion",
        text="Complete an available objective-critical margin comparison.",
    ),
    CatalogRequirement(
        id="requirement:acquisition",
        kind="completion",
        text="Complete an available objective-critical acquisition comparison.",
    ),
    CatalogRequirement(
        id="requirement:structured-metrics",
        kind="completion",
        text="Retain required structured metric comparisons.",
    ),
    CatalogRequirement(
        id="requirement:visualization",
        kind="completion",
        text="Provide an explicitly requested visualization.",
    ),
    CatalogRequirement(
        id="requirement:evidence",
        kind="completion",
        text="Retain exact source-derived evidence for material claims.",
    ),
)


class ReviewContractError(ValueError):
    """The returned review is not grounded in the supplied review boundary."""


def _digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _metric_scope(item: object) -> tuple[object, ...]:
    metric = normalize_metric_comparison(item)  # type: ignore[arg-type]
    return (
        metric.metric_key,
        tuple((d.name, d.value) for d in metric.dimensions),
        metric.baseline_period,
        metric.comparison_period,
        metric.comparison_type.value,
    )


def build_validation_catalog(candidate: CriticCandidate) -> ValidationCatalog:
    """Build the small deterministic catalog visible to a reviewer."""

    objective_identity = _digest(candidate.objective)
    requirements = (
        CatalogRequirement(
            id=ORIGINAL_OBJECTIVE_REQUIREMENT,
            kind="objective",
            text=candidate.objective,
        ),
        *COMPLETION_REQUIREMENTS,
    )
    targets: list[CatalogTarget] = [
        CatalogTarget(id="target:answer", kind="answer", candidate_pointer="/answer"),
        CatalogTarget(
            id="target:objective", kind="objective", candidate_pointer="/objective"
        ),
    ]
    for index, finding in enumerate(candidate.findings):
        targets.append(
            CatalogTarget(
                id=f"target:finding:{finding.id}",
                kind="finding",
                candidate_pointer=f"/findings/{index}",
                result_ids=tuple(filter(None, (finding.result_id, finding.id))),
            )
        )
    metric_slots: dict[str, list[tuple[int, object]]] = {}
    for index, item in enumerate(candidate.metric_comparisons):
        slot = "target:metric:" + _digest(_metric_scope(item))
        metric_slots.setdefault(slot, []).append((index, item))
    for slot, entries in metric_slots.items():
        if len(entries) != 1:
            continue
        index, item = entries[0]
        targets.append(
            CatalogTarget(
                id=slot,
                kind="metric",
                candidate_pointer=f"/metric_comparisons/{index}",
                result_ids=tuple(filter(None, (item.result_id,))),
            )
        )
    statistic_slots: dict[str, list[tuple[int, object]]] = {}
    for index, item in enumerate(candidate.statistical_assessments):
        scope = (
            item.metric_key,
            tuple(
                sorted(
                    (d.name.strip().lower(), d.value.strip()) for d in item.dimensions
                )
            ),
            item.baseline_period,
            item.comparison_period,
            item.method,
        )
        slot = "target:statistic:" + _digest(scope)
        statistic_slots.setdefault(slot, []).append((index, item))
    for slot, entries in statistic_slots.items():
        if len(entries) != 1:
            continue
        index, item = entries[0]
        targets.append(
            CatalogTarget(
                id=slot,
                kind="statistic",
                candidate_pointer=f"/statistical_assessments/{index}",
                result_ids=tuple(filter(None, (item.result_id,))),
            )
        )
    for requirement in requirements:
        targets.append(
            CatalogTarget(
                id=f"target:missing:{requirement.id}", kind="missing_requirement"
            )
        )
    return ValidationCatalog(
        objective_identity=objective_identity,
        requirements=requirements,
        targets=tuple(targets),
    )


def _pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ReviewContractError(f"invalid JSON pointer: {pointer}")
    value = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(token)] if isinstance(value, list) else value[token]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ReviewContractError(
                f"JSON pointer does not resolve: {pointer}"
            ) from error
    return value


def _validate_anchor(
    anchor: ObjectionEvidence, candidate: CriticCandidate, ledger: AnalysisLedger
) -> None:
    if anchor.source is EvidenceAnchorSource.CANDIDATE:
        document = candidate.model_dump(mode="json")
    else:
        event = next(
            (item for item in ledger.tool_events if item.id == anchor.event_id), None
        )
        if event is None or event.status is not ToolEventStatus.SUCCEEDED:
            raise ReviewContractError(
                f"unknown or unsuccessful tool event: {anchor.event_id}"
            )
        document = {"arguments": event.arguments, "output": event.output}
    retained = _pointer(document, anchor.pointer)
    if type(retained) is not type(anchor.value) or retained != anchor.value:
        raise ReviewContractError(
            f"evidence value does not match retained value at {anchor.pointer}"
        )


def blocker_identity(blocker: ValidationBlocker, catalog: ValidationCatalog) -> str:
    """Assign identity independent of prose, evidence order, and result values."""

    return _digest(
        (
            VALIDATION_CONTRACT_VERSION,
            catalog.objective_identity,
            blocker.requirement_id,
            blocker.target_id,
            blocker.category.value,
        )
    )


def validate_review(
    review: ValidationResult,
    candidate: CriticCandidate,
    ledger: AnalysisLedger,
    *,
    catalog: ValidationCatalog | None = None,
) -> ValidationResult:
    """Validate all references and return the application-normalized review."""

    catalog = catalog or build_validation_catalog(candidate)
    requirement_ids = {item.id for item in catalog.requirements}
    targets = {item.id: item for item in catalog.targets}
    finding_ids = {item.id for item in candidate.findings}
    blockers: list[ValidationBlocker] = []
    identities: dict[str, ValidationBlocker] = {}
    incoming_blockers = review.blockers or adapt_legacy_issues(review.issues, candidate)
    for incoming in incoming_blockers:
        if incoming.requirement_id not in requirement_ids:
            raise ReviewContractError(f"unknown requirement: {incoming.requirement_id}")
        target = targets.get(incoming.target_id)
        if target is None:
            raise ReviewContractError(f"unknown target: {incoming.target_id}")
        allowed_result_ids = finding_ids | {
            result_id for item in catalog.targets for result_id in item.result_ids
        }
        if any(
            result_id not in allowed_result_ids
            for result_id in incoming.affected_result_ids
        ):
            raise ReviewContractError("blocker cites an unknown affected result")
        for anchor in incoming.evidence:
            _validate_anchor(anchor, candidate, ledger)
        if incoming.category is BlockerCategory.MISSING_REQUESTED_COMPARISON:
            requirement = next(
                item
                for item in catalog.requirements
                if item.id == incoming.requirement_id
            )
            if (
                requirement.kind != "objective"
                or requirement.text not in candidate.objective
                or incoming.objective_clause is None
                or incoming.objective_clause not in candidate.objective
            ):
                raise ReviewContractError(
                    "requested comparison is not anchored to the original objective"
                )
        if (
            incoming in review.blockers
            and incoming.category
            in {
                BlockerCategory.WRONG_GRAIN,
                BlockerCategory.WRONG_DENOMINATOR,
                BlockerCategory.INCORRECT_NUMERICAL_CLAIM,
                BlockerCategory.UNSUPPORTED_ASSERTED_FACT,
                BlockerCategory.SELECTED_RESULT_CONFLICT,
            }
            and not any(
                anchor.source is EvidenceAnchorSource.TOOL_EVENT
                for anchor in incoming.evidence
            )
        ):
            raise ReviewContractError(
                "a factual data objection requires successful execution evidence"
            )
        normalized = incoming.model_copy(
            update={"id": blocker_identity(incoming, catalog)}
        )
        prior = identities.get(normalized.id or "")
        if prior is not None and prior != normalized:
            raise ReviewContractError("conflicting duplicate blocker identity")
        if prior is None:
            identities[normalized.id or ""] = normalized
            blockers.append(normalized)
    limitations: list[ValidationLimitation] = []
    for limitation in review.limitations:
        if (
            limitation.requirement_id is not None
            and limitation.requirement_id not in requirement_ids
        ):
            raise ReviewContractError(
                f"unknown limitation requirement: {limitation.requirement_id}"
            )
        if limitation.target_id is not None and limitation.target_id not in targets:
            raise ReviewContractError(
                f"unknown limitation target: {limitation.target_id}"
            )
        for anchor in limitation.evidence:
            _validate_anchor(anchor, candidate, ledger)
        limitations.append(limitation)
    checked = set(review.checked_finding_ids)
    if not checked.issubset(finding_ids):
        raise ReviewContractError(
            "review checked a finding that is not in the candidate"
        )
    legacy_blocking = bool(review.issues)
    status = (
        ValidationStatus.REVISE
        if blockers or legacy_blocking
        else ValidationStatus.PASS
    )
    if review.status is ValidationStatus.REVISE and not blockers and not review.issues:
        status = ValidationStatus.REVISE
    return review.model_copy(
        update={
            "contract_version": VALIDATION_CONTRACT_VERSION,
            "status": status,
            "blockers": blockers,
            "limitations": limitations,
        }
    )


_ISSUE_CATEGORY = {
    "structured_metric_conflict": BlockerCategory.SELECTED_RESULT_CONFLICT,
    "structured_metric_completeness": BlockerCategory.OBJECTIVE_NOT_ANSWERED,
    "task_completeness": BlockerCategory.OBJECTIVE_NOT_ANSWERED,
    "chart_completeness": BlockerCategory.OBJECTIVE_NOT_ANSWERED,
    "evidence_provenance": BlockerCategory.UNSUPPORTED_ASSERTED_FACT,
    "denominator": BlockerCategory.WRONG_DENOMINATOR,
    "definition_error": BlockerCategory.WRONG_GRAIN,
    "structured_metric": BlockerCategory.INCORRECT_NUMERICAL_CLAIM,
}


def adapt_legacy_issues(
    issues: Iterable[ValidationIssue],
    candidate: CriticCandidate,
    *,
    requirement_id: str = "requirement:evidence",
) -> list[ValidationBlocker]:
    """Conservatively convert deterministic/legacy defects into blockers."""

    anchor = ObjectionEvidence(
        source=EvidenceAnchorSource.CANDIDATE, pointer="/answer", value=candidate.answer
    )
    blockers: list[ValidationBlocker] = []
    for issue in issues:
        category = _ISSUE_CATEGORY.get(
            issue.category or "", BlockerCategory.UNSUPPORTED_ASSERTED_FACT
        )
        repair_class = (
            RepairClass.COMPUTATION
            if category
            in {
                BlockerCategory.WRONG_GRAIN,
                BlockerCategory.WRONG_DENOMINATOR,
                BlockerCategory.SELECTED_RESULT_CONFLICT,
                BlockerCategory.OBJECTIVE_NOT_ANSWERED,
            }
            else RepairClass.SYNTHESIS_SELECTION
        )
        blockers.append(
            ValidationBlocker(
                category=category,
                requirement_id=requirement_id,
                target_id="target:answer",
                evidence=[anchor],
                message=issue.message,
                smallest_feasible_repair=issue.recommendation
                or "Correct or remove the unsupported material claim.",
                repair_class=repair_class,
            )
        )
    return blockers


def limitations_only(*categories: LimitationCategory) -> ValidationResult:
    """Convenience used by deterministic fixtures and direct callers."""

    return ValidationResult(
        contract_version=VALIDATION_CONTRACT_VERSION,
        status=ValidationStatus.PASS,
        limitations=[
            ValidationLimitation(
                category=category, message=category.value.replace("_", " ")
            )
            for category in categories
        ],
    )


def is_essential_impossible(review: ValidationResult) -> bool:
    return any(
        item.repair_class is RepairClass.IMPOSSIBLE_WITH_CURRENT_DATA
        for item in review.blockers
    )


def repair_class_for(review: ValidationResult) -> RepairClass:
    """Choose the least-permissive scope that can address every blocker."""

    if any(item.repair_class is RepairClass.COMPUTATION for item in review.blockers):
        return RepairClass.COMPUTATION
    return RepairClass.SYNTHESIS_SELECTION


__all__ = [
    "ORIGINAL_OBJECTIVE_REQUIREMENT",
    "ReviewContractError",
    "adapt_legacy_issues",
    "blocker_identity",
    "build_validation_catalog",
    "is_essential_impossible",
    "limitations_only",
    "repair_class_for",
    "validate_review",
]
