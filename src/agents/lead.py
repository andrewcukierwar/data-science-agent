"""Lead Data Scientist manager agent and its bounded state tools."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agents import (
    Agent,
    RunContextWrapper,
    ToolOutputText,
    function_tool,
)
from agents.audit_evidence import (
    AuditEvidenceCatalog,
    build_audit_evidence_catalog,
    persist_audit_result,
)
from agents.correction import run_bounded_evidence_correction
from agents.evidence import (
    executed_references,
    finding_reference_aliases,
    has_source_lineage,
    material_claims,
    resolve_citations,
    resolve_material_claims,
    unsupported_claim_ids,
)
from agents.hypothesis_state import (
    HypothesisEvidenceError,
    validate_hypothesis_transition,
)
from agents.model_usage import ModelUsageHooks, run_agent_with_usage
from agents.output_contract import (
    STRUCTURED_DIMENSION_GUIDANCE,
    AgentOutputContractError,
    require_strict_output,
    strict_output_type,
)
from agents.runtime import (
    DEFAULT_AGENT_TURN_LIMITS,
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    PermissionDeniedError,
    ToolResponse,
)
from agents.tools import tools_for_role
from orchestration.ledger import AnalysisLedger
from schemas.audit import AuditResult
from schemas.findings import Finding, SpecialistResult, canonicalize_specialist_result
from schemas.lead import LeadResult, SpecialistTask
from schemas.metrics import (
    MetricComparison,
    compile_metric_comparisons,
    deduplicate_metric_comparisons,
    metric_comparison_identity,
    metric_comparison_scope_identity,
    metric_definition_contexts_compatible,
    normalize_metric_comparison,
    normalize_metric_key,
)
from schemas.run_state import Hypothesis, hypothesis_requires_evidence

LEAD_OBJECTIVE = (
    "Own the analytical objective, coordinate bounded specialist investigations, "
    "and construct an evidence-backed candidate answer."
)

LEAD_INSTRUCTIONS = f"""You are the Lead Data Scientist and manager of an
evidence-backed business analysis run.

You own the user's analytical objective and the candidate answer. Keep control of the
investigation: create or update an explicit investigation plan, formulate testable
hypotheses, delegate bounded objectives to specialist agents, integrate their typed
results, and decide whether a material follow-up analysis is worthwhile.

Architectural boundaries:

- You have no SQL or Python execution tool and must not perform raw computation,
  calculate results mentally, or pretend to have inspected data.
- No SQL. No Python. Delegate all computation to the approved specialists.
- Delegate data inspection, SQL, Python, and inferential work to the approved
  specialist-as-tools. Specialists cannot delegate to other agents.
- Use typed task fields (objective, scope, hypotheses, required outputs, and
  constraints) when delegating. Give each specialist a bounded question rather than
  an unscoped request.
- Do not use a handoff. Specialist calls return structured results to you and you
  remain responsible for synthesis.

Required investigation behavior:

1. Inspect the workspace and read relevant business definitions before committing to
   a metric or explanation.
2. Use update_investigation_plan and record_hypothesis to persist an explicit plan
   and hypothesis tree. Leave a hypothesis open while you are still testing it;
   an open hypothesis needs no evidence_refs and you must never invent one to
   satisfy the field. Set status to supported, rejected, or inconclusive only
   together with exact evidence_refs naming the executed evidence that decided
   it — a successful tool-event ID, a saved query/script path, or a verified
   artifact. This applies to every resolved hypothesis, including qualitative
   and data-quality ones resolved from the data audit: cite the audit claim's
   evidence_refs from DATA_AUDIT_EVIDENCE_CATALOG_JSON, never the audit itself.
   record_hypothesis refuses a resolution whose references do not resolve and
   returns the references that are available; keep the hypothesis open or cite
   one of those instead of retrying the same claim.
3. The application has already completed and persisted the mandatory Data Audit
   before you start. Use the supplied audit as the data-quality and relationship
   baseline; do not delegate another broad audit. The audit itself is internal
   run state, not an evidence reference. DATA_AUDIT_EVIDENCE_CATALOG_JSON lists
   every audit claim with the exact executed evidence references that support
   it; cite those `evidence_refs` verbatim whenever an audit observation
   supports a finding, hypothesis, recommendation, or caveat. A catalog
   `claim_id` names a claim and is not citable, and there is no reference named
   `completed_data_audit`, `data_audit`, or `audit`. Delegate bounded decomposition
   and metric work to Analyst, and inferential questions to Statistician. When a
   material cohort or channel difference affects the answer, ask Statistician to
   assess uncertainty and practical significance. When a multi-component
   decomposition would benefit from visual comparison, ask Analyst to save a
   relevant chart artifact. Treat specialist outputs as evidence-bearing
   structured results, not as unverified prose.
