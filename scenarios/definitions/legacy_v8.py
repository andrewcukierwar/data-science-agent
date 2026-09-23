"""Read-only scenario definitions for evaluation under the frozen v8 contract.

These snapshots preserve the v1.0 public questions and v1.2 evaluator identity.
They reuse the same deterministic source definitions and expected numerical
values, without modifying or migrating any retained v8 workspace.
"""

from scenarios.definitions.business_root_cause import BUSINESS_ROOT_CAUSE_SCENARIOS
from scenarios.definitions.canonical_profitability import (
    CANONICAL_PROFITABILITY_SCENARIO,
)
from scenarios.definitions.data_quality import DATA_QUALITY_SCENARIOS
from scenarios.definitions.experiments import EXPERIMENT_SCENARIOS
from scenarios.definitions.mix import CHANNEL_MIX_CONFOUNDING_SCENARIO
from scenarios.definitions.models import ScenarioDefinition

_LEGACY_QUESTIONS = {
    "missing-reporting-day": (
        "Assess whether the available reporting supports a reliable Q2 business "
        "comparison and describe any limitations."
    ),
    "partial-latest-reporting-day": (
        "Assess whether the latest available reporting period is complete enough "
        "for a business decision and describe any limitations."
    ),
    "retention-q2-deterioration": (
        "Why did profitability change across customer cohorts, and what should "
        "the company do about it?"
    ),
    "cogs-q2-margin-deterioration": (
        "Why did profitability change in the latest reporting period, and what "
        "should the company do about it?"
    ),
    "discount-refund-q2-deterioration": (
        "Why did profitability change in the latest reporting period, and what "
        "should the company do about it?"
    ),
    "channel-mix-confounding": (
        "Did a channel change cause the latest acquisition performance change, "
        "and what should be investigated?"
    ),
    "meaningful-ab-treatment-effect": (
        "Should the company roll out the tested customer experience based on "
        "the observed experiment results?"
    ),
    "no-effect-ab-experiment": (
        "Should the company roll out the tested customer experience based on "
        "the observed experiment results?"
    ),
    "significant-but-immaterial-ab-effect": (
        "Should the company roll out the tested customer experience based on "
        "the observed experiment results?"
    ),
}


def _legacy_definition(definition: ScenarioDefinition) -> ScenarioDefinition:
    updates: dict[str, object] = {
        "scenario_version": "1.0",
        "evaluator_version": "1.2",
    }
    if definition.scenario_id in _LEGACY_QUESTIONS:
        updates["user_question"] = _LEGACY_QUESTIONS[definition.scenario_id]
    if definition.statistical_expectation is not None:
        updates["statistical_expectation"] = (
            definition.statistical_expectation.model_copy(
                update={
                    "required_procedure": None,
                    "required_effect_size_method": None,
                    "definition_context": None,
                }
            )
        )
    return definition.model_copy(update=updates)


LEGACY_V8_SCENARIOS = tuple(
    _legacy_definition(definition)
    for definition in (
        CANONICAL_PROFITABILITY_SCENARIO,
        *BUSINESS_ROOT_CAUSE_SCENARIOS,
        *DATA_QUALITY_SCENARIOS,
        *EXPERIMENT_SCENARIOS,
        CHANNEL_MIX_CONFOUNDING_SCENARIO,
    )
)


__all__ = ["LEGACY_V8_SCENARIOS"]
