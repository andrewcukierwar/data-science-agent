# 0002: Manager-specialist agent architecture

- Status: Accepted
- Date: 2026-08-17
- Phase: Phase 1

## Context

The system needs autonomous delegation while keeping computation, validation,
and user-facing synthesis separate. Peer-to-peer handoffs or unrestricted tool
sharing would make ownership and permission boundaries difficult to audit.

## Decision

Use the OpenAI Agents SDK's plain `Agent` plus function-tool architecture:

- The Lead owns the objective, investigation plan, hypotheses, bounded
  delegation, synthesis, recommendations, and candidate final answer.
- The Lead has no `run_sql` or `run_python` tool and performs no raw
  computation.
- Analyst and Statistician are exposed to the Lead as agents-as-tools with
  bounded typed tasks, not free-form handoffs.
- Specialists cannot delegate.
- The Data Auditor is a mandatory application lifecycle preflight. It is not a
  normal Lead tool and should not be redundantly invoked for a broad re-audit.
- The Critic is an application lifecycle validator and is not Lead-delegated
  analytical work.

Role-bound tool surfaces are enforced through `AgentRunContext`:

- Auditor: workspace/document/relation inspection, SQL, and Python;
- Analyst: workspace/document/relation inspection, SQL, Python, and approved
  artifact saving;
- Statistician: documents and Python for inferential work;
- Critic: workspace/documents/relations, SQL, Python, and bounded evidence
  inspection;
- Lead: context reading and narrow ledger-management tools only.

Nested role state is task-local using `ContextVar`. Overlapping Analyst and
Statistician calls therefore see their own permissions, and exceptions or turn
failures restore the parent Lead role.

Specialists may emit concise local finding IDs such as `F1`. Persistence adds a
deterministic role namespace, for example `analyst:F1` and `statistician:F1`, so
independent model outputs do not need to coordinate globally unique IDs.

## Alternatives considered

- Handoffs were rejected because the Lead must retain ownership of the final
  objective and answer.
- Giving the Lead SQL/Python was rejected because it bypasses specialist
  provenance and architectural tests.
- A second orchestration framework was rejected as unnecessary abstraction.
- A shared mutable role stack was tried and failed under concurrent nested
  specialist calls.

## Consequences

- Architectural restrictions are tested independently of model behavior.
- Agent outputs use typed Pydantic schemas such as `AuditResult`,
  `SpecialistResult`, `LeadResult`, and `ValidationResult`.
- Agent prompts guide analytical procedure, but application boundaries enforce
  permissions and identity.
- The shared context must be checked at lifecycle boundaries: Lead is `LEAD`,
  Critic is `CRITIC`, and no nested role remains after a specialist call.

## Verification

See `src/agents/runtime.py`, `src/agents/lead.py`, specialist modules under
`src/agents/`, `src/orchestration/runner.py`, and the corresponding
`tests/test_*agent*.py`, `tests/test_lead.py`, and `tests/test_runner.py`.