4. Record material open questions and decide explicitly whether each needs follow-up
   analysis. Do not pursue a follow-up merely because it is interesting; explain its
   decision value and available evidence. When the objective asks why a major
   driver changed, distinguish attribution from root cause and continue into
   available upstream drivers until the mechanism is supported or the data
   genuinely cannot discriminate. If acquisition efficiency or CAC materially
   deteriorates, decompose spend, traffic/sessions, conversion, acquired
   customers, CAC, and downstream LTV. When traffic, session, or funnel data
   exists, investigate traffic volume and conversion before concluding why
   customer acquisition changed. Examine downstream customer value as an
   alternative explanation for acquisition quality deterioration. Do not
   finalize while an open question is both necessary to answer the primary
   objective and answerable with available data/tools within the remaining
   budget. For profitability questions, make the final candidate address net
   revenue, COGS, contribution before marketing, marketing spend/acquisition
   efficiency, the largest relevant segment, and downstream customer value.
   State material non-drivers explicitly. When acquisition economics explain a
   material change, connect the observed path from spend to sessions/traffic,
   conversion, acquired customers, CAC, and downstream LTV/value. Separate that
   observed mechanism from unsupported claims about why an upstream metric
   changed.
5. Construct concise candidate findings and recommendations. Every quantitative
   finding and every recommendation must include evidence_refs pointing to an
   executed tool event, artifact, or saved query/script path. Distinguish observed
   associations from unsupported causal claims, preserve caveats, and never invent
   evidence or scenario ground truth. For comparable period changes, use a stable
   metric identifier, set value_unit to relative_change_fraction, and report the
   relative change as a decimal fraction (0.10 means +10%). Important quantitative
   period or segment comparisons supporting the answer should also be represented
   in LeadResult.metric_comparisons with generic metric keys, dimensions, periods,
   comparison type, unit, value, exact evidence_refs, and definition_context when
   population, date basis, observation window, numerator, denominator, or a
   definition reference distinguishes the estimand. Do not use evaluator,
   scenario, or prompt-specific identifiers for those metric keys.
   State material non-drivers and data-quality limitations explicitly so the
   answer distinguishes the supported mechanism from plausible alternatives.
   Specialist finding IDs such as analyst:F1 or statistician:H1 are intermediate
   labels, not executed evidence references. Copy the exact evidence_refs from the
   specialist finding when citing its result; do not use a local finding ID as a
   substitute for its query, script, artifact, or tool-event reference.
6. Return only a valid LeadResult. The answer is a candidate answer for the later
   validation/orchestration layer; do not implement or simulate the Critic feedback
   loop here. {STRUCTURED_DIMENSION_GUIDANCE}

