"""Versioned discovery and lookup for deterministic evaluation scenarios."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from evaluation.contracts import (
    ModelVisibleScenarioContext,
    ScenarioEvaluationSpec,
    ScenarioMetadata,
)
from evaluation.engine import ScenarioRules
from scenarios.definitions import (
    BUSINESS_ROOT_CAUSE_SCENARIOS,
    CANONICAL_PROFITABILITY_SCENARIO,
    DATA_QUALITY_SCENARIOS,
    EXPERIMENT_SCENARIOS,
)
from scenarios.definitions.models import ScenarioDefinition, ScenarioModelContext
from scenarios.invariants import (
    ScenarioInvariantSuite,
    experiment_invariant_suite,
    synthetic_ecommerce_invariant_suite,
)
from schemas.metrics import MetricComparison

if TYPE_CHECKING:
    from scenarios.injection import ScenarioRun


@dataclass(frozen=True, order=True, slots=True)
class ScenarioKey:
    """Stable lookup key for one versioned scenario definition."""

    scenario_id: str
    scenario_version: str

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("scenario_id must not be empty")
        if not self.scenario_version.strip():
            raise ValueError("scenario_version must not be empty")
        if re.fullmatch(r"\d+\.\d+", self.scenario_version) is None:
            raise ValueError("scenario_version must use major.minor format")


ScenarioGenerator = Callable[..., "ScenarioRun"]
ScenarioEvaluator = Callable[[], ScenarioRules]


@dataclass(frozen=True, slots=True)
class ScenarioRegistration:
    """Complete generator/evaluator registration for one versioned scenario."""

    metadata: ScenarioMetadata
    evaluation_spec: ScenarioEvaluationSpec
    model_visible_context: ScenarioModelContext
    generator_name: str
    generator: ScenarioGenerator
    evaluator_name: str
    evaluator: ScenarioEvaluator
    invariant_suite: ScenarioInvariantSuite

    @property
    def key(self) -> ScenarioKey:
        """Return the unique catalog key."""

        return ScenarioKey(self.metadata.scenario_id, self.metadata.scenario_version)

    @property
    def scenario_id(self) -> str:
        """Return the registered scenario identifier."""

        return self.metadata.scenario_id

    @property
    def scenario_version(self) -> str:
        """Return the registered scenario version."""

        return self.metadata.scenario_version

    def generate(self, *args: Any, **kwargs: Any) -> ScenarioRun:
        """Generate this scenario through its registered deterministic factory."""

        return self.generator(*args, **kwargs)

    def evaluator_rules(self) -> ScenarioRules:
        """Build the evaluator rules registered for this scenario version."""

        rules = self.evaluator()
        if (
            rules.scenario_id != self.scenario_id
            or rules.scenario_version != self.scenario_version
            or rules.evaluator_version != self.metadata.evaluator_version
        ):
            raise ScenarioCatalogError(
                f"evaluator {self.evaluator_name} does not match "
                f"{self.scenario_id}@{self.scenario_version}"
            )
        return rules

    def validate_generated(self, generated: ScenarioRun):
        """Run the registered common and observable-ground-truth invariants."""

        if (
            generated.definition.scenario_id != self.scenario_id
            or generated.definition.scenario_version != self.scenario_version
            or generated.definition.evaluator_version != self.metadata.evaluator_version
        ):
            raise ScenarioCatalogError(
                f"generator {self.generator_name} returned an unrelated or "
                "incorrectly versioned scenario"
            )
        return self.invariant_suite.validate(generated.dataset)

    def generate_validated(self, *args: Any, **kwargs: Any) -> ScenarioRun:
        """Generate and fail fast if common or observable invariants do not pass."""

        generated = self.generate(*args, **kwargs)
        self.validate_generated(generated).assert_valid()
        return generated

    def model_context_contract(self) -> ModelVisibleScenarioContext:
        """Return the generic model-visible projection without evaluator fields."""

        return ModelVisibleScenarioContext(
            scenario_id=self.model_visible_context.scenario_id,
            scenario_version=self.model_visible_context.scenario_version,
            name=self.model_visible_context.name,
            user_question=self.model_visible_context.user_question,
        )


class ScenarioCatalogError(ValueError):
    """Raised for duplicate, missing, or inconsistent scenario registrations."""


class ScenarioCatalog:
    """Immutable-style registry with explicit version-aware resolution."""

    def __init__(self, registrations: tuple[ScenarioRegistration, ...]) -> None:
        self._registrations = tuple(registrations)
        by_key: dict[ScenarioKey, ScenarioRegistration] = {}
        for registration in self._registrations:
            if registration.key in by_key:
                raise ScenarioCatalogError(
                    f"duplicate scenario registration: {registration.key}"
                )
            if (
                registration.evaluation_spec.scenario_id != registration.scenario_id
                or registration.evaluation_spec.scenario_version
                != registration.scenario_version
                or registration.evaluation_spec.evaluator_version
                != registration.metadata.evaluator_version
            ):
                raise ScenarioCatalogError(
                    f"evaluation contract does not match {registration.key}"
                )
            if registration.invariant_suite.expected_metrics != tuple(
                registration.evaluation_spec.ground_truth
            ):
                raise ScenarioCatalogError(
                    f"invariant ground truth does not match {registration.key}"
                )
            if (
                registration.model_visible_context.scenario_id
                != registration.scenario_id
                or registration.model_visible_context.scenario_version
                != registration.scenario_version
                or registration.model_visible_context.name != registration.metadata.name
                or registration.model_visible_context.user_question
                != registration.metadata.user_question
            ):
                raise ScenarioCatalogError(
                    f"model-visible context does not match {registration.key}"
                )
            by_key[registration.key] = registration
        self._by_key = by_key

    def __iter__(self) -> Iterator[ScenarioRegistration]:
        return iter(self._registrations)

    def __len__(self) -> int:
        return len(self._registrations)

    @property
    def registrations(self) -> tuple[ScenarioRegistration, ...]:
        """Return registrations in stable key order."""

        return tuple(sorted(self._registrations, key=lambda item: item.key))

    def resolve(
        self,
        scenario_id: str,
        scenario_version: str | None = None,
    ) -> ScenarioRegistration:
        """Resolve one scenario, requiring a version when multiple exist."""

        candidates = [
            registration
            for registration in self._registrations
            if registration.scenario_id == scenario_id
        ]
        if scenario_version is not None:
            try:
                return self._by_key[ScenarioKey(scenario_id, scenario_version)]
            except KeyError as exc:
                raise ScenarioCatalogError(
                    f"unknown scenario registration: {scenario_id}@{scenario_version}"
                ) from exc
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise ScenarioCatalogError(f"unknown scenario registration: {scenario_id}")
        versions = ", ".join(sorted(item.scenario_version for item in candidates))
        raise ScenarioCatalogError(
            f"scenario {scenario_id!r} has multiple versions; choose one of: {versions}"
        )

    def generator_for(
        self,
        scenario_id: str,
        scenario_version: str | None = None,
    ) -> ScenarioGenerator:
        """Return the uniquely resolved generator factory."""

        return self.resolve(scenario_id, scenario_version).generator

    def evaluator_for(
        self,
        scenario_id: str,
        scenario_version: str | None = None,
    ) -> ScenarioEvaluator:
        """Return the uniquely resolved evaluator factory."""

        return self.resolve(scenario_id, scenario_version).evaluator


def _canonical_registration() -> ScenarioRegistration:
    from evaluation.rules import canonical_rules
    from scenarios.injection import (
        generate_canonical_profitability_scenario,
        observe_canonical_ground_truth,
    )

    definition = CANONICAL_PROFITABILITY_SCENARIO
    return ScenarioRegistration(
        metadata=definition.to_metadata(),
        evaluation_spec=definition.to_evaluation_spec(),
        model_visible_context=definition.model_visible_context(),
        generator_name="generate_canonical_profitability_scenario",
        generator=generate_canonical_profitability_scenario,
        evaluator_name="canonical_rules",
        evaluator=canonical_rules,
        invariant_suite=synthetic_ecommerce_invariant_suite(
            expected_metrics=definition.ground_truth,
            metric_observer=observe_canonical_ground_truth,
        ),
    )


def _business_registration(
    definition: ScenarioDefinition,
    *,
    generator_name: str,
    generator: ScenarioGenerator,
    evaluator_name: str,
    evaluator: ScenarioEvaluator,
    metric_observer: Callable[[object], Sequence[MetricComparison]],
) -> ScenarioRegistration:
    return ScenarioRegistration(
        metadata=definition.to_metadata(),
        evaluation_spec=definition.to_evaluation_spec(),
        model_visible_context=definition.model_visible_context(),
        generator_name=generator_name,
        generator=generator,
        evaluator_name=evaluator_name,
        evaluator=evaluator,
        invariant_suite=synthetic_ecommerce_invariant_suite(
            expected_metrics=definition.ground_truth,
            metric_observer=metric_observer,
        ),
    )


def _business_registrations() -> tuple[ScenarioRegistration, ...]:
    from evaluation.rules import (
        cogs_margin_rules,
        discount_refund_rules,
        retention_rules,
    )
    from scenarios.business_scenarios import (
        generate_cogs_margin_deterioration_scenario,
        generate_discount_refund_deterioration_scenario,
        generate_retention_deterioration_scenario,
        observe_cogs_margin_ground_truth,
        observe_discount_refund_ground_truth,
        observe_retention_ground_truth,
    )

    definitions = {
        definition.scenario_id: definition
        for definition in BUSINESS_ROOT_CAUSE_SCENARIOS
    }
    return (
        _business_registration(
            definitions["retention-q2-deterioration"],
            generator_name="generate_retention_deterioration_scenario",
            generator=generate_retention_deterioration_scenario,
            evaluator_name="retention_rules",
            evaluator=retention_rules,
            metric_observer=observe_retention_ground_truth,
        ),
        _business_registration(
            definitions["cogs-q2-margin-deterioration"],
            generator_name="generate_cogs_margin_deterioration_scenario",
            generator=generate_cogs_margin_deterioration_scenario,
            evaluator_name="cogs_margin_rules",
            evaluator=cogs_margin_rules,
            metric_observer=observe_cogs_margin_ground_truth,
        ),
        _business_registration(
            definitions["discount-refund-q2-deterioration"],
            generator_name="generate_discount_refund_deterioration_scenario",
            generator=generate_discount_refund_deterioration_scenario,
            evaluator_name="discount_refund_rules",
            evaluator=discount_refund_rules,
            metric_observer=observe_discount_refund_ground_truth,
        ),
    )


def _data_quality_registrations() -> tuple[ScenarioRegistration, ...]:
    from evaluation.rules import (
        missing_reporting_day_rules,
        partial_latest_day_rules,
    )
    from scenarios.data_quality_scenarios import (
        generate_missing_reporting_day_scenario,
        generate_partial_latest_reporting_day_scenario,
        observe_missing_reporting_day_ground_truth,
        observe_partial_latest_reporting_day_ground_truth,
    )

    definitions = {
        definition.scenario_id: definition for definition in DATA_QUALITY_SCENARIOS
    }
    return (
        ScenarioRegistration(
            metadata=definitions["missing-reporting-day"].to_metadata(),
            evaluation_spec=definitions["missing-reporting-day"].to_evaluation_spec(),
            model_visible_context=definitions[
                "missing-reporting-day"
            ].model_visible_context(),
            generator_name="generate_missing_reporting_day_scenario",
            generator=generate_missing_reporting_day_scenario,
            evaluator_name="missing_reporting_day_rules",
            evaluator=missing_reporting_day_rules,
            invariant_suite=synthetic_ecommerce_invariant_suite(
                expected_metrics=definitions["missing-reporting-day"].ground_truth,
                metric_observer=observe_missing_reporting_day_ground_truth,
            ),
        ),
        ScenarioRegistration(
            metadata=definitions["partial-latest-reporting-day"].to_metadata(),
            evaluation_spec=definitions[
                "partial-latest-reporting-day"
            ].to_evaluation_spec(),
            model_visible_context=definitions[
                "partial-latest-reporting-day"
            ].model_visible_context(),
            generator_name="generate_partial_latest_reporting_day_scenario",
            generator=generate_partial_latest_reporting_day_scenario,
            evaluator_name="partial_latest_day_rules",
            evaluator=partial_latest_day_rules,
            invariant_suite=synthetic_ecommerce_invariant_suite(
                expected_metrics=definitions[
                    "partial-latest-reporting-day"
                ].ground_truth,
                metric_observer=observe_partial_latest_reporting_day_ground_truth,
            ),
        ),
    )


def _experiment_registrations() -> tuple[ScenarioRegistration, ...]:
    from evaluation.rules import (
        immaterial_experiment_rules,
        meaningful_experiment_rules,
        no_effect_experiment_rules,
    )
    from scenarios.experiment_scenarios import (
        generate_immaterial_experiment_scenario,
        generate_meaningful_experiment_scenario,
        generate_no_effect_experiment_scenario,
        observe_immaterial_experiment_ground_truth,
        observe_meaningful_experiment_ground_truth,
        observe_no_effect_experiment_ground_truth,
    )

    definitions = {
        definition.scenario_id: definition for definition in EXPERIMENT_SCENARIOS
    }
    registrations = (
        (
            "meaningful-ab-treatment-effect",
            "generate_meaningful_experiment_scenario",
            generate_meaningful_experiment_scenario,
            "meaningful_experiment_rules",
            meaningful_experiment_rules,
            observe_meaningful_experiment_ground_truth,
        ),
        (
            "no-effect-ab-experiment",
            "generate_no_effect_experiment_scenario",
            generate_no_effect_experiment_scenario,
            "no_effect_experiment_rules",
            no_effect_experiment_rules,
            observe_no_effect_experiment_ground_truth,
        ),
        (
            "significant-but-immaterial-ab-effect",
            "generate_immaterial_experiment_scenario",
            generate_immaterial_experiment_scenario,
            "immaterial_experiment_rules",
            immaterial_experiment_rules,
            observe_immaterial_experiment_ground_truth,
        ),
    )
    return tuple(
        ScenarioRegistration(
            metadata=definitions[scenario_id].to_metadata(),
            evaluation_spec=definitions[scenario_id].to_evaluation_spec(),
            model_visible_context=definitions[scenario_id].model_visible_context(),
            generator_name=generator_name,
            generator=generator,
            evaluator_name=evaluator_name,
            evaluator=evaluator,
            invariant_suite=experiment_invariant_suite(
                expected_metrics=definitions[scenario_id].ground_truth,
                metric_observer=observer,
            ),
        )
        for (
            scenario_id,
            generator_name,
            generator,
            evaluator_name,
            evaluator,
            observer,
        ) in registrations
    )


def discover_scenarios() -> ScenarioCatalog:
    """Discover all built-in versioned scenario registrations."""

    # Keep discovery explicit and deterministic until the catalog is large
    # enough to justify module scanning. Duplicate keys are rejected by the
    # catalog constructor rather than silently shadowed.
    return ScenarioCatalog(
        (
            _canonical_registration(),
            *_business_registrations(),
            *_data_quality_registrations(),
            *_experiment_registrations(),
        )
    )


def get_scenario(
    scenario_id: str,
    scenario_version: str | None = None,
) -> ScenarioRegistration:
    """Resolve one built-in scenario registration."""

    return discover_scenarios().resolve(scenario_id, scenario_version)


__all__ = [
    "ScenarioCatalog",
    "ScenarioCatalogError",
    "ScenarioEvaluator",
    "ScenarioGenerator",
    "ScenarioKey",
    "ScenarioRegistration",
    "discover_scenarios",
    "get_scenario",
]
