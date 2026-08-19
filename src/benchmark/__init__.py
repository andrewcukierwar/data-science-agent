"""Resumable benchmark matrix planning, execution, and reporting."""

from benchmark.aggregation import (
    AGGREGATION_VERSION,
    aggregate_manifest,
    build_benchmark_report,
)
from benchmark.preflight import (
    PreflightCheck,
    PreflightError,
    PreflightReport,
    assert_run_outcome,
    check_run_outcome,
)
from benchmark.runner import (
    BenchmarkCell,
    BenchmarkCellResult,
    BenchmarkError,
    BenchmarkExecutionSummary,
    BenchmarkPilotReport,
    BenchmarkRunner,
    canonical_run_record_digest,
)

__all__ = [
    "BenchmarkCell",
    "PreflightCheck",
    "PreflightError",
    "PreflightReport",
    "assert_run_outcome",
    "check_run_outcome",
    "BenchmarkCellResult",
    "BenchmarkError",
    "BenchmarkExecutionSummary",
    "BenchmarkPilotReport",
    "BenchmarkRunner",
    "canonical_run_record_digest",
    "AGGREGATION_VERSION",
    "aggregate_manifest",
    "build_benchmark_report",
]