The Lead may save approved analysis artifacts, but must keep outputs reproducible and
within the workspace permissions. Keep model-visible tool results concise.
"""


def _sdk_response(response: ToolResponse) -> ToolOutputText:
    """Encode a typed state-tool result for the model channel."""

    return ToolOutputText(text=response.model_dump_json())


def _context(wrapper: RunContextWrapper[AgentRunContext]) -> AgentRunContext:
    context = wrapper.context
    context.bind_tool_agent(getattr(wrapper, "agent", None))
    return context


@function_tool
def update_investigation_plan(
    ctx: RunContextWrapper[AgentRunContext],
    steps: list[str],
) -> ToolOutputText:
    """Persist the current bounded investigation plan for the run."""

    tool_name = "update_investigation_plan"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        plan = context.ledger.update_investigation_plan(steps)
        return _sdk_response(ToolResponse.ok(tool_name, {"steps": plan}))
    except (PermissionDeniedError, ValueError, OSError) as error:
        return _sdk_response(ToolResponse.failed(tool_name, "state_error", str(error)))


@function_tool
def record_hypothesis(
    ctx: RunContextWrapper[AgentRunContext],
    hypothesis: Hypothesis,
) -> ToolOutputText:
    """Create or update one explicit hypothesis in the run ledger.

    A hypothesis may stay open with no evidence. Resolving one to supported,
    rejected, or inconclusive requires exact executed evidence references, and
    an unsupported resolution is refused here rather than persisted and failed
    at the end of the run.
    """

    tool_name = "record_hypothesis"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        # Validate before the ledger is touched, so a refused transition leaves
        # the current hypothesis and the append-only history unchanged and a
        # resumed run cannot inherit it.
        validated = validate_hypothesis_transition(hypothesis, context.ledger)
        saved = context.ledger.upsert_hypothesis(validated)
        return _sdk_response(ToolResponse.ok(tool_name, saved.model_dump(mode="json")))
    except HypothesisEvidenceError as error:
        return _sdk_response(
            ToolResponse.failed(
                tool_name,
                "invalid_hypothesis_transition",
                str(error),
                data=error.as_tool_data(),
            )
        )
    except (PermissionDeniedError, ValueError, OSError, KeyError) as error:
        return _sdk_response(ToolResponse.failed(tool_name, "state_error", str(error)))


@function_tool
def record_open_question(
    ctx: RunContextWrapper[AgentRunContext],
    question: str,
) -> ToolOutputText:
    """Persist a material unanswered question for Lead follow-up decisions."""

    tool_name = "record_open_question"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        saved = context.ledger.add_open_question(question)
        return _sdk_response(ToolResponse.ok(tool_name, {"question": saved}))
    except (PermissionDeniedError, ValueError, OSError) as error:
        return _sdk_response(ToolResponse.failed(tool_name, "state_error", str(error)))


class LeadEvidenceError(ValueError):
    """Raised when a Lead result cites evidence that was not executed.

    The response satisfied the strict output schema, so this is a semantic
    contract failure rather than a malformed model response. ``invalid_fields``
    names exactly which output fields failed, which is what a bounded
    correction attempt needs in order to be specific rather than a blind retry.
    """

    def __init__(self, message: str, invalid_fields: tuple[str, ...] = ()) -> None:
        self.invalid_fields = invalid_fields
        super().__init__(message)


class _NestedSpecialistHooks(ModelUsageHooks):
    """Apply specialist permissions and budgets to an agent-as-tool run.

    Extending ``ModelUsageHooks`` keeps a nested specialist response accounted
    for at its own boundary. A nested run shares the parent run's usage
    accumulator, so the Lead's end-of-run reconciliation still covers any
    specialist response whose hook did not fire.
    """

    def __init__(self, role: AgentRole) -> None:
        self.role = role

    async def on_agent_start(self, context: Any, agent: Agent[Any]) -> None:
        runtime_context = context.context
        runtime_context.record_specialist_invocation()
        runtime_context.enter_nested_role(self.role)

    async def on_agent_end(
        self,
        context: Any,
        agent: Agent[Any],
        output: Any,
    ) -> None:
        runtime_context = context.context
        try:
            if self.role is AgentRole.DATA_AUDITOR:
                audit = (
                    output
                    if isinstance(output, AuditResult)
                    else AuditResult.model_validate(output)
                )
                persist_audit_result(audit, runtime_context)
            elif self.role in {AgentRole.ANALYST, AgentRole.STATISTICIAN}:
                try:
                    specialist_result = (
                        output
                        if isinstance(output, SpecialistResult)
                        else SpecialistResult.model_validate(output)
                    )
                except ValidationError:
                    # The SDK supplies typed output in production. Keep the
                    # hook's cleanup and trace behavior robust for test doubles
                    # or SDK failures that do not produce a specialist result.
                    specialist_result = None
                if specialist_result is not None:
                    if self.role is AgentRole.ANALYST:
                        from agents.analyst import persist_analyst_result

                        specialist_result = persist_analyst_result(
                            specialist_result,
                            runtime_context,
                        )
                    else:
                        from agents.statistician import persist_statistician_result

                        specialist_result = persist_statistician_result(
                            specialist_result,
                            runtime_context,
                        )
                    runtime_context.ledger.record_specialist_result(
                        self.role.value,
                        specialist_result,
                    )
            runtime_context.ledger.record_agent_event(
                agent_name=agent.name,
                agent_role=self.role.value,
                status="succeeded",
                model=str(agent.model) if agent.model is not None else None,
                objective=_nested_objective(context),
                output_type=type(output).__name__,
            )
        finally:
            runtime_context.exit_nested_role(self.role)
            runtime_context.assert_base_role(AgentRole.LEAD)


def _nested_objective(context: Any) -> str | None:
    """Extract only the bounded objective from a typed specialist tool input."""

    tool_input = getattr(context, "tool_input", None)
    if isinstance(tool_input, dict):
        objective = tool_input.get("objective")
        return objective if isinstance(objective, str) and objective.strip() else None
    return None


def _specialist_tool(
    specialist: Agent[AgentRunContext],
    *,
    role: AgentRole,
    tool_name: str,
    description: str,
    max_turns: int | None = None,
) -> Any:
    """Expose a specialist through the SDK's manager-agent tool mechanism."""

    if specialist.handoffs:
        raise ValueError("specialists exposed to Lead cannot have handoffs")
    hooks = _NestedSpecialistHooks(role)

    async def _failure_error(
        context: RunContextWrapper[AgentRunContext],
        error: Exception,
    ) -> str:
        """Restore the parent role if a nested run fails before its end hook."""

        runtime_context = context.context
        if runtime_context.agent_role is role:
            runtime_context.exit_nested_role(role)
        runtime_context.assert_base_role(AgentRole.LEAD)
        runtime_context.ledger.record_agent_event(
            agent_name=specialist.name,
            agent_role=role.value,
            status="failed",
            model=str(specialist.model) if specialist.model is not None else None,
            objective=_nested_objective(context),
            error=str(error),
        )
        return f"{role.value} specialist failed: {error}"

    return specialist.as_tool(
        tool_name=tool_name,
        tool_description=description,
        custom_output_extractor=(
            _canonical_specialist_output(role, specialist.name)
            if role in {AgentRole.ANALYST, AgentRole.STATISTICIAN}
            else None
        ),
        max_turns=max_turns,
        parameters=SpecialistTask,
        include_input_schema=True,
        hooks=hooks,
        failure_error_function=_failure_error,
    )


