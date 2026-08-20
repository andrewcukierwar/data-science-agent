# 0009: Typed audit provenance across architecture boundaries

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2
- Extends: [0004](0004-evidence-provenance-and-metric-compilation.md)
- Covers: remediation R20 (runtime contract) and R21 (offline enforcement)

## Context

Decision 0004 required material quantitative claims to cite exact executed
evidence, but it was applied to findings, metrics, hypotheses, and statistical
assessments — not to the Data Audit. `AuditResult` carried table warnings and
run limitations as bare strings and gave table profiles no references at all,
so a successfully executed audit could state material facts with no way to
prove where they came from.

That gap is invisible in the single-agent architecture, where the generalist
still holds its own tool results, and fatal in the multi-agent architecture,
where the Lead has no SQL, Python, or internal-state access. The 2026-08-20
live R6 canary showed exactly that asymmetry: the Data Auditor executed its
checks successfully, the Lead was handed the provenance-free audit JSON under
the heading `COMPLETED_DATA_AUDIT_JSON`, and it resolved hypothesis `H2` with
the invented reference `completed_data_audit`. The production evidence gate
correctly rejected the run.

The fix is not to weaken that gate, to prescribe a tool mix, or to retry until
a favorable output appears. The audit's provenance has to travel with the
claim.

## Decision

Audit contract `2.0` makes every material audit claim evidence-bearing:

- table warnings and run limitations are typed `AuditObservation` objects with a
  `statement` and `evidence_refs`, not strings;
- `TableAudit` carries its own `evidence_refs` for the row count, date
  coverage, duplicate rate, and missingness it asserts;
- `DataQualityIssue.evidence_refs` is enforced rather than optional in practice.

Both architectures persist an audit through one boundary,
`agents.audit_evidence.persist_audit_result`. It canonicalizes each claim's
references against the ledger using the same resolver that validates Lead
output, and refuses to persist a non-blocked audit whose material claims have
missing, failed, ambiguous, or fabricated provenance. A blocked audit is exempt
because it aborts the run under its own blocked-audit condition before any of
its statements can influence a candidate answer.

Claim identity is owned by the application. `audit_claims` derives positional,
collision-free claim IDs (`audit:table:0`, `audit:table:0:warning:1`,
`audit:issue:0`, `audit:limitation:0`) so two claims can never share an ID even
when a model repeats a table name or an issue ID.

The Lead receives that provenance as a bounded typed `AuditEvidenceCatalog`
under `DATA_AUDIT_EVIDENCE_CATALOG_JSON`: one entry per audit claim that
resolves, its canonical references, and the flattened set of citable
references. Only resolving claims are listed, so nothing in the catalog can be
cited into an unsupported answer. A `claim_id` is a label, not a reference. The
catalog is the whole mechanism — the Lead gains no SQL, no Python, and no
access to internal state.

`inspect_relations` now returns the `tool_event_id` of its persisted event.
Without it, an auditor could establish a row count with a successful tool call
and still have no reference to cite for it.

Offline scoring enforces the same boundary from persisted state alone, because
the benchmark's source of truth is the evaluator and it runs against workspaces
the current runtime never touched. The claim projection therefore lives in
`schemas.audit`, where the evaluator can reach it without importing anything
that executes agents, and `evaluate_workspace` resolves executed references once
for the audit, capability, and provenance checks so they cannot disagree. A
completed `AuditResult` no longer satisfies the data-audit capability on its
own: the audit must state a material claim and every material claim must
resolve. Each required issue ID is scored twice — presence and provenance — so
a defect asserted from failed or fabricated evidence is visibly distinguished
from one that was actually established. A clean audit must demonstrate a
performed check through a supported table profile or limitation; reporting no
defects is evidence of a clean dataset only when the checks behind it ran.

That clean-audit rule is deliberately tool- and role-neutral, preserving
decisions 0002 and the R1/R7 architecture-neutrality contract: a reference from
SQL, Python, relation inspection, or a verified artifact satisfies it equally,
and no check inspects which agent produced the audit.

Version handling:

- the catalog evaluator version advances from `1.1` to `1.2` for the scoring
  change, so records scored under the old rules are not silently rescored under
  the new ones;
- persisted state is written at `CURRENT_STATE_SCHEMA_VERSION = "1.1"`, which
  the offline evaluation contracts accept alongside `legacy` and `1.0`;
- `output_schema_fingerprint()` covers `AuditResult` and `GeneralistResult`, so
  the contract change invalidates any existing pilot estimate and forces a new
  benchmark manifest version before paid execution;
- contract `1.0` payloads still load: their strings are preserved as
  observations with explicitly empty `evidence_refs`. Compatibility keeps old
  workspaces readable; it never invents provenance for them, so a legacy audit
  claim correctly reads as unsupported.

## Consequences

- A completed audit whose material claims are unsupported now fails the run at
  the audit boundary instead of producing a Lead failure two stages later.
- Semantically equivalent single-agent and multi-agent audits expose identical
  claim-level provenance, so the architecture comparison is not confounded by
  where the audit happened to run.
- The Data Auditor must spend a tool call on any statement it wants to make.
  Preferring an omitted statement to an unsupported one is the intended
  trade-off.
- Retained pre-`2.0` workspaces remain loadable, but their audit claims carry no
  provenance and correctly score as unsupported. The retained Phase-1 canonical
  workspace goes from 4 to 7 offline failures for exactly this reason; it
  already failed before the change.
- Offline rescore of a manifest bound to evaluator `1.1` is refused under `1.2`
  rules. That is the identity binding working, not a defect: rescoring old
  evidence under new rules is the thing the version exists to prevent. Rescoring
  such a manifest requires explicitly supplying rules pinned to `1.1`.

## Verification

See `src/schemas/audit.py`, `src/agents/audit_evidence.py`,
`src/evaluation/primitives.py`, `tests/test_audit_provenance.py`, and
`tests/test_audit_provenance_scoring.py`.
