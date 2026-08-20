"""Statistician specialist using Python-backed inferential analysis."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Final

from agents import Agent
from agents.evidence import (
    executed_references,
    finding_reference_aliases,
    resolve_citations,
)
from agents.model_usage import run_agent_with_usage
from agents.output_contract import (
    STRUCTURED_DIMENSION_GUIDANCE,
    require_strict_output,
    strict_output_type,
)
from agents.runtime import AgentRole, AgentRunConfig, AgentRunContext
from agents.tools import tools_for_role
from orchestration.ledger import AnalysisLedger
from schemas.findings import SpecialistResult, canonicalize_specialist_result
from schemas.metrics import (
    deduplicate_metric_comparisons,
    normalize_metric_comparison,
)
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
5. Use explicit date boundaries or explicit quarter inclusion for named periods;
   never classify every period that is not Q1 as Q2. Reconcile cohort counts to
   the customers acquisition table before inference.
6. Identify source grain before combining facts, aggregate each source to a
   common reporting grain before joining, and reconcile counts and totals after
   material joins.
7. Return material period/segment comparisons as generic MetricComparison
   objects with exact values, units, periods, dimensions, and evidence refs. For
   nonzero baselines, include a relative_change comparison in addition to an
   absolute difference when both are material to the conclusion.
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
- For named periods such as Q1 and Q2, use explicit date boundaries or explicit
  quarter inclusion. Never treat every period that is not Q1 as Q2, and
  reconcile derived cohorts to the customers acquisition table.
- Identify the grain of every input before combining facts. Aggregate to the
  common reporting grain before joining, especially when spend is daily and
  outcomes are customer- or order-level.
- Select a test based on the outcome type, sample design, pairing, independence,
  distribution, and variance structure. Use Python for all calculations.
- Check and report assumptions, sample-size limitations, missingness, outliers,
  dependence, and any relevant robustness or sensitivity analysis.
- Report confidence intervals, effect sizes, p-values, and practical meaning;
  never treat a p-value as the size or importance of an effect.
- For a configured experiment expectation, return exactly one typed statistical
  assessment with the conclusion, estimate, confidence interval, p-value,
  effect size, practical-significance threshold, checked assumptions, and
  causal interpretation. Do not omit an assumption merely because the result
  is statistically significant.
- Account for multiple comparisons when several segments, metrics, or periods
  are tested. Distinguish planned tests from exploratory results.
- Treat observational period or channel comparisons as associations. Do not
  claim that a channel, campaign, or intervention caused an outcome without an
  appropriate causal design.
- Return material period/segment comparisons as generic `MetricComparison`
  objects in addition to Findings, preserving exact computed values, units,
  periods, dimensions, evidence references, and definition_context. Use the
  context for population, date basis, observation window, numerator,
  denominator, and definition reference when they distinguish estimands. Do
  not reconstruct values from prose or use scenario-specific metric IDs. For a
  nonzero baseline, include a
  relative_change comparison in addition to an absolute difference when both
  are material to the inferential conclusion.
{STRUCTURED_DIMENSION_GUIDANCE}
- Attach every quantitative Finding to an executed Python script, tool event,
  or registered artifact in `evidence_refs`. Copy exact references returned by
  `run_python`, `save_artifact`, or another approved evidence tool; never
  construct a path manually or invent evidence.
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
        output_type=strict_output_type(SpecialistResult),
    )


create_statistician_agent = build_statistician_agent


def _executed_references(ledger: AnalysisLedger) -> set[str]:
    return executed_references(ledger)


def validate_statistician_result(
    result: SpecialistResult,
    ledger: AnalysisLedger,
) -> SpecialistResult:
    """Require every quantitative finding to cite executed evidence."""

    executed_refs = _executed_references(ledger)
    aliases = finding_reference_aliases(ledger)

    def canonical(references: list[str]) -> tuple[list[str], bool]:
        resolution = resolve_citations(
            references,
            executed_refs=executed_refs,
            aliases=aliases,
        )
        return list(resolution.canonical_references), resolution.is_supported

    invalid_findings: list[str] = []
    findings = []
    for finding in result.findings:
        references, supported = canonical(finding.evidence_refs)
        if not supported:
            invalid_findings.append(finding.id)
        findings.append(finding.model_copy(update={"evidence_refs": references}))
    invalid_comparisons: list[str] = []
    comparisons = []
    for comparison in deduplicate_metric_comparisons(result.metric_comparisons):
        references, supported = canonical(comparison.evidence_refs)
        if not supported:
            invalid_comparisons.append(comparison.metric_key)
        comparisons.append(
            normalize_metric_comparison(
                comparison.model_copy(update={"evidence_refs": references})
            )
        )
    invalid_assessments: list[str] = []
    assessments = []
    for assessment in result.statistical_assessments:
        references, supported = canonical(assessment.evidence_refs)
        if not supported:
            invalid_assessments.append(assessment.metric_key)
        assessments.append(assessment.model_copy(update={"evidence_refs": references}))
    result = result.model_copy(
        update={
            "findings": findings,
            "metric_comparisons": comparisons,
            "statistical_assessments": assessments,
        }
    )
    if invalid_findings:
        raise StatisticianEvidenceError(
            "statistical findings cite no executed evidence: "
            + ", ".join(invalid_findings)
        )
    if invalid_comparisons:
        raise StatisticianEvidenceError(
            "metric comparisons cite no executed evidence: "
            + ", ".join(invalid_comparisons)
        )
    if invalid_assessments:
        raise StatisticianEvidenceError(
            "statistical assessments cite no executed evidence: "
            + ", ".join(invalid_assessments)
        )
    return result


def _artifact_kind(path: str) -> ArtifactKind:
    return _ARTIFACT_SUFFIXES.get(Path(path).suffix.lower(), ArtifactKind.OTHER)


def _artifact_id(path: str) -> str:
    digest = sha256(path.encode("utf-8")).hexdigest()[:20]
    return f"statistician-{digest}"


def _persist_artifacts(
    result: SpecialistResult,
    ledger: AnalysisLedger,
    artifact_manager: ArtifactManager,
) -> None:
    """Register model-listed files only when execution returned their refs."""

    executed_refs = executed_references(ledger)

    for path in result.artifacts:
        if artifact_manager.ledger.get_artifact(path) is not None:
            continue
        existing = next(
            (
                artifact
                for artifact in artifact_manager.ledger.artifacts
                if artifact.path == path
            ),
            None,
        )
        if existing is not None:
            if path not in executed_refs:
                raise StatisticianArtifactError(
                    f"statistical artifact was not returned by an executed tool: {path}"
                )
            try:
                artifact_manager.register(
                    path,
                    artifact_id=existing.id,
                    kind=_artifact_kind(path),
                    description="Statistician analysis artifact",
                    overwrite=True,
                )
            except (OSError, ValueError) as exc:
                raise StatisticianArtifactError(
                    f"statistical artifact could not be refreshed: {path}: {exc}"
                ) from exc
            continue
        if path not in executed_refs:
            raise StatisticianArtifactError(
                f"statistical artifact was not returned by an executed tool: {path}"
            )
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

    canonical_result = canonicalize_specialist_result(
        result,
        AgentRole.STATISTICIAN.value,
    )
    _persist_artifacts(
        canonical_result,
        context.ledger,
        context.artifact_manager,
    )
    canonical_result = validate_statistician_result(canonical_result, context.ledger)
    for finding in canonical_result.findings:
        context.ledger.upsert_finding(finding)
    for comparison in canonical_result.metric_comparisons:
        context.ledger.upsert_metric_comparison(comparison)
    return canonical_result


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
    result = await run_agent_with_usage(
        selected_agent,
        objective,
        context=context,
        max_turns=context.run_config.turn_limit,
    )
    output = require_strict_output(
        result.final_output,
        SpecialistResult,
        agent_name=selected_agent.name,
    )
    output = persist_statistician_result(output, context)
    context.ledger.record_specialist_result(AgentRole.STATISTICIAN.value, output)
    return output


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
