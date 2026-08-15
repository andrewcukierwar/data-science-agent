"""Synthetic datasets and deterministic evaluation scenarios."""

from scenarios.definitions import (
    CANONICAL_PROFITABILITY_SCENARIO,
    GroundTruthMetric,
    InjectedCondition,
    ScenarioDefinition,
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
)

__all__ = [
    "CANONICAL_PROFITABILITY_SCENARIO",
    "CanonicalProfitabilityScenarioInjector",
    "CanonicalScenarioInjectionConfig",
    "GroundTruthMetric",
    "InjectedCondition",
    "ScenarioDefinition",
    "ScenarioRun",
    "SyntheticEcommerceConfig",
    "SyntheticEcommerceDataset",
    "SyntheticEcommerceGenerator",
    "generate_canonical_profitability_scenario",
    "generate_synthetic_ecommerce",
    "inject_canonical_profitability_scenario",
]
