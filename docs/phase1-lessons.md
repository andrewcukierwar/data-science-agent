# Phase 1 MVP hardening: decisions, failures, and handoff

Last updated: 2026-08-17

This is the durable handoff for the Phase 0 foundation and Phase 1 canonical
MVP hardening work completed in this development thread. It records explicit
engineering decisions, observed failures, fixes, verification commands, and
remaining risks. It deliberately excludes credentials, private chain-of-thought,
and evaluator-only expected numeric values from any model-visible surface.

The focused architecture records are indexed in
[`docs/decisions/`](decisions/README.md). `PROJECT_PLAN.md` remains the product
architecture and roadmap; this file explains what implementation experience
taught us and why the current guardrails exist.

## Executive status

- Current phase: **Phase 1: Multi-Agent MVP**. Phase 2 has not begun.
- Phase 0 deterministic infrastructure is implemented: workspaces, ledger,
  artifact provenance, bounded DuckDB, Docker Python, synthetic generation,
  tests, and CI.
- The five-agent lifecycle exists: mandatory Auditor, Lead manager, Analyst,
  Statistician, and Critic, with bounded follow-up/remediation.
- The canonical synthetic funnel is internally coherent and evaluator truth is
  derived from model-visible Q1/Q2 relationships.
- A persisted workspace can be evaluated offline with zero model calls.
- Live tests and the canonical run remain opt-in and are excluded from normal
  CI.

The most important current warning is that `.runs/canonical-mvp/canonical-q2-mvp`
is a mutable path. An earlier Terra run discussed in this thread was overwritten
by a later `--force` Luna run. The current on-disk workspace is therefore not
the earlier Terra regression snapshot.

## Stable architecture contracts

### Execution and evidence

- Inputs and docs are copied into each workspace and are read-only.
- SQL uses validated DuckDB relations registered from approved Parquet inputs;
  agents do not use filesystem paths or arbitrary `read_parquet()` calls.
- `inspect_relations` is the schema contract for Auditor, Analyst, and Critic.
- Python runs in a network-disabled, non-root, resource-constrained Docker
  container and reads raw inputs at `/workspace/inputs`.
- Python does not share DuckDB's registered connection/views.
- New or modified Python outputs in approved directories become explicit,
  checksummed evidence tied to that execution. Unrelated pre-existing files do
  not.
- Artifacts are relative, confined to `working/` or `outputs/`, protected from
  traversal/symlink escape, and not silently overwritten.
- Material quantitative findings cite exact returned evidence references.
- Hard-coded `VALUES` SQL cannot be sole evidence for a material conclusion.

### Agent ownership and permissions

- Lead owns the objective and answer but has no SQL/Python.
- Analyst and Statistician are bounded agents-as-tools and cannot delegate.
- Data Auditor runs once as mandatory preflight and is not a Lead tool.
- Critic is mandatory lifecycle validation, not analytical-specialist budget.
- Nested roles use task-local state; concurrent specialists cannot leak roles.
- Specialist local finding IDs are namespaced at persistence, such as
  `analyst:F1` and `statistician:F1`.

### Orchestration

- Lifecycle: workspace/ledger -> `RUNNING` -> audit -> Lead -> bounded
  objective-critical continuation -> completion check -> Critic -> targeted
  remediation -> Critic re-review -> `COMPLETED` or constrained `BLOCKED`.
- A Lead that sets `follow_up_analysis=true` cannot be silently finalized.
- After Critic `REVISE`, remediation goes directly to re-review; no generic
  follow-up cycle is inserted between them.
- Atomic shared ledger reservations prevent concurrent budget overshoot.
- Remediation-only budget/turn failures preserve the last valid candidate and
  validation in a constrained report rather than erasing the analysis.
- Initial audit or Lead failures remain fatal because no usable candidate exists.

### Current limits

| Limit | Value |
| --- | ---: |
| Lead turns | 16 |
| Data Auditor turns | 12 |
| Analyst turns | 10 |
| Statistician turns | 10 |
| Critic turns | 8 |
| Lead-delegated specialist invocations | 12 |
| SQL executions | 30 |
| Python executions | 20 |
| Critic loops | 2 |
| Charts | 4 |

These are configurable and intentionally layered: role turns do not replace
SQL/Python/specialist/Critic/chart budgets.

## Canonical analytical contract

The user asks:

