# 0018: Bound complete agent invocation latency

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2 / Task 10 readiness

## Context

The fourth renewed R19 pilot reached a Critic invocation after 32 successfully
accounted provider responses, then remained inside one SDK call for more than
thirty minutes. The OpenAI transport's ten-minute request timeout may be
retried twice and is not an end-to-end orchestration bound. The benchmark had
turn and tool limits but no elapsed-time limit for one agent invocation.

The interrupted attempt was retained with 2,051.45 seconds elapsed, 263,188
accounted tokens, incomplete usage, unavailable cost, and a typed
`interrupted` outcome. No later pilot stratum or matrix cell ran.

## Decision

- Bound every complete Agents SDK invocation to 300 seconds, including provider
  retries and tool turns.
- Apply the same bound to every role in both architectures, including bounded
  evidence-correction invocations.
- Freeze the bound as `agent_run_timeout_seconds` in the immutable benchmark
  run configuration so changing it invalidates the manifest and pilot digest.
- Preserve response-boundary usage. A timeout with an unreconciled in-flight
  response marks usage incomplete and cost unavailable; it never becomes a
  known zero.
- Propagate the existing typed `timeout` block reason and failure category.

## Consequences

A provider operation can no longer occupy a benchmark worker indefinitely.
Timeouts remain operational evidence and cannot authorize a cost pilot. Any
replacement pilot requires a new clean-revision manifest because the execution
configuration and code revision changed.

## Verification

Regressions cover configuration bounds, terminal timeout accounting, recorder
cleanup, typed timeout propagation, and manifest freezing. The restricted
deterministic suite passes 689 tests, Ruff passes across 167 files, and the
10 x 2 x 3 dry-run produces 60
unique cells. Docker and provider-backed reruns remain required to reclose R6;
the execution environment denied those external capabilities after reaching
its tool-usage allowance.
