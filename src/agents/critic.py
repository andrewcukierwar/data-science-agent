"""Critic / Validator specialist for evidence-backed analysis review."""

from __future__ import annotations

from math import isclose
from pathlib import Path

from agents import Agent
from agents.evidence import evidence_events, has_source_lineage
from agents.model_usage import run_agent_with_usage
from agents.output_contract import require_strict_output, strict_output_type
from agents.runtime import AgentRole, AgentRunConfig, AgentRunContext
from agents.tools import tools_for_role
from orchestration.budgets import BudgetResource
from orchestration.ledger import AnalysisLedger
from schemas.metrics import (
    MetricComparison,
    dimension_mapping,
    metric_comparison_identity,
    metric_comparison_scope_identity,
    normalize_metric_comparison,
)
from schemas.validation import (
    CriticCandidate,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)

CRITIC_OBJECTIVE = (
    "Validate the candidate analysis and recommendations against their evidence."
)

_FALLBACK_SKILL_GUIDANCE = """Validation procedure:

1. Reproduce material numbers from the referenced query or script.
2. Check definitions, denominators, table grain, joins, artifacts, and causal
   language. Confirm spend was aggregated before joining to lower-grain facts.
3. For named periods, verify explicit boundaries and reconcile cohort counts to
   the acquisition table; do not treat every non-Q1 period as Q2.
4. Check whether the candidate answered the objective, resolved material
   follow-up questions, and investigated available upstream mechanisms.
5. When acquisition economics materially support the explanation, require the
   final synthesis to close spend -> sessions/traffic -> conversion -> acquired
   customers -> CAC -> downstream LTV/value, while distinguishing observed
   relationships from unsupported upstream causal explanations.
6. Return PASS only when the candidate is supported and complete; otherwise
   return REVISE with severity, evidence, and a concrete remediation.
"""


def candidate_completeness_validation(
    candidate: CriticCandidate,
    *,
    context: AgentRunContext | None = None,
) -> ValidationResult | None:
    """Apply deterministic completeness gates before model-based review.

    ``follow_up_analysis`` is an explicit Lead contract, rather than merely a
    caveat for the final prose.  The runner normally resolves it before
    invoking the Critic.  This guard also protects direct Critic callers and
    bounded runs that reach Critic after continuation capacity is exhausted.
    """

    issues: list[ValidationIssue] = []
    if candidate.follow_up_analysis:
        question = (
            candidate.follow_up_rationale
            or (candidate.open_questions[0] if candidate.open_questions else None)
            or "The candidate requests additional analysis."
        )
        issues.append(
            ValidationIssue(
                id="V-COMPLETENESS-FOLLOW-UP",
                severity=ValidationSeverity.HIGH,
                category="task_completeness",
                message=(
                    "The candidate explicitly leaves objective-critical follow-up "
                    f"analysis unresolved: {question}"
                ),
                evidence_refs=candidate.evidence_refs,
                recommendation=(
                    "Complete the bounded follow-up with the appropriate specialist, "
                    "or set follow_up_analysis=false only after documenting why the "
                    "question is unanswerable or immaterial."
                ),
            )
        )

    if _profitability_requires_margin_review(candidate, context):
        issues.append(
            ValidationIssue(
                id="V-COMPLETENESS-MARGIN",
                severity=ValidationSeverity.HIGH,
                category="task_completeness",
                message=(
                    "The profitability candidate does not complete the revenue, "
                    "COGS, contribution-before-marketing, and margin comparison "
                    "even though COGS data is available."
                ),
                recommendation=(
                    "Compare net revenue, COGS, contribution before marketing, and "
                    "contribution margin or the COGS/revenue ratio. State whether "
                    "broad margin is a material driver or non-driver before "
                    "finalizing the profitability explanation."
                ),
            )
        )

    if _acquisition_closure_required(candidate, context):
        missing = _acquisition_closure_missing(candidate)
        if missing:
            issues.append(
                ValidationIssue(
                    id="V-COMPLETENESS-ACQUISITION",
                    severity=ValidationSeverity.HIGH,
                    category="task_completeness",
                    message=(
                        "The candidate invokes an acquisition-efficiency explanation "
                        "but does not close the observable funnel: missing "
                        + ", ".join(missing)
                        + "."
                    ),
                    recommendation=(
                        "Explicitly connect spend, sessions/traffic, conversion, "
                        "acquired customers, CAC, and downstream LTV/value. "
                        "Separate observed relationships from unsupported causal "
                        "explanations for upstream changes."
                    ),
                )
            )

        if candidate.structured_metrics_required:
            missing_metrics = _acquisition_metric_comparisons_missing(candidate)
            if missing_metrics:
                issues.append(
                    ValidationIssue(
                        id="V-COMPLETENESS-STRUCTURED-METRICS",
                        severity=ValidationSeverity.HIGH,
                        category="structured_metric_completeness",
                        message=(
                            "The candidate relies on a material acquisition-efficiency "
                            "decomposition but is missing structured comparisons for: "
                            + ", ".join(missing_metrics)
                            + "."
                        ),
                        recommendation=(
                            "Reuse the exact structured comparisons returned by the "
                            "specialist evidence for each material funnel component."
                        ),
                    )
                )

        if (
            candidate.visualization_requested
            and context is not None
            and context.ledger.budget.charts_created < context.ledger.budget.max_charts
            and not any(
                artifact.kind.value == "chart" for artifact in context.ledger.artifacts
            )
        ):
            issues.append(
                ValidationIssue(
                    id="V-COMPLETENESS-CHART",
                    severity=ValidationSeverity.MEDIUM,
                    category="chart_completeness",
                    message=(
                        "The candidate contains a material multi-component "
                        "decomposition but no useful registered chart artifact."
                    ),
                    recommendation=(
                        "Delegate one bounded chart creation task to the Analyst and "
                        "carry the returned chart artifact reference forward."
                    ),
                )
            )

    if not issues:
        return None

    return ValidationResult(
        status=ValidationStatus.REVISE,
        issues=issues,
        checked_finding_ids=[finding.id for finding in candidate.findings],
        summary="The candidate is not complete for the stated objective.",
    )