> Why did profitability decline in Q2, and what should the company do about it?

The agent receives only raw scenario data, `business_definitions.md`, and that
question. It never receives the clean baseline, scenario definition, evaluator
expected values, or expected conclusion.

### Acquisition funnel semantics

The current session schema is an acquisition funnel:

```text
session_id, session_date, channel, device, converted, customer_id
```

- Every acquired customer corresponds to exactly one converted acquisition
  session on acquisition date with the same channel and device.
- Converted sessions carry `customer_id`.
- Non-converting sessions are anonymous and intentionally have null
  `customer_id`; this is not a data-quality defect.
- Converted sessions reconcile exactly to acquired customers by period/channel.
- Anonymous non-converting traffic is generated separately.

The canonical transformation changes the coherent funnel rather than applying
independent session and customer manipulations. The exact small configuration is
seed 42, 1,000 customers, 4,000 orders, 8,000 sessions, 4 products, 365 days.

### Profitability definition

The reporting metric is **90-day acquisition-cohort reporting contribution
profit**:

```text
SUM(net_revenue - cogs)
for orders from acquisition_date through acquisition_date + 90 days
for customers acquired in the reporting period
minus marketing_spend for the matching acquisition period/channel
```

Q1 and Q2 use the same observation window. A calendar-order-date profit
calculation is a different estimand even if a model gives it the same human
label. `MetricDefinitionContext` preserves population, date basis, observation
window, numerator, denominator, and definition reference during remediation.

### Generic analytical closure

No prompt contains a canonical answer, but generic guidance requires:

- profitability: net revenue, COGS, contribution before marketing, marketing
  spend, reporting contribution profit, largest relevant segment, downstream
  customer value, and explicit material non-drivers;
- acquisition economics when material: spend -> sessions -> conversion ->
  acquired customers -> CAC -> downstream LTV/value;
- grain safety: aggregate each fact table to a common reporting grain before
  joining and reconcile totals before/after material joins;
- explicit reporting-period boundaries—never classify every non-Q1 row as Q2;
- distinction between observed accounting/funnel mechanism and unsupported
  causal explanations for why an upstream metric changed.

## Structured metric and evidence flow

The intended flow is:

```text
SpecialistResult
  -> ledger
  -> LeadResult
  -> deterministic metric compilation
  -> CriticCandidate
  -> Critic completeness/validation
  -> report
  -> offline canonical evaluator
```

`MetricComparison` carries generic identity, dimensions, periods, comparison
type, value, unit, evidence, and optional definition context. It does not carry
scenario-specific evaluator IDs.

The deterministic compiler normalizes aliases and periods/units, removes a
redundant dimension prefix from the key, merges evidence on consistent repeated
measurements, exposes material conflicts, and lets corrected remediation values
supersede stale values. The final compiled set—not an arbitrary ledger-wide
union—is intended to be the single structured source consumed by Lead, Critic,
report, and evaluator.

For canonical evaluation, exact normalized dimensions are preferred. If no
exact match exists, compatible supersets can corroborate one another. More than
one consistent observation is not itself a failure; materially conflicting
values are. Numeric tolerances remain evaluator-only and unchanged.

Semantic evaluation must distinguish an asserted mechanism from a possible
future investigation. Mentioning “conversion” as a hypothesis is not credit for
showing that conversion declined and explains acquisition deterioration.

## Failure history and what each failure taught us

