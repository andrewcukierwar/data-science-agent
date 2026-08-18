"""Deterministic V1 two-arm experiment sources and basic statistics."""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, sqrt
from pathlib import Path
from statistics import NormalDist
from typing import ClassVar

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scenarios.definitions import (
    IMMATERIAL_EXPERIMENT_SCENARIO,
    MEANINGFUL_EXPERIMENT_SCENARIO,
    NO_EFFECT_EXPERIMENT_SCENARIO,
)
from scenarios.definitions.models import ScenarioDefinition
from scenarios.injection import ScenarioRun
from scenarios.sources import write_deterministic_sources
from schemas.metrics import MetricComparison, MetricComparisonType
from schemas.statistics import (
    CausalInterpretation,
    ConfidenceInterval,
    StatisticalAssessment,
    StatisticalConclusion,
)

_EXPERIMENT_DEFINITIONS = """

## Experiment definitions

- Each row is one independently assigned participant and contains one binary
  outcome. A participant appears once in the experiment source.
- The control and treatment arms are compared with a two-sided 95% confidence
  interval for the difference in outcome rates.
- Effect size is reported separately from the raw outcome-rate difference.
  Practical significance is assessed against the documented absolute difference
  threshold of 0.05.
- Random assignment supports a treatment-effect interpretation for the enrolled
  population. It does not justify claims about populations or outcomes outside
  the experiment.
""".strip()


class ExperimentScenarioConfig(BaseModel):
    """Seed and sampling parameters for one deterministic experiment fixture."""

    model_config = ConfigDict(extra="forbid")

    seed: int = Field(default=2026, ge=0)
    control_n: int = Field(ge=2)
    treatment_n: int = Field(ge=2)
    control_rate: float = Field(ge=0, le=1)
    treatment_rate: float = Field(ge=0, le=1)
    practical_threshold: float = Field(default=0.05, ge=0)

    @model_validator(mode="after")
    def rates_are_non_degenerate(self) -> ExperimentScenarioConfig:
        if not (0 < self.control_rate < 1 and 0 < self.treatment_rate < 1):
            raise ValueError(
                "experiment fixture rates must be strictly between 0 and 1"
            )
        return self


@dataclass(frozen=True, slots=True)
class ExperimentDataset:
    """Generated experiment observations and neutral business definitions."""

    observations: pd.DataFrame
    business_definitions: str

    _TABLE_FILES: ClassVar[dict[str, str]] = {
        "experiment_observations": "experiment_observations.parquet",
        "business_definitions": "business_definitions.md",
    }

    def table_map(self) -> dict[str, pd.DataFrame]:
        return {"experiment_observations": self.observations}

    def write(
        self,
        output_dir: str | Path,
        *,
        overwrite: bool = False,
    ) -> dict[str, Path]:
        return write_deterministic_sources(
            output_dir,
            self.table_map(),
            {"business_definitions": self.business_definitions},
            table_filenames={
                "experiment_observations": self._TABLE_FILES["experiment_observations"]
            },
            document_filenames={
                "business_definitions": self._TABLE_FILES["business_definitions"]
            },
            overwrite=overwrite,
        )


def _default_config(definition: ScenarioDefinition) -> ExperimentScenarioConfig:
    return ExperimentScenarioConfig.model_validate(
        definition.generation_config["experiment"]
    )


def _generate_dataset(config: ExperimentScenarioConfig) -> ExperimentDataset:
    rng = np.random.default_rng(config.seed)
    rows: list[dict[str, object]] = []
    for assignment, sample_size, rate in (
        ("control", config.control_n, config.control_rate),
        ("treatment", config.treatment_n, config.treatment_rate),
    ):
        outcomes = np.zeros(sample_size, dtype=np.int8)
        outcomes[: round(sample_size * rate)] = 1
        rng.shuffle(outcomes)
        rows.extend(
            {
                "subject_id": f"S{index:07d}",
                "experiment": "checkout-v1",
                "assignment": assignment,
                "outcome": int(outcome),
            }
            for index, outcome in enumerate(outcomes, start=len(rows) + 1)
        )
    return ExperimentDataset(
        observations=pd.DataFrame(rows),
        business_definitions=_EXPERIMENT_DEFINITIONS + "\n",
    )


def _arm_rate(frame: pd.DataFrame, assignment: str) -> float:
    arm = frame.loc[frame["assignment"].eq(assignment), "outcome"]
    if arm.empty:
        raise ValueError(f"experiment arm {assignment!r} is empty")
    return float(arm.mean())


