"""Canonical scenario adapter over the generic offline evaluation engine."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from evaluation.contracts import (
    WorkspaceVersionCompatibilityError,
    check_workspace_version_compatibility,
)
from evaluation.engine import evaluate_workspace
from evaluation.primitives import (
    numeric_ground_truth_failures,
    reconcile_metric_candidates,
    select_metric_candidates,
)
from evaluation.rules import canonical_rules
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO, GroundTruthMetric
from schemas.lead import LeadResult
from schemas.metrics import (
    MetricComparison,
    normalize_metric_dimensions,
    normalize_metric_key,
    normalize_metric_period,
)
from schemas.run_state import ArtifactKind
from tools.workspace import Workspace, WorkspaceManager

if TYPE_CHECKING:
    from orchestration.runner import AnalysisRunResult


class CanonicalAcceptanceError(AssertionError):
    """Raised when a canonical run does not satisfy its scenario contract."""


@dataclass(frozen=True, slots=True)
class CanonicalAcceptanceSummary:
    """Small machine-readable summary retained for the Phase 1 CLI."""

    run_id: str
    workspace: str
    status: str
    checks: tuple[str, ...]
    sql_events: int
    python_events: int
    chart_artifacts: int
    agent_events: int
    specialist_results: int
    model_requests: int
    total_tokens: int
    elapsed_seconds: float
    estimated_cost_usd: float | None

    def as_dict(self) -> dict[str, object]:
        """Return JSON-serializable acceptance metadata."""

        return {
            "run_id": self.run_id,
            "workspace": self.workspace,
            "status": self.status,
            "checks": list(self.checks),
            "sql_events": self.sql_events,
            "python_events": self.python_events,
            "chart_artifacts": self.chart_artifacts,
            "agent_events": self.agent_events,
            "specialist_results": self.specialist_results,
            "model_requests": self.model_requests,
            "total_tokens": self.total_tokens,
            "elapsed_seconds": self.elapsed_seconds,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


def _open_workspace_path(workspace_path: str | Path) -> Workspace:
    """Open one existing workspace after checking its persisted version."""

    root = Path(workspace_path).expanduser().resolve()
    if not root.is_dir():
        raise CanonicalAcceptanceError(f"workspace does not exist: {root}")
    try:
        check_workspace_version_compatibility(root)
        return WorkspaceManager(root.parent).open_workspace(root.name)
    except WorkspaceVersionCompatibilityError as exc:
        raise CanonicalAcceptanceError(str(exc)) from exc
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise CanonicalAcceptanceError(f"workspace layout is invalid: {exc}") from exc


def _metric_comparisons_from_input(
    values: Iterable[MetricComparison] | LeadResult,
) -> list[MetricComparison]:
    """Compatibility helper for focused canonical evaluator tests."""

    if isinstance(values, LeadResult):
        return values.metric_comparisons
    return list(values)


def _normalized_metric_identifier(metric: str) -> str:
    """Compatibility wrapper for generic metric-key normalization."""

    return normalize_metric_key(metric, {})


def _normalized_period(period: str) -> str:
    """Compatibility wrapper for generic period normalization."""

    return normalize_metric_period(period).lower()


def _normalized_dimensions(dimensions: dict[str, str]) -> dict[str, str]:
    """Compatibility wrapper for generic dimension normalization."""

    return {
        key: value.lower()
        for key, value in normalize_metric_dimensions(dimensions).items()
    }


def _selected_metric_candidates(
    comparisons: list[MetricComparison],
    expected: GroundTruthMetric,
) -> list[MetricComparison]:
    """Compatibility wrapper for generic metric candidate selection."""

    return select_metric_candidates(comparisons, expected)


def _reconcile_metric_candidates(
    candidates: list[MetricComparison],
    expected: GroundTruthMetric,
) -> tuple[MetricComparison | None, bool]:
    """Compatibility wrapper for generic metric reconciliation."""

    return reconcile_metric_candidates(candidates, expected)


def _canonical_numeric_ground_truth_failures(
    values: Iterable[MetricComparison] | LeadResult,
) -> list[str]:
    """Compatibility wrapper around the generic numeric evaluator primitive."""

    return numeric_ground_truth_failures(
        _metric_comparisons_from_input(values),
        CANONICAL_PROFITABILITY_SCENARIO.ground_truth,
    )


def _canonical_text_rule(check_id: str):
    """Resolve a legacy helper to the registered canonical text rule."""

    return next(
        rule.predicate
        for rule in canonical_rules().root_cause_rules
        if rule.check_id == check_id
    )


def _has_asserted_primary_driver(text: str) -> bool:
    """Compatibility wrapper for the generic asserted-mechanism primitive."""

    return _canonical_text_rule("conversion_mechanism")(text)


def _has_primary_channel_contribution(text: str) -> bool:
    """Compatibility wrapper for the generic material-driver primitive."""

    return _canonical_text_rule("primary_channel_driver")(text)


def _has_acquisition_efficiency_decomposition(text: str) -> bool:
    """Compatibility wrapper for the generic concept-list primitive."""

    return _canonical_text_rule("acquisition_efficiency_decomposition")(text)


def _has_stable_ltv_statement(text: str) -> bool:
    """Compatibility wrapper for the generic stability primitive."""

    return _canonical_text_rule("stable_ltv")(text)


def _has_margin_non_driver_statement(text: str) -> bool:
    """Compatibility wrapper for the generic non-driver primitive."""

    return _canonical_text_rule("margin_non_driver")(text)


def _report_has_recommendations(report_text: str) -> bool:
    """Compatibility helper for the persisted report section check."""

    match = re.search(
        r"(?ims)^## Recommendations\s*$\n(?P<body>.*?)(?=^## |\Z)",
        report_text,
    )
    if match is None:
        return False
    body = match.group("body").strip().lower()
    return bool(body) and "no recommendations were returned" not in body


def _report_recommendation_evidence_refs(report_text: str) -> list[str]:
    """Compatibility helper for report evidence extraction."""

    match = re.search(
        r"(?ims)^## Recommendations\s*$\n(?P<body>.*?)(?=^## |\Z)",
        report_text,
    )
    if match is None:
        return []
    return [
        reference.strip()
        for group in re.findall(r"(?i)evidence:\s*([^)]*)\)", match.group("body"))
        for reference in group.split(",")
        if reference.strip()
    ]


def evaluate_canonical_workspace(
    workspace: Workspace | str | Path,
) -> CanonicalAcceptanceSummary:
    """Evaluate a canonical workspace with no model, agent, or API activity."""

    try:
        result = evaluate_workspace(workspace, canonical_rules())
    except WorkspaceVersionCompatibilityError as exc:
        raise CanonicalAcceptanceError(str(exc)) from exc
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise CanonicalAcceptanceError(str(exc)) from exc

    failures = [
        check.message for check in result.checks if check.status.value == "fail"
    ]
    if failures:
        raise CanonicalAcceptanceError("; ".join(failures))

    state = result.snapshot.state
    successful_sql = [
        event
        for event in state.tool_events
        if event.tool_name == "run_sql" and event.status.value == "succeeded"
    ]
    successful_python = [
        event
        for event in state.tool_events
        if event.tool_name == "run_python" and event.status.value == "succeeded"
    ]
    chart_artifacts = [
        artifact for artifact in state.artifacts if artifact.kind is ArtifactKind.CHART
    ]
    return CanonicalAcceptanceSummary(
        run_id=state.run_id,
        workspace=str(result.snapshot.workspace.root),
        status=state.status.value,
        checks=(
            "lifecycle",
            "data_quality",
            "numeric_comparisons",
            "provenance",
            "root_cause_and_non_drivers",
            "statistics",
            "unsupported_claims",
            "task_completeness",
        ),
        sql_events=len(successful_sql),
        python_events=len(successful_python),
        chart_artifacts=len(chart_artifacts),
        agent_events=len(state.agent_events),
        specialist_results=len(state.specialist_results),
        model_requests=state.usage.requests,
        total_tokens=state.usage.total_tokens,
        elapsed_seconds=state.elapsed_seconds or 0.0,
        estimated_cost_usd=state.estimated_cost_usd,
    )


def evaluate_canonical_run(
    result: AnalysisRunResult,
) -> CanonicalAcceptanceSummary:
    """Evaluate a live result through the same persisted-workspace engine."""

    if result.workspace is None:
        raise CanonicalAcceptanceError("run did not create a workspace")
    return evaluate_canonical_workspace(result.workspace)


__all__ = [
    "CanonicalAcceptanceError",
    "CanonicalAcceptanceSummary",
    "evaluate_canonical_run",
    "evaluate_canonical_workspace",
]
