"""Reusable deterministic checks for persisted analysis workspaces.

Each function in this module is deliberately side-effect free.  It receives
typed persisted state (and, where needed, a read-only workspace) and returns
typed evaluator checks.  Scenario modules provide the expectations and text
rules; they do not need to clone the workspace/lifecycle/provenance logic.
"""

from __future__ import annotations

import re
import stat
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from math import isclose, isfinite

from agents.evidence import executed_references
from evaluation.contracts import EvaluationCheck, EvaluationCheckStatus
from orchestration.ledger import AnalysisLedger
from scenarios.definitions.models import GroundTruthMetric
from schemas.audit import AuditStatus, IssueSeverity
from schemas.findings import SpecialistResult
from schemas.lead import LeadResult
from schemas.metrics import (
    MetricComparison,
    compile_metric_comparisons,
    metric_definition_contexts_match,
    normalize_metric_comparison,
    normalize_metric_dimensions,
    normalize_metric_key,
    normalize_metric_period,
    normalize_metric_unit,
)
from schemas.run_state import (
    AgentEventStatus,
    AnalysisRunState,
    ArtifactKind,
    RunStatus,
    ToolEventStatus,
)
from schemas.statistics import StatisticalAssessment, StatisticalExpectation
from schemas.validation import ValidationStatus
from tools.artifacts import ArtifactManager
from tools.workspace import Workspace

_CORROBORATION_RELATIVE_TOLERANCE = 1e-3
_CORROBORATION_ABSOLUTE_TOLERANCE = 1e-3


@dataclass(frozen=True, slots=True)
class TextRule:
    """A named deterministic semantic rule supplied by a scenario."""

    check_id: str
    description: str
    predicate: Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class DataQualityPolicy:
    """Scenario-specific expectations for the persisted data-audit result."""

    required_audit_status: AuditStatus = AuditStatus.COMPLETE
    maximum_issue_severity: IssueSeverity | None = IssueSeverity.LOW
    required_issue_ids: tuple[str, ...] = ()
    forbidden_issue_ids: tuple[str, ...] = ()
    forbid_any_issues: bool = False