def _experiment_summary(
    dataset: ExperimentDataset,
    *,
    practical_threshold: float,
) -> StatisticalAssessment:
    frame = dataset.observations
    control = frame.loc[frame["assignment"].eq("control"), "outcome"]
    treatment = frame.loc[frame["assignment"].eq("treatment"), "outcome"]
    control_rate = _arm_rate(frame, "control")
    treatment_rate = _arm_rate(frame, "treatment")
    estimate = treatment_rate - control_rate
    standard_error = sqrt(
        control_rate * (1 - control_rate) / len(control)
        + treatment_rate * (1 - treatment_rate) / len(treatment)
    )
    if standard_error == 0:
        margin = 0.0
        p_value = 1.0 if estimate == 0 else 0.0
    else:
        z_score = estimate / standard_error
        margin = 1.959963984540054 * standard_error
        p_value = 2 * (1 - NormalDist().cdf(abs(z_score)))
    effect_size = 2 * asin(sqrt(treatment_rate)) - 2 * asin(sqrt(control_rate))
    practically_significant = abs(estimate) >= practical_threshold
    if p_value < 0.05 and practically_significant:
        conclusion = StatisticalConclusion.SIGNIFICANT_AND_PRACTICAL
    elif p_value < 0.05:
        conclusion = StatisticalConclusion.SIGNIFICANT_BUT_IMMATERIAL
    else:
        conclusion = StatisticalConclusion.NOT_STATISTICALLY_SIGNIFICANT
    return StatisticalAssessment(
        metric_key="experiment_conversion_effect",
        dimensions={"experiment": "checkout-v1"},
        baseline_period="control participants",
        comparison_period="treatment participants",
        method="two-proportion z test",
        unit_of_analysis="independently assigned participant",
        conclusion=conclusion,
        confidence_level=0.95,
        estimate=estimate,
        confidence_interval=ConfidenceInterval(
            lower=estimate - margin,
            upper=estimate + margin,
        ),
        p_value=p_value,
        effect_size=effect_size,
        practical_significance_threshold=practical_threshold,
        practically_significant=practically_significant,
        assumptions_checked=(
            "independent observations",
            "binary outcome",
            "random assignment",
            "adequate sample size",
            "two-sided alpha=0.05",
        ),
        causal_interpretation=CausalInterpretation.CAUSAL_EFFECT_SUPPORTED,
        evidence_refs=["generated-ground-truth:checkout-treatment-effect"],
    )


def observe_experiment_ground_truth(
    dataset: ExperimentDataset,
) -> tuple[MetricComparison, ...]:
    control_rate = _arm_rate(dataset.observations, "control")
    treatment_rate = _arm_rate(dataset.observations, "treatment")
    return (
        MetricComparison(
            metric_key="experiment_conversion_effect",
            dimensions={"experiment": "checkout-v1"},
            baseline_period="control participants",
            comparison_period="treatment participants",
            comparison_type=MetricComparisonType.ABSOLUTE_DIFFERENCE,
            value=treatment_rate - control_rate,
            unit="fraction",
            evidence_refs=["generated-ground-truth:checkout-treatment-effect"],
            definition_context=MEANINGFUL_EXPERIMENT_SCENARIO.ground_truth[
                0
            ].definition_context,
        ),
    )


def _generate_scenario(
    definition: ScenarioDefinition,
    config: ExperimentScenarioConfig | None,
) -> ScenarioRun:
    selected = config or _default_config(definition)
    dataset = _generate_dataset(selected)
    return ScenarioRun(
        dataset=dataset,
        definition=definition,
        injection_config=selected,
    )


def generate_meaningful_experiment_scenario(
    config: ExperimentScenarioConfig | None = None,
) -> ScenarioRun:
    return _generate_scenario(MEANINGFUL_EXPERIMENT_SCENARIO, config)


def generate_no_effect_experiment_scenario(
    config: ExperimentScenarioConfig | None = None,
) -> ScenarioRun:
    return _generate_scenario(NO_EFFECT_EXPERIMENT_SCENARIO, config)


def generate_immaterial_experiment_scenario(
    config: ExperimentScenarioConfig | None = None,
) -> ScenarioRun:
    return _generate_scenario(IMMATERIAL_EXPERIMENT_SCENARIO, config)


def observe_meaningful_experiment_ground_truth(
    dataset: ExperimentDataset,
) -> tuple[MetricComparison, ...]:
    return observe_experiment_ground_truth(dataset)


def observe_no_effect_experiment_ground_truth(
    dataset: ExperimentDataset,
) -> tuple[MetricComparison, ...]:
    return observe_experiment_ground_truth(dataset)


def observe_immaterial_experiment_ground_truth(
    dataset: ExperimentDataset,
) -> tuple[MetricComparison, ...]:
    return observe_experiment_ground_truth(dataset)


def statistical_assessment_for_scenario(
    dataset: ExperimentDataset,
    definition: ScenarioDefinition,
) -> StatisticalAssessment:
    expectation = definition.statistical_expectation
    if expectation is None:
        raise ValueError("scenario has no statistical expectation")
    return _experiment_summary(
        dataset,
        practical_threshold=expectation.practical_significance_threshold,
    )


__all__ = [
    "ExperimentDataset",
    "ExperimentScenarioConfig",
    "generate_immaterial_experiment_scenario",
    "generate_meaningful_experiment_scenario",
    "generate_no_effect_experiment_scenario",
    "observe_experiment_ground_truth",
    "observe_immaterial_experiment_ground_truth",
    "observe_meaningful_experiment_ground_truth",
    "observe_no_effect_experiment_ground_truth",
    "statistical_assessment_for_scenario",
]
