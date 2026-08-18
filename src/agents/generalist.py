"""Fair single-agent analysis baseline.

The generalist deliberately has one SDK Agent and one bounded primitive-tool
surface.  It does not construct specialist agents, expose agent-as-tool
wrappers, or register handoffs.  The application runner persists its typed
audit, candidate, and self-critique through the same contracts used by the
five-agent architecture.
"""

from __future__ import annotations

from agents import Agent, AgentOutputSchema, Runner
from agents.critic import persist_validation_result
from agents.lead import (
    persist_lead_result,
    record_hypothesis,
    record_open_question,
    update_investigation_plan,
)
from agents.runtime import AgentRole, AgentRunConfig, AgentRunContext
from agents.tools import tools_for_role
from schemas.generalist import GeneralistResult

GENERALIST_OBJECTIVE = (
    "Complete the full evidence-backed business analysis as one bounded generalist "
    "agent, from data audit through validated synthesis."
)

GENERALIST_INSTRUCTIONS = """You are the Generalist Data Scientist running a
fair single-agent analysis baseline.

You alone own the complete lifecycle: audit the data and definitions, perform SQL
and Python analysis, run basic statistical checks when relevant, challenge your
own claims, and synthesize the final answer. Do not invoke, delegate to, hand off
to, or ask another agent to perform any work. In particular, never call a Lead,
Data Auditor, Analyst, Statistician, or Critic agent; you are the only agent in
this architecture.

Use only the bounded workspace, document, DuckDB, Python, artifact, evidence, and
observable investigation-state tools supplied to you. Respect every tool result,
budget limit, row/text limit, and sandbox boundary. Inputs are read-only. Persist
an explicit investigation plan and hypotheses when they help explain the work.

Required behavior:

1. Inspect the workspace and read the relevant business definitions before
   choosing populations, dates, denominators, metrics, or causal language.
2. Audit table coverage, keys, joins, missingness, date coverage, and data-quality
   limitations. Do not treat hidden scenario ground truth or evaluator rules as
   evidence; they are not available to you.
3. Use SQL/Python for every material number. Preserve exact evidence_refs to
   executed tool events, query/script paths, or registered artifacts. Save useful
   charts or reproducible scripts when appropriate.
4. For every important comparison, return a generic MetricComparison with the
   correct population, date basis, observation window, numerator, denominator,
   unit, and exact evidence_refs. Use relative_change_fraction for comparable
   period changes and report 0.10 for +10%.
   For experiment questions, also return one typed StatisticalAssessment per
   required estimand in candidate.statistical_assessments, including the method,
   uncertainty, effect size, assumptions, causal interpretation, and exact
   evidence_refs.
5. Distinguish observed association from causal proof. State plausible
   non-drivers, unsupported-claim limits, assumptions, confidence intervals or
   effect sizes, and practical significance when the question is experimental.
6. Before finalizing, perform a self-critique against the evidence, metric
   definitions, task completeness, provenance, and unsupported claims. Return
   REVISE when a material issue remains and explain it in the typed validation.
7. Return only one valid GeneralistResult containing the completed AuditResult,
   candidate LeadResult, and ValidationResult. Do not add fields or prose outside
   that schema. The candidate must have follow_up_analysis=false unless the
   validation explicitly explains why a material question is unanswerable.

The candidate answer is later rendered by the shared deterministic report
contract. Never invent evidence, numbers, scenario conclusions, or evaluator-only
fields.
"""


def build_generalist_agent(
    config: AgentRunConfig | None = None,
    *,
    model: str | None = None,
    instructions: str | None = None,
) -> Agent[AgentRunContext]:
    """Build the sole generalist agent with no specialist capability surface."""

    if config is not None and config.agent_role is not AgentRole.GENERALIST:
        raise ValueError("Generalist requires an AgentRunConfig with generalist role")
    selected_model = model or (config.model if config is not None else None)

    # State tools live with the original Lead implementation, but these are
    # plain ledger tools.  They are intentionally included without any of the
    # Lead's delegate_to_* wrappers or nested-agent hooks.
    state_tools = [
        update_investigation_plan,
        record_hypothesis,
        record_open_question,
    ]
    return Agent[AgentRunContext](
        name="Generalist Data Scientist",
        instructions=instructions or GENERALIST_INSTRUCTIONS,
        model=selected_model,
        tools=[*tools_for_role(AgentRole.GENERALIST), *state_tools],
        handoffs=[],
        output_type=AgentOutputSchema(GeneralistResult, strict_json_schema=False),
    )


create_generalist_agent = build_generalist_agent


def persist_generalist_result(
    result: GeneralistResult,
    context: AgentRunContext,
) -> GeneralistResult:
    """Persist the generalist output through the shared evidence boundaries."""

    if context.agent_role is not AgentRole.GENERALIST:
        raise ValueError("persist_generalist_result requires a Generalist context")
    context.ledger.record_audit(result.audit)
    candidate = persist_lead_result(result.candidate, context)
    validation = persist_validation_result(
        result.validation,
        context.ledger,
        allow_issue_updates=True,
    )
    return result.model_copy(update={"candidate": candidate, "validation": validation})


def _generalist_input(
    objective: str,
    *,
    business_context: str | None = None,
) -> str:
    """Build only model-visible user context for one generalist request."""

    sections = [f"OBJECTIVE:\n{objective}"]
    if business_context:
        sections.append(f"BUSINESS_CONTEXT:\n{business_context}")
    sections.append(
        "Complete the audit, analysis, self-critique, and synthesis in this one "
        "bounded run. Return the typed GeneralistResult only."
    )
    return "\n\n".join(sections)


async def run_generalist(
    context: AgentRunContext,
    objective: str,
    *,
    business_context: str | None = None,
    agent: Agent[AgentRunContext] | None = None,
) -> GeneralistResult:
    """Run and persist one generalist request without specialist invocations."""

    if context.agent_role is not AgentRole.GENERALIST:
        raise ValueError("run_generalist requires a Generalist AgentRunContext")
    selected_agent = agent or build_generalist_agent(context.run_config)
    result = await Runner.run(
        selected_agent,
        _generalist_input(objective, business_context=business_context),
        context=context,
        max_turns=context.run_config.turn_limit,
    )
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    context.record_sdk_usage(usage)
    output = result.final_output
    if not isinstance(output, GeneralistResult):
        output = GeneralistResult.model_validate(output)
    return persist_generalist_result(output, context)


__all__ = [
    "GENERALIST_INSTRUCTIONS",
    "GENERALIST_OBJECTIVE",
    "build_generalist_agent",
    "create_generalist_agent",
    "persist_generalist_result",
    "run_generalist",
]
