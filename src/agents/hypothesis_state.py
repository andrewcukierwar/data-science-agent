"""Validation for hypothesis state transitions.

An invalid resolved hypothesis used to be accepted into the ledger and only
surface at the end of the run, when the final Lead response was validated. By
then the poisoned state had already been persisted and appended to the
append-only history, the model had no chance to correct it, and a resumed run
could inherit it.

This module moves the check to the moment the transition is requested. It
applies the one shared rule from ``schemas.hypotheses``: an open hypothesis may
carry no evidence, and every resolved hypothesis must cite canonical executed
evidence.
"""

from __future__ import annotations

from agents.evidence import (
    executed_references,
    finding_reference_aliases,
    resolve_citations,
)
from orchestration.ledger import AnalysisLedger
from schemas.run_state import Hypothesis, HypothesisStatus, hypothesis_requires_evidence

# The refusal names references the model can actually use. It is bounded so a
# long-running investigation cannot flood the model channel with a tool error.
MAX_SUGGESTED_REFERENCES = 20


class HypothesisEvidenceError(ValueError):
    """Raised when a resolved hypothesis does not cite executed evidence.

    The typed attributes carry everything a caller needs to explain the refusal
    without re-deriving it: which transition was refused, which references did
    not resolve, and which references are available instead.
    """

    def __init__(
        self,
        hypothesis: Hypothesis,
        *,
        unresolved_refs: tuple[str, ...],
        resolved_refs: tuple[str, ...],
        available_refs: tuple[str, ...],
    ) -> None:
        self.hypothesis_id = hypothesis.id
        self.requested_status = hypothesis.status
        self.unresolved_refs = unresolved_refs
        self.resolved_refs = resolved_refs
        self.available_refs = available_refs
        detail = (
            ", ".join(unresolved_refs)
            if unresolved_refs
            else "no evidence_refs were supplied"
        )
        super().__init__(
            f"hypothesis {hypothesis.id} cannot be recorded as "
            f"{hypothesis.status.value} because it cites no executed evidence: "
            f"{detail}"
        )

    def as_tool_data(self) -> dict[str, object]:
        """Return an actionable payload for the model-visible tool response."""

        return {
            "hypothesis_id": self.hypothesis_id,
            "requested_status": self.requested_status.value,
            "unresolved_evidence_refs": list(self.unresolved_refs),
            "resolved_evidence_refs": list(self.resolved_refs),
            "available_evidence_refs": list(self.available_refs),
            "remedy": (
                "Keep the hypothesis open, or resolve it citing exact "
                "references from available_evidence_refs. Do not invent a "
                "reference and do not cite a failed execution."
            ),
        }


def canonical_hypothesis_evidence(
    hypothesis: Hypothesis,
    ledger: AnalysisLedger,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split a hypothesis's citations into canonical and unresolved references."""

    resolution = resolve_citations(
        hypothesis.evidence_refs,
        executed_refs=executed_references(ledger),
        aliases=finding_reference_aliases(ledger),
    )
    return resolution.resolved, resolution.unresolved


def hypothesis_has_executed_evidence(
    hypothesis: Hypothesis,
    ledger: AnalysisLedger,
) -> bool:
    """Return whether this hypothesis satisfies the shared evidence rule."""

    if not hypothesis_requires_evidence(hypothesis.status):
        return True
    resolved, unresolved = canonical_hypothesis_evidence(hypothesis, ledger)
    return bool(resolved) and not unresolved


def validate_hypothesis_transition(
    hypothesis: Hypothesis,
    ledger: AnalysisLedger,
) -> Hypothesis:
    """Return the hypothesis to persist, or refuse an unsupported transition.

    An open hypothesis is returned untouched: it may legitimately have no
    evidence yet, and canonicalizing what it does cite could quietly drop a
    reference the model still intends to use. A resolved hypothesis is rewritten
    with its canonical references so the persisted state, the final Lead result,
    and offline evaluation all read the same thing.
    """

    if not hypothesis_requires_evidence(hypothesis.status):
        return hypothesis
    resolved, unresolved = canonical_hypothesis_evidence(hypothesis, ledger)
    if unresolved or not resolved:
        raise HypothesisEvidenceError(
            hypothesis,
            unresolved_refs=unresolved,
            resolved_refs=resolved,
            available_refs=tuple(
                sorted(executed_references(ledger))[:MAX_SUGGESTED_REFERENCES]
            ),
        )
    return hypothesis.model_copy(update={"evidence_refs": list(resolved)})


__all__ = [
    "MAX_SUGGESTED_REFERENCES",
    "HypothesisEvidenceError",
    "HypothesisStatus",
    "canonical_hypothesis_evidence",
    "hypothesis_has_executed_evidence",
    "validate_hypothesis_transition",
]
