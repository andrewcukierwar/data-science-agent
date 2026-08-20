# 0015: Metric remediation preserves cited evidence and canonical identity

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2 / Task 10 readiness

## Context

The first R19 pilot attempted after the final R6 gate retained a blocked
multi-agent `canonical-q2-profitability` cell. The Critic correctly found that
the remediated candidate stated a 4.26% overall CAC increase while its
structured metrics still contained an older 6.99% value from a different
execution. The run stopped at the declared Critic limit; it was not rescored or
replaced and did not enter the benchmark matrix.

Three application-boundary defects allowed that stale value to survive:

1. `overall_cac` and `cac` were treated as different metric identities;
2. specialist metric reuse could substitute the only same-scope specialist
   value even when the Lead cited a different execution; and
3. a Critic issue categorized as `metric_definition_consistency` did not enable
   the definition correction requested by that issue.

## Decision

- Normalize `overall_cac` to the generic `cac` measure.
- Reuse a specialist comparison only when the Lead cites that specialist's
  evidence. Same identity or scope without shared evidence is not sufficient.
- Treat every `metric_definition*` Critic category, plus the legacy
  `definition_error` category, as explicit permission to correct an estimand.
- Keep unrelated validation categories on the definition-preserving path.
- Retain the blocked pilot as calibration evidence and require a new clean code
  revision, manifest, and stratified pilot before the remaining matrix runs.

## Consequences

Lead remediation can no longer silently change a measurement's provenance to
an unrelated successful execution. Equivalent CAC labels compile to one
identity, so a corrected value replaces or conflicts with a stale value instead
of coexisting invisibly. The failed pilot remains an operational failure, not an
analytical score or a candidate for success-based replacement.

## Verification

Targeted regressions cover the CAC alias, unrelated-evidence substitution, and
definition-category routing. The renewed R6 run passed 678 non-live tests, three
Docker-backed integrations (681 deterministic passes total), Ruff lint and
format checks, and the four-test provider-backed architecture gate in 92.86
seconds.
