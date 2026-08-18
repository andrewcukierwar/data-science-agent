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
from scenarios.definitions.data_quality import (
    DATA_QUALITY_SCENARIOS,
    MISSING_REPORTING_DAY_SCENARIO,
    PARTIAL_LATEST_DAY_SCENARIO,
)
from scenarios.definitions.experiments import (
    EXPERIMENT_SCENARIOS,
    IMMATERIAL_EXPERIMENT_SCENARIO,
    MEANINGFUL_EXPERIMENT_SCENARIO,
    NO_EFFECT_EXPERIMENT_SCENARIO,
)
from scenarios.definitions.mix import CHANNEL_MIX_CONFOUNDING_SCENARIO
from scenarios.definitions.models import (
    GroundTruthMetric,
    InjectedCondition,
    ScenarioDefinition,
    ScenarioModelContext,
)

__all__ = [
    "CANONICAL_PROFITABILITY_SCENARIO",
    "CHANNEL_MIX_CONFOUNDING_SCENARIO",
    "BUSINESS_ROOT_CAUSE_SCENARIOS",
    "COGS_MARGIN_DETERIORATION_SCENARIO",
    "DISCOUNT_REFUND_DETERIORATION_SCENARIO",
    "DATA_QUALITY_SCENARIOS",
    "GroundTruthMetric",
    "InjectedCondition",
    "MISSING_REPORTING_DAY_SCENARIO",
    "PARTIAL_LATEST_DAY_SCENARIO",
    "EXPERIMENT_SCENARIOS",
    "IMMATERIAL_EXPERIMENT_SCENARIO",
    "MEANINGFUL_EXPERIMENT_SCENARIO",
    "NO_EFFECT_EXPERIMENT_SCENARIO",
    "RETENTION_DETERIORATION_SCENARIO",
    "ScenarioModelContext",
    "ScenarioDefinition",
]
