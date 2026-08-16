"""Lead Data Scientist manager agent and its bounded state tools."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agents import (
    Agent,
    RunContextWrapper,
    RunHooks,
    Runner,
    ToolOutputText,
    function_tool,
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
from schemas.run_state import Hypothesis

LEAD_OBJECTIVE = (
    "Own the analytical objective, coordinate bounded specialist investigations, "
    "and construct an evidence-backed candidate answer."
)

LEAD_INSTRUCTIONS = """You are the Lead Data Scientist and manager of an evidence-backed
business analysis run.

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
   and hypothesis tree. Update hypothesis status to supported, rejected, or
   inconclusive only when the returned evidence supports that disposition.
3. Delegate an audit when data quality or relationships are uncertain. Delegate
   bounded decomposition and metric work to Analyst, and inferential questions to
   Statistician. When a material cohort or channel difference affects the answer,
   ask Statistician to assess uncertainty and practical significance. When a
   multi-component decomposition would benefit from visual comparison, ask
   Analyst to save a relevant chart artifact. Treat specialist outputs as
   evidence-bearing structured results, not as unverified prose.
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
   budget.
5. Construct concise candidate findings and recommendations. Every quantitative
   finding and every recommendation must include evidence_refs pointing to an
   executed tool event, artifact, or saved query/script path. Distinguish observed
   associations from unsupported causal claims, preserve caveats, and never invent
   evidence or scenario ground truth. For comparable period changes, use a stable
   metric identifier, set value_unit to relative_change_fraction, and report the
   relative change as a decimal fraction (0.10 means +10%).
   Specialist finding IDs such as analyst:F1 or statistician:H1 are intermediate
   labels, not executed evidence references. Copy the exact evidence_refs from the
   specialist finding when citing its result; do not use a local finding ID as a
   substitute for its query, script, artifact, or tool-event reference.
6. Return only a valid LeadResult. The answer is a candidate answer for the later
   validation/orchestration layer; do not implement or simulate the Critic feedback
   loop here.

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
    """Create or update one explicit hypothesis in the run ledger."""

    tool_name = "record_hypothesis"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        saved = context.ledger.upsert_hypothesis(hypothesis)
        return _sdk_response(ToolResponse.ok(tool_name, saved.model_dump(mode="json")))
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
    """Raised when a Lead result cites evidence that was not executed."""


class _NestedSpecialistHooks(RunHooks[AgentRunContext]):
    """Apply specialist permissions and budgets to an agent-as-tool run."""

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
                runtime_context.ledger.record_audit(audit)
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
            _canonical_specialist_output(role)
            if role in {AgentRole.ANALYST, AgentRole.STATISTICIAN}
            else None
        ),
        max_turns=max_turns,
        parameters=SpecialistTask,
        include_input_schema=True,
        hooks=hooks,
        failure_error_function=_failure_error,
    )


def _canonical_specialist_output(role: AgentRole) -> Any:
    """Build an extractor that returns the persisted namespaced result."""

    async def _extract(run_result: Any) -> str:
        output = getattr(run_result, "final_output", None)
        try:
            result = (
                output
                if isinstance(output, SpecialistResult)
                else SpecialistResult.model_validate_json(output)
                if isinstance(output, str)
                else SpecialistResult.model_validate(output)
            )
        except (TypeError, ValidationError):
            return str(output)

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
    data_auditor: Agent[AgentRunContext] | None = None,
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
    if data_auditor is None:
        from agents.auditor import build_data_auditor_agent

        data_auditor = build_data_auditor_agent(model=selected_model)

    state_tools = [
        update_investigation_plan,
        record_hypothesis,
        record_open_question,
    ]
    specialist_tools = [
        _specialist_tool(
            data_auditor,
            role=AgentRole.DATA_AUDITOR,
            tool_name="delegate_to_data_auditor",
            description="Delegate a bounded data-quality or relationship audit.",
            max_turns=specialist_turns(AgentRole.DATA_AUDITOR),
        ),
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
        output_type=LeadResult,
    )


create_lead_agent = build_lead_agent


def _executed_references(ledger: AnalysisLedger) -> set[str]:
    references = {event.id for event in ledger.tool_events}
    for event in ledger.tool_events:
        references.update(event.artifact_refs)
        references.update(
            value
            for key in (
                "query_id",
                "query_path",
                "script_id",
                "script_path",
            )
            if isinstance(value := event.arguments.get(key), str)
        )
        if event.output is not None:
            generated_refs = event.output.get("generated_evidence_refs", [])
            if isinstance(generated_refs, list):
                references.update(
                    value for value in generated_refs if isinstance(value, str)
                )
    references.update(artifact.id for artifact in ledger.artifacts)
    references.update(artifact.path for artifact in ledger.artifacts)
    return references


def _finding_reference_aliases(ledger: AnalysisLedger) -> dict[str, list[Finding]]:
    """Index persisted specialist findings by canonical and unique local IDs."""

    aliases: dict[str, list[Finding]] = {}
    for finding in ledger.findings:
        aliases.setdefault(finding.id, []).append(finding)
        if ":" in finding.id:
            local_id = finding.id.rsplit(":", maxsplit=1)[1]
            aliases.setdefault(local_id, []).append(finding)
    return aliases