def _profitability_requires_margin_review(
    candidate: CriticCandidate,
    context: AgentRunContext | None,
) -> bool:
    """Require a COGS/margin discussion when the workspace exposes COGS."""

    if context is None or not _workspace_has_cogs(context):
        return False
    objective_text = candidate.objective.lower()
    if not any(term in objective_text for term in ("profit", "margin", "contribution")):
        return False
    candidate_text = " ".join(
        [
            candidate.answer,
            *(
                item.statement + " " + (item.metric or "")
                for item in candidate.findings
            ),
            *(hypothesis.statement for hypothesis in candidate.hypotheses),
            *candidate.recommendations,
        ]
    ).lower()
    metric_keys = {
        normalize_metric_comparison(item).metric_key
        for item in candidate.metric_comparisons
    }
    required_metric_groups = (
        {"net_revenue"},
        {"cogs"},
        {"contribution_before_marketing", "gross_contribution"},
        {"contribution_margin", "cogs_to_revenue_ratio", "cogs_revenue_ratio"},
    )
    has_structured_components = all(
        bool(metric_keys.intersection(group)) for group in required_metric_groups
    )
    required_components = (
        ("net revenue", "revenue"),
        ("cogs",),
        ("contribution before marketing", "gross contribution"),
        ("margin", "cogs/revenue", "cogs to revenue"),
    )
    has_prose_components = all(
        any(term in candidate_text for term in alternatives)
        for alternatives in required_components
    )
    non_driver_terms = (
        "not a driver",
        "non-driver",
        "non driver",
        "did not drive",
        "didn't drive",
        "not material",
        "no broad",
        "stable margin",
        "margin was stable",
        "margin was effectively stable",
        "margin remained effectively stable",
        "margin deterioration was not a material driver",
        "cogs was stable",
        "cogs remained stable",
    )
    driver_terms = (
        "margin driver",
        "cogs driver",
        "margin deteriorated",
        "cogs deteriorated",
        "margin increased",
        "cogs increased",
        "margin changed materially",
        "cogs changed materially",
    )
    return not (
        (has_structured_components or has_prose_components)
        and any(term in candidate_text for term in (*non_driver_terms, *driver_terms))
    )