@dataclass(frozen=True, slots=True)
class StatisticsPolicy:
    """Scenario-specific expectations for typed statistical work."""

    required_report_terms: tuple[str, ...] = ()
    expectations: tuple[StatisticalExpectation, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskCompletenessPolicy:
    """Common hard completion gates, configurable per scenario."""

    require_plan: bool = True
    require_hypothesis_history: bool = True
    require_findings: bool = True
    require_structured_metrics: bool = True
    require_chart: bool = True
    require_final_critic_pass: bool = True
    require_recommendations: bool = True
    require_agent_trace: bool = True
    require_tool_trace: bool = True


def _check(
    check_id: str,
    passed: bool,
    message: str,
    *,
    warning: bool = False,
) -> EvaluationCheck:
    """Build a stable check record with no timestamp or random identifier."""

    if passed:
        status = EvaluationCheckStatus.PASS
    elif warning:
        status = EvaluationCheckStatus.WARN
    else:
        status = EvaluationCheckStatus.FAIL
    return EvaluationCheck(check_id=check_id, status=status, message=message)


def evaluate_lifecycle(
    state: AnalysisRunState,
    *,
    check_prefix: str = "lifecycle",
) -> tuple[EvaluationCheck, ...]:
    """Check operational completion, persisted errors, and budget integrity."""

    checks = [
        _check(
            f"{check_prefix}:status",
            state.status is RunStatus.COMPLETED,
            f"run status is {state.status.value}",
        ),
        _check(
            f"{check_prefix}:error",
            state.error is None,
            "completed run has no persisted error"
            if state.error is None
            else f"persisted run error: {state.error}",
        ),
    ]
    budget_checks = (
        (
            "sql_executions",
            state.run_budget.sql_executions,
            state.run_budget.max_sql_executions,
        ),
        (
            "python_executions",
            state.run_budget.python_executions,
            state.run_budget.max_python_executions,
        ),
        (
            "specialist_invocations",
            state.run_budget.specialist_invocations,
            state.run_budget.max_specialist_invocations,
        ),
        (
            "critic_loops",
            state.run_budget.critic_loops,
            state.run_budget.max_critic_loops,
        ),
        (
            "charts_created",
            state.run_budget.charts_created,
            state.run_budget.max_charts,
        ),
    )
    for resource, used, limit in budget_checks:
        checks.append(
            _check(
                f"{check_prefix}:budget:{resource}",
                used <= limit,
                f"{resource} usage is {used}/{limit}",
            )
        )
    if state.status is RunStatus.COMPLETED:
        checks.extend(
            (
                _check(
                    f"{check_prefix}:usage_requests",
                    state.usage.requests > 0,
                    "completed run has model request usage",
                ),
                _check(
                    f"{check_prefix}:usage_tokens",
                    state.usage.total_tokens > 0,
                    "completed run has model token usage",
                ),
                _check(
                    f"{check_prefix}:elapsed",
                    state.elapsed_seconds is not None,
                    "completed run has elapsed-time metadata",
                ),
                _check(
                    f"{check_prefix}:cost_metadata",
                    state.cost_estimation_note is not None,
                    "completed run has cost metadata",
                ),
            )
        )
    return tuple(checks)


def _normalized_period(period: str) -> str:
    return normalize_metric_period(period).lower()


def _normalized_dimensions(dimensions: dict[str, str]) -> dict[str, str]:
    return {
        key: value.lower()
        for key, value in normalize_metric_dimensions(dimensions).items()
    }


def _metric_identity_matches(
    comparison: MetricComparison,
    expected: GroundTruthMetric,
) -> bool:
    normalized_actual = normalize_metric_comparison(comparison)
    expected_metric_key = normalize_metric_key(
        expected.metric_key,
        expected.dimensions,
    )
    return (
        normalized_actual.metric_key == expected_metric_key
        and _normalized_period(comparison.baseline_period)
        == _normalized_period(expected.baseline_period)
        and _normalized_period(comparison.comparison_period)
        == _normalized_period(expected.comparison_period)
        and comparison.comparison_type is expected.comparison_type
        and normalized_actual.unit
        == normalize_metric_unit(expected.value_unit, expected.comparison_type)
        and metric_definition_contexts_match(
            normalized_actual.definition_context,
            expected.definition_context,
        )
    )


def _compatible_dimension_superset(
    actual: dict[str, str],
    expected: dict[str, str],
) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


def select_metric_candidates(
    comparisons: Sequence[MetricComparison],
    expected: GroundTruthMetric,
) -> list[MetricComparison]:
    """Select exact estimands before compatible, more-specific corroboration."""

    expected_dimensions = _normalized_dimensions(expected.dimensions)
    candidates = [
        normalize_metric_comparison(comparison)
        for comparison in comparisons
        if _metric_identity_matches(comparison, expected)
    ]
    exact = [
        comparison
        for comparison in candidates
        if _normalized_dimensions(comparison.dimensions) == expected_dimensions
    ]
    if exact:
        return exact
    return [
        comparison
        for comparison in candidates
        if _compatible_dimension_superset(
            _normalized_dimensions(comparison.dimensions),
            expected_dimensions,
        )
    ]


def reconcile_metric_candidates(
    candidates: Sequence[MetricComparison],
    expected: GroundTruthMetric,
) -> tuple[MetricComparison | None, bool]:
    """Compile corroborating measurements and flag material conflicts."""

    expected_dimensions = normalize_metric_dimensions(expected.dimensions)
    projected = [
        comparison.model_copy(update={"dimensions": expected_dimensions})
        for comparison in candidates
    ]
    compilation = compile_metric_comparisons(projected)
    if compilation.conflicts:
        return None, True
    reconciled = compilation.comparisons
    materially_consistent = all(
        isclose(
            left.value,
            right.value,
            rel_tol=_CORROBORATION_RELATIVE_TOLERANCE,
            abs_tol=_CORROBORATION_ABSOLUTE_TOLERANCE,
        )
        for index, left in enumerate(reconciled)
        for right in reconciled[index + 1 :]
    )
    if not materially_consistent:
        return None, True
    return (reconciled[-1] if reconciled else None), False


def metric_comparisons_from_input(
    values: Iterable[MetricComparison] | LeadResult | SpecialistResult,
) -> list[MetricComparison]:
    """Accept common typed outputs for focused primitive tests."""

    if isinstance(values, (LeadResult, SpecialistResult)):
        return values.metric_comparisons
    return list(values)


def numeric_ground_truth_failures(
    values: Iterable[MetricComparison] | LeadResult | SpecialistResult,
    expected_metrics: Sequence[GroundTruthMetric],
) -> list[str]:
    """Return deterministic missing, conflicting, stale, or incorrect metrics."""

    failures: list[str] = []
    comparisons = metric_comparisons_from_input(values)
    for metric in expected_metrics:
        candidates = select_metric_candidates(comparisons, metric)
        if not candidates:
            failures.append(f"missing numeric ground-truth finding: {metric.id}")
            continue
        comparison, conflicting = reconcile_metric_candidates(candidates, metric)
        if conflicting:
            failures.append(
                f"materially conflicting numeric findings for metric: {metric.id}"
            )
            continue
        if comparison is None:
            failures.append(f"missing numeric ground-truth finding: {metric.id}")
            continue
        if not isfinite(comparison.value):
            failures.append(f"numeric finding is not finite: {metric.id}")
            continue
        if abs(comparison.value - metric.expected_relative_change) > metric.tolerance:
            failures.append(
                f"{metric.id}={comparison.value} is outside "
                f"{metric.expected_relative_change} +/- {metric.tolerance}"
            )
    return failures


def evaluate_numeric_comparisons(
    comparisons: Iterable[MetricComparison],
    expected_metrics: Sequence[GroundTruthMetric],
    *,
    check_prefix: str = "numeric",
) -> tuple[EvaluationCheck, ...]:
    """Evaluate generic metric identity, scope, tolerance, and conflicts."""

    actual = list(comparisons)
    checks: list[EvaluationCheck] = []
    for metric in expected_metrics:
        failures = numeric_ground_truth_failures(actual, (metric,))
        checks.append(
            _check(
                f"{check_prefix}:{metric.id}",
                not failures,
                "metric matches expected identity and tolerance"
                if not failures
                else failures[0],
            )
        )
    return tuple(checks)


def compile_final_metric_set(
    comparisons: Iterable[MetricComparison],
    *,
    check_prefix: str = "metric_set",
) -> tuple[tuple[MetricComparison, ...], tuple[EvaluationCheck, ...]]:
    """Compile the one final metric set shared by report, Critic, and evaluator."""

    compilation = compile_metric_comparisons(list(comparisons))
    checks = [
        _check(
            f"{check_prefix}:conflict:{conflict.metric_key}",
            False,
            f"materially conflicting final metric values remain for "
            f"{conflict.metric_key}",
        )
        for conflict in compilation.conflicts
    ]
    if not checks:
        checks.append(
            _check(
                f"{check_prefix}:compiled",
                True,
                "final metric comparisons compiled without material conflicts",
            )
        )
    return tuple(compilation.comparisons), tuple(checks)


def evaluate_data_quality(
    state: AnalysisRunState,
    policy: DataQualityPolicy,
    *,
    check_prefix: str = "data_quality",
) -> tuple[EvaluationCheck, ...]:
    """Evaluate audit completion, expected defects, and false positives."""

    audit = state.audit
    if audit is None:
        return (
            _check(
                f"{check_prefix}:audit_present",
                False,
                "completed Data Audit is missing",
            ),
        )

    checks = [
        _check(
            f"{check_prefix}:audit_status",
            audit.status is policy.required_audit_status,
            f"audit status is {audit.status.value}; expected "
            f"{policy.required_audit_status.value}",
        )
    ]
    issue_ids = {issue.id for issue in audit.issues}
    for issue_id in policy.required_issue_ids:
        checks.append(
            _check(
                f"{check_prefix}:required:{issue_id}",
                issue_id in issue_ids,
                f"required data-quality issue {issue_id} is present",
            )
        )
    for issue_id in policy.forbidden_issue_ids:
        checks.append(
            _check(
                f"{check_prefix}:forbidden:{issue_id}",
                issue_id not in issue_ids,
                f"forbidden data-quality issue {issue_id} is absent",
            )
        )
    if policy.maximum_issue_severity is not None:
        severity_order = {
            IssueSeverity.LOW: 1,
            IssueSeverity.MEDIUM: 2,
            IssueSeverity.HIGH: 3,
        }
        material = [
            issue
            for issue in audit.issues
            if severity_order[issue.severity]
            > severity_order[policy.maximum_issue_severity]
        ]
        checks.append(
            _check(
                f"{check_prefix}:false_positive_material_issue",
                not material,
                "no unexpected material data-quality issue is reported"
                if not material
                else "audit reports an unexpected material data-quality issue: "
                + ", ".join(issue.id for issue in material),
            )
        )
    if policy.forbid_any_issues:
        checks.append(
            _check(
                f"{check_prefix}:no_issues",
                not audit.issues,
                "clean-data audit contains no data-quality issues"
                if not audit.issues
                else "audit reports issues for a clean-data expectation: "
                + ", ".join(issue.id for issue in audit.issues),
            )
        )
    return tuple(checks)


def evaluate_statistics(
    state: AnalysisRunState,
    report_text: str,
    policy: StatisticsPolicy,
    *,
    check_prefix: str = "statistics",
) -> tuple[EvaluationCheck, ...]:
    """Evaluate typed statistical output and report-level requirements.

    Statistical work is accepted from any architecture.  Multi-agent runs
    persist assessments inside specialist results, while a generalist can
    persist the same typed assessments directly on the run state.  The
    evaluator intentionally inspects the output contract, never the producer
    role.
    """

    checks: list[EvaluationCheck] = []
    lowered = report_text.lower()
    checks.extend(
        _check(
            f"{check_prefix}:term:{index}",
            term.lower() in lowered,
            f"report contains required statistical term: {term}",
        )
        for index, term in enumerate(policy.required_report_terms, start=1)
    )
    assessments = _statistical_assessments(state)
    for index, expectation in enumerate(policy.expectations, start=1):
        matches = [
            assessment
            for assessment in assessments
            if _statistical_assessment_matches(assessment, expectation)
        ]
        prefix = f"{check_prefix}:expectation:{index}"
        checks.append(
            _check(
                f"{prefix}:present",
                len(matches) == 1,
                "exactly one typed statistical assessment is present"
                if len(matches) == 1
                else "typed statistical assessment is missing or ambiguous",
            )
        )
        if len(matches) == 1:
            checks.extend(
                _evaluate_statistical_assessment(prefix, matches[0], expectation)
            )
    return tuple(checks) or (
        _check(
            f"{check_prefix}:not_required",
            True,
            "no scenario-specific statistical requirement was declared",
        ),
    )


def _statistical_assessments(
    state: AnalysisRunState,
) -> tuple[StatisticalAssessment, ...]:
    """Return unique typed assessments independent of producing architecture."""

    assessments = [*state.statistical_assessments]
    assessments.extend(
        assessment
        for record in state.specialist_results
        for assessment in record.result.statistical_assessments
    )
    unique: list[StatisticalAssessment] = []
    seen: set[str] = set()
    for assessment in assessments:
        identity = assessment.model_dump_json()
        if identity not in seen:
            seen.add(identity)
            unique.append(assessment)
    return tuple(unique)


def _statistical_assessment_matches(
    assessment: StatisticalAssessment,
    expectation: StatisticalExpectation,
) -> bool:
    """Match a typed assessment to an expected estimand without using prose."""

    return (
        normalize_metric_key(assessment.metric_key, assessment.dimensions)
        == normalize_metric_key(expectation.metric_key, expectation.dimensions)
        and normalize_metric_dimensions(assessment.dimensions)
        == normalize_metric_dimensions(expectation.dimensions)
        and normalize_metric_period(assessment.baseline_period)
        == normalize_metric_period(expectation.baseline_period)
        and normalize_metric_period(assessment.comparison_period)
        == normalize_metric_period(expectation.comparison_period)
    )


def _evaluate_statistical_assessment(
    prefix: str,
    assessment: StatisticalAssessment,
    expectation: StatisticalExpectation,
) -> tuple[EvaluationCheck, ...]:
    """Check the V1 statistical conclusion and its supporting quantities."""

    expected_interval = expectation.expected_confidence_interval
    actual_interval = assessment.confidence_interval
    assumptions = {item.strip().lower() for item in assessment.assumptions_checked}
    required_assumptions = {
        item.strip().lower() for item in expectation.required_assumptions
    }
    return (
        _check(
            f"{prefix}:conclusion",
            assessment.conclusion is expectation.expected_conclusion,
            "statistical conclusion matches the seeded expectation",
        ),
        _check(
            f"{prefix}:confidence_level",
            abs(assessment.confidence_level - expectation.confidence_level) <= 1e-12,
            "confidence level is explicit and correct",
        ),
        _check(
            f"{prefix}:estimate",
            abs(assessment.estimate - expectation.expected_estimate)
            <= expectation.estimate_tolerance,
            "estimated effect matches the seeded effect",
        ),
        _check(
            f"{prefix}:confidence_interval",
            abs(actual_interval.lower - expected_interval.lower)
            <= expectation.confidence_interval_tolerance
            and abs(actual_interval.upper - expected_interval.upper)
            <= expectation.confidence_interval_tolerance,
            "confidence interval matches the seeded interval",
        ),
        _check(
            f"{prefix}:p_value",
            abs(assessment.p_value - expectation.expected_p_value)
            <= expectation.p_value_tolerance,
            "p-value matches the seeded test result",
        ),
        _check(
            f"{prefix}:effect_size",
            abs(assessment.effect_size - expectation.expected_effect_size)
            <= expectation.effect_size_tolerance,
            "effect size matches the seeded effect size",
        ),
        _check(
            f"{prefix}:practical_threshold",
            abs(
                assessment.practical_significance_threshold
                - expectation.practical_significance_threshold
            )
            <= 1e-12,
            "practical significance threshold is explicit and correct",
        ),
        _check(
            f"{prefix}:practical_significance",
            assessment.practically_significant
            is expectation.expected_practically_significant,
            "practical significance conclusion matches the seeded expectation",
        ),
        _check(
            f"{prefix}:assumptions",
            required_assumptions.issubset(assumptions),
            "required statistical assumptions are reported",
        ),
        _check(
            f"{prefix}:causal_restraint",
            assessment.causal_interpretation
            is expectation.expected_causal_interpretation,
            "causal interpretation matches the declared study design",
        ),
    )


def _evidence_refs(workspace: Workspace) -> set[str]:
    """Resolve only successful and verified persisted evidence references."""

    return executed_references(AnalysisLedger(workspace))


def _successful_tool_events(state: AnalysisRunState, tool_name: str):
    return [
        event
        for event in state.tool_events
        if event.tool_name == tool_name and event.status is ToolEventStatus.SUCCEEDED
    ]


def _report_recommendation_evidence_refs(report_text: str) -> list[str]:
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


def evaluate_provenance(
    workspace: Workspace,
    state: AnalysisRunState,
    report_text: str,
    *,
    check_prefix: str = "provenance",
) -> tuple[EvaluationCheck, ...]:
    """Verify executed evidence, registered artifacts, and report citations."""

    successful_sql = _successful_tool_events(state, "run_sql")
    successful_python = _successful_tool_events(state, "run_python")
    checks = [
        _check(
            f"{check_prefix}:sql_execution",
            bool(successful_sql),
            "successful SQL evidence is present",
        ),
        _check(
            f"{check_prefix}:python_execution",
            bool(successful_python),
            "successful Python evidence is present",
        ),
    ]
    for read_only_directory in (workspace.inputs, workspace.docs):
        for path in sorted(
            read_only_directory.rglob("*"),
            key=lambda candidate: str(candidate.relative_to(workspace.root)),
        ):
            if path.is_file():
                writable = path.stat().st_mode & (
                    stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
                )
                checks.append(
                    _check(
                        f"{check_prefix}:read_only:{path.relative_to(workspace.root)}",
                        not writable,
                        "read-only source is not writable: "
                        f"{path.relative_to(workspace.root)}",
                    )
                )
    for event in [*successful_sql, *successful_python]:
        for reference in event.artifact_refs:
            checks.append(
                _check(
                    f"{check_prefix}:tool_artifact:{event.id}:{reference}",
                    (workspace.root / reference).is_file(),
                    f"tool evidence path exists: {reference}",
                )
            )

    refs = _evidence_refs(workspace)
    for finding in state.findings:
        checks.append(
            _check(
                f"{check_prefix}:finding:{finding.id}",
                bool(finding.evidence_refs)
                and all(reference in refs for reference in finding.evidence_refs),
                f"finding {finding.id} cites executed evidence",
            )
        )
    for hypothesis in state.hypotheses:
        if hypothesis.status.value != "open":
            checks.append(
                _check(
                    f"{check_prefix}:hypothesis:{hypothesis.id}",
                    bool(hypothesis.evidence_refs)
                    and any(
                        reference in refs for reference in hypothesis.evidence_refs
                    ),
                    f"resolved hypothesis {hypothesis.id} cites executed evidence",
                )
            )
    for comparison in state.metric_comparisons:
        checks.append(
            _check(
                f"{check_prefix}:metric:{comparison.metric_key}",
                bool(comparison.evidence_refs)
                and all(reference in refs for reference in comparison.evidence_refs),
                f"metric {comparison.metric_key} cites executed evidence",
            )
        )
    for assessment in _statistical_assessments(state):
        checks.append(
            _check(
                f"{check_prefix}:statistical_assessment:{assessment.metric_key}",
                bool(assessment.evidence_refs)
                and all(reference in refs for reference in assessment.evidence_refs),
                "statistical assessment cites executed evidence",
            )
        )

    if state.final_report is None:
        checks.append(
            _check(
                f"{check_prefix}:final_report",
                False,
                "final report record is missing",
            )
        )
    else:
        report_path = workspace.root / state.final_report.path
        checks.extend(
            (
                _check(
                    f"{check_prefix}:final_report_kind",
                    state.final_report.kind is ArtifactKind.REPORT,
                    "final report is registered as a report artifact",
                ),
                _check(
                    f"{check_prefix}:final_report_file",
                    report_path.is_file(),
                    "final report file exists",
                ),
            )
        )
    recommendation_refs = _report_recommendation_evidence_refs(report_text)
    checks.extend(
        (
            _check(
                f"{check_prefix}:recommendation_refs_present",
                bool(recommendation_refs),
                "recommendations cite evidence"
                if recommendation_refs
                else "final report recommendations cite no evidence",
            ),
            _check(
                f"{check_prefix}:recommendation_refs_executed",
                bool(recommendation_refs)
                and all(reference in refs for reference in recommendation_refs),
                "recommendation evidence references were executed",
            ),
        )
    )
    for artifact in state.artifacts:
        try:
            verified = ArtifactManager(
                workspace, AnalysisLedger(workspace)
            ).verify_artifact(artifact.id)
        except (OSError, ValueError, KeyError):
            verified = False
        checks.append(
            _check(
                f"{check_prefix}:artifact:{artifact.id}",
                verified,
                f"artifact provenance verified: {artifact.id}",
            )
        )
    return tuple(checks)


def _analysis_sentences(text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<!\d)[.!?]+(?!\d)|\n+", text)
        if sentence.strip()
    ]