def _resolve_evidence_reference(
    reference: str,
    *,
    executed_refs: set[str],
    aliases: dict[str, list[Finding]],
    resolving: set[str] | None = None,
) -> list[str]:
    """Resolve a direct or uniquely aliased specialist finding reference."""

    if reference in executed_refs:
        return [reference]
    candidates = aliases.get(reference, [])
    if len(candidates) != 1:
        return []
    resolving = set() if resolving is None else resolving
    if reference in resolving:
        return []
    resolving.add(reference)
    resolved: list[str] = []
    for nested_reference in candidates[0].evidence_refs:
        resolved.extend(
            _resolve_evidence_reference(
                nested_reference,
                executed_refs=executed_refs,
                aliases=aliases,
                resolving=resolving,
            )
        )
    return list(dict.fromkeys(resolved))


def _canonicalize_evidence_refs(
    references: list[str],
    *,
    executed_refs: set[str],
    aliases: dict[str, list[Finding]],
) -> list[str]:
    """Replace intermediate finding IDs with exact executed evidence refs."""

    resolved: list[str] = []
    for reference in references:
        resolved.extend(
            _resolve_evidence_reference(
                reference,
                executed_refs=executed_refs,
                aliases=aliases,
            )
        )
    return list(dict.fromkeys(resolved))


def validate_lead_result(
    result: LeadResult,
    ledger: AnalysisLedger,
) -> LeadResult:
    """Require Lead outputs to cite exact executed evidence references."""

    executed_refs = _executed_references(ledger)
    aliases = _finding_reference_aliases(ledger)
    canonical_findings = [
        finding.model_copy(
            update={
                "evidence_refs": _canonicalize_evidence_refs(
                    finding.evidence_refs,
                    executed_refs=executed_refs,
                    aliases=aliases,
                )
                or finding.evidence_refs
            }
        )
        for finding in result.findings
    ]
    canonical_recommendations = [
        recommendation.model_copy(
            update={
                "evidence_refs": _canonicalize_evidence_refs(
                    recommendation.evidence_refs,
                    executed_refs=executed_refs,
                    aliases=aliases,
                )
                or recommendation.evidence_refs
            }
        )
        for recommendation in result.recommendations
    ]
    canonical_hypotheses = [
        hypothesis.model_copy(
            update={
                "evidence_refs": _canonicalize_evidence_refs(
                    hypothesis.evidence_refs,
                    executed_refs=executed_refs,
                    aliases=aliases,
                )
                or hypothesis.evidence_refs
            }
        )
        for hypothesis in result.hypotheses
    ]
    result = result.model_copy(
        update={
            "findings": canonical_findings,
            "recommendations": canonical_recommendations,
            "hypotheses": canonical_hypotheses,
        }
    )
    invalid_findings = [
        finding.id
        for finding in result.findings
        if (finding.metric is not None or finding.value is not None)
        and not any(reference in executed_refs for reference in finding.evidence_refs)
    ]
    invalid_recommendations = [
        recommendation.id
        for recommendation in result.recommendations
        if not any(
            reference in executed_refs for reference in recommendation.evidence_refs
        )
    ]
    invalid_hypotheses = [
        hypothesis.id
        for hypothesis in result.hypotheses
        if hypothesis.status.value != "open"
        and not any(
            reference in executed_refs for reference in hypothesis.evidence_refs
        )
    ]
    if invalid_findings or invalid_recommendations or invalid_hypotheses:
        details = [
            *[f"finding:{item}" for item in invalid_findings],
            *[f"recommendation:{item}" for item in invalid_recommendations],
            *[f"hypothesis:{item}" for item in invalid_hypotheses],
        ]
        raise LeadEvidenceError(
            "lead outputs cite no executed evidence: " + ", ".join(details)
        )
    return result


def _persist_result(result: LeadResult, context: AgentRunContext) -> LeadResult:
    """Persist observable Lead conclusions without starting the later critic loop."""

    result = validate_lead_result(result, context.ledger)
    for hypothesis in result.hypotheses:
        context.ledger.upsert_hypothesis(hypothesis)
    for question in result.open_questions:
        context.ledger.add_open_question(question)
    for finding in result.findings:
        context.ledger.upsert_finding(finding)
    return result


def persist_lead_result(result: LeadResult, context: AgentRunContext) -> LeadResult:
    """Persist a Lead result for callers that manage the SDK lifecycle."""

    return _persist_result(result, context)


def _lead_input(
    objective: str,
    *,
    business_context: str | None = None,
    audit: AuditResult | None = None,
) -> str:
    """Build the bounded application context supplied to the Lead."""

    if business_context is None and audit is None:
        return objective
    sections = [f"OBJECTIVE:\n{objective}"]
    if business_context:
        sections.append(f"BUSINESS_CONTEXT:\n{business_context}")
    if audit is not None:
        sections.append(
            "COMPLETED_DATA_AUDIT_JSON:\n" + audit.model_dump_json(indent=2)
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
    result = await Runner.run(
        selected_agent,
        _lead_input(
            objective,
            business_context=business_context,
            audit=audit,
        ),
        context=context,
        max_turns=context.run_config.turn_limit,
    )
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    context.record_sdk_usage(usage)
    output = result.final_output
    if not isinstance(output, LeadResult):
        output = LeadResult.model_validate(output)
    return _persist_result(output, context)


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
