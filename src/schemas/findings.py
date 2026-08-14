"""Schemas for specialist findings and structured specialist results."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

NonEmptyString = Annotated[str, Field(min_length=1)]


class ConfidenceLevel(StrEnum):
    """Confidence attached to an analytical finding."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Finding(BaseModel):
    """A concise claim supported by traceable analytical evidence."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    statement: NonEmptyString
    metric: NonEmptyString | None = None
    value: float | None = None
    evidence_refs: list[NonEmptyString] = Field(min_length=1)
    confidence: ConfidenceLevel
    caveats: list[NonEmptyString] = Field(default_factory=list)


class SpecialistResult(BaseModel):
    """Structured output returned by a specialist to the Lead agent."""

    model_config = ConfigDict(extra="forbid")

    objective: NonEmptyString
    findings: list[Finding] = Field(default_factory=list)
    artifacts: list[NonEmptyString] = Field(default_factory=list)
    methods_used: list[NonEmptyString] = Field(default_factory=list)
    follow_up_questions: list[NonEmptyString] = Field(default_factory=list)
    caveats: list[NonEmptyString] = Field(default_factory=list)
