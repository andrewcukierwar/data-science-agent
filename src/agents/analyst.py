"""Analyst specialist using the shared deterministic tool surface."""

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
5. When CAC or acquired-customer volume is materially changing and the required
   data exists, decompose the acquisition path:
   marketing spend -> traffic/sessions -> conversion -> acquired customers
   -> CAC -> downstream LTV/value. Compare the relevant periods and segments,
   reconcile the funnel to customer and order cohorts, and return evidence for
   each material step. Do not run this full decomposition when acquisition
   economics are not material to the assigned objective.
6. Identify the grain of every source before joining facts of different grains.
   Aggregate each source to the common reporting grain first. Never join
   period/channel marketing spend directly to customer or order rows and then
   sum spend. Reconcile row counts and aggregate totals before and after
   material joins.
7. For profitability, explicitly decompose net revenue, COGS, contribution
   before marketing, marketing spend, and reporting contribution profit. State
   whether revenue, COGS/margin, or marketing economics are material drivers or
   non-drivers. Compute and compare COGS, contribution before marketing, and
   contribution margin or COGS/revenue ratio; state explicitly when broad margin
   deterioration is not material.
8. For named periods, use explicit date boundaries or explicit quarter
   inclusion. Never classify every period that is not Q1 as Q2. Reconcile
   derived cohort counts to the customers acquisition table before inference.
9. Use `inspect_relations` and the registered SQL relation names rather than
   filesystem paths. Use SQL for bounded aggregation and Python for
   reproducible analysis or charts. Python runs separately from the SQL
   connection; read raw approved files under `/workspace/inputs` with pandas
   or PyArrow when needed. Save useful analysis artifacts and cite their
   executed evidence.
10. Return material period/segment comparisons as generic `MetricComparison`
    objects in addition to prose Findings. Copy the exact metric identity,
    value, periods, unit, and evidence references from executed analysis; do not
    use evaluator-specific or scenario-specific IDs. For every material
    nonzero-baseline period comparison, include a relative_change comparison in
    addition to an absolute difference when both support the conclusion. Do not
    reconstruct a comparison already returned by an executed specialist tool.
11. When acquisition economics are material, close the observable path in the
    final result: marketing spend -> sessions/traffic -> conversion -> acquired
    customers -> CAC -> downstream LTV/value. Distinguish observed accounting or
    funnel relationships from unsupported explanations for why an upstream
    metric changed.
12. Separate observations from explanations. Do not claim causality from a
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
- Use `inspect_relations` before authoring SQL so relation names, columns, and
  DuckDB types come from the approved metadata surface rather than guesses.
- Define each metric, period, denominator, cohort, and treatment rule before
  computing it.
- Identify the grain of every source before joining facts of different grains.
  Aggregate each source to the common reporting grain first; never join
  period/channel marketing spend directly to customer or order rows and then
  sum spend. Reconcile row counts and aggregate totals before and after
  material joins.
- For profitability objectives, explicitly address net revenue, COGS,
  contribution before marketing, marketing spend, and reporting contribution
  profit, including material non-drivers.
- Use explicit boundaries or explicit quarter inclusion for named periods.
  Never define Q2 as every period that is not Q1, and reconcile cohort counts
  to the customers acquisition table before inference.
- Use bounded SQL for aggregations and joins; use Python for reproducible
  calculations, statistical checks, or charts when SQL is insufficient.
- Save only useful, reproducible analysis artifacts under approved paths.
- Treat each material quantitative claim as unsupported until it is tied to an
  executed query/script or registered artifact.
- Put exact evidence references in every material quantitative Finding's
  `evidence_refs`. Copy references verbatim from the results returned by
  `run_sql`, `run_python`, `save_artifact`, or another approved evidence tool;
  never construct a path manually or assume that a workspace file was
  executed evidence. If evidence is unavailable, do not make the quantitative
  claim and add a follow-up question instead.
- Distinguish observed period differences from unsupported causal explanations.
  Include caveats when the data supports association but not causation.
- When an analysis reveals a material unanswered sub-question, record it in
  `follow_up_questions` so the Lead can decide whether to investigate it.
- Return material period/segment comparisons as generic `MetricComparison`
  objects in addition to Findings. Reuse the exact computed value, identity,
  periods, unit, evidence references, and definition_context; use the context
  for population, date basis, observation window, numerator, denominator, and
  definition reference when they distinguish estimands. Never reconstruct a
  comparison from prose or use evaluator-specific metric IDs.

Return only a valid SpecialistResult. Keep findings concise and decision-useful.
{STRUCTURED_DIMENSION_GUIDANCE}