def _canonical_specialist_output(role: AgentRole, agent_name: str) -> Any:
    """Build an extractor that returns the persisted namespaced result."""

    async def _extract(run_result: Any) -> str:
        output = getattr(run_result, "final_output", None)
        # The specialist declares a strict typed output, so a response that is
        # not already that type is a model/schema failure. Returning its raw
        # text would hand the Lead an unvalidated result and hide the failure.
        try:
            result = require_strict_output(
                output,
                SpecialistResult,
                agent_name=agent_name,
            )
        except AgentOutputContractError:
            if not isinstance(output, str):
                raise
            try:
                result = SpecialistResult.model_validate_json(output)
            except ValidationError as error:
                raise AgentOutputContractError(
                    agent_name,
                    SpecialistResult,
                    output,
                ) from error

        # The nested hook persists the role-specific form. The output
        # extractor repeats the idempotent transformation so the Lead sees the
        # same canonical IDs that were written to the ledger.
        return canonicalize_specialist_result(
            result,
            role.value,
        ).model_dump_json()

    return _extract


def build_lead_agent(
    config: AgentRunConfig | None = None,
    *,
    model: str | None = None,
    instructions: str | None = None,
    analyst: Agent[AgentRunContext] | None = None,
    statistician: Agent[AgentRunContext] | None = None,
) -> Agent[AgentRunContext]:
    """Build the Lead manager with specialists exposed as tools, never handoffs."""

    if config is not None and config.agent_role is not AgentRole.LEAD:
        raise ValueError("Lead requires an AgentRunConfig with lead role")
    selected_model = model or (config.model if config is not None else None)

    def specialist_turns(role: AgentRole) -> int:
        if config is not None:
            return config.turn_limit_for(role)
        return DEFAULT_AGENT_TURN_LIMITS[role]

    if analyst is None:
        from agents.analyst import build_analyst_agent

        analyst = build_analyst_agent(model=selected_model)
    if statistician is None:
        from agents.statistician import build_statistician_agent

        statistician = build_statistician_agent(model=selected_model)

    state_tools = [
        update_investigation_plan,
        record_hypothesis,
        record_open_question,
    ]
    specialist_tools = [
        _specialist_tool(
            analyst,
            role=AgentRole.ANALYST,
            tool_name="delegate_to_analyst",
            description=(
                "Delegate bounded SQL/Python business analytics and decomposition."
            ),
            max_turns=specialist_turns(AgentRole.ANALYST),
        ),
        _specialist_tool(
            statistician,
            role=AgentRole.STATISTICIAN,
            tool_name="delegate_to_statistician",
            description="Delegate a bounded inferential or statistical question.",
            max_turns=specialist_turns(AgentRole.STATISTICIAN),
        ),
    ]
    return Agent[AgentRunContext](
        name="Lead Data Scientist",
        instructions=instructions or LEAD_INSTRUCTIONS,
        model=selected_model,
        tools=[*tools_for_role(AgentRole.LEAD), *state_tools, *specialist_tools],
        handoffs=[],
        output_type=strict_output_type(LeadResult),
    )


