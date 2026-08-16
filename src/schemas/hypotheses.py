"""Shared typed schemas for investigation hypotheses."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

NonEmptyString = Annotated[str, Field(min_length=1)]


class HypothesisStatus(StrEnum):
    """Current disposition of an investigation hypothesis."""

    OPEN = "open"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class Hypothesis(BaseModel):
    """An explicit, testable explanation tracked by the Lead."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    statement: NonEmptyString
    status: HypothesisStatus = HypothesisStatus.OPEN
    evidence_refs: list[NonEmptyString] = Field(default_factory=list)
    rationale: NonEmptyString | None = None
    parent_id: NonEmptyString | None = None