def contains_asserted_mechanism(
    text: str,
    *,
    subject_terms: Sequence[str],
    mechanism_terms: Sequence[str],
    change_terms: Sequence[str],
    causal_terms: Sequence[str],
    uncertainty_terms: Sequence[str] = (),
) -> bool:
    """Require a non-speculative sentence linking a mechanism to a change."""

    for sentence in _analysis_sentences(text.lower()):
        if not all(re.search(term, sentence) for term in subject_terms):
            continue
        if not all(re.search(term, sentence) for term in mechanism_terms):
            continue
        if any(re.search(term, sentence) for term in uncertainty_terms):
            continue
        if not any(re.search(term, sentence) for term in change_terms):
            continue
        if any(re.search(term, sentence) for term in causal_terms):
            return True
    return False


def contains_material_driver(
    text: str,
    *,
    subject_terms: Sequence[str],
    material_terms: Sequence[str],
    outcome_terms: Sequence[str],
) -> bool:
    """Require a material driver statement for a named subject."""

    for sentence in _analysis_sentences(text.lower()):
        if (
            all(re.search(term, sentence) for term in subject_terms)
            and any(re.search(term, sentence) for term in material_terms)
            and any(re.search(term, sentence) for term in outcome_terms)
        ):
            return True
    return False


def contains_all_concepts(text: str, patterns: Sequence[str]) -> bool:
    """Require every concept in a scenario's decomposition checklist."""

    lowered = text.lower()
    return all(re.search(pattern, lowered) for pattern in patterns)