create_lead_agent = build_lead_agent


def _reuse_specialist_metric_comparisons(
    comparisons: list[MetricComparison],
    ledger: AnalysisLedger,
    findings: list[Finding] | None = None,
) -> list[MetricComparison]:
    """Reuse exact specialist comparisons selected by Lead evidence."""

    specialist_index: dict[tuple[object, ...], MetricComparison] = {}
    specialist_scope_index: dict[tuple[object, ...], list[MetricComparison]] = {}
    for record in ledger.specialist_results:
        for comparison in record.result.metric_comparisons:
            comparison = normalize_metric_comparison(comparison)
            specialist_index[metric_comparison_identity(comparison)] = comparison
            specialist_scope_index.setdefault(
                metric_comparison_scope_identity(comparison), []
            ).append(comparison)
    reused: list[MetricComparison] = []
    for comparison in comparisons:
        normalized = normalize_metric_comparison(comparison)
        exact = specialist_index.get(metric_comparison_identity(normalized))
        if exact is not None:
            reused.append(exact)
            continue
        scoped = specialist_scope_index.get(
            metric_comparison_scope_identity(normalized), []
        )
        reused.append(scoped[0] if len(scoped) == 1 else normalized)
    for finding in findings or []:
        if finding.metric is None:
            continue
        for record in ledger.specialist_results:
            for comparison in record.result.metric_comparisons:
                comparison = normalize_metric_comparison(comparison)
                if normalize_metric_key(
                    finding.metric, comparison.dimensions
                ) == comparison.metric_key and set(finding.evidence_refs).intersection(
                    comparison.evidence_refs
                ):
                    reused.append(comparison)
    return deduplicate_metric_comparisons(reused)


def _preserve_metric_definitions(
    comparisons: list[MetricComparison],
    prior_result: LeadResult,
) -> list[MetricComparison]:
    """Keep prior estimands stable across a Lead remediation replacement."""

    current = [normalize_metric_comparison(item) for item in comparisons]
    prior = [
        normalize_metric_comparison(item) for item in prior_result.metric_comparisons
    ]
    consumed: set[int] = set()
    merged: list[MetricComparison] = []
    for previous in prior:
        exact_matches = [
            (index, item)
            for index, item in enumerate(current)
            if index not in consumed
            and metric_comparison_identity(item) == metric_comparison_identity(previous)
        ]
        if exact_matches:
            previous_repeated = any(
                item.value == previous.value for _, item in exact_matches
            )
            corrected = [
                (index, item)
                for index, item in exact_matches
                if item.value != previous.value
            ]
            if previous_repeated and corrected:
                # A replacement LeadResult commonly carries the stale comparison
                # forward and appends its correction. The latest correction is
                # authoritative for this remediation cycle; consume both copies.
                consumed.update(index for index, _ in exact_matches)
                merged.append(corrected[-1][1])
            else:
                consumed.update(index for index, _ in exact_matches)
                merged.extend(item for _, item in exact_matches)
            continue

        same_metric = [
            (index, item)
            for index, item in enumerate(current)
            if index not in consumed
            and metric_comparison_scope_identity(item)
            == metric_comparison_scope_identity(previous)
        ]
        if same_metric:
            compatible = [
                (index, item)
                for index, item in same_metric
                if metric_definition_contexts_compatible(
                    previous.definition_context,
                    item.definition_context,
                )
            ]
            incompatible = [
                (index, item)
                for index, item in same_metric
                if (index, item) not in compatible
            ]
            for index, item in compatible:
                consumed.add(index)
                merged.append(
                    item.model_copy(
                        update={
                            "definition_context": (
                                item.definition_context or previous.definition_context
                            )
                        }
                    )
                )
            if not compatible:
                merged.append(previous)
            for index, item in incompatible:
                consumed.add(index)
                merged.append(item)
            continue
        merged.append(previous)

    merged.extend(item for index, item in enumerate(current) if index not in consumed)
    return deduplicate_metric_comparisons(merged)


