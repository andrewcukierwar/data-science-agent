"""Data Auditor specialist using the shared deterministic tool surface."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from agents.model_usage import run_agent_with_usage
from agents.output_contract import require_strict_output, strict_output_type
from agents.runtime import AgentRole, AgentRunConfig, AgentRunContext
from agents.tools import tools_for_role
from schemas.audit import AuditResult

DATA_AUDITOR_OBJECTIVE = (
    "Perform a complete preflight audit of the available data, schemas, dates, "
    "relationships, definitions, and data-quality risks."
)

_FALLBACK_SKILL_GUIDANCE = """Data-audit procedure:

1. Inspect all available files and read the relevant business definitions.
2. Use the relation metadata inspection tool to obtain approved table names,
   columns, DuckDB types, and row counts before writing analysis queries.
3. For every table, record schema/types, row count, date coverage, missingness,
   candidate keys, duplicate rates, and plausible relationships.
4. Check expected temporal granularity and internal gaps. Distinguish a missing
   reporting day from a valid sparse event table.
5. Check duplicates only for candidate identifiers; repeated foreign-key values
   are not duplicate records by themselves.
6. Report only anomalies supported by executed SQL/Python evidence. Prefer a
   limitation to an invented data-quality issue.
"""


def _skill_guidance() -> str:
    """Load repository audit guidance with a safe fallback."""

    skill_path = Path(__file__).resolve().parents[2] / "skills" / "data_auditing.md"
    try:
        content = skill_path.read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK_SKILL_GUIDANCE
    return content or _FALLBACK_SKILL_GUIDANCE


DATA_AUDITOR_INSTRUCTIONS = f"""You are the Data Auditor specialist in an
evidence-backed business analytics system.

Perform an internal preflight audit using only the workspace, business
definitions, and results from your approved deterministic tools. You cannot
delegate, hand off, invoke another agent, or produce a user-facing report.
Return only a valid AuditResult for the calling orchestration layer.

Required workflow:

- Inspect the workspace before making claims and read relevant business
  definitions before interpreting columns or metrics.
- Call `inspect_relations` before writing schema, column, or row-count SQL. Use
  the returned relation names and exact columns; never guess names such as
  `spend_date` or `column_type`.
- The approved canonical relation names are `customers`, `orders`, `sessions`,
  and `marketing_spend` when those input files are present. Use the metadata
  response rather than assuming every run has all four.
- Record row counts and date coverage for each table where dates exist.
- Measure missingness by important column and check duplicate rates for likely
  identifier columns. Repeated foreign-key values are not duplicate records.
- Identify likely primary keys and plausible relationships, checking key
  coverage before calling a relationship valid.
- Check expected temporal granularity and suspicious internal gaps. Do not call
  a sparse event table defective merely because every calendar day is absent.
- Check obvious anomalies with bounded SQL or reproducible Python, including
  impossible values, invalid dates, broken references, and extreme counts when
  the data supports the comparison. Prefer SQL against known registered table
  names; use Python only when a profiling or distribution check genuinely
  needs it.
- `run_python` is a separate isolated environment and does not inherit the
  DuckDB connection or registered views from `run_sql`. If Python is needed,
  read approved raw files with pandas or PyArrow under `/workspace/inputs`.
- Record issues only when supported by observed tool evidence. If a check is
  unavailable or ambiguous, record a limitation instead of inventing a problem.
- Use issue evidence_refs for executed query/script paths or tool-event IDs when
  available. Keep the audit concise and actionable.

The result is persisted as internal run state. It is not a final user-facing
answer and must not include unsupported business explanations.

Procedural skill guidance:
{_skill_guidance()}
"""

AUDITOR_OBJECTIVE = DATA_AUDITOR_OBJECTIVE
AUDITOR_INSTRUCTIONS = DATA_AUDITOR_INSTRUCTIONS


def build_data_auditor_agent(
    config: AgentRunConfig | None = None,
    *,
    model: str | None = None,
    instructions: str | None = None,
) -> Agent[AgentRunContext]:
    """Build the Data Auditor with its approved tools and no delegation."""

    if config is not None and config.agent_role is not AgentRole.DATA_AUDITOR:
        raise ValueError("Data Auditor requires a data_auditor run configuration")
    selected_model = model or (config.model if config is not None else None)
    return Agent[AgentRunContext](
        name="Data Auditor",
        instructions=instructions or DATA_AUDITOR_INSTRUCTIONS,
        model=selected_model,
        tools=tools_for_role(AgentRole.DATA_AUDITOR),
        handoffs=[],
        output_type=strict_output_type(AuditResult),
    )


build_auditor_agent = build_data_auditor_agent
create_data_auditor_agent = build_data_auditor_agent


async def run_data_auditor(
    context: AgentRunContext,
    objective: str = DATA_AUDITOR_OBJECTIVE,
    *,
    agent: Agent[AgentRunContext] | None = None,
) -> AuditResult:
    """Run the Data Auditor once and persist its typed result in the ledger."""

    if context.agent_role is not AgentRole.DATA_AUDITOR:
        raise ValueError("run_data_auditor requires a Data Auditor context")
    context.record_specialist_invocation()
    selected_agent = agent or build_data_auditor_agent(context.run_config)
    result = await run_agent_with_usage(
        selected_agent,
        objective,
        context=context,
        max_turns=context.run_config.turn_limit,
    )
    output = require_strict_output(
        result.final_output,
        AuditResult,
        agent_name=selected_agent.name,
    )
    return context.ledger.record_audit(output)


run_auditor = run_data_auditor


__all__ = [
    "AUDITOR_INSTRUCTIONS",
    "AUDITOR_OBJECTIVE",
    "DATA_AUDITOR_INSTRUCTIONS",
    "DATA_AUDITOR_OBJECTIVE",
    "build_auditor_agent",
    "build_data_auditor_agent",
    "create_data_auditor_agent",
    "run_auditor",
    "run_data_auditor",
]