| Symptom | Root cause | Durable fix or invariant |
| --- | --- | --- |
| CI failed before tests: “Caching for 'uv' is not supported.” | `actions/setup-python` used `cache: uv` while `astral-sh/setup-uv` already owned uv caching. | Removed setup-python uv cache; deterministic CI uses setup-uv caching. |
| Live run had no requests/tool events. | Codex host sandbox could not reach `api.openai.com`; separate organization/credit issues also occurred. | Treat host API access as an explicitly approved live operation; do not modify Docker's no-network boundary. |
| Agent output rejected by SDK schema. | Strict structured-output/schema configuration was incompatible. | Use typed SDK output schemas with tested construction; no API calls in unit tests. |
| Auditor exhausted a small turn limit. | It guessed `spend_date`, used `column_type`, and expected SQL views inside a fresh Python DuckDB connection. | Added `inspect_relations`; documented SQL registered views vs isolated Python files; preferred audit workflow; Auditor limit 12. |
| Canonical “conversion decline” only moved channel attribution. | Injector relabeled customers from Meta to Organic while keeping their orders. | Remove would-be acquisitions and dependent orders with referential integrity. |
| Session conversion could not cause acquisition. | Sessions belonged to already-acquired customers and occurred after acquisition. | Redefined sessions as the acquisition funnel and reconciled converted sessions to customers. |
| Small fixtures had absurd CAC/spend. | Marketing spend stayed at 50k-customer scale. | Scale spend with configured company size while retaining reasonable default economics. |
| False one-cent audit warnings. | `net_revenue` was rounded independently from its documented components. | Construct rounded components so gross - discount - refund equals net revenue exactly. |
| Customer LTV had zero variance. | One economic realization was reused across large customer/order cycles. | Generate order-level timing/product/quantity/price/discount/refund/COGS variation while balancing exposure. |
| Analyst and Statistician both emitted `F1`. | Models independently chose local IDs but ledger expected global uniqueness. | Deterministically namespace IDs by specialist role. |
| Nested direct vs Lead specialist calls persisted differently. | Persistence logic existed in standalone wrappers but not shared nested hooks. | Normalize specialist persistence and generated-artifact registration across invocation paths. |
| Critic could not directly resolve cited evidence. | It had workspace/SQL/Python but no bounded reference resolver. | Added `inspect_evidence(ref)` for tool-event/artifact resolution. |
| Python-created CSV/chart could not be cited exactly. | Execution result lacked file-delta provenance. | Detect approved new/modified files, hash/size/register them, associate refs with the ToolEvent, and return concise refs. |
| Pre-existing file masqueraded as executed evidence. | Path existence was mistaken for execution lineage. | Only files changed by that execution or explicitly registered via approved artifact flow receive execution provenance. |
| SQL usage reached 24/20. | Concurrent calls performed check -> execute -> increment. | Atomically reserve one unit on the shared ledger before execution; no double counting in wrappers/services. |
| Lead context remained Analyst; remediation failed “run_lead requires a Lead AgentRunContext.” | Shared mutable nested role stack and shared restoration token raced across tasks. | Use `ContextVar` task-local scopes and lifecycle role invariants; restore on every exception path. |
| Remediation failure discarded a usable candidate. | Runner treated late bounded failure as fatal. | Persist constrained `BLOCKED` report with last candidate, unresolved Critic issues, and stop reason. |
| Critic could be blocked by specialist budget. | Mandatory Critic calls were double-counted as Lead specialist invocations. | Specialist budget means Lead analytical delegation only; Critic uses `max_critic_loops`. |
| Lead identified a material follow-up then finalized. | `follow_up_analysis` was advisory and CriticCandidate omitted open/follow-up state. | Orchestrator enforces bounded continuations; CriticCandidate includes answer, hypotheses, open questions, follow-up state/rationale. |
| Critic passed “where” without answering “why.” | Completeness checks saw evidence correctness but not available upstream causes. | Critic rejects material, answerable unresolved mechanisms and acquisition decompositions that stop early. |
| Profit analysis multiplied spend. | Period/channel spend was joined directly to customer/order rows and summed. | Aggregate facts to common grain first; reconcile row counts/totals around joins. |
| Q2 included Q3/Q4. | Code treated every period not equal to Q1 as Q2. | Require explicit boundaries/quarter inclusion and cohort reconciliation. |
| Remediation reversed the profit conclusion. | Specialist silently changed from 90-day acquisition-cohort profit to calendar-order-date profit. | First-class metric definition context; remediation keeps estimand fixed unless Critic flags it. |
| Final Lead omitted specialist-computed metrics. | Lead had to reconstruct structured values from prose. | Specialists return comparisons; deterministic selection/compilation carries material comparisons through synthesis. |
| Equivalent labels caused duplicate or stale metrics. | Keys included segments (`meta_cac`) and aliases/units varied. | Normalize generic key aliases, dimension prefixes, period labels, and safe unit aliases; remediation supersedes stale identity. |
| Consistent duplicate CAC estimates failed acceptance. | Evaluator treated multiple matches as ambiguity. | Prefer exact dimensions; reconcile consistent compatible observations; fail only material conflict. |
| LTV exact and more-specific observations both matched expected channel. | Expected dimensions were matched as a subset without deterministic preference. | Exact-dimension match wins; compatible supersets are fallback corroboration. |
| Semantic mechanism check failed around a currency decimal. | Sentence splitting broke at the decimal point in a value such as `$5,235.57`. | Decimal-safe sentence segmentation and assertion-level semantic checks. |
| Report metadata was stale. | Elapsed/cost were finalized after rendering. | Finalize model identity, usage, elapsed, and cost before report generation. |
| Charts existed but were hard to find in report. | Final report lacked a dedicated artifact section. | Render Lead-listed chart artifacts under Supporting Visualizations. |

