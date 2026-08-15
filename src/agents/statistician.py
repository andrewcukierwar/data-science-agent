"""Statistician specialist using Python-backed inferential analysis."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Final

from agents import Agent, Runner
from agents.runtime import AgentRole, AgentRunConfig, AgentRunContext
from agents.tools import tools_for_role
from orchestration.ledger import AnalysisLedger
from schemas.findings import SpecialistResult
from schemas.run_state import ArtifactKind
from tools.artifacts import ArtifactManager

STATISTICIAN_OBJECTIVE = (
    "Assess the statistical credibility and practical importance of the assigned "
    "business difference."
)

_FALLBACK_SKILL_GUIDANCE = """Statistical analysis procedure:

1. State the estimand, null hypothesis, alternative, population, and unit of
   analysis before selecting a test.
2. Match the test to the design and outcome, check assumptions, and report the
   effect size with a confidence interval and p-value.
3. Distinguish statistical significance from a business-relevant effect size.
4. Treat multiple comparisons and observational differences cautiously; do not
   claim causality without an appropriate design.
"""

_ARTIFACT_SUFFIXES: Final[dict[str, ArtifactKind]] = {
    ".py": ArtifactKind.SCRIPT,
    ".sql": ArtifactKind.QUERY,
    ".png": ArtifactKind.CHART,
    ".jpg": ArtifactKind.CHART,
    ".jpeg": ArtifactKind.CHART,
    ".svg": ArtifactKind.CHART,
    ".html": ArtifactKind.CHART,
    ".md": ArtifactKind.REPORT,
    ".pdf": ArtifactKind.REPORT,
}


def _skill_guidance() -> str:
    """Load repository statistical guidance with a safe fallback."""

    skill_path = (
        Path(__file__).resolve().parents[2] / "skills" / "statistical_analysis.md"
    )
    try:
        content = skill_path.read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK_SKILL_GUIDANCE
    return content or _FALLBACK_SKILL_GUIDANCE


STATISTICIAN_INSTRUCTIONS = f"""You are the Statistician specialist in an
evidence-backed business analytics system.

You answer inferential and statistical questions, not generic exploratory
analytics. Use only business definitions, approved documents, and Python
analysis executed through the approved tool. You cannot delegate, hand off,
invoke another agent, or produce a user-facing final report. Return only a
valid SpecialistResult for the calling orchestration layer.
SQL is not an approved tool for this role; use Python unless a future run
explicitly changes the permission boundary for a demonstrated need.

Required workflow:

- Read the relevant business definitions before choosing an estimand or
  interpreting a metric.
- State the unit of analysis, target population, estimand, null hypothesis,
  alternative hypothesis, and practical decision threshold.
- Select a test based on the outcome type, sample design, pairing, independence,
  distribution, and variance structure. Use Python for all calculations.
- Check and report assumptions, sample-size limitations, missingness, outliers,
  dependence, and any relevant robustness or sensitivity analysis.
- Report confidence intervals, effect sizes, p-values, and practical meaning;
  never treat a p-value as the size or importance of an effect.
- Account for multiple comparisons when several segments, metrics, or periods
  are tested. Distinguish planned tests from exploratory results.
- Treat observational period or channel comparisons as associations. Do not
  claim that a channel, campaign, or intervention caused an outcome without an
  appropriate causal design.
- Attach every quantitative Finding to an executed Python script, tool event,
  or registered artifact in `evidence_refs`. Use exact relative workspace paths
  returned by the tool; never invent evidence.
- List only analysis artifacts that the executed script actually created under
  `working/` or `outputs/`.

The result is an internal, concise statistical assessment for Lead consumption,
not a user-facing narrative. Include caveats and follow-up questions when the
design or data cannot support a strong conclusion.