Procedural skill guidance:
{_skill_guidance()}
"""


class AnalystEvidenceError(ValueError):
    """Raised when a material Analyst finding cites unexecuted evidence."""


class AnalystArtifactError(ValueError):
    """Raised when an Analyst-listed artifact cannot be safely registered."""


_ARTIFACT_SUFFIXES: Final[dict[str, ArtifactKind]] = {
    ".html": ArtifactKind.CHART,
    ".jpeg": ArtifactKind.CHART,
    ".jpg": ArtifactKind.CHART,
    ".png": ArtifactKind.CHART,
    ".svg": ArtifactKind.CHART,
    ".md": ArtifactKind.REPORT,
    ".pdf": ArtifactKind.REPORT,
    ".py": ArtifactKind.SCRIPT,
    ".sql": ArtifactKind.QUERY,
}


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
        output_type=strict_output_type(SpecialistResult),
    )


create_analyst_agent = build_analyst_agent


def validate_analyst_result(
    result: SpecialistResult,
    ledger: AnalysisLedger,
) -> SpecialistResult:
    """Ensure material findings reference executed tool or artifact evidence."""

    executed_refs = executed_references(ledger)
    aliases = finding_reference_aliases(ledger)

    def canonical(references: list[str]) -> tuple[list[str], bool]:
        resolution = resolve_citations(
            references,
            executed_refs=executed_refs,
            aliases=aliases,
        )
        return list(resolution.canonical_references), resolution.is_supported

    invalid_refs: list[str] = []
    findings = []
    for finding in result.findings:
        references, supported = canonical(finding.evidence_refs)
        if not supported:
            invalid_refs.append(finding.id)
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
    result = result.model_copy(
        update={"findings": findings, "metric_comparisons": comparisons}
    )

    if invalid_refs:
        raise AnalystEvidenceError(
            "material findings cite no executed evidence: " + ", ".join(invalid_refs)
        )
    if invalid_comparisons:
        raise AnalystEvidenceError(
            "metric comparisons cite no executed evidence: "
            + ", ".join(invalid_comparisons)
        )
    return result


def _persist_analyst_artifacts(
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
                raise AnalystArtifactError(
                    f"analyst artifact was not returned by an executed tool: {path}"
                )
            try:
                artifact_manager.register(
                    path,
                    artifact_id=existing.id,
                    kind=_ARTIFACT_SUFFIXES.get(
                        Path(path).suffix.lower(), ArtifactKind.OTHER
                    ),
                    description="Analyst analysis artifact",
                    overwrite=True,
                )
            except (OSError, ValueError) as exc:
                raise AnalystArtifactError(
                    f"analyst artifact could not be refreshed: {path}: {exc}"
                ) from exc
            continue
        if path not in executed_refs:
            raise AnalystArtifactError(
                f"analyst artifact was not returned by an executed tool: {path}"
            )
        try:
            artifact_manager.register(
                path,
                artifact_id=f"analyst-{sha256(path.encode('utf-8')).hexdigest()[:20]}",
                kind=_ARTIFACT_SUFFIXES.get(
                    Path(path).suffix.lower(), ArtifactKind.OTHER
                ),
                description="Analyst analysis artifact",
            )
        except (OSError, ValueError) as exc:
            raise AnalystArtifactError(
                f"analyst artifact could not be registered: {path}: {exc}"
            ) from exc


def persist_analyst_result(
    result: SpecialistResult,
    context: AgentRunContext,
) -> SpecialistResult:
    """Validate and persist Analyst findings for direct or nested runs."""

    canonical_result = canonicalize_specialist_result(
        result,
        AgentRole.ANALYST.value,
    )
    _persist_analyst_artifacts(
        canonical_result,
        context.ledger,
        context.artifact_manager,
    )
    canonical_result = validate_analyst_result(canonical_result, context.ledger)
    for finding in canonical_result.findings:
        context.ledger.upsert_finding(finding)
    for comparison in canonical_result.metric_comparisons:
        context.ledger.upsert_metric_comparison(comparison)
    return canonical_result


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
    output = persist_analyst_result(output, context)
    context.ledger.record_specialist_result(AgentRole.ANALYST.value, output)
    return output


__all__ = [
    "ANALYST_INSTRUCTIONS",
    "ANALYST_OBJECTIVE",
    "AnalystArtifactError",
    "AnalystEvidenceError",
    "build_analyst_agent",
    "create_analyst_agent",
    "persist_analyst_result",
    "run_analyst",
    "validate_analyst_result",
]