## Current persisted workspace: verify timestamps, do not trust the fixed name

At the time this handoff was written, these files had modification time
**2026-08-17 21:43:16 -0400**:

- `.runs/canonical-mvp/canonical-q2-mvp/state/analysis_ledger.json`
- `.runs/canonical-mvp/canonical-q2-mvp/outputs/report.md`

They belong to a **gpt-5.6-luna** run, not the earlier Terra run discussed in the
thread. The current ledger is:

- status: `BLOCKED`;
- final Critic: `REVISE` after 2 critic loops;
- elapsed: about 276.2 seconds;
- model requests: 74;
- total tokens: 607,889;
- SQL executions: 30;
- Python executions: 14;
- specialist invocations: 10;
- estimated cost: about $0.0956 using the configured Luna rates;
- report: `outputs/report.md`, titled “Constrained Analysis Report.”

The current report contains the useful funnel evidence—Meta spend up, sessions
approximately flat, conversion and acquired customers down, CAC up, and no
detectable LTV movement—but remains constrained because Critic considered
follow-up state unresolved. Offline evaluation currently reports:

```text
run did not complete successfully
final Critic validation did not pass
final analysis does not assert that Meta conversion deterioration explains the acquisition decline
```

This exact failure is the current reproducible state. Do not describe this
workspace as `COMPLETED`/Critic `PASS` without rechecking the ledger and file
timestamps.

### Historical Terra note

Earlier in this thread, a Terra run at the same fixed path was reported as
operationally successful (`COMPLETED`, Critic `PASS`) and analytically found the
canonical funnel. Work then fixed offline evaluator semantics around compatible
LTV observations and decimal-safe root-cause text. Because a later `--force`
run replaced the directory, that Terra snapshot is no longer independently
verifiable at this path. Treat its details as historical development context,
not as an archived regression fixture.

The corrective operational practice is:

- use a unique `--run-id` for each meaningful live run; or
- copy/archive a completed workspace before using `--force`;
- always select “latest” by ledger/report timestamps, not by a guessed directory
  suffix or name.

## Commands

### Deterministic setup and checks

```bash
uv sync --frozen
uv run pytest -q -m "not live"
uv run ruff check .
uv run ruff format --check .
```

If the host uv cache is not writable in a restricted environment, use a
task-scoped cache rather than changing project configuration:

```bash
UV_CACHE_DIR=/private/tmp/data-science-agent-uv-cache uv run pytest -q -m "not live"
```

### Docker integration checks

```bash
uv run pytest -q tests/test_phase0_integration.py tests/test_python_docker.py
```

Docker tests may need access to the Docker socket. They should run where Docker
is available and otherwise skip with a clear reason.

### Offline canonical evaluation (no OpenAI calls)

```bash
uv run python scripts/evaluate_canonical_workspace.py \
  .runs/canonical-mvp/canonical-q2-mvp
```

Run this before considering a paid rerun. It loads only persisted ledger/report
state and must make zero API calls.

### Paid canonical live run (manual opt-in only)

```bash
uv run python scripts/run_canonical_mvp.py --force
```

Requirements:

- `.env` or environment contains `OPENAI_API_KEY` and
  `OPENAI_DEFAULT_MODEL`;
- Docker image/runtime is available;
- the host process can reach `api.openai.com`;
- the user intentionally accepts model cost and replacement of the fixed run
  directory.

Never run this in deterministic CI. Never weaken the analysis container's
network isolation to make the host API request work. Prefer a unique run ID:

```bash
uv run python scripts/run_canonical_mvp.py --run-id canonical-q2-YYYYMMDD-HHMM
```

## Remaining risks and follow-ups

1. **Mutable canonical path.** `--force` destroyed the earlier Terra regression
   fixture. Add an archival/immutable-run convention before declaring the
   canonical workspace a long-lived benchmark artifact.
