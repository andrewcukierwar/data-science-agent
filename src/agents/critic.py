"""Critic / Validator specialist for evidence-backed analysis review."""

from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner
from agents.runtime import AgentRole, AgentRunConfig, AgentRunContext
from agents.tools import tools_for_role
from orchestration.budgets import BudgetResource
from orchestration.ledger import AnalysisLedger
from schemas.validation import CriticCandidate, ValidationResult

CRITIC_OBJECTIVE = (
    "Validate the candidate analysis and recommendations against their evidence."
)

_FALLBACK_SKILL_GUIDANCE = """Validation procedure:

1. Reproduce material numbers from the referenced query or script.
2. Check definitions, denominators, joins, artifacts, and causal language.
3. Return PASS only when the candidate is supported; otherwise return REVISE
   with severity, evidence, and a concrete remediation.
"""


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
- Check joins for accidental row multiplication, duplicate keys, unresolved
  foreign keys, and mismatched grains.
- Compare findings with the actual query/script outputs and registered artifact
  contents. Flag inconsistencies, contradictions, or artifacts that do not
  support the claim.
- Flag unsupported causal language in observational comparisons. Association,
  timing, or correlation is not proof that a campaign, channel, or intervention
  caused an outcome.
- Check whether the candidate ignored a documented data-quality issue or used a
  data-quality limitation to make an unsupported recommendation.
- Judge whether each recommendation is supported by the available evidence and
  is proportional to the uncertainty. Identify important alternative
  explanations when the evidence does not discriminate between them.
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
        output_type=ValidationResult,
    )


build_validator_agent = build_critic_agent
create_critic_agent = build_critic_agent
create_validator_agent = build_critic_agent


def _candidate_prompt(candidate: CriticCandidate) -> str:
    """Serialize the typed candidate without exposing local context internals."""

    return (
        "Validate this candidate analysis. The listed evidence references are "
        "workspace-relative paths, tool-event IDs, or registered artifact IDs. "
        "Use the approved tools to inspect or reproduce them.\n\n"
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

    # Check both limits before consuming either one so an exhausted critic loop
    # cannot leave a misleading specialist-invocation increment behind.
    context.check_budget(BudgetResource.SPECIALIST_INVOCATIONS)
    context.check_budget(BudgetResource.CRITIC_LOOPS)
    context.consume_budget(BudgetResource.SPECIALIST_INVOCATIONS)
    context.consume_budget(BudgetResource.CRITIC_LOOPS)

    selected_agent = agent or build_critic_agent(context.run_config)
    result = await Runner.run(
        selected_agent,
        _candidate_prompt(candidate),
        context=context,
        max_turns=context.run_config.max_agent_turns,
    )
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    context.record_sdk_usage(usage)
    output = result.final_output
    if not isinstance(output, ValidationResult):
        output = ValidationResult.model_validate(output)
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
