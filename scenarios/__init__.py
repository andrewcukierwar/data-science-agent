"""Synthetic datasets and deterministic evaluation scenarios."""

from scenarios.definitions import (
    CANONICAL_PROFITABILITY_SCENARIO,
    GroundTruthMetric,
    InjectedCondition,
    ScenarioDefinition,
    ScenarioModelContext,
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
    synthetic_ecommerce_invariant_suite,
    validate_synthetic_ecommerce_baseline,
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
    "CanonicalProfitabilityScenarioInjector",
    "CanonicalScenarioInjectionConfig",
    "GroundTruthMetric",
    "InjectedCondition",
    "ScenarioModelContext",
    "ScenarioDefinition",
    "ScenarioCatalog",
    "ScenarioCatalogError",
    "ScenarioKey",
    "ScenarioRegistration",
    "ScenarioRun",
    "SyntheticEcommerceConfig",
    "SyntheticEcommerceDataset",
    "SyntheticEcommerceGenerator",
    "generate_canonical_profitability_scenario",
    "generate_synthetic_ecommerce",
    "discover_scenarios",
    "DatasetInvariantSpec",
    "DateInvariant",
    "DateRelationInvariant",
    "DocumentedNullInvariant",
    "EconomicIdentityInvariant",
    "ForeignKeyInvariant",
    "get_scenario",
    "inject_canonical_profitability_scenario",
    "observe_canonical_ground_truth",
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
    "synthetic_ecommerce_invariant_suite",
    "validate_synthetic_ecommerce_baseline",
    "write_deterministic_sources",
]