2. **Final Lead persistence.** Findings, metrics, hypotheses, validations, and
   report are persisted, but legacy/offline recommendation provenance may still
   be parsed from report Markdown rather than a standalone persisted final
   `LeadResult`. A future schema migration should persist the final typed Lead
   candidate/recommendations directly.
3. **Final-metric source discipline.** Keep testing that report, Critic, and
   evaluator consume the selected canonical metric set rather than a ledger-wide
   union of every intermediate observation.
4. **Metric normalization heuristics.** Alias, dimension, definition-context,
   and corroboration rules are generic but should be expanded only with new
   scenario regression fixtures. Do not weaken conflict detection to make a
   benchmark green.
5. **Chart budget vs artifacts.** Older run counters and registered chart
   artifacts may differ. Acceptance should require valid chart provenance and
   treat the budget counter as a hard usage invariant, not infer one from the
   other.
6. **Recovered failures in completed runs.** Failed tool events can coexist with
   a valid final run. Evaluate successful evidence and final validation, while
   retaining failed events for observability.
7. **Live-run expense.** Canonical runs can use many requests and hundreds of
   thousands of tokens. Keep them opt-in and exhaust deterministic/offline
   checks first.
8. **Unknown pricing.** Usage remains valid when a model lacks registry pricing,
   but cost will be null unless all rates are configured or overridden.
9. **No Phase 2 scope.** Do not add UI, AWS, Kubernetes, another agent framework,
   or broader scenario infrastructure until Phase 1 closure is explicit.

## What not to regress

- Do not expose evaluator-only expected values or scenario answers to agents.
- Do not give Lead SQL/Python or let specialists delegate.
- Do not expose arbitrary host files or network access to analysis Python.
- Do not require models to invent globally unique IDs.
- Do not treat duplicate consistent measurements as conflicts.
- Do not silently substitute metric populations, windows, grains, or date bases.
- Do not let concurrent budget reservations overshoot hard limits.
- Do not let specialist exhaustion eliminate mandatory Critic capacity.
- Do not mark an unvalidated candidate `COMPLETED`.
- Do not use hard-coded summary SQL as sole material evidence.
- Do not run paid/live tests in normal CI.

## High-value file map for the next session

| Concern | Files |
| --- | --- |
| Roadmap and rules | `AGENTS.md`, `PROJECT_PLAN.md`, `README.md` |
| Runtime permissions/roles | `src/agents/runtime.py`, `src/agents/tools.py` |
| Agent behavior | `src/agents/auditor.py`, `analyst.py`, `statistician.py`, `lead.py`, `critic.py` |
| Procedural guidance | `skills/data_auditing.md`, `business_analytics.md`, `statistical_analysis.md`, `critic_validation.md` |
| Runner and budgets | `src/orchestration/runner.py`, `budgets.py`, `ledger.py` |
| Usage/pricing | `src/orchestration/pricing.py`, `src/schemas/run_state.py` |
| Metric contracts | `src/schemas/metrics.py`, `src/agents/evidence.py` |
| Workspaces/execution | `src/tools/workspace.py`, `sql.py`, `python.py`, `artifacts.py`, `src/sandbox/executor.py` |
| Synthetic scenario | `scenarios/generator/generator.py`, `scenarios/injection.py`, `scenarios/definitions/` |
| Canonical evaluation | `src/evaluation/canonical.py`, `scripts/evaluate_canonical_workspace.py` |
| Paid runner | `scripts/run_canonical_mvp.py` |
| Core regression tests | `tests/test_runner.py`, `test_canonical_acceptance.py`, `test_canonical_metric_compilation.py`, `test_metric_definition_hardening.py`, `test_scenarios.py` |

## Session handoff checklist

Before changing agent prompts or paying for another run:

1. Read `AGENTS.md`, `PROJECT_PLAN.md`, this file, and the decision log.
2. Check `git status` and preserve unrelated user changes.
3. Inspect workspace ledger/report mtimes and model identity.
4. Run the offline evaluator and record its exact failures.
5. Trace `SpecialistResult -> ledger -> LeadResult -> metric compilation ->
   CriticCandidate -> report -> evaluator` for the failing field.
6. Add a deterministic regression fixture before changing behavior.
7. Run deterministic pytest, Docker tests where available, Ruff check, and Ruff
   format check.
8. Only then consider a uniquely named, explicitly approved paid live run.