def _acquisition_closure_required(
    candidate: CriticCandidate,
    context: AgentRunContext | None,
) -> bool:
    """Detect material acquisition analysis that needs a complete funnel path."""

    objective_text = candidate.objective.lower()
    if not any(term in objective_text for term in ("profit", "acquisition", "cac")):
        return False
    candidate_text = " ".join(
        [
            candidate.answer,
            *(finding.statement for finding in candidate.findings),
            *(hypothesis.statement for hypothesis in candidate.hypotheses),
            *candidate.recommendations,
            *(comparison.metric_key for comparison in candidate.metric_comparisons),
        ]
    ).lower()
    acquisition_terms = (
        "spend",
        "acquisition",
        "cac",
        "conversion",
        "acquired customer",
        "new customer",
        "ltv",
        "session",
        "traffic",
    )
    if sum(term in candidate_text for term in acquisition_terms) < 2:
        return False
    if context is None:
        return True
    input_relations = getattr(context.sql_service, "input_relations", {})
    return "sessions" in input_relations or "customers" in input_relations


def _acquisition_closure_missing(candidate: CriticCandidate) -> list[str]:
    """Return missing generic acquisition-funnel components."""

    text = " ".join(
        [
            candidate.answer,
            *(finding.statement for finding in candidate.findings),
            *(hypothesis.statement for hypothesis in candidate.hypotheses),
            *candidate.recommendations,
            *(comparison.metric_key for comparison in candidate.metric_comparisons),
        ]
    ).lower()
    checks = {
        "marketing spend": ("marketing spend", "spend", "marketing"),
        "sessions/traffic": ("session", "traffic"),
        "conversion": ("conversion", "converted"),
        "acquired customers": ("acquired customer", "new customer", "customer volume"),
        "CAC": ("cac", "customer acquisition cost"),
        "downstream LTV/value": ("ltv", "lifetime value", "customer value"),
    }
    return [
        label
        for label, terms in checks.items()
        if not any(term in text for term in terms)
    ]


def _acquisition_metric_comparisons_missing(
    candidate: CriticCandidate,
) -> list[str]:
    """Return material acquisition metrics absent from structured output."""

    comparisons = [
        normalize_metric_comparison(item) for item in candidate.metric_comparisons
    ]
    required = {
        "marketing_spend": "marketing spend",
        "sessions": "sessions/traffic",
        "conversion_rate": "conversion",
        "acquired_customers": "acquired customers",
        "cac": "CAC",
        "ltv": "downstream LTV/value",
    }
    dimension_groups: dict[tuple[tuple[str, str], ...], set[str]] = {}
    for comparison in comparisons:
        dimensions = tuple(
            sorted(
                (dimension.name, dimension.value.lower())
                for dimension in comparison.dimensions
            )
        )
        dimension_groups.setdefault(dimensions, set()).add(comparison.metric_key)

    material_dimensions = [
        dimensions
        for dimensions, keys in dimension_groups.items()
        if "cac" in keys
        and dimensions
        and any(
            value not in {"all", "all channels", "overall"} for _, value in dimensions
        )
    ]
    if not material_dimensions:
        material_dimensions = [()]

    missing: list[str] = []
    for dimensions in material_dimensions:
        keys = dimension_groups.get(dimensions, set())
        dimension_label = ", ".join(f"{key}={value}" for key, value in dimensions)
        for key, label in required.items():
            if key not in keys:
                missing.append(
                    f"{label} ({dimension_label})" if dimension_label else label
                )
    return missing