def validate_lead_result(
    result: LeadResult,
    ledger: AnalysisLedger,
    *,
    prior_result: LeadResult | None = None,
    allow_definition_change: bool = False,
) -> LeadResult:
    """Require every Lead citation to resolve to exact executed evidence.

    Canonicalization is lossless: a citation that resolves is replaced by the
    exact executed reference it stands for, and one that does not is preserved
    so the failure stays visible. A claim is supported only when every one of
    its citations resolves — the same rule the Critic and the offline evaluator
    apply to the same persisted workspace.
    """

    executed_refs = executed_references(ledger)
    aliases = finding_reference_aliases(ledger)

    def canonical(references: list[str]) -> list[str]:
        return list(
            resolve_citations(
                references,
                executed_refs=executed_refs,
                aliases=aliases,
            ).canonical_references
        )

    canonical_findings = [
        finding.model_copy(update={"evidence_refs": canonical(finding.evidence_refs)})
        for finding in result.findings
    ]
    canonical_recommendations = [
        recommendation.model_copy(
            update={"evidence_refs": canonical(recommendation.evidence_refs)}
        )
        for recommendation in result.recommendations
    ]
    # An open hypothesis keeps its citations verbatim: it is still being tested,
    # and rewriting a reference it has not yet earned would change its meaning.
    canonical_hypotheses = [
        hypothesis.model_copy(
            update={"evidence_refs": canonical(hypothesis.evidence_refs)}
        )
        if hypothesis_requires_evidence(hypothesis.status)
        else hypothesis
        for hypothesis in result.hypotheses
    ]
    canonical_metric_comparisons = [
        comparison.model_copy(
            update={"evidence_refs": canonical(comparison.evidence_refs)}
        )
        for comparison in result.metric_comparisons
    ]
    canonical_statistical_assessments = [
        assessment.model_copy(
            update={"evidence_refs": canonical(assessment.evidence_refs)}
        )
        for assessment in result.statistical_assessments
    ]
    canonical_metric_comparisons = _reuse_specialist_metric_comparisons(
        canonical_metric_comparisons,
        ledger,
        canonical_findings,
    )
    if prior_result is not None and not allow_definition_change:
        canonical_metric_comparisons = _preserve_metric_definitions(
            canonical_metric_comparisons,
            prior_result,
        )
    compilation = compile_metric_comparisons(canonical_metric_comparisons)
    result = result.model_copy(
        update={
            "findings": canonical_findings,
            "recommendations": canonical_recommendations,
            "hypotheses": canonical_hypotheses,
            "metric_comparisons": compilation.comparisons,
            "metric_conflicts": compilation.conflicts,
            "statistical_assessments": canonical_statistical_assessments,
        }
    )
    unsupported = unsupported_claim_ids(
        resolve_material_claims(
            material_claims(
                findings=result.findings,
                recommendations=result.recommendations,
                hypotheses=result.hypotheses,
                metric_comparisons=result.metric_comparisons,
                statistical_assessments=result.statistical_assessments,
            ),
            ledger,
        )
    )
    invalid_lineage = [
        finding.id
        for finding in result.findings
        if (finding.metric is not None or finding.value is not None)
        and not has_source_lineage(ledger, finding.evidence_refs)
    ]
    invalid_lineage.extend(
        f"metric_comparison:{comparison.metric_key}"
        for comparison in result.metric_comparisons
        if not has_source_lineage(ledger, comparison.evidence_refs)
    )
    invalid_lineage.extend(
        f"statistical_assessment:{assessment.metric_key}"
        for assessment in result.statistical_assessments
        if not has_source_lineage(ledger, assessment.evidence_refs)
    )
    details = [*unsupported, *[f"source_lineage:{item}" for item in invalid_lineage]]
    if details:
        raise LeadEvidenceError(
            "lead outputs cite no executed evidence: " + ", ".join(details),
            tuple(details),
        )
    return result


