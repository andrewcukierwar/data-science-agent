"""Synthetic datasets and deterministic evaluation scenarios."""

from scenarios.business_scenarios import (
    CogsMarginScenarioInjectionConfig,
    DiscountRefundScenarioInjectionConfig,
    RetentionScenarioInjectionConfig,
    generate_cogs_margin_deterioration_scenario,
    generate_discount_refund_deterioration_scenario,
    generate_retention_deterioration_scenario,
    inject_cogs_margin_deterioration,
    inject_discount_refund_deterioration,
    inject_retention_deterioration,
    observe_cogs_margin_ground_truth,
    observe_discount_refund_ground_truth,
    observe_retention_ground_truth,
)
from scenarios.data_quality_scenarios import (
    MissingReportingDayInjectionConfig,
    PartialLatestDayInjectionConfig,
    generate_missing_reporting_day_scenario,
    generate_partial_latest_reporting_day_scenario,
    inject_missing_reporting_day,
    inject_partial_latest_reporting_day,
    observe_missing_reporting_day_ground_truth,
    observe_partial_latest_reporting_day_ground_truth,
)
from scenarios.definitions import (
    BUSINESS_ROOT_CAUSE_SCENARIOS,
    CANONICAL_PROFITABILITY_SCENARIO,
    COGS_MARGIN_DETERIORATION_SCENARIO,
    DATA_QUALITY_SCENARIOS,
    DISCOUNT_REFUND_DETERIORATION_SCENARIO,
    EXPERIMENT_SCENARIOS,
    IMMATERIAL_EXPERIMENT_SCENARIO,
    MEANINGFUL_EXPERIMENT_SCENARIO,
    MISSING_REPORTING_DAY_SCENARIO,
    NO_EFFECT_EXPERIMENT_SCENARIO,
    PARTIAL_LATEST_DAY_SCENARIO,
    RETENTION_DETERIORATION_SCENARIO,
    GroundTruthMetric,
    InjectedCondition,
    ScenarioDefinition,
    ScenarioModelContext,
)
from scenarios.experiment_scenarios import (
    ExperimentDataset,
    ExperimentScenarioConfig,
    generate_immaterial_experiment_scenario,
    generate_meaningful_experiment_scenario,
    generate_no_effect_experiment_scenario,
    observe_immaterial_experiment_ground_truth,
    observe_meaningful_experiment_ground_truth,
    observe_no_effect_experiment_ground_truth,
    statistical_assessment_for_scenario,
)
from scenarios.generator import (
    SyntheticEcommerceConfig,
    SyntheticEcommerceDataset,
    SyntheticEcommerceGenerator,
    generate_synthetic_ecommerce,
)
from scenarios.injection import (
    CanonicalProfitabilityScenarioInjector,
    CanonicalScenarioInjectionConfig,
    ScenarioDataset,
    ScenarioRun,
    generate_canonical_profitability_scenario,
    inject_canonical_profitability_scenario,
    observe_canonical_ground_truth,
)
from scenarios.invariants import (
    DatasetInvariantSpec,
    DateInvariant,
    DateRelationInvariant,
    DocumentedNullInvariant,
    EconomicIdentityInvariant,
    ForeignKeyInvariant,
    InvariantReport,
    InvariantViolation,
    KeyInvariant,
    RowInvariant,
    ScenarioInvariantError,
    ScenarioInvariantSuite,
    check_dataset_invariants,
    check_metric_identities,
    check_observable_ground_truth,
    experiment_invariant_suite,
    synthetic_ecommerce_invariant_suite,
    validate_synthetic_ecommerce_baseline,
)
from scenarios.mix_scenarios import (
    ChannelMixScenarioInjectionConfig,
    generate_channel_mix_confounding_scenario,
    inject_channel_mix_confounding,
    observe_channel_mix_ground_truth,
)
from scenarios.sources import SourceWriteError, write_deterministic_sources


def discover_scenarios():
    """Lazily discover built-in scenarios without creating import cycles."""

    from scenarios.catalog import discover_scenarios as discover

    return discover()


def get_scenario(scenario_id: str, scenario_version: str | None = None):
    """Lazily resolve one built-in scenario registration."""

    from scenarios.catalog import get_scenario as resolve

    return resolve(scenario_id, scenario_version)