Procedural skill guidance:
{_skill_guidance()}
"""


class StatisticianEvidenceError(ValueError):
    """Raised when a statistical finding lacks executed evidence."""


class StatisticianArtifactError(ValueError):
    """Raised when a claimed statistical artifact is unsafe or unavailable."""


def build_statistician_agent(
    config: AgentRunConfig | None = None,
    *,
    model: str | None = None,
    instructions: str | None = None,
) -> Agent[AgentRunContext]:
    """Build the Statistician with Python/document tools and no delegation."""

    if config is not None and config.agent_role is not AgentRole.STATISTICIAN:
        raise ValueError("Statistician requires a statistician run configuration")
    selected_model = model or (config.model if config is not None else None)
    return Agent[AgentRunContext](
        name="Statistician",
        instructions=instructions or STATISTICIAN_INSTRUCTIONS,
        model=selected_model,
        tools=tools_for_role(AgentRole.STATISTICIAN),
        handoffs=[],
        output_type=SpecialistResult,
    )


create_statistician_agent = build_statistician_agent


def _executed_references(ledger: AnalysisLedger) -> set[str]:
    references = {event.id for event in ledger.tool_events}
    references.update(
        reference for event in ledger.tool_events for reference in event.artifact_refs
    )
    references.update(artifact.id for artifact in ledger.artifacts)
    references.update(artifact.path for artifact in ledger.artifacts)
    return references


def validate_statistician_result(
    result: SpecialistResult,
    ledger: AnalysisLedger,
) -> SpecialistResult:
    """Require every quantitative finding to cite executed evidence."""

    executed_refs = _executed_references(ledger)
    invalid_findings = [
        finding.id
        for finding in result.findings
        if (finding.metric is not None or finding.value is not None)
        and not any(reference in executed_refs for reference in finding.evidence_refs)
    ]
    if invalid_findings:
        raise StatisticianEvidenceError(
            "statistical findings cite no executed evidence: "
            + ", ".join(invalid_findings)
        )
    return result


def _artifact_kind(path: str) -> ArtifactKind:
    return _ARTIFACT_SUFFIXES.get(Path(path).suffix.lower(), ArtifactKind.OTHER)


def _artifact_id(path: str) -> str:
    digest = sha256(path.encode("utf-8")).hexdigest()[:20]
    return f"statistician-{digest}"


def _persist_artifacts(
    result: SpecialistResult,
    artifact_manager: ArtifactManager,
) -> None:
    """Register model-listed files after ArtifactManager safety checks."""

    for path in result.artifacts:
        if artifact_manager.ledger.get_artifact(path) is not None:
            continue
        if any(artifact.path == path for artifact in artifact_manager.ledger.artifacts):
            continue
        try:
            artifact_manager.register(
                path,
                artifact_id=_artifact_id(path),
                kind=_artifact_kind(path),
                description="Statistician analysis artifact",
            )
        except (OSError, ValueError) as exc:
            raise StatisticianArtifactError(
                f"statistical artifact could not be registered: {path}: {exc}"
            ) from exc


def persist_statistician_result(
    result: SpecialistResult,
    context: AgentRunContext,
) -> SpecialistResult:
    """Validate and persist statistical findings and claimed artifacts."""

    _persist_artifacts(result, context.artifact_manager)
    validate_statistician_result(result, context.ledger)
    for finding in result.findings:
        existing = next(
            (
                current
                for current in context.ledger.findings
                if current.id == finding.id
            ),
            None,
        )
        if existing is None:
            context.ledger.add_finding(finding)
        elif existing != finding:
            raise StatisticianEvidenceError(
                f"finding id already exists with different content: {finding.id}"
            )
    return result


async def run_statistician(
    context: AgentRunContext,
    objective: str = STATISTICIAN_OBJECTIVE,
    *,
    agent: Agent[AgentRunContext] | None = None,
) -> SpecialistResult:
    """Run the Statistician and persist its evidence-backed result."""

    if context.agent_role is not AgentRole.STATISTICIAN:
        raise ValueError("run_statistician requires a Statistician context")
    context.record_specialist_invocation()
    selected_agent = agent or build_statistician_agent(context.run_config)
    result = await Runner.run(selected_agent, objective, context=context)
    output = result.final_output
    if not isinstance(output, SpecialistResult):
        output = SpecialistResult.model_validate(output)
    return persist_statistician_result(output, context)


__all__ = [
    "STATISTICIAN_INSTRUCTIONS",
    "STATISTICIAN_OBJECTIVE",
    "StatisticianArtifactError",
    "StatisticianEvidenceError",
    "build_statistician_agent",
    "create_statistician_agent",
    "persist_statistician_result",
    "run_statistician",
    "validate_statistician_result",
]