def validate_metric_compilation_conflicts(
    candidate: CriticCandidate,
) -> ValidationResult | None:
    """Require remediation of materially conflicting duplicate measurements."""

    if not candidate.metric_conflicts:
        return None
    issues = [
        ValidationIssue(
            id=f"V-METRIC-CONFLICT-{index}",
            severity=ValidationSeverity.HIGH,
            category="structured_metric_conflict",
            message=(
                f"Materially conflicting values remain for '{conflict.metric_key}' "
                f"with dimensions {dimension_mapping(conflict.dimensions)}: "
                + ", ".join(str(item.value) for item in conflict.comparisons)
                + "."
            ),
            evidence_refs=list(
                dict.fromkeys(
                    reference
                    for item in conflict.comparisons
                    for reference in item.evidence_refs
                )
            ),
            recommendation=(
                "Reconcile the computations at the same grain and definition; "
                "retain a corrected comparison only when its evidence resolves "
                "the material discrepancy."
            ),
        )
        for index, conflict in enumerate(candidate.metric_conflicts, start=1)
    ]
    return ValidationResult(
        status=ValidationStatus.REVISE,
        issues=issues,
        summary="Materially conflicting structured metrics require remediation.",
    )


def _workspace_has_cogs(context: AgentRunContext) -> bool:
    """Read only the approved orders schema without spending a SQL budget."""

    input_relations = getattr(context.sql_service, "input_relations", {})
    orders_path = input_relations.get("orders")
    if orders_path is None:
        return False
    try:
        if orders_path.suffix.lower() == ".parquet":
            import pyarrow.parquet as parquet

            return "cogs" in parquet.read_schema(orders_path).names
        if orders_path.suffix.lower() == ".csv":
            return "cogs" in orders_path.open(encoding="utf-8").readline().split(",")
    except (OSError, ValueError, ImportError):
        return False
    return False


def _events_for_evidence(
    ledger: AnalysisLedger,
    references: list[str],
) -> list[object]:
    """Resolve direct tool-event, path, and artifact evidence references."""

    return list(evidence_events(ledger, references))


def _event_metric_comparisons(event: object) -> list[MetricComparison]:
    """Extract explicitly structured metric payloads retained by a tool event."""

    output = getattr(event, "output", None)
    if not isinstance(output, dict):
        return []
    payload = output.get("metric_comparisons", output.get("metric_comparison"))
    if payload is None:
        payload = output.get("metrics")
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        columns = output.get("columns")
        rows = output.get("rows")
        if isinstance(columns, list) and isinstance(rows, list):
            payload = [
                dict(zip(columns, row, strict=False))
                for row in rows
                if isinstance(row, list)
            ]
        else:
            return []

    comparisons: list[MetricComparison] = []
    for item in payload:
        if not isinstance(item, dict) or "metric_key" not in item:
            continue
        item = dict(item)
        item.setdefault("evidence_refs", [event.id])
        try:
            comparisons.append(MetricComparison.model_validate(item))
        except (TypeError, ValueError):
            continue
    return comparisons


