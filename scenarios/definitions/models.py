"""Pydantic contracts for deterministic scenario metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from schemas.metrics import MetricComparisonType, MetricDefinitionContext

if TYPE_CHECKING:
    from evaluation.contracts import (
        ModelVisibleScenarioContext,
        ScenarioEvaluationSpec,
        ScenarioMetadata,
    )

NonEmptyString = Annotated[str, Field(min_length=1)]
VersionString = Annotated[str, Field(pattern=r"^\d+\.\d+$", min_length=3)]


class InjectedCondition(BaseModel):
    """A known transformation applied to one or more baseline tables."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    description: NonEmptyString
    affected_tables: tuple[NonEmptyString, ...] = Field(min_length=1)
    relative_change: float | None = None


class GroundTruthMetric(BaseModel):
    """Evaluator-only expected value and generic metric identity."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    description: NonEmptyString
    comparison: NonEmptyString
    metric_key: NonEmptyString
    dimensions: dict[NonEmptyString, NonEmptyString] = Field(default_factory=dict)
    baseline_period: NonEmptyString
    comparison_period: NonEmptyString
    comparison_type: MetricComparisonType
    value_unit: NonEmptyString = "relative_change_fraction"
    definition_context: MetricDefinitionContext | None = None
    expected_relative_change: float
    tolerance: float = Field(ge=0)


class ScenarioDefinition(BaseModel):
    """Typed evaluator metadata for one deterministic business scenario."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: NonEmptyString
    scenario_version: VersionString = "1.0"
    name: NonEmptyString
    user_question: NonEmptyString
    seed: int = Field(default=42, ge=0)
    generation_config: dict[str, JsonValue] = Field(default_factory=dict)
    evaluator_version: VersionString = "1.0"
    injected_conditions: tuple[InjectedCondition, ...] = Field(min_length=1)
    expected_primary_driver: NonEmptyString
    expected_secondary_findings: tuple[NonEmptyString, ...] = Field(min_length=1)
    known_non_drivers: tuple[NonEmptyString, ...] = Field(min_length=1)
    expected_data_quality_findings: tuple[NonEmptyString, ...] = Field(min_length=1)
    ground_truth: tuple[GroundTruthMetric, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identities_are_unique(self) -> ScenarioDefinition:
        metric_ids = [metric.id for metric in self.ground_truth]
        comparisons = [metric.comparison for metric in self.ground_truth]
        if len(set(metric_ids)) != len(metric_ids):
            raise ValueError("ground_truth metric IDs must be unique")
        if len(set(comparisons)) != len(comparisons):
            raise ValueError("ground_truth comparison IDs must be unique")
        condition_ids = [condition.id for condition in self.injected_conditions]
        if len(set(condition_ids)) != len(condition_ids):
            raise ValueError("injected condition IDs must be unique")
        return self

    def model_visible_context(self) -> ScenarioModelContext:
        """Return the allow-listed context safe to pass to an analysis model.

        ``ScenarioDefinition`` remains evaluator-only because it contains
        injected conditions and expected answers.  Callers constructing a
        prompt must use this projection rather than dumping the definition.
        """

        return ScenarioModelContext(
            scenario_id=self.scenario_id,
            scenario_version=self.scenario_version,
            name=self.name,
            user_question=self.user_question,
        )

    def metadata_contract(self) -> ScenarioMetadata:
        """Project public scenario fields into the generic versioned contract."""

        # Imported lazily because the evaluation contracts intentionally import
        # these neutral scenario value models for their evaluator-only fields.
        from evaluation.contracts import ScenarioMetadata

        return ScenarioMetadata(
            scenario_id=self.scenario_id,
            scenario_version=self.scenario_version,
            name=self.name,
            seed=self.seed,
            generation_config=self.generation_config,
            user_question=self.user_question,
            evaluator_version=self.evaluator_version,
        )

    def to_metadata(self) -> ScenarioMetadata:
        """Alias for callers using the generic contract naming convention."""

        return self.metadata_contract()

    def evaluation_contract(self) -> ScenarioEvaluationSpec:
        """Project evaluator-only fields into the generic versioned contract."""

        from evaluation.contracts import ScenarioEvaluationSpec

        return ScenarioEvaluationSpec(
            scenario_id=self.scenario_id,
            scenario_version=self.scenario_version,
            evaluator_version=self.evaluator_version,
            injected_conditions=self.injected_conditions,
            expected_primary_driver=self.expected_primary_driver,
            expected_secondary_findings=self.expected_secondary_findings,
            known_non_drivers=self.known_non_drivers,
            expected_data_quality_findings=self.expected_data_quality_findings,
            ground_truth=self.ground_truth,
        )

    def to_evaluation_spec(self) -> ScenarioEvaluationSpec:
        """Alias for callers using the generic contract naming convention."""

        return self.evaluation_contract()

    def model_visible_contract(self) -> ModelVisibleScenarioContext:
        """Return the generic allow-listed model-visible projection."""

        from evaluation.contracts import ModelVisibleScenarioContext

        return ModelVisibleScenarioContext(
            scenario_id=self.scenario_id,
            scenario_version=self.scenario_version,
            name=self.name,
            user_question=self.user_question,
        )


class ScenarioModelContext(BaseModel):
    """Strict model-visible scenario projection with no evaluator fields."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: NonEmptyString
    scenario_version: VersionString
    name: NonEmptyString
    user_question: NonEmptyString
