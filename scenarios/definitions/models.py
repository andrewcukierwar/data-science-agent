"""Pydantic contracts for deterministic scenario metadata."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

NonEmptyString = Annotated[str, Field(min_length=1)]


class InjectedCondition(BaseModel):
    """A known transformation applied to one or more baseline tables."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    description: NonEmptyString
    affected_tables: tuple[NonEmptyString, ...] = Field(min_length=1)
    relative_change: float | None = None


class GroundTruthMetric(BaseModel):
    """Expected relative change and tolerance for an evaluator metric."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    description: NonEmptyString
    comparison: NonEmptyString
    value_unit: NonEmptyString = "relative_change_fraction"
    expected_relative_change: float
    tolerance: float = Field(ge=0)


class ScenarioDefinition(BaseModel):
    """Typed evaluator metadata for one deterministic business scenario."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: NonEmptyString
    name: NonEmptyString
    user_question: NonEmptyString
    injected_conditions: tuple[InjectedCondition, ...] = Field(min_length=1)
    expected_primary_driver: NonEmptyString
    expected_secondary_findings: tuple[NonEmptyString, ...] = Field(min_length=1)
    known_non_drivers: tuple[NonEmptyString, ...] = Field(min_length=1)
    expected_data_quality_findings: tuple[NonEmptyString, ...] = Field(min_length=1)
    ground_truth: tuple[GroundTruthMetric, ...] = Field(min_length=1)
