# 0005: Bounded orchestration and graceful degradation

- Status: Accepted
- Date: 2026-08-17
- Phase: Phase 1

## Context

Agent turn limits alone do not bound local tool work, and a check-then-increment
budget implementation overshot under concurrent specialist calls. Remediation
failures also discarded otherwise usable Lead and Critic state.

## Decision

`AnalysisRunner` deterministically owns the lifecycle:

1. create/open workspace and ledger, then mark `RUNNING`;
2. execute one mandatory Data Auditor preflight and persist it;
3. invoke Lead with objective, business context, and audit;
4. allow bounded Lead specialist delegation;
5. if Lead says objective-critical follow-up is needed, allow at most two Lead
   continuation cycles before initial Critic review;
6. run finalization completeness checks for material metrics and a useful chart
   when required and affordable;
7. invoke Critic;
8. on `REVISE`, perform targeted Lead remediation, then go directly to Critic
   re-review—no intervening generic follow-up cycle;
9. persist a completed report only on Critic `PASS`;
10. if bounded remediation cannot finish, preserve the candidate and Critic
    issues and emit a constrained `BLOCKED` report;
11. mark unrecoverable pre-candidate errors `FAILED` with observable state.

Budget units are atomically reserved before counted work begins using
synchronization shared at the ledger/run level. Concurrent callers cannot
overshoot and tool wrappers do not double count service-level usage.

Current default run budgets:

| Resource | Limit | Meaning |
| --- | ---: | --- |
| Specialist invocations | 12 | Lead-delegated Analyst/Statistician work |
| SQL executions | 30 | All counted DuckDB executions |
| Python executions | 20 | All counted Docker Python executions |
| Critic loops | 2 | Mandatory lifecycle validation/re-review |
| Charts | 4 | Registered chart creation units |

Critic calls are controlled by `max_critic_loops` and do not consume the
Lead-delegated specialist budget. Mandatory audit is likewise not a Lead
specialist call.

Current default SDK turn limits are role-specific:

| Role | Turns |
| --- | ---: |
| Lead | 16 |
| Data Auditor | 12 |
| Analyst | 10 |
| Statistician | 10 |
| Critic | 8 |

All limits remain configurable; none replaces the run budgets.

## Alternatives considered

- Raising all limits was rejected because it hides contract and concurrency
  bugs.
- Letting specialist exhaustion prevent Critic validation was rejected because
  mandatory validation capacity must be guaranteed separately.
- Failing the whole run after a remediation-only failure was rejected because a
  constrained report is more truthful and useful than discarding valid state.

## Consequences

- `COMPLETED` means final Critic `PASS`; an unresolved candidate is `BLOCKED`,
  not misleadingly completed.
- Constrained reports name unresolved validation issues and the budget/turn
  condition that stopped remediation.
- Initial audit or initial Lead failure remains fatal because no usable
  candidate exists.
- Failed tool or specialist attempts may coexist with an ultimately completed
  run, provided successful evidence and final validation satisfy acceptance.

## Verification

See `src/orchestration/budgets.py`, `src/orchestration/ledger.py`,
`src/orchestration/runner.py`, `src/agents/runtime.py`, and concurrency,
budget, continuation, remediation, and constrained-report tests in
`tests/test_agent_runtime.py`, `tests/test_ledger.py`, and
`tests/test_runner.py`.