def contains_stable_conclusion(
    text: str,
    *,
    value_terms: Sequence[str],
    stable_terms: Sequence[str],
) -> bool:
    """Require stability language in the same sentence as the measured value."""

    for sentence in _analysis_sentences(text.lower()):
        if all(re.search(term, sentence) for term in value_terms) and any(
            re.search(term, sentence) for term in stable_terms
        ):
            return True
    return False


def contains_non_driver_conclusion(
    text: str,
    *,
    subject_terms: Sequence[str],
    non_driver_terms: Sequence[str],
) -> bool:
    """Require explicit language ruling a known non-driver out."""

    for sentence in _analysis_sentences(text.lower()):
        if all(re.search(term, sentence) for term in subject_terms) and any(
            re.search(term, sentence) for term in non_driver_terms
        ):
            return True
    return False


def evaluate_root_cause(
    text: str,
    rules: Sequence[TextRule],
    *,
    check_prefix: str = "root_cause",
) -> tuple[EvaluationCheck, ...]:
    """Run scenario-provided primary-driver and non-driver semantic rules."""

    return tuple(
        _check(
            f"{check_prefix}:{rule.check_id}",
            rule.predicate(text),
            rule.description,
        )
        for rule in rules
    )


def evaluate_unsupported_claims(
    text: str,
    *,
    forbidden_patterns: Sequence[str] = (
        r"\bproves?\b",
        r"\bguarantee[sd]?\b",
        r"\bdefinitively\b",
        r"\bcausal proof\b",
        r"\bwithout uncertainty\b",
    ),
    check_prefix: str = "unsupported_claims",
) -> tuple[EvaluationCheck, ...]:
    """Reject deterministic overclaims that cannot be supported offline."""

    lowered = text.lower()
    matches = [pattern for pattern in forbidden_patterns if re.search(pattern, lowered)]
    return (
        _check(
            f"{check_prefix}:forbidden_language",
            not matches,
            "report contains no unsupported certainty or causal-proof language"
            if not matches
            else "report contains unsupported claim language: " + ", ".join(matches),
        ),
    )