def validate_structured_metric_comparisons(
    candidate: CriticCandidate,
    ledger: AnalysisLedger,
) -> ValidationResult | None:
    """Detect structured metric identity or value conflicts in retained evidence."""

    issues: list[ValidationIssue] = []
    for index, comparison in enumerate(candidate.metric_comparisons, start=1):
        comparison = normalize_metric_comparison(comparison)
        evidence_comparisons = [
            normalize_metric_comparison(evidence_comparison)
            for event in _events_for_evidence(ledger, comparison.evidence_refs)
            for evidence_comparison in _event_metric_comparisons(event)
        ]
        if not evidence_comparisons:
            continue
        identity = metric_comparison_identity(comparison)
        matching = [
            item
            for item in evidence_comparisons
            if metric_comparison_identity(item) == identity
        ]
        issue_id = f"V-METRIC-{index}"
        if not matching:
            scope_matches = [
                item
                for item in evidence_comparisons
                if metric_comparison_scope_identity(item)
                == metric_comparison_scope_identity(comparison)
            ]
            if scope_matches:
                candidate_context = (
                    comparison.definition_context.model_dump(exclude_none=True)
                    if comparison.definition_context is not None
                    else None
                )
                evidence_contexts = [
                    item.definition_context.model_dump(exclude_none=True)
                    if item.definition_context is not None
                    else None
                    for item in scope_matches
                ]
                issues.append(
                    ValidationIssue(
                        id=issue_id,
                        severity=ValidationSeverity.HIGH,
                        category="metric_definition",
                        message=(
                            f"Structured metric '{comparison.metric_key}' has a "
                            "definition-scope mismatch: the candidate and cited "
                            f"evidence use different populations/date bases/windows "
                            f"({candidate_context} "
                            f"vs {evidence_contexts})."
                        ),
                        evidence_refs=comparison.evidence_refs,
                        recommendation=(
                            "Keep the documented population, date basis, observation "
                            "window, numerator, and denominator fixed, or label a "
                            "different estimand as a separate comparison."
                        ),
                    )
                )
                continue
            issues.append(
                ValidationIssue(
                    id=issue_id,
                    severity=ValidationSeverity.HIGH,
                    category="structured_metric",
                    message=(
                        f"Structured metric '{comparison.metric_key}' does not match "
                        "the metric identity, dimensions, periods, comparison type, "
                        "or unit in its cited evidence."
                    ),
                    evidence_refs=comparison.evidence_refs,
                    recommendation=(
                        "Reuse the exact structured comparison emitted by the "
                        "executed evidence."
                    ),
                )
            )
            continue
        if not any(
            item.unit == comparison.unit
            and isclose(item.value, comparison.value, rel_tol=1e-6, abs_tol=1e-9)
            for item in matching
        ):
            issues.append(
                ValidationIssue(
                    id=issue_id,
                    severity=ValidationSeverity.HIGH,
                    category="structured_metric",
                    message=(
                        f"Structured metric '{comparison.metric_key}' value "
                        "is inconsistent with its cited evidence."
                    ),
                    evidence_refs=comparison.evidence_refs,
                    recommendation=(
                        "Copy the exact value and unit from the executed metric "
                        "evidence."
                    ),
                )
            )
    if not issues:
        return None
    return ValidationResult(
        status=ValidationStatus.REVISE,
        issues=issues,
        summary="One or more structured metric comparisons conflict with evidence.",
    )


def validate_candidate_evidence_provenance(
    candidate: CriticCandidate,
    ledger: AnalysisLedger,
) -> ValidationResult | None:
    """Reject material claims whose sole SQL evidence is hard-coded output."""

    invalid: list[str] = []
    for finding in candidate.findings:
        if (
            finding.metric is not None or finding.value is not None
        ) and not has_source_lineage(ledger, finding.evidence_refs):
            invalid.append(f"finding:{finding.id}")
    invalid.extend(
        f"metric_comparison:{comparison.metric_key}"
        for comparison in candidate.metric_comparisons
        if not has_source_lineage(ledger, comparison.evidence_refs)
    )
    if not invalid:
        return None
    return ValidationResult(
        status=ValidationStatus.REVISE,
        issues=[
            ValidationIssue(
                id="V-EVIDENCE-SOURCE-LINEAGE",
                severity=ValidationSeverity.HIGH,
                category="evidence_provenance",
                message=(
                    "Material quantitative claims rely solely on analysis that does "
                    "not visibly derive from an approved input relation: "
                    + ", ".join(invalid)
                ),
                recommendation=(
                    "Re-run the calculation from approved input relations or a "
                    "source-derived evidence artifact; do not use VALUES-only SQL "
                    "containing previously computed results."
                ),
            )
        ],
        checked_finding_ids=[finding.id for finding in candidate.findings],
        summary="Material evidence provenance is not source-derived.",
    )


def _skill_guidance() -> str:
    """Load repository critic guidance with a safe fallback."""

    skill_path = Path(__file__).resolve().parents[2] / "skills" / "critic_validation.md"
    try:
        content = skill_path.read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK_SKILL_GUIDANCE
    return content or _FALLBACK_SKILL_GUIDANCE


