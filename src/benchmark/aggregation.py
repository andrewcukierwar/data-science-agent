"""Deterministic benchmark aggregation and report construction."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from math import sqrt

from scipy.stats import t as student_t

from evaluation.contracts import (
    AggregateBenchmarkResult,
    AggregateDenominator,
    ArchitectureComparison,
    ArchitectureMetricComparison,
    BenchmarkManifest,
    BenchmarkReport,
    BenchmarkRunRecord,
    BenchmarkTableRow,
    DistributionSummary,
    EvaluatorStatus,
    LifecycleStatus,
    UncertaintyInterval,
)

AGGREGATION_VERSION = "1.1"
CONFIDENCE_LEVEL = 0.95
ALPHA = 1.0 - CONFIDENCE_LEVEL


def _rounded(value: float) -> float:
    return round(float(value), 12)


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate a quantile for an empty sample")
    if len(values) == 1:
        return _rounded(values[0])
    position = (len(values) - 1) * probability
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(values) - 1)
    fraction = position - lower_index
    value = values[lower_index] + fraction * (values[upper_index] - values[lower_index])
    return _rounded(value)


def _distribution(values: Iterable[float]) -> DistributionSummary:
    ordered = sorted(float(value) for value in values)
    sample_size = len(ordered)
    if sample_size == 0:
        return DistributionSummary(
            sample_size=0,
            uncertainty_status="no_observations",
        )

    quantiles = {
        "p25": _quantile(ordered, 0.25),
        "p50": _quantile(ordered, 0.50),
        "p75": _quantile(ordered, 0.75),
    }
    mean = _rounded(sum(ordered) / sample_size)
    minimum = _rounded(ordered[0])
    maximum = _rounded(ordered[-1])
    if sample_size == 1:
        return DistributionSummary(
            sample_size=1,
            mean=mean,
            minimum=minimum,
            maximum=maximum,
            quantiles=quantiles,
            uncertainty_status="insufficient_sample",
        )

    variance = sum((value - mean) ** 2 for value in ordered) / (sample_size - 1)
    stddev = _rounded(sqrt(variance))
    critical_value = float(
        student_t.ppf((1.0 + CONFIDENCE_LEVEL) / 2.0, sample_size - 1)
    )
    half_width = critical_value * stddev / sqrt(sample_size)
    uncertainty = UncertaintyInterval(
        confidence_level=CONFIDENCE_LEVEL,
        lower=_rounded(mean - half_width),
        upper=_rounded(mean + half_width),
        sample_size=sample_size,
    )
    return DistributionSummary(
        sample_size=sample_size,
        mean=mean,
        stddev=stddev,
        minimum=minimum,
        maximum=maximum,
        quantiles=quantiles,
        uncertainty=uncertainty,
        uncertainty_status="estimable",
    )


def _evaluable(record: BenchmarkRunRecord) -> bool:
    return (
        record.lifecycle.status is LifecycleStatus.COMPLETED
        and record.evaluator_result.status
        in {EvaluatorStatus.PASS, EvaluatorStatus.FAIL}
        and record.score_breakdown is not None
    )


def _metric_value(record: BenchmarkRunRecord, metric_key: str) -> float | None:
    if metric_key == "estimated_cost_usd":
        return record.cost.estimated_cost_usd
    if metric_key == "latency_seconds":
        return record.latency.elapsed_seconds
    if not _evaluable(record) or record.score_breakdown is None:
        return None
    if metric_key == "overall_score":
        return record.score_breakdown.overall_score
    return record.score_breakdown.dimensions.get(metric_key)


def _failure_taxonomy(
    expected_repetitions: int,
    records: Sequence[BenchmarkRunRecord],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    missing = max(expected_repetitions - len(records), 0)
    if missing:
        counts["missing"] = missing
    for record in records:
        if record.lifecycle.status is not LifecycleStatus.COMPLETED:
            category = record.lifecycle.failure_category
            key = f"lifecycle:{category.value if category is not None else 'unknown'}"
            counts[key] = counts.get(key, 0) + 1
        if record.evaluator_result.status is not EvaluatorStatus.PASS:
            key = f"evaluator:{record.evaluator_result.status.value}"
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _aggregate_for_cell(
    manifest: BenchmarkManifest,
    scenario_id: str,
    scenario_version: str,
    architecture: str,
    records: Sequence[BenchmarkRunRecord],
) -> AggregateBenchmarkResult:
    score_keys = sorted(
        {
            key
            for record in records
            if _evaluable(record) and record.score_breakdown is not None
            for key in (*record.score_breakdown.dimensions, "overall_score")
        }
    )
    score_distributions = {
        key: _distribution(
            value
            for record in records
            if (value := _metric_value(record, key)) is not None
        )
        for key in score_keys
    }
    mean_scores = {
        key: summary.mean
        for key, summary in score_distributions.items()
        if summary.mean is not None
    }
    known_costs = [
        record.cost.estimated_cost_usd
        for record in records
        if record.cost.estimated_cost_usd is not None
    ]
    latency_distribution = _distribution(
        record.latency.elapsed_seconds for record in records
    )
    cost_distribution = _distribution(known_costs)
    completed_runs = sum(
        record.lifecycle.status is LifecycleStatus.COMPLETED for record in records
    )
    failed_runs = len(records) - completed_runs
    evaluated_runs = sum(_evaluable(record) for record in records)
    missing_repetitions = max(manifest.repetitions - len(records), 0)
    denominator = AggregateDenominator(
        expected_repetitions=manifest.repetitions,
        observed_repetitions=len(records),
        missing_repetitions=missing_repetitions,
        completed_runs=completed_runs,
        failed_runs=failed_runs,
        evaluated_runs=evaluated_runs,
        completion_rate=_rounded(completed_runs / manifest.repetitions),
        evaluation_rate=_rounded(evaluated_runs / manifest.repetitions),
    )
    return AggregateBenchmarkResult(
        scenario_id=scenario_id,
        scenario_version=scenario_version,
        architecture=architecture,
        expected_repetitions=manifest.repetitions,
        observed_repetitions=len(records),
        completed_runs=completed_runs,
        failed_runs=failed_runs,
        evaluated_runs=evaluated_runs,
        mean_scores=mean_scores,
        mean_estimated_cost=cost_distribution.mean,
        mean_elapsed_seconds=latency_distribution.mean or 0.0,
        denominator=denominator,
        score_distributions=score_distributions,
        cost_distribution=cost_distribution,
        latency_distribution=latency_distribution,
        failure_taxonomy=_failure_taxonomy(manifest.repetitions, records),
    )


def _comparison_for_metric(
    metric_key: str,
    left_records: dict[int, BenchmarkRunRecord],
    right_records: dict[int, BenchmarkRunRecord],
    left_architecture: str,
    right_architecture: str,
) -> ArchitectureMetricComparison:
    left_values = [
        value
        for record in left_records.values()
        if (value := _metric_value(record, metric_key)) is not None
    ]
    right_values = [
        value
        for record in right_records.values()
        if (value := _metric_value(record, metric_key)) is not None
    ]
    paired_values: list[float] = []
    paired_repetitions: list[int] = []
    for repetition in sorted(set(left_records) & set(right_records)):
        left_value = _metric_value(left_records[repetition], metric_key)
        right_value = _metric_value(right_records[repetition], metric_key)
        if left_value is not None and right_value is not None:
            paired_repetitions.append(repetition)
            paired_values.append(right_value - left_value)
    paired_distribution = _distribution(paired_values)
    paired_sample_size = len(paired_values)
    p_value: float | None = None
    test_method = "not_estimable"
    conclusion = "insufficient_sample"
    if paired_sample_size >= 2:
        test_method = "paired_t"
        mean_difference = paired_distribution.mean or 0.0
        stddev = paired_distribution.stddev or 0.0
        if stddev == 0.0:
            p_value = 1.0 if mean_difference == 0.0 else 0.0
        else:
            statistic = mean_difference / (stddev / sqrt(paired_sample_size))
            p_value = _rounded(
                min(
                    1.0,
                    2.0 * float(student_t.sf(abs(statistic), paired_sample_size - 1)),
                )
            )
        interval = paired_distribution.uncertainty
        conclusion = (
            "supported_difference"
            if interval is not None and (interval.lower > 0 or interval.upper < 0)
            else "not_supported"
        )
    return ArchitectureMetricComparison(
        metric_key=metric_key,
        left_architecture=left_architecture,
        right_architecture=right_architecture,
        difference_definition=f"{right_architecture} minus {left_architecture}",
        left_sample_size=len(left_values),
        right_sample_size=len(right_values),
        paired_sample_size=paired_sample_size,
        paired_repetitions=tuple(paired_repetitions),
        mean_left=_distribution(left_values).mean,
        mean_right=_distribution(right_values).mean,
        mean_difference=paired_distribution.mean,
        paired_difference_distribution=paired_distribution,
        alpha=ALPHA,
        p_value=p_value,
        test_method=test_method,
        conclusion=conclusion,
    )


def _architecture_comparisons(
    manifest: BenchmarkManifest,
    records_by_cell: dict[tuple[str, str, str], list[BenchmarkRunRecord]],
) -> tuple[ArchitectureComparison, ...]:
    comparisons: list[ArchitectureComparison] = []
    for reference in manifest.scenario_references:
        for left_architecture, right_architecture in itertools.combinations(
            sorted(manifest.architectures), 2
        ):
            left_records = {
                record.repetition: record
                for record in records_by_cell.get(
                    (
                        reference.scenario_id,
                        reference.scenario_version,
                        left_architecture,
                    ),
                    [],
                )
            }
            right_records = {
                record.repetition: record
                for record in records_by_cell.get(
                    (
                        reference.scenario_id,
                        reference.scenario_version,
                        right_architecture,
                    ),
                    [],
                )
            }
            metric_keys = sorted(
                {
                    key
                    for record in (*left_records.values(), *right_records.values())
                    if _evaluable(record) and record.score_breakdown is not None
                    for key in (*record.score_breakdown.dimensions, "overall_score")
                }
            )
            if any(
                record.cost.estimated_cost_usd is not None
                for record in (*left_records.values(), *right_records.values())
            ):
                metric_keys.append("estimated_cost_usd")
            if left_records or right_records:
                metric_keys.append("latency_seconds")
            metric_keys = sorted(set(metric_keys))
            metrics = tuple(
                _comparison_for_metric(
                    metric_key,
                    left_records,
                    right_records,
                    left_architecture,
                    right_architecture,
                )
                for metric_key in metric_keys
            )
            comparisons.append(
                ArchitectureComparison(
                    scenario_id=reference.scenario_id,
                    scenario_version=reference.scenario_version,
                    left_architecture=left_architecture,
                    right_architecture=right_architecture,
                    pairing_definition=(
                        "same scenario/version, seed, repetition, model/provider, "
                        "run configuration, and budgets"
                    ),
                    metrics=metrics,
                )
            )
    return tuple(comparisons)


def aggregate_manifest(manifest: BenchmarkManifest) -> BenchmarkManifest:
    """Recompute deterministic aggregates without modifying raw run records."""

    records_by_cell: dict[tuple[str, str, str], list[BenchmarkRunRecord]] = {}
    for record in manifest.run_records:
        records_by_cell.setdefault(
            (record.scenario_id, record.scenario_version, record.architecture),
            [],
        ).append(record)
    aggregates = tuple(
        _aggregate_for_cell(
            manifest,
            reference.scenario_id,
            reference.scenario_version,
            architecture,
            records_by_cell.get(
                (reference.scenario_id, reference.scenario_version, architecture),
                [],
            ),
        )
        for reference in manifest.scenario_references
        for architecture in manifest.architectures
    )
    comparisons = _architecture_comparisons(manifest, records_by_cell)
    values = manifest.model_dump(mode="json")
    values.update(
        {
            "aggregation_version": AGGREGATION_VERSION,
            "aggregates": [item.model_dump(mode="json") for item in aggregates],
            "architecture_comparisons": [
                item.model_dump(mode="json") for item in comparisons
            ],
        }
    )
    return BenchmarkManifest.model_validate(values)


def build_benchmark_report(manifest: BenchmarkManifest) -> BenchmarkReport:
    """Build a portable report with flat rows and inferential comparisons."""

    aggregated = aggregate_manifest(manifest)
    rows: list[BenchmarkTableRow] = []
    for aggregate in aggregated.aggregates:
        overall = aggregate.score_distributions.get("overall_score")
        rows.append(
            BenchmarkTableRow(
                scenario_id=aggregate.scenario_id,
                scenario_version=aggregate.scenario_version,
                architecture=aggregate.architecture,
                expected_repetitions=aggregate.expected_repetitions,
                observed_repetitions=aggregate.observed_repetitions,
                missing_repetitions=(
                    aggregate.denominator.missing_repetitions
                    if aggregate.denominator is not None
                    else aggregate.expected_repetitions - aggregate.observed_repetitions
                ),
                completed_runs=aggregate.completed_runs,
                failed_runs=aggregate.failed_runs,
                evaluated_runs=aggregate.evaluated_runs,
                completion_rate=(
                    aggregate.denominator.completion_rate
                    if aggregate.denominator is not None
                    else aggregate.completed_runs / aggregate.expected_repetitions
                ),
                evaluation_rate=(
                    aggregate.denominator.evaluation_rate
                    if aggregate.denominator is not None
                    else aggregate.evaluated_runs / aggregate.expected_repetitions
                ),
                overall_score_mean=overall.mean if overall is not None else None,
                overall_score_ci_lower=(
                    overall.uncertainty.lower
                    if overall is not None and overall.uncertainty is not None
                    else None
                ),
                overall_score_ci_upper=(
                    overall.uncertainty.upper
                    if overall is not None and overall.uncertainty is not None
                    else None
                ),
                mean_estimated_cost=aggregate.mean_estimated_cost,
                mean_elapsed_seconds=(
                    aggregate.latency_distribution.mean
                    if aggregate.latency_distribution is not None
                    else None
                ),
                failure_taxonomy=aggregate.failure_taxonomy,
            )
        )
    expected_cells = (
        len(aggregated.scenario_references)
        * len(aggregated.architectures)
        * aggregated.repetitions
    )
    return BenchmarkReport(
        manifest_id=aggregated.manifest_id,
        manifest_status=aggregated.status,
        aggregation_version=aggregated.aggregation_version,
        expected_matrix_cells=expected_cells,
        observed_raw_records=len(aggregated.run_records),
        missing_matrix_cells=max(expected_cells - len(aggregated.run_records), 0),
        aggregates=aggregated.aggregates,
        architecture_comparisons=aggregated.architecture_comparisons,
        table_rows=tuple(rows),
    )


__all__ = [
    "AGGREGATION_VERSION",
    "ALPHA",
    "CONFIDENCE_LEVEL",
    "aggregate_manifest",
    "build_benchmark_report",
]
