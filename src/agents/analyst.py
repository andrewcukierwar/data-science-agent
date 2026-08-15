"""Analyst specialist using the shared deterministic tool surface."""

from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner
from agents.runtime import AgentRole, AgentRunConfig, AgentRunContext
from agents.tools import tools_for_role
from orchestration.ledger import AnalysisLedger
from schemas.findings import SpecialistResult

ANALYST_OBJECTIVE = (
    "Decompose the Q1-to-Q2 profitability change and identify which major "
    "component changed most."
)

_FALLBACK_SKILL_GUIDANCE = """Business analytics procedure:

1. Read business definitions before computing any KPI. State the numerator,
   denominator, population, time window, and treatment of refunds/cancellations.
2. Decompose the requested KPI into major components before searching for a
   cause. Compare periods on consistent definitions and denominators.
3. Segment by relevant channel, cohort, customer, product, region, device, and
   funnel stage. Use cohorts when acquisition timing affects later outcomes.
4. For acquisition, calculate CAC as spend divided by newly acquired customers;
   calculate LTV over an explicit post-acquisition window and reconcile both to
   the same cohort and channel.
5. Use SQL for bounded aggregation and Python for reproducible analysis or
   charts. Save useful analysis artifacts and cite their executed evidence.
6. Separate observations from explanations. Do not claim causality from a
   period comparison alone; state limitations and propose a follow-up test.
"""


def _skill_guidance() -> str:
    """Load repository skill guidance with a safe fallback for installations."""

    skill_path = (
        Path(__file__).resolve().parents[2] / "skills" / "business_analytics.md"
    )
    try:
        content = skill_path.read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK_SKILL_GUIDANCE
    return content or _FALLBACK_SKILL_GUIDANCE


ANALYST_INSTRUCTIONS = f"""You are the Analyst specialist in an evidence-backed
business analytics system.

You investigate the assigned objective using only the workspace data,
business definitions, and results from your approved deterministic tools. You
cannot delegate, hand off, or invoke another agent. Do not infer hidden facts
or use scenario ground truth that is not present in the workspace.

Required workflow:

- Inspect the workspace and read the relevant business definitions first.
- Define each metric, period, denominator, cohort, and treatment rule before
  computing it.
- Use bounded SQL for aggregations and joins; use Python for reproducible
  calculations, statistical checks, or charts when SQL is insufficient.
- Save only useful, reproducible analysis artifacts under approved paths.
- Treat each material quantitative claim as unsupported until it is tied to an
  executed query/script or registered artifact.
- Put exact evidence references in every material quantitative Finding's
  `evidence_refs`. Use the query/script path or artifact identifier returned by
  the tools; never invent a reference. If evidence is unavailable, do not make
  the quantitative claim and add a follow-up question instead.
- Distinguish observed period differences from unsupported causal explanations.
  Include caveats when the data supports association but not causation.
- When an analysis reveals a material unanswered sub-question, record it in
  `follow_up_questions` so the Lead can decide whether to investigate it.

Return only a valid SpecialistResult. Keep findings concise and decision-useful.

Procedural skill guidance:
{_skill_guidance()}
"""


class AnalystEvidenceError(ValueError):
    """Raised when a material Analyst finding cites unexecuted evidence."""


def build_analyst_agent(
    config: AgentRunConfig | None = None,
    *,
    model: str | None = None,
    instructions: str | None = None,
) -> Agent[AgentRunContext]:
    """Build the Analyst with structured output and no delegation surface."""

    if config is not None and config.agent_role is not AgentRole.ANALYST:
        raise ValueError("Analyst requires an AgentRunConfig with analyst role")
    selected_model = model or (config.model if config is not None else None)
    return Agent[AgentRunContext](
        name="Analyst",
        instructions=instructions or ANALYST_INSTRUCTIONS,
        model=selected_model,
        tools=tools_for_role(AgentRole.ANALYST),
        handoffs=[],
        output_type=SpecialistResult,
    )


create_analyst_agent = build_analyst_agent


def validate_analyst_result(
    result: SpecialistResult,
    ledger: AnalysisLedger,
) -> SpecialistResult:
    """Ensure material findings reference executed tool or artifact evidence."""

    executed_refs = {event.id for event in ledger.tool_events}
    executed_refs.update(
        reference for event in ledger.tool_events for reference in event.artifact_refs
    )
    executed_refs.update(artifact.id for artifact in ledger.artifacts)
    executed_refs.update(artifact.path for artifact in ledger.artifacts)

    invalid_refs: list[str] = []
    for finding in result.findings:
        is_quantitative = finding.metric is not None or finding.value is not None
        if is_quantitative and not any(
            reference in executed_refs for reference in finding.evidence_refs
        ):
            invalid_refs.append(finding.id)
    if invalid_refs:
        raise AnalystEvidenceError(
            "material findings cite no executed evidence: " + ", ".join(invalid_refs)
        )
    return result


async def run_analyst(
    context: AgentRunContext,
    objective: str,
    *,
    agent: Agent[AgentRunContext] | None = None,
) -> SpecialistResult:
    """Run the Analyst once and validate its evidence references."""

    if context.agent_role is not AgentRole.ANALYST:
        raise ValueError("run_analyst requires an Analyst AgentRunContext")
    context.record_specialist_invocation()
    selected_agent = agent or build_analyst_agent(context.run_config)
    result = await Runner.run(selected_agent, objective, context=context)
    output = result.final_output
    if not isinstance(output, SpecialistResult):
        output = SpecialistResult.model_validate(output)
    return validate_analyst_result(output, context.ledger)


__all__ = [
    "ANALYST_INSTRUCTIONS",
    "ANALYST_OBJECTIVE",
    "AnalystEvidenceError",
    "build_analyst_agent",
    "create_analyst_agent",
    "run_analyst",
    "validate_analyst_result",
]