CRITIC_INSTRUCTIONS = f"""You are the Critic / Validator specialist in an
evidence-backed business analytics system.

You independently review the candidate findings and recommendations supplied
in the task input. Use the workspace, business definitions, candidate evidence,
and only your approved deterministic tools. You cannot delegate, hand off, or
invoke another agent. Return only a ValidationResult for the calling
orchestration layer, not a user-facing final report.

Required review procedure:

- Inspect the workspace and read the relevant business definitions before
  judging any metric or recommendation.
- Trace every material finding and recommendation to its cited query, script,
  tool event, or registered artifact. Use inspect_evidence for a cited event or
  artifact before reproducing important numerical claims with SQL or Python;
  compare values, units, periods, and rounding.
- Check that metric definitions, date windows, cohorts, refund/cancellation
  rules, and reporting conventions match the documented business definitions.
- Check denominators, especially CAC/new-customer denominators, rates, cohort
  sizes, and contribution-profit components.
- Validate each structured metric comparison's generic identity, periods, unit,
  value, exact evidence_refs, and definition_context; treat population, date
  basis, observation window, numerator, and denominator as part of the estimand.
  A cohort-window comparison and a calendar-event comparison with the same
  metric key are distinct scopes, not an unexplained numerical contradiction.
  Reproduce important comparisons from the cited evidence rather than trusting
  labels or prose.
- Check joins for accidental row multiplication, duplicate keys, unresolved
  foreign keys, and mismatched grains.
- For profitability questions, check that the candidate addressed net revenue,
  COGS/margin, contribution before marketing, marketing spend/acquisition
  efficiency, the largest relevant segment, and material downstream customer
  value. Require material non-drivers to be stated explicitly when the data is
  available.
- Compare findings with the actual query/script outputs and registered artifact
  contents. Flag inconsistencies, contradictions, or artifacts that do not
  support the claim.
- Reject a material quantitative claim whose only inspectable SQL evidence is a
  hard-coded VALUES result that does not read an approved input relation. A
  source-derived summary is acceptable only when its lineage is retained.
- Flag unsupported causal language in observational comparisons. Association,
  timing, or correlation is not proof that a campaign, channel, or intervention
  caused an outcome.
- Check whether the candidate ignored a documented data-quality issue or used a
  data-quality limitation to make an unsupported recommendation.
- Judge whether each recommendation is supported by the available evidence and
  is proportional to the uncertainty. Identify important alternative
  explanations when the evidence does not discriminate between them.
- Check task completeness, not just evidence correctness. Review the candidate
  answer, hypothesis dispositions, open questions, and follow-up decision.
  Return REVISE when the candidate itself identifies an unresolved question that
  is material to the objective and answerable with the available data/tools,
  when it says a feasible analysis is still needed to distinguish central
  explanations, or when it reports a metric movement but stops before examining
  an available upstream mechanism even though the objective asks why.
- Treat follow_up_analysis=true as an explicit request for more work, not as a
  harmless caveat. It may pass only when the unresolved question is genuinely
  unanswerable or immaterial to the objective; otherwise require the Lead to
  complete the bounded follow-up.
- Return PASS when no material issue remains. Return REVISE with one or more
  severity-based ValidationIssue objects when remediation is required. Each
  issue should include exact evidence_refs and a concrete recommendation when
  possible.

Do not invent a defect or contradiction. If evidence is unavailable, state the
limitation in the ValidationResult summary or issue rather than assuming a
fact. Keep the result concise and actionable.

Procedural skill guidance:
{_skill_guidance()}
"""

VALIDATOR_INSTRUCTIONS = CRITIC_INSTRUCTIONS
VALIDATOR_OBJECTIVE = CRITIC_OBJECTIVE


class CriticPersistenceError(ValueError):
    """Raised when a validation issue conflicts with persisted ledger state."""


def build_critic_agent(
    config: AgentRunConfig | None = None,
    *,
    model: str | None = None,
    instructions: str | None = None,
) -> Agent[AgentRunContext]:
    """Build the Critic with read, SQL, and Python tools and no delegation."""

    if config is not None and config.agent_role is not AgentRole.CRITIC:
        raise ValueError("Critic requires a critic run configuration")
    selected_model = model or (config.model if config is not None else None)
    return Agent[AgentRunContext](
        name="Critic",
        instructions=instructions or CRITIC_INSTRUCTIONS,
        model=selected_model,
        tools=tools_for_role(AgentRole.CRITIC),
        handoffs=[],
        output_type=strict_output_type(ValidationResult),
    )


