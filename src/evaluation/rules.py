"""Registered scenario rules composed from generic offline primitives."""

from __future__ import annotations

from evaluation.engine import ScenarioRules
from evaluation.primitives import (
    DataQualityPolicy,
    StatisticsPolicy,
    TaskCompletenessPolicy,
    TextRule,
    contains_all_concepts,
    contains_asserted_mechanism,
    contains_material_driver,
    contains_non_driver_conclusion,
    contains_stable_conclusion,
)
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from schemas.audit import IssueSeverity


def canonical_rules() -> ScenarioRules:
    """Return evaluator-only rules for the canonical profitability scenario."""

    evaluation_spec = CANONICAL_PROFITABILITY_SCENARIO.to_evaluation_spec()
    return ScenarioRules(
        scenario_id=evaluation_spec.scenario_id,
        scenario_version=evaluation_spec.scenario_version,
        evaluator_version=evaluation_spec.evaluator_version,
        expected_metrics=evaluation_spec.ground_truth,
        root_cause_rules=(
            TextRule(
                check_id="primary_channel_driver",
                description=(
                    "final analysis identifies the target channel as the largest "
                    "material driver"
                ),
                predicate=lambda text: contains_material_driver(
                    text,
                    subject_terms=(r"\bmeta\b",),
                    material_terms=(
                        r"largest",
                        r"primary",
                        r"main",
                        r"material",
                        r"major",
                        r"biggest",
                    ),
                    outcome_terms=(r"profit", r"decline", r"driver"),
                ),
            ),
            TextRule(
                check_id="conversion_mechanism",
                description=(
                    "final analysis asserts that conversion deterioration explains "
                    "the acquisition decline"
                ),
                predicate=lambda text: contains_asserted_mechanism(
                    text,
                    subject_terms=(r"\bmeta\b",),
                    mechanism_terms=(r"conversion",),
                    change_terms=(
                        r"declin",
                        r"fell",
                        r"drop",
                        r"down",
                        r"deteriorat",
                        r"lower",
                        r"decreas",
                        r"reduc",
                        r"worsen",
                    ),
                    causal_terms=(
                        r"drove",
                        r"drives",
                        r"explain",
                        r"caused",
                        r"cause",
                        r"led to",
                        r"resulted in",
                        r"primary",
                        r"main",
                        r"largest",
                        r"responsible",
                        r"accounted for",
                        r"mechanism",
                    ),
                    uncertainty_terms=(
                        r"may be worth",
                        r"might",
                        r"could",
                        r"possible",
                        r"possibly",
                        r"uncertain",
                        r"unclear",
                        r"unknown",
                        r"question",
                        r"investigat",
                    ),
                ),
            ),
            TextRule(
                check_id="acquisition_efficiency_decomposition",
                description=(
                    "final analysis covers spend, sessions, conversion, acquired "
                    "customers, CAC, and LTV"
                ),
                predicate=lambda text: contains_all_concepts(
                    text,
                    (
                        r"marketing[_ ]spend|spend",
                        r"conversion",
                        r"acquired[_ ]customers|new customers|customer volume",
                        r"\bcac\b|customer[_ ]acquisition[_ ]cost",
                        r"\bltv\b|lifetime value|customer value",
                    ),
                ),
            ),
            TextRule(
                check_id="stable_ltv",
                description=(
                    "final analysis characterizes acquired-customer LTV as stable"
                ),
                predicate=lambda text: contains_stable_conclusion(
                    text,
                    value_terms=(r"\bltv\b|lifetime value|customer value",),
                    stable_terms=(
                        r"stable",
                        r"unchanged",
                        r"approximately flat",
                        r"effectively identical",
                        r"held",
                    ),
                ),
            ),
            TextRule(
                check_id="margin_non_driver",
                description=(
                    "final analysis rules out broad COGS or margin deterioration"
                ),
                predicate=lambda text: contains_non_driver_conclusion(
                    text,
                    subject_terms=(r"cogs|margin",),
                    non_driver_terms=(
                        r"stable",
                        r"unchanged",
                        r"no broad",
                        r"not .*driver",
                        r"did not",
                        r"didn't",
                        r"not explain",
                        r"not the cause",
                        r"not responsible",
                        r"consistent",
                    ),
                ),
            ),
        ),
        data_quality_policy=DataQualityPolicy(
            maximum_issue_severity=IssueSeverity.LOW,
        ),
        statistics_policy=StatisticsPolicy(
            required_specialist_roles=("statistician",),
        ),
        task_policy=TaskCompletenessPolicy(
            required_agent_roles=(
                "data_auditor",
                "lead",
                "analyst",
                "statistician",
                "critic",
            )
        ),
    )


def rules_for_scenario(
    scenario_id: str,
    scenario_version: str | None = None,
) -> ScenarioRules:
    """Resolve the deterministic evaluator through the versioned catalog."""

    from scenarios.catalog import get_scenario

    try:
        return get_scenario(scenario_id, scenario_version).evaluator_rules()
    except (KeyError, ValueError) as exc:
        raise KeyError(
            f"no offline evaluator is registered for "
            f"{scenario_id}@{scenario_version or '*'}"
        ) from exc


__all__ = ["canonical_rules", "rules_for_scenario"]
