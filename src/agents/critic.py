"""Critic / Validator specialist for evidence-backed analysis review."""

from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner
from agents.runtime import AgentRole, AgentRunConfig, AgentRunContext
from agents.tools import tools_for_role
from orchestration.budgets import BudgetResource
from orchestration.ledger import AnalysisLedger
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
2. Check definitions, denominators, joins, artifacts, and causal language.
3. Check whether the candidate answered the objective, resolved material
   follow-up questions, and investigated available upstream mechanisms.
4. Return PASS only when the candidate is supported and complete; otherwise
   return REVISE with severity, evidence, and a concrete remediation.
"""


def candidate_completeness_validation(
    candidate: CriticCandidate,
) -> ValidationResult | None:
    """Apply deterministic completeness gates before model-based review.

    ``follow_up_analysis`` is an explicit Lead contract, rather than merely a
    caveat for the final prose.  The runner normally resolves it before
    invoking the Critic.  This guard also protects direct Critic callers and
    bounded runs that reach Critic after continuation capacity is exhausted.
    """

    if not candidate.follow_up_analysis:
        return None

    question = (
        candidate.follow_up_rationale
        or (candidate.open_questions[0] if candidate.open_questions else None)
        or "The candidate requests additional analysis."
    )
    issue = ValidationIssue(
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
    return ValidationResult(
        status=ValidationStatus.REVISE,
        issues=[issue],
        checked_finding_ids=[finding.id for finding in candidate.findings],
        summary="The candidate is not complete for the stated objective.",
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

    # Reserve both resources together so an exhausted critic loop cannot leave
    # a misleading specialist-invocation increment behind.
    context.consume_budgets(
        BudgetResource.SPECIALIST_INVOCATIONS,
        BudgetResource.CRITIC_LOOPS,
    )

    completeness = candidate_completeness_validation(candidate)
    if completeness is not None:
        return persist_validation_result(
            completeness,
            context.ledger,
            allow_issue_updates=True,
        )

    selected_agent = agent or build_critic_agent(context.run_config)
    result = await Runner.run(
        selected_agent,
        _candidate_prompt(candidate),
        context=context,
        max_turns=context.run_config.turn_limit,
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
    "candidate_completeness_validation",
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