build_validator_agent = build_critic_agent
create_critic_agent = build_critic_agent
create_validator_agent = build_critic_agent


def _candidate_prompt(candidate: CriticCandidate) -> str:
    """Serialize the typed candidate without exposing local context internals."""

    return (
        "Validate this candidate analysis. The listed evidence references are "
        "workspace-relative paths, tool-event IDs, or registered artifact IDs. "
        "Use the approved tools to inspect or reproduce them. Check the "
        "candidate completeness fields as well as its evidence.\n\n"
        "CANDIDATE_ANALYSIS_JSON:\n"
        f"{candidate.model_dump_json(indent=2)}"
    )


def persist_validation_result(
    result: ValidationResult,
    ledger: AnalysisLedger,
    *,
    allow_issue_updates: bool = False,
) -> ValidationResult:
    """Persist one validation result and its unique issues in the ledger.

    Remediation loops may update an issue with the same stable identifier; the
    default direct-operation behavior remains conflict-safe for callers that do
    not explicitly opt into that lifecycle behavior.
    """

    existing_issues = {issue.id: issue for issue in ledger.validation_issues}
    for issue in result.issues:
        existing = existing_issues.get(issue.id)
        if existing is not None and existing != issue and not allow_issue_updates:
            raise CriticPersistenceError(
                f"validation issue id already exists with different content: {issue.id}"
            )
    ledger.add_validation_result(result)
    for issue in result.issues:
        if allow_issue_updates:
            ledger.upsert_validation_issue(issue)
        elif issue.id not in existing_issues:
            ledger.add_validation_issue(issue)
    return result


async def run_critic(
    context: AgentRunContext,
    candidate: CriticCandidate,
    *,
    agent: Agent[AgentRunContext] | None = None,
) -> ValidationResult:
    """Review a typed candidate and persist the typed validation result."""

    if context.agent_role is not AgentRole.CRITIC:
        raise ValueError("run_critic requires a Critic context")

    # Critic is mandatory lifecycle validation, not Lead-delegated analytical
    # specialist work. Its hard limit is the separate critic-loop budget.
    context.consume_budget(BudgetResource.CRITIC_LOOPS)

    conflicts = validate_metric_compilation_conflicts(candidate)
    if conflicts is not None:
        return persist_validation_result(
            conflicts,
            context.ledger,
            allow_issue_updates=True,
        )
    completeness = candidate_completeness_validation(candidate, context=context)
    if completeness is not None:
        return persist_validation_result(
            completeness,
            context.ledger,
            allow_issue_updates=True,
        )
    metric_validation = validate_structured_metric_comparisons(
        candidate,
        context.ledger,
    )
    if metric_validation is not None:
        return persist_validation_result(
            metric_validation,
            context.ledger,
            allow_issue_updates=True,
        )
    provenance_validation = validate_candidate_evidence_provenance(
        candidate,
        context.ledger,
    )
    if provenance_validation is not None:
        return persist_validation_result(
            provenance_validation,
            context.ledger,
            allow_issue_updates=True,
        )

    selected_agent = agent or build_critic_agent(context.run_config)
    result = await run_agent_with_usage(
        selected_agent,
        _candidate_prompt(candidate),
        context=context,
        max_turns=context.run_config.turn_limit,
    )
    output = require_strict_output(
        result.final_output,
        ValidationResult,
        agent_name=selected_agent.name,
    )
    return persist_validation_result(
        output,
        context.ledger,
        allow_issue_updates=True,
    )


run_validator = run_critic


__all__ = [
    "CRITIC_INSTRUCTIONS",
    "CRITIC_OBJECTIVE",
    "CriticPersistenceError",
    "candidate_completeness_validation",
    "validate_structured_metric_comparisons",
    "VALIDATOR_INSTRUCTIONS",
    "VALIDATOR_OBJECTIVE",
    "build_critic_agent",
    "build_validator_agent",
    "create_critic_agent",
    "create_validator_agent",
    "persist_validation_result",
    "run_critic",
    "run_validator",
]
