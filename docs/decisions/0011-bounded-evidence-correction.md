# 0011: One bounded correction for a semantic provenance failure

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2
- Extends: [0009](0009-audit-provenance-across-architectures.md),
  [0010](0010-hypothesis-evidence-rule.md)
- Covers: remediation R23

## Context

The 2026-08-20 multi-agent canary returned well-formed JSON that satisfied the
strict output schema in every respect except one: hypothesis `H2` cited
`completed_data_audit`, a reference that never existed. The provenance gate
correctly rejected it and the run terminated.

That outcome is right but wasteful. A response that parses cleanly and makes an
unsupported claim is not a malformed model response — it is a valid document
with a fixable citation. Throwing away a complete multi-agent run over it costs
the whole run's tokens and produces no analytical observation. Rerunning the run
from the start is worse: that is resampling until a favourable output appears,
which is precisely what the provenance gate exists to prevent, and it would
quietly turn a failed cell into a passing one.

## Decision

A strict-schema-valid response whose citations do not resolve gets **one**
correction attempt, and only one.

The bound is structural, not conventional.
`AgentRunConfig.evidence_correction_attempts` is validated `ge=0, le=1`, so no
configuration can turn a bounded correction into repeated resampling, and the
correction agent runs with `max_turns=1`.

The correction agent has **no tools at all** — no SQL, no Python, no specialist
delegation, no Critic. It therefore reuses the run's existing executions and
consumes no additional resource budget; the only new cost is one model call,
which is recorded like any other. Its sole capability is to re-emit the same
typed result.

The correction request is specific rather than a blind retry. It carries the
exact output field IDs that failed (`LeadEvidenceError.invalid_fields`), the
validator's message, the previous output verbatim, and a bounded
`EvidenceCorrectionCatalog` of every reference the run can legitimately cite:
executed tool-event IDs and query/script paths, persisted specialist findings
with their canonical references, and the audit evidence catalog from decision
0009. That catalog is derived entirely from the run's own executed evidence — it
contains no scenario ground truth, no evaluator rules, and no internal
orchestration state.

The instructions tell the model to change only `evidence_refs` and to remove a
claim it cannot support rather than manufacture a citation for it. The
application never edits citations itself: the corrected response is put through
the identical validating persistence boundary that rejected the first one. If it
fails again, that failure is raised and the run ends. Nothing is rewritten and
nothing is retried a second time.

Both model calls stay observable. The first response's rejection is recorded as
a failed agent event carrying the validation message; the correction is recorded
as its own event with real start and completion times. Both bind to the active
attempt, and usage from both calls accumulates through the normal response-
boundary accounting.

**The single-agent baseline gets the same allowance.** R23's wording names the
Lead, but giving a second attempt at valid provenance to only one architecture
would hand that architecture an advantage the comparison would then measure.
`run_generalist` uses the identical mechanism on `GeneralistResult`.

Audit provenance failures are deliberately *not* corrected. `AuditEvidenceError`
stays terminal: the audit is a preflight whose claims the rest of the run builds
on, and decision 0009 already refuses to persist it unsupported.

The configured allowance is frozen into the benchmark manifest's
`run_configuration.parameters`, so it is covered by the declaration digest and
changing it requires a new manifest version before paid execution.

## Consequences

- A recoverable citation error costs one extra model call instead of a whole
  run. An unrecoverable one still fails, explicitly.
- Cells that complete may have made two Lead calls. Cost and latency
  observations reflect that honestly; the manifest records the allowance that
  made it possible.
- The correction cannot repair anything that needs new analysis. If a claim has
  no supporting execution anywhere in the run, the only correct outcomes are
  removing the claim or failing — both of which the instructions ask for.

## Verification

See `src/agents/correction.py`, `src/agents/lead.py`,
`src/agents/generalist.py`, and `tests/test_evidence_correction.py`.