def _persist_result(
    result: LeadResult,
    context: AgentRunContext,
    *,
    prior_result: LeadResult | None = None,
    allow_definition_change: bool = False,
) -> LeadResult:
    """Persist observable Lead conclusions without starting the later critic loop."""

    result = validate_lead_result(
        result,
        context.ledger,
        prior_result=prior_result,
        allow_definition_change=allow_definition_change,
    )
    for hypothesis in result.hypotheses:
        context.ledger.upsert_hypothesis(hypothesis)
    for question in result.open_questions:
        context.ledger.add_open_question(question)
    for finding in result.findings:
        context.ledger.upsert_finding(finding)
    context.ledger.replace_metric_comparisons(result.metric_comparisons)
    context.ledger.replace_statistical_assessments(result.statistical_assessments)
    return result


def persist_lead_result(
    result: LeadResult,
    context: AgentRunContext,
    *,
    prior_result: LeadResult | None = None,
    allow_definition_change: bool = False,
) -> LeadResult:
    """Persist a Lead result for callers that manage the SDK lifecycle."""

    return _persist_result(
        result,
        context,
        prior_result=prior_result,
        allow_definition_change=allow_definition_change,
    )


def _lead_input(
    objective: str,
    *,
    business_context: str | None = None,
    audit: AuditResult | None = None,
    audit_evidence: AuditEvidenceCatalog | None = None,
) -> str:
    """Build the bounded application context supplied to the Lead.

    The Lead has no SQL, Python, or internal-state access, so the audit's
    provenance has to cross the architecture boundary with it. The typed
    catalog carries exactly that: each audit claim and the canonical executed
    references that establish it, and nothing else from the run's internals.
    """

    if business_context is None and audit is None:
        return objective
    sections = [f"OBJECTIVE:\n{objective}"]
    if business_context:
        sections.append(f"BUSINESS_CONTEXT:\n{business_context}")
    if audit is not None:
        sections.append("DATA_AUDIT_RESULT_JSON:\n" + audit.model_dump_json(indent=2))
    if audit_evidence is not None:
        sections.append(
            "DATA_AUDIT_EVIDENCE_CATALOG_JSON:\n"
            + audit_evidence.model_dump_json(indent=2)
        )
        sections.append(
            "The data audit is internal run state, not an evidence reference. "
            "Cite an audit claim only through the exact evidence_refs listed for "
            "it in DATA_AUDIT_EVIDENCE_CATALOG_JSON. A claim_id is a label, not "
            "a reference, and no reference named completed_data_audit exists."
        )
    sections.append(
        "Use this context as evidence and create/update the persisted plan and "
        "hypotheses before constructing the candidate analysis."
    )
    return "\n\n".join(sections)


async def run_lead(
    context: AgentRunContext,
    objective: str,
    *,
    business_context: str | None = None,
    audit: AuditResult | None = None,
    agent: Agent[AgentRunContext] | None = None,
) -> LeadResult:
    """Run the Lead manager once and persist its candidate conclusions."""

    if context.agent_role is not AgentRole.LEAD:
        raise ValueError("run_lead requires a Lead AgentRunContext")
    selected_agent = agent or build_lead_agent(context.run_config)
    audit_evidence = (
        build_audit_evidence_catalog(audit, context.ledger)
        if audit is not None
        else None
    )
    result = await run_agent_with_usage(
        selected_agent,
        _lead_input(
            objective,
            business_context=business_context,
            audit=audit,
            audit_evidence=audit_evidence,
        ),
        context=context,
        max_turns=context.run_config.turn_limit,
    )
    output = require_strict_output(
        result.final_output,
        LeadResult,
        agent_name=selected_agent.name,
    )
    try:
        return _persist_result(output, context)
    except LeadEvidenceError as error:
        # Strict output succeeded and only the citations are wrong, so this is a
        # semantic contract failure the model can repair from evidence that
        # already exists. Exactly one bounded attempt, with no new execution.
        return await run_bounded_evidence_correction(
            context,
            output,
            error,
            output_type=LeadResult,
            persist=lambda corrected: _persist_result(corrected, context),
            agent_name=selected_agent.name,
            model=str(selected_agent.model)
            if selected_agent.model is not None
            else None,
            audit_evidence=audit_evidence,
        )


__all__ = [
    "LEAD_INSTRUCTIONS",
    "LEAD_OBJECTIVE",
    "LeadEvidenceError",
    "build_lead_agent",
    "create_lead_agent",
    "persist_lead_result",
    "record_hypothesis",
    "record_open_question",
    "run_lead",
    "update_investigation_plan",
    "validate_lead_result",
]
