# 0016: Benchmark cells reserve capacity for two analytical remediations

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2 / Task 10 readiness

## Context

The replacement R19 pilot at decision 0015's clean revision preserved correct
provenance but blocked after analytical validation. The initial canonical
profitability analysis consumed all 30 SQL executions. Its one available
remediation then introduced a grain-invalid totals query and an inconsistent
practical-significance interpretation; the second Critic call correctly refused
the candidate. No capacity remained to execute the corrected aggregation.

The project plan calls for approximately two remediation cycles, while the
benchmark declaration allowed two Critic calls total: an initial review and
only one remediation/re-review. The generic Phase 1 defaults remain useful for
ordinary runs, but they under-provision this declared ten-scenario benchmark.

## Decision

The Phase 2 benchmark defaults now declare, identically for both architectures:

- 40 SQL executions, retaining a bounded ten-execution correction reserve over
  the observed initial workload; and
- three Critic calls, representing the initial review plus at most two
  remediation/re-review cycles.

All other resource and role-turn limits remain unchanged. These values are
frozen into each manifest and its canonical declaration digest. The R19 pilot
must measure their actual usage, cost, and latency before the remaining matrix
can run.

## Consequences

The evaluator and scenario rules are unchanged. A candidate still completes
only after Critic `PASS`; the change gives the Lead bounded capacity to correct
a Critic-proven aggregation defect instead of publishing a constrained report
solely because the initial investigation exhausted SQL. Both architectures
receive the same declared limits, and the full-run cost gate remains
authoritative.

## Verification

Deterministic tests pin the manifest resource limits, their conversion to a
runtime `RunBudget`, and the lifecycle sequence of two remediations followed by
a third passing Critic call. The renewed R6 run passed 680 non-live tests, three
Docker-backed integrations (683 deterministic passes total), Ruff lint and
format checks, and the four-test provider-backed architecture gate in 104.16
seconds. A new clean revision and stratified pilot are required before Task 10
execution.