def __getattr__(name: str):
    """Lazily expose catalog classes without import cycles."""

    if name in {
        "ScenarioCatalog",
        "ScenarioCatalogError",
        "ScenarioKey",
        "ScenarioRegistration",
    }:
        from scenarios import catalog

        return getattr(catalog, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CANONICAL_PROFITABILITY_SCENARIO",
    "CHANNEL_MIX_CONFOUNDING_SCENARIO",
    "BUSINESS_ROOT_CAUSE_SCENARIOS",
    "DATA_QUALITY_SCENARIOS",
    "EXPERIMENT_SCENARIOS",
    "COGS_MARGIN_DETERIORATION_SCENARIO",
    "DISCOUNT_REFUND_DETERIORATION_SCENARIO",
    "CanonicalProfitabilityScenarioInjector",
    "CanonicalScenarioInjectionConfig",
    "CogsMarginScenarioInjectionConfig",
    "DiscountRefundScenarioInjectionConfig",
    "GroundTruthMetric",
    "InjectedCondition",
    "ScenarioModelContext",
    "ScenarioDefinition",
    "ScenarioCatalog",
    "ScenarioCatalogError",
    "ScenarioKey",
    "ScenarioRegistration",
    "ScenarioRun",
    "ScenarioDataset",
    "ExperimentDataset",
    "ExperimentScenarioConfig",
    "SyntheticEcommerceConfig",
    "SyntheticEcommerceDataset",
    "SyntheticEcommerceGenerator",
    "generate_canonical_profitability_scenario",
    "generate_channel_mix_confounding_scenario",
    "generate_cogs_margin_deterioration_scenario",
    "generate_discount_refund_deterioration_scenario",
    "generate_retention_deterioration_scenario",
    "generate_synthetic_ecommerce",
    "generate_missing_reporting_day_scenario",
    "generate_partial_latest_reporting_day_scenario",
    "generate_immaterial_experiment_scenario",
    "generate_meaningful_experiment_scenario",
    "generate_no_effect_experiment_scenario",
    "discover_scenarios",
    "DatasetInvariantSpec",
    "DateInvariant",
    "DateRelationInvariant",
    "DocumentedNullInvariant",
    "EconomicIdentityInvariant",
    "ForeignKeyInvariant",
    "get_scenario",
    "inject_canonical_profitability_scenario",
    "inject_channel_mix_confounding",
    "inject_cogs_margin_deterioration",
    "inject_discount_refund_deterioration",
    "inject_retention_deterioration",
    "inject_missing_reporting_day",
    "inject_partial_latest_reporting_day",
    "observe_canonical_ground_truth",
    "observe_channel_mix_ground_truth",
    "observe_cogs_margin_ground_truth",
    "observe_discount_refund_ground_truth",
    "observe_retention_ground_truth",
    "observe_missing_reporting_day_ground_truth",
    "observe_partial_latest_reporting_day_ground_truth",
    "observe_immaterial_experiment_ground_truth",
    "observe_meaningful_experiment_ground_truth",
    "observe_no_effect_experiment_ground_truth",
    "statistical_assessment_for_scenario",
    "RETENTION_DETERIORATION_SCENARIO",
    "MISSING_REPORTING_DAY_SCENARIO",
    "PARTIAL_LATEST_DAY_SCENARIO",
    "MEANINGFUL_EXPERIMENT_SCENARIO",
    "NO_EFFECT_EXPERIMENT_SCENARIO",
    "IMMATERIAL_EXPERIMENT_SCENARIO",
    "RetentionScenarioInjectionConfig",
    "ChannelMixScenarioInjectionConfig",
    "MissingReportingDayInjectionConfig",
    "PartialLatestDayInjectionConfig",
    "InvariantReport",
    "InvariantViolation",
    "KeyInvariant",
    "RowInvariant",
    "ScenarioInvariantError",
    "ScenarioInvariantSuite",
    "SourceWriteError",
    "check_dataset_invariants",
    "check_metric_identities",
    "check_observable_ground_truth",
    "experiment_invariant_suite",
    "synthetic_ecommerce_invariant_suite",
    "validate_synthetic_ecommerce_baseline",
    "write_deterministic_sources",
]