def evaluate_task_completeness(
    workspace: Workspace,
    state: AnalysisRunState,
    report_text: str,
    policy: TaskCompletenessPolicy,
    *,
    check_prefix: str = "task_completeness",
) -> tuple[EvaluationCheck, ...]:
    """Evaluate final report, Critic, plan, evidence, and output completeness."""

    checks: list[EvaluationCheck] = []
    if policy.require_plan:
        checks.append(
            _check(
                f"{check_prefix}:investigation_plan",
                bool(state.investigation_plan),
                "investigation plan is present",
            )
        )
    if policy.require_hypothesis_history:
        checks.append(
            _check(
                f"{check_prefix}:hypothesis_history",
                bool(state.hypotheses) and bool(state.hypothesis_history),
                "current hypotheses and hypothesis history are present",
            )
        )
    if policy.require_findings:
        checks.append(
            _check(
                f"{check_prefix}:findings",
                bool(state.findings),
                "findings are present",
            )
        )
    if policy.require_structured_metrics:
        checks.append(
            _check(
                f"{check_prefix}:structured_metrics",
                bool(state.metric_comparisons),
                "structured metric comparisons are present",
            )
        )
    if policy.require_chart:
        checks.append(
            _check(
                f"{check_prefix}:chart",
                any(
                    artifact.kind is ArtifactKind.CHART for artifact in state.artifacts
                ),
                "a chart artifact is present",
            )
        )
    if policy.require_final_critic_pass:
        checks.append(
            _check(
                f"{check_prefix}:critic",
                bool(state.validation_results)
                and state.validation_results[-1].status is ValidationStatus.PASS,
                "final Critic validation passed",
            )
        )
    if policy.require_recommendations:
        match = re.search(
            r"(?ims)^## Recommendations\s*$\n(?P<body>.*?)(?=^## |\Z)",
            report_text,
        )
        body = match.group("body").strip().lower() if match else ""
        checks.append(
            _check(
                f"{check_prefix}:recommendations",
                bool(body) and "no recommendations were returned" not in body,
                "final report has recommendations",
            )
        )
    report = state.final_report
    checks.append(
        _check(
            f"{check_prefix}:report_file",
            report is not None and (workspace.root / report.path).is_file(),
            "final report file exists",
        )
    )
    if policy.require_agent_trace:
        checks.append(
            _check(
                f"{check_prefix}:agent_trace",
                any(
                    event.status is AgentEventStatus.SUCCEEDED
                    for event in state.agent_events
                ),
                "successful agent execution trace is present",
            )
        )
    if policy.require_tool_trace:
        checks.append(
            _check(
                f"{check_prefix}:tool_trace",
                any(
                    event.status is ToolEventStatus.SUCCEEDED
                    for event in state.tool_events
                ),
                "successful tool execution trace is present",
            )
        )
    return tuple(checks)


__all__ = [
    "DataQualityPolicy",
    "StatisticsPolicy",
    "TaskCompletenessPolicy",
    "TextRule",
    "contains_all_concepts",
    "contains_asserted_mechanism",
    "contains_material_driver",
    "contains_non_driver_conclusion",
    "contains_stable_conclusion",
    "compile_final_metric_set",
    "evaluate_data_quality",
    "evaluate_lifecycle",
    "evaluate_numeric_comparisons",
    "evaluate_provenance",
    "evaluate_root_cause",
    "evaluate_statistics",
    "evaluate_task_completeness",
    "evaluate_unsupported_claims",
    "metric_comparisons_from_input",
    "numeric_ground_truth_failures",
    "reconcile_metric_candidates",
    "select_metric_candidates",
]
