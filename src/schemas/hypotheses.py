"""Shared typed schemas for investigation hypotheses.

One rule governs hypothesis evidence everywhere it is written or read: an open
hypothesis may carry no evidence, and every resolved hypothesis — supported,
rejected, or inconclusive — must cite canonical executed evidence. The model
sees it in the field descriptions below and in the agent instructions; the state
tool enforces it when the transition is requested; final Lead validation and
offline evaluation apply the same predicate to persisted state.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

NonEmptyString = Annotated[str, Field(min_length=1)]

_EVIDENCE_RULE = (
    "Exact executed evidence references: a successful tool-event ID, saved "
    "query/script path, or verified artifact. Required and non-empty for any "
    "status other than open; leave empty while the hypothesis is open rather "
    "than inventing a reference."
)


class HypothesisStatus(StrEnum):
    """Current disposition of an investigation hypothesis."""

    OPEN = "open"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


def hypothesis_requires_evidence(status: HypothesisStatus | str) -> bool:
    """Return whether this disposition must cite executed evidence.

    The single shared predicate for the state tool, final Lead validation, and
    offline evaluation, so the three cannot drift apart on which transitions
    need provenance.
    """

    return HypothesisStatus(status) is not HypothesisStatus.OPEN


class Hypothesis(BaseModel):
    """An explicit, testable explanation tracked by the Lead."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    statement: NonEmptyString
    status: HypothesisStatus = Field(
        default=HypothesisStatus.OPEN,
        description=(
            "Resolve to supported, rejected, or inconclusive only once "
            "executed evidence supports that disposition; a resolved "
            "hypothesis without exact evidence_refs is rejected."
        ),
    )
    evidence_refs: list[NonEmptyString] = Field(
        default_factory=list,
        description=_EVIDENCE_RULE,
    )
    rationale: NonEmptyString | None = None
    parent_id: NonEmptyString | None = None


__all__ = [
    "Hypothesis",
    "HypothesisStatus",
    "NonEmptyString",
    "hypothesis_requires_evidence",
]
