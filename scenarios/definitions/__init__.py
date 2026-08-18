"""Typed scenario definitions and evaluator ground truth."""

from scenarios.definitions.business_root_cause import (
    BUSINESS_ROOT_CAUSE_SCENARIOS,
    COGS_MARGIN_DETERIORATION_SCENARIO,
    DISCOUNT_REFUND_DETERIORATION_SCENARIO,
    RETENTION_DETERIORATION_SCENARIO,
)
from scenarios.definitions.canonical_profitability import (
    CANONICAL_PROFITABILITY_SCENARIO,
)
from scenarios.definitions.models import (
    GroundTruthMetric,
    InjectedCondition,
    ScenarioDefinition,
    ScenarioModelContext,
)

__all__ = [
    "CANONICAL_PROFITABILITY_SCENARIO",
    "BUSINESS_ROOT_CAUSE_SCENARIOS",
    "COGS_MARGIN_DETERIORATION_SCENARIO",
    "DISCOUNT_REFUND_DETERIORATION_SCENARIO",
    "GroundTruthMetric",
    "InjectedCondition",
    "RETENTION_DETERIORATION_SCENARIO",
    "ScenarioModelContext",
    "ScenarioDefinition",
]
