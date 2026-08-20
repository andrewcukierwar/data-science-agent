# 0017: Bounded provenance correction covers every architecture boundary

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2 / Task 10 readiness
- Supersedes in part: [0011](0011-bounded-evidence-correction.md)

## Context

A 40-SQL/three-Critic R19 pilot completed two analytical remediation cycles but
then stopped when its one-turn Lead citation correction could not see the newest
LTV evidence. The correction catalog selected alphabetically early references
and merged ledger findings; repeated specialist-local IDs had overwritten the
new append-only LTV findings before the catalog was built.

After that catalog defect was fixed, both provider-backed architecture canaries
returned valid typed audits whose second limitation cited a fabricated
reference. Decision 0011 gave the generalist/Lead output one correction attempt
but deliberately left the multi-agent audit terminal. The observed common audit
failure showed that exclusion made architecture parity incomplete and discarded
otherwise usable runs for a citation-only error.

## Decision

- Build the correction catalog newest-first from append-only specialist result
  records, then supplement it with merged ledger findings.
- Prioritize the bounded findings' executed references before filling the
  remaining catalog slots deterministically.
- Give a strict-schema-valid `AuditResult` exactly the same one-turn, no-tool,
  same-validator citation correction as other final analytical outputs.
- Apply that audit correction both to the dedicated Data Auditor and to the
  audit nested in `GeneralistResult`.
- Preserve the single-attempt ceiling. A second invalid audit remains a named
  provenance failure, and the correction cannot change facts or run tools.

## Consequences

Recent remediation evidence cannot disappear merely because older references
sort first or specialists reuse local IDs. Both architectures receive the same
narrow opportunity to repair a citation-only audit error. R20/R21 enforcement
is unchanged: unsupported audit claims still cannot be persisted or scored.

## Verification

Regressions cover newest-first bounded catalog selection, multi-agent audit
correction, single-agent audit correction, and terminal failure after a second
invalid audit. The renewed R6 run passed 684 non-live tests, three Docker-backed
integrations (687 deterministic passes total), Ruff lint and format checks, and
the four-test provider-backed architecture gate in 80.92 seconds.
