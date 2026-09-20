# Engineering and analytical-reliability audit

Date: 2026-09-18; focused verification completed 2026-09-19. Scope: the existing Phase 2 system and retained `phase2-task10-20260820-v8` experiment. This is an audit and proposed remediation plan, not an implementation or a new benchmark result.

**The benchmark demonstrates an unreliable analytical system, but its near-zero numerical score does not demonstrate that nearly every calculation was numerically wrong.** On the 18 completed cells, there are 34 numerical ground-truth checks: **32 fail with “missing numeric ground-truth finding”; two pass; none reaches an out-of-tolerance numerical failure.** Missing investigations, missing structured comparisons, and incompatible metric identities are collapsed into the same outcome. Separately, the retained evidence demonstrates serious calculation and statistical-output errors that those identity failures can conceal.

The six causes that best explain the result are:

1. **The analytical deliverable is not reliably specified or assembled.** Useful calculations remain in SQL, prose, or specialist output instead of becoming the required scoped comparisons. Runtime and evaluator disagree about metric identity, reporting periods, statistical identity, and completion requirements. All nine completed experiment cells have the correct treatment-effect estimate somewhere in typed output, yet all nine fail the numeric identity check.
2. **Successful execution is treated as evidence support, while the actual result is often unavailable to the next agent.** SQL rows are returned to the executing model but omitted from the persisted tool event. The evidence inspector does not recognize all references the provenance validator accepts. The Critic consequently repeats calculations or asks for inaccessible outputs, consuming the repair budget.
3. **The LLM reauthors routine calculations and copies numerical results into schemas without a numerical binding.** There are demonstrable wrong-grain counts, incorrect coverage checks, exclusion of zero-order customers, floating-point false alarms, and correct computed p-values copied as `1.0`. Typed schemas and valid citations do not establish calculation correctness.
4. **The Critic and remediation loop do not consistently converge on the original decision.** They sometimes identify real errors, but also invent facts, demand unavailable causal evidence, or escalate peripheral limitations. Multi-agent spends its budget validating and repairing an expanding task; single-agent can terminate on its own correct REVISE diagnosis without repairing it.
5. **Cheap profiling tasks can become effectively unbounded SQL.** Four of five timeout cells have an unfinished date-range query cross-joining large fact tables. A result-row limit is not an execution-cost limit. Invocation timeout and process-exit hardening contain symptoms without making these queries useful or interruptible at the tool boundary.
6. **The test suite validates infrastructure and evaluator contracts much more strongly than the path from business data to final numerical claims.** Calibration fixtures deliberately insert expected answers and expected identities; they establish that the evaluator can accept a constructed correct report, not that a runtime calculation produces one. Missing end-to-end analytical fixtures allowed the preceding failures to coexist with a passing suite.

The smallest credible intervention is a shared calculation-result path, a handful of deterministic analytical operations, bounded SQL execution, and a narrower completion/repair policy. More agents, more global retries, or a larger generic provenance framework are not supported by this evidence. The five-agent design has not demonstrated a quality benefit.

## Evidence and limits

Reviewed repository guidance, README, architecture/roadmap, Phase 1 lessons, Phase 2 status/results, all architecture decisions, scenario definitions/generators/invariants, benchmark execution and aggregation, offline evaluation, both runtime paths, all agent definitions/guidance, tools, ledger, schemas, evidence resolution, and relevant tests. Primary empirical evidence is the [immutable manifest](../.runs/phase2-task10-20260820-v8/benchmark.json) and its 60 retained workspaces, not just the published means.

I inspected lifecycle and evaluation records across all 60 cells and examined representative ledgers, audits, plans, hypotheses, specialist results, validation histories, SQL, Python, and reports across every scenario and both architectures. Selected safe queries and independent aggregate calculations were replayed against read-only Parquet inputs. Pathological cross-joins were inspected, not executed. No model calls or paid benchmark cells were run.

Important forensic limit: the repository does not retain complete provider conversations or SQL result rows. A saved query alone does not establish that it succeeded or what the model saw; execution status must be checked separately. Recomputed values below are identified as replay/independent calculation, not represented as original saved outputs. Failed final responses are not always recoverable in full. Therefore this audit does not assign an exact percentage of *all* 60 cells' latent calculations to each error class.

The follow-up verification compared the audited runtime, tools, schemas, evaluator, scenario and guidance files with frozen revision `b7ca12c`; these paths are unchanged. Evidentiary labels below distinguish demonstrated defects from inferred causal impact. Expected remediation benefits remain hypotheses until tested. The newly verified scenario-ID exposure is an integrity defect, not a demonstrated explanation for poor completion.

Run notation below: `scenario / M or S / rN`, where M is multi-agent and S is single-agent. The full path is:

` .runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-{scenario}-1_0-{multi-agent|single-agent}-r{N}/ `

Within it, inspect `state/analysis_ledger.json`, `working/queries/`, `working/scripts/`, and `outputs/report.md`. Links in the detailed traces point directly to representative evidence.

The frozen experiment used revision `b7ca12c`, `gpt-5.6-luna`, three repetitions per architecture/scenario, 40 SQL executions, 20 Python executions, 12 specialist invocations, three Critic loops, and a 300-second invocation bound. These are the existing experiment's conditions, not proposed new settings.

Observed outcomes remain unchanged: M completed 6, blocked 20, failed 4; S completed 12, blocked 9, failed 9. All 18 completed cells failed evaluation; 42 cells were not analytically evaluated. M's six completions are all experiments. Published known-cost means are approximately $0.0989 versus $0.0112, and mean latencies 378.2 versus 73.8 seconds. Completed-subset analytical averages are not a comparable architecture experiment because the scenario composition differs.

## What the numerical score actually measures here

The path in [evaluation/primitives.py](../src/evaluation/primitives.py) is identity selection before value comparison. `_metric_identity_matches` checks metric key, periods, comparison type, unit, and definition context; dimension selection follows. `evaluate_numeric_comparisons` cannot compare a value if selection produces no candidate.

| Observation | Interpretation |
|---|---|
| 32/34 completed-cell numeric checks say “missing numeric ground-truth finding” | No compatible final structured estimand; not direct evidence of an out-of-tolerance calculation |
| Two numeric checks pass, both canonical / S / r2 | Meta CAC and acquired-customer changes successfully survived the whole contract |
| Nine completed experiment cells contain the correct effect estimate in typed metrics or assessments | Repeated output-identity failures despite successful core arithmetic |
| COGS / S / r1 emits overall COGS change rather than the required Google comparison | Correct aggregate is not a substitute for the required segment; a real coverage/synthesis failure |
| Discount / S / r2–r3 investigate November–December | No Q1–Q2 calculation to score; partly a task-period specification defect |
| Missing-day / S / r2 concludes reporting is complete | A real incorrect analysis, not something semantic normalization should rescue |
| Experiment p-values are sometimes corrupted after correct computation | A real numerical propagation failure that identity rejection currently masks |

“Calculated correctly but not surfaced,” “did not calculate the required quantity,” and “calculated or copied incorrectly” all occur. A revised diagnostic report should separate these stages while retaining strict end-to-end success as the principal outcome. Do not retrospectively count blocked runs or prose-only numbers as official v8 passes.

**Focused verification:** Independently filtered `run_records` for `lifecycle.status == "completed"` and counted their `evaluator_result.checks` with `check_id` starting `numeric:` in both `benchmark.json` and `benchmark-rescored.json`. Each yields 18 cells, 34 checks, 32 missing-finding failures and two passes; there are no other numerical-check failures. The passes are `numeric:meta-q2-cac` and `numeric:meta-q2-acquired-customers` in canonical S r2.

For each completed experiment, an independent read-only aggregation of `experiment_observations.parquet` computed `SUM(outcome)/COUNT(*)` by assignment, then treatment minus control. The following final ledger fields match those differences within floating-point precision; every row's `numeric:checkout-treatment-effect` check fails identity selection. Indices are zero-based. This verifies the effect estimate, not the entire assessment or its p-value.

| Experiment cell | Control / treatment successes and denominators | Difference | Final typed field |
|---|---|---:|---|
| Meaningful M r1 | 400/2,000; 600/2,000 | 0.10 | `metric_comparisons[0].value` |
| Meaningful M r3 | 400/2,000; 600/2,000 | 0.10 | `metric_comparisons[2].value` |
| Meaningful S r1 | 400/2,000; 600/2,000 | 0.10 | `metric_comparisons[0].value` |
| No-effect M r1 | 500/2,000; 500/2,000 | 0 | `metric_comparisons[0].value` |
| No-effect M r2 | 500/2,000; 500/2,000 | 0 | `metric_comparisons[0].value` |
| No-effect M r3 | 500/2,000; 500/2,000 | 0 | `statistical_assessments[0].estimate` |
| No-effect S r1 | 500/2,000; 500/2,000 | 0 | `metric_comparisons[0].value` |
| Immaterial M r3 | 7,500/25,000; 8,000/25,000 | 0.02 | `metric_comparisons[0].value` |
| Immaterial S r3 | 7,500/25,000; 8,000/25,000 | 0.02 | `metric_comparisons[1].value` |

## A. Root causes and important defects

### R1 — Task, metric, and final-output contracts are misaligned

**Severity:** Critical. **Architecture:** shared. **Explains:** numerical/output-contract failures; some wasted investigation and repair.

**Evidentiary status: Directly demonstrated.** Missing final identities and conflicting task periods are observable; attributing a particular model's period choice to the specification is an inference. Period hints are available through run metadata, as qualified below.

**Affected evidence:** all completed experiments; canonical S r1–r2; COGS S r1/r3; discount S r2–r3; channel-mix S r3; both data-quality scenarios.

**Code paths:** [metrics.py](../src/schemas/metrics.py), especially `normalize_metric_period` and `metric_definition_contexts_match`; [primitives.py](../src/evaluation/primitives.py), especially `_metric_identity_matches`, `select_metric_candidates`, `evaluate_data_quality`; [scenario definitions](../scenarios/definitions/); [benchmark runner](../src/benchmark/runner.py) `_default_executor`; [Lead persistence](../src/agents/lead.py) `_reuse_specialist_metric_comparisons` and `_persist_result`.

Evidence:

- Experiments expect `experiment_conversion_effect`, dimension `experiment=checkout-v1`, periods `control participants` and `treatment participants`, absolute difference, unit `fraction`. Correct estimates arrive under names such as `binary_outcome_rate_difference`, periods `control arm`/`treatment arm`, units `proportion` or `rate`, sometimes without the experiment dimension. Those are not all safely interchangeable in general, but the raw data and retained experiment outputs establish equivalence in several inspected cases.
- Canonical S r2 emits correct-enough CAC and customer changes under recognized identities. Its LTV comparison uses `q1 2025 acquisition cohort` rather than recognized `Q1 2025`; it omits structured spend and conversion changes even though the answer gives their levels. Qualitatively correct diagnosis does not assemble a complete comparison set.
- The COGS and discount user questions say “latest reporting period”; channel mix says “latest acquisition performance change.” Inputs cover the full year. Evaluator expectations specifically require Q1 versus Q2. The runner supplies the public question, and all 60 retained `business_context` fields are null. The corresponding retained business definitions specify cohort semantics but no target Q1/Q2 comparison. However, `inspect_workspace` exposes descriptive scenario IDs, including Q2 and cause hints; the target is therefore **not wholly absent from accessible runtime information**. November–December or Q3–Q4 remains defensible from the actual question; metadata hints do not resolve its contradictory scope. Retention's “across customer cohorts” question is also underspecified.
- Rechecked all five distinct retained business-definition documents, initial-input builders, loaded agent guidance and output schemas. Canonical explicitly names Q1/Q2 and is not part of this period-mismatch claim. Generic guidance warns against treating every non-Q1 row as Q2; it does not assign Q2 to the other tasks. `ANALYST_OBJECTIVE` contains a Q1/Q2 example but is not supplied by the benchmark delegation path. Experiment definitions specify a two-sided 95% rate-difference interval and 0.05 practical threshold, but not the evaluator's exact metric/period identifiers or Cohen's h. Data-quality definitions specify coverage semantics, not required issue IDs. Charts are encouraged conditionally, not uniformly required in the runtime contract.
- `DataQualityIssue.id` is a free identifier, but evaluation requires specific issue IDs such as `missing_reporting_day`. A correct supported issue named `DQ-001` is not recognized. This confuses issue identity with issue classification.
- Existing text normalization is neither a full semantic contract nor a proof of equivalence. Adding aliases for these ten scenarios would overfit and risk accepting genuinely different populations/windows.

**Recommended fix:** Establish a small public analysis specification: requested decision, explicit reporting dates when supplied, grain/population, observation window, dimensions, numerator/denominator, and comparison operation. Let reusable calculation tools produce the resulting identity and values. Preserve an open custom-metric path. Have synthesis select computed records rather than rename/retype them. Runtime and evaluator should consume the same public meaning; evaluator-only expected values and tolerances stay private. For future scenario versions, clarify reporting dates in the task without naming the target channel or injected mechanism. Separate data-quality issue IDs from a general issue type and structured scope.

**Expected benefit:** Converts existing correct work into inspectable final comparisons and prevents wrong-period investigations; makes residual arithmetic errors measurable.

**Regression risk:** Overbroad semantic equivalence could accept wrong grains or arm direction. A fixed list of benchmark metric names could become answer leakage. Preserve strict population, period, denominator, dimension, and operation checks; do not treat absolute and relative change as synonyms.

**Tests:** Equivalent explicit dates versus quarter labels; arm labels bound to actual assignment values; reversed treatment/control rejected; overall versus segment rejected; second-order retention versus any-order conversion rejected; supported issues recognized independently of arbitrary IDs; missing target comparison remains a failure. Add tests for full final comparison coverage from generic calculation output, not hand-inserted ground truth.

### R2 — Evidence that passes provenance cannot reliably be inspected

**Severity:** Critical. **Architecture:** shared implementation, much greater observed M impact. **Explains:** operational failure and loss of useful numerical work.

**Evidentiary status: Directly demonstrated.** Successful audit events lack rows and their query aliases fail the inspector's lookup contract; retained reviews complain about these exact references. How much completion/cost would improve after repair is not measured.

**Code paths:** [SQL execution](../src/tools/sql.py) `execute`/`_build_event`; [tool adapters](../src/agents/tools.py) `run_sql`, `run_python`, `inspect_evidence`; [evidence resolution](../src/agents/evidence.py) `_event_references`/`executed_references`.

SQL's returned `QueryExecutionResult` contains rows, but the persisted successful `ToolEvent.output` contains columns, row count, limits and truncation metadata only. Query IDs and paths accepted by provenance are not all resolvable by `inspect_evidence`: exact event IDs work, a query ID is not looked up as an alias, and a SQL path returns source text. `run_sql` does not return its `tool-{query_id}` event ID directly. Python stdout is persisted in bounded form, but inspecting a script path still returns code rather than that execution's output.

In partial-latest-day M r2, Critic reviews repeatedly complain about valid audit query references being uninspectable and queries containing no output. Later reviews reproduce the principal result correctly but still block on references and scope wording. Canonical and retention runs show similar repeated reproduction. A conservative text review found output-visibility complaints in at least 14 M reviews. This is not an argument to waive evidence checks: the inspection interface really is incomplete.

**Recommended fix:** Persist bounded SQL result rows or an immutable result artifact at execution time; return the event ID and result reference; resolve accepted query/script aliases to their successful execution output. Keep source inspection available separately. Handle ambiguous/reused aliases explicitly. Extend the existing mechanism rather than introduce another provenance subsystem.

**Expected benefit:** Gives Lead/Critic the same observed result, reduces recomputation and false provenance disputes, enables forensic review and deterministic metric extraction.

**Regression risk:** Large result payloads, truncation mistaken for full data, or selecting a stale execution. Bound output; carry truncation status, query identity, and attempt identity; do not use an incomplete row sample as a population aggregate.

**Tests:** Every accepted successful reference form resolves to the same execution and values; failed/ambiguous references do not resolve as successful; dates/decimals/nulls serialize consistently; truncated result status survives inspection; Critic can inspect an audit result without executing SQL again.

### R3 — Routine computation and numerical transcription are left to the model

**Severity:** Critical. **Architecture:** shared. **Explains:** true analytical errors, misleading reports, and unnecessary remediation.

**Evidentiary status: Directly demonstrated.** Successful retained queries replay the wrong counts/ratios, and saved Python outputs disagree with typed p-values. The precise model-internal reason for transcription errors is unavailable.

**Code paths:** model-authored [Analyst](../src/agents/analyst.py), [Auditor](../src/agents/auditor.py), [Statistician](../src/agents/statistician.py), [generalist](../src/agents/generalist.py); [statistics schema](../src/schemas/statistics.py); SQL/Python adapters; `validate_analyst_result`/`validate_statistician_result`.

Concrete independent problems:

- **Wrong denominator after a join:** retention M r1's `email_fixed_maturity_metrics.sql` counts joined order rows as acquired customers, yielding 9,186 and 7,583 instead of 2,466 and 2,493. Replay gives LTV around $164 in both periods, concealing the real $611.03 to $498.42 deterioration. An earlier query counted any purchasing customer as retained instead of customers with at least two orders.
- **False clean audit:** missing-day S r2 counts `c.channel` from the expected calendar/channel grid after a LEFT JOIN instead of matched `m.channel`. Every expected date appears complete even when no actual records match.
- **False defect from floating-point equality:** channel-mix M r1's audit tests exact `net_revenue <> gross_revenue - discount - refund`. Independent replay finds 65,917 differences with maximum magnitude about `1.14e-13`, and none above $0.011. The resulting anomaly premise drives later quarantine/sensitivity work. This is not a material economic identity violation.
- **Zero-order customers disappear from LTV:** canonical S r2 groups a LEFT JOIN by customer, then takes `AVG(net_rev)` without filling null customer revenue with zero. Replay gives Meta LTV $605.9848/$605.4743 instead of $605.2476/$605.2497. Four Q1 and one Q2 acquired customers have no qualifying order. The qualitative “flat” conclusion survives, but the calculation violates the documented denominator.
- **Correct p-values become `1.0`:** meaningful M r3's JSON result contains `1.9504926019459696e-13`; its typed specialist/final assessment contains `p_value=1.0` while concluding significant and practical. Immaterial M r3 computes approximately `1.33e-6`, then persists `1.0` with a significant-but-immaterial conclusion. Its Critic reproduces a small p-value yet returns PASS. Immaterial S r3 instead emits approximately `1e-13`, also incorrect.
- **Unsupported uncertainty:** retention S r3's Python computes an Email January–June difference and small p-value, but its typed output contains `p=1`, an interval not derived by the cited script, and inconsistent practical-significance fields. Its self-critique catches problems, but the run blocks. The retained evidence establishes unsupported output, not how the model generated the interval.

The schemas constrain shapes and ranges; they do not bind the model's `estimate`, interval or p-value to an executed result. Valid references only show that some source-linked execution exists. Analyst validation also does not validate its statistical assessments as thoroughly as Statistician validation: immaterial M r3 contains an Analyst assessment citing the business-definition document as statistical evidence.

**Recommended fix:** A small deterministic layer for profiles/coverage, period/cohort aggregations and ratios, and supported statistical procedures. Return an immutable typed calculation record with input bindings, component values, scope, method, warnings and output numbers. Specialists/Lead select record IDs; prose remains model-authored. For unsupported methods, retain SQL/Python but require explicit result serialization and inspectable inputs. Validate mathematical consistency where it is method-independent; do not impose one test's identities on every statistical method.

**Expected benefit:** Prevents repeated known errors and transcription loss; reduces SQL retries and specialist calls; makes numerical accuracy independently testable.

**Regression risk:** Incorrect business mappings can make deterministic wrong answers repeatable. The tool must require explicit source/grain/window/denominator choices and expose them. It must not silently impute, choose attribution rules, redefine contribution profit, or assume independence.

**Tests:** Hand-calculated datasets with repeat orders and nonbuyers; unequal channel/segment sizes; duplicated dimension joins; zero denominators; null revenue; boundary-day orders; incomplete cohorts; daily expected grids versus sparse events; harmless floating error versus real cents-level discrepancies; known binary experiments and continuous-outcome tests; exact result-to-specialist-to-Lead numerical preservation. Test incorrect mappings are rejected or clearly surfaced.

### R4 — Statistical/final-state propagation has holes, including stale history

**Severity:** High. **Architecture:** shared renderer/evaluator, additional M handoff exposure. **Explains:** numerical/statistical output failure; latent stale-state failures.

**Evidentiary status: Directly demonstrated** for omitted fields and final/history mixing. **Plausible but not yet demonstrated** that stale assessment reuse caused a specific v8 numerical failure. An in-memory correction of meaningful M r3's final p-value still leaves both corrected and historical `1.0` assessments in `_statistical_assessments`; no retained state was changed.

**Code paths:** [CriticCandidate](../src/schemas/validation.py); [runner](../src/orchestration/runner.py) `_candidate` and `_render_report`; [Lead](../src/agents/lead.py) `_reuse_specialist_metric_comparisons`/`_persist_result`; [ledger](../src/orchestration/ledger.py) finding upserts, replacements and specialist append; [primitives.py](../src/evaluation/primitives.py) `_statistical_assessments`/`evaluate_statistics`.

`CriticCandidate` has metric comparisons but no statistical-assessments field. The final renderer does not render the typed statistical assessments; it also omits finding caveats, metric definition context, and detailed audit issues. Thus even a correct typed assessment need not reach the Critic or the report's uncertainty explanation. The Lead has a metric reuse helper but no equivalent reliable preservation of selected statistics.

Meanwhile, evaluation collects both final assessments and *all historical specialist assessments*, deduplicating by full JSON, then requires exactly one matching assessment. A corrected assessment can coexist with a superseded one and be marked ambiguous. Final metrics use a selected set; statistical history does not. Findings are upserted rather than replaced as a final selected set, and evaluation's analysis text includes ledger findings; withdrawn material can still affect language/root-cause checks.

The statistical-history defect is demonstrable from code, but I do not attribute the current experiment's numeric collapse to it: earlier identity mismatches often prevent those records from matching at all. Likewise, history retention alone does not prove a particular final number was stale.

**Recommended fix:** Pass and render the exact selected final calculation/assessment records, including uncertainty, assumptions, scope and audit limitations. Keep historical records as history; evaluate the selected final set and explicit unresolved conflicts. Apply the same statistical provenance checks regardless of producing role. Require Critic claims/citations to resolve too.

**Expected benefit:** Prevents omission of correct results, makes statistical contradictions visible, avoids future “correction creates ambiguity” failures.

**Regression risk:** Selecting only final records could hide an unresolved contradiction. Require an explicit supersession/selection decision; preserve raw history and conflict diagnostics without treating every historical value as a current claim.

**Tests:** Corrected assessment supersedes an earlier one; two unresolved final estimates still fail; final candidate/report/evaluator use identical values; uncertainty/caveats are rendered; invalid Analyst statistical evidence is rejected; withdrawn findings do not remain final claims; fabricated Critic evidence references are rejected.

### R5 — Critique and repair consume effort without a stable completion target

**Severity:** High. **Architecture:** principally M; S has a separate terminal self-critique limitation. **Explains:** operational failure and delayed/misguided analysis.

**Evidentiary status: Strongly supported.** Review counts, fabricated channel references, contradictory objections and terminal self-REVISE behavior are directly demonstrated. Their aggregate causal contribution, and the benefit of a narrower policy, require an ablation; not every REVISE is erroneous.

**Code paths:** [Critic](../src/agents/critic.py) prompt, completeness gates, `run_critic`, `persist_validation_result`; [multi-agent runner](../src/orchestration/runner.py) review/remediation loop and definition-change permission; [single-agent runner](../src/orchestration/generalist_runner.py) self-critique handling; Lead and generalist instructions.

Across retained ledgers, M records 57 REVISE and six PASS reviews, including 11 deterministic completeness reviews. Eleven M cells consume all 40 SQL slots; seven consume all 12 specialist invocations; three consume all 20 Python slots. No S cell exhausts these global tool budgets. These are symptoms of repeated work, not evidence that all caps should rise.

Examples of mixed Critic quality:

- Retention M r1 correctly detects the first retention-definition error, but subsequent remediation introduces wrong grain and unsupported maturity assumptions. Later requests include campaign/audience analysis unsupported by the available schema.
- Partial-latest-day M r2 twice calls the missing channel “Paid Social”; the actual missing channel is Affiliate. It also emits nonexistent artifact/event references in its own validation issues.
- Canonical M r1's second review calls a Meta contribution difference incorrect while repeating the same approximately `-$268,648.21` value. Other objections are legitimate, so this is not a claim that the entire candidate deserved PASS.
- No-effect M r1 reaches PASS only after three reviews, with repeated demands around randomization/external generalization despite the documented randomized design and a correct zero observed effect. Honest limits should remain in the report without automatically blocking the enrolled-population conclusion.
- S self-critique returning REVISE immediately produces a constrained terminal run. Retention S r3 recognizes errors but cannot repair them through that final-output path, even with tool budget remaining.

**Recommended fix:** Tie blockers to the original objective and selected evidence. Require each blocker to identify the failed analytical claim, inspectable evidence, and a feasible correction. Distinguish unsupported factual claims from acknowledged limitations and optional follow-up. Give both architectures a bounded, in-budget route to correct deterministic finalization errors before terminal output. Do not add unlimited retries or automatically turn REVISE into PASS. Share general analytical procedures across architectures so the comparison isolates specialization rather than unequal prompt guidance.

**Expected benefit:** Fewer repeated queries and false blockers; true detected errors can be corrected; stopping becomes a decision about delivered analysis.

**Regression risk:** Narrowing critique can suppress legitimate objections. Use adversarial fixtures where the correct answer is to block, and track error detection alongside completion.

**Tests:** Wrong grain/denominator must block; acknowledged external-validity limits alone must not prevent a supported within-sample result; missing requested target comparison must block; impossible follow-up is identified as a limitation; fabricated Critic facts/citations fail; one correction resolves a specific error without redoing the audit; self-REVISE can repair within the original cap and still terminates when unresolved.

### R6 — SQL execution bounds do not bound computation

**Severity:** High. **Architecture:** shared. **Explains:** operational timeouts and wasted resources.

**Evidentiary status: Strongly supported.** All four queries and absent terminal events coexist with recorded invocation timeouts. No execution profiler establishes exact time attribution. Removing the cross-product from independent date profiling is directly demonstrated below; production cancellation and agent adoption remain untested.

**Code paths:** [SQL service](../src/tools/sql.py) `_execute_sql`; [usage/invocation wrapper](../src/agents/model_usage.py) `run_agent_with_usage`; benchmark CLI timeout/shutdown behavior documented in ADRs 0018 and 0021.

Four of five timeout cells contain saved, unfinished date-range queries:

| Cell | Query | Problem |
|---|---|---|
| COGS M r3 | `date_ranges.sql` | Cross-joins orders, customers, sessions and marketing spend to find independent min/max dates |
| Discount M r2 | `date_ranges.sql` | Cross-joins customers, sessions and marketing spend |
| Partial-latest-day S r2 | `latest_source_dates.sql` | Cross-joins customers, orders and sessions |
| Partial-latest-day S r3 | `source_latest_dates.sql` | Cross-joins all four sources |

At nominal source sizes, the three-fact-table product alone is around `1.2e16` rows. Saved queries have no completed execution event. This is strong evidence of the immediate workload associated with those timeouts, not a profiler proof of every elapsed second. The fifth timeout, immaterial M r1, is not explained by this pattern.

Focused verification rediscovered DATE/TIMESTAMP columns from each affected workspace's Parquet schema and independently ran `COUNT(*)`, `MIN(date_column)` and `MAX(date_column)` per relation, without a scenario-name or column-name mapping. All four profiles completed in approximately 0.005–0.020 seconds locally: 50,000 customers, 240,000 orders, 1,000,000 sessions, and 1,825 marketing rows (1,824 for partial-day sources), with Jan 1–Dec 31 bounds. Scalar comparisons then give source lags without joining facts. This removes the multiplicative workload for the requested operation; it does not prove future agents cannot author another expensive query. These timings are a local feasibility check, not a benchmark speedup estimate.

`fetchmany(max_rows + 1)` bounds returned rows after execution; it does not bound aggregate work. Connections are correctly closed in `finally` when execution returns. An asynchronous invocation timeout cannot by itself reliably stop a running synchronous DuckDB worker; exiting the benchmark subprocess avoids a shutdown hang but does not solve the analytical operation.

**Recommended fix:** Add a deterministic per-relation profile for row counts, keys and requested date bounds; compute independent summaries independently. Add a tested SQL deadline/interrupt or isolated worker termination mechanism with resource limits. Preserve failure records and release connections on cancellation. Do not solve this by allowing the cross-join more time.

**Expected benefit:** Removes a repeated catastrophic failure from cheap audit work and returns an actionable tool error before the entire agent invocation expires.

**Regression risk:** Tight limits may reject legitimate large joins. Make limits explicit, validate representative workloads, and preserve raw SQL for justified joins; avoid a blanket ban on CROSS JOIN, which is valid for small expected grids.

**Tests:** Date profiling across differently sized sources; safe small calendar cross-join; intentionally expensive query cancellation; connection/worker cleanup after cancellation; subsequent query succeeds; budget charged once; terminal event emitted without late mutation of a finalized workspace.

### R7 — Tests do not exercise the critical data-to-answer path

**Severity:** High. **Architecture:** shared development process. **Explains:** why defects persist; not an independent runtime mechanism.

**Evidentiary status: Directly demonstrated** for the inspected calibration gap and passing-suite/incorrect-analysis coexistence. The claim that additional analytical fixtures will improve autonomous investigation is **Plausible but not yet demonstrated**; deterministic tool correctness and model investigation quality need separate validation.

**Code/tests:** [task6 calibration](../tests/test_task6_calibration.py), [scenario statistics tests](../tests/test_data_quality_statistics_scenarios.py), [canonical compilation tests](../tests/test_canonical_metric_compilation.py), [SQL tests](../tests/test_sql.py), specialist/runner/provenance tests.

The calibrated “correct” fixture copies `registration.evaluation_spec.ground_truth` into metric records and expected statistics into assessments. Its evidence query counts a tiny orders table; the chart fixture is placeholder bytes. This is reasonable for testing evaluator plumbing, but the cited query does not compute the claimed business values. Such fixtures cannot detect incorrect cohort arithmetic, false audit results, or result-to-report number changes. Existing normalization/compilation tests are useful, but often begin with the exact identities expected by evaluation.

**Recommended fix:** Keep those unit fixtures and add a small independent suite that computes from hand-checkable input data through the actual tool/result/final-report path. Add metamorphic properties and retained-regression examples. Keep expected answers in tests/evaluator only.

**Expected benefit:** Establishes that the proposed deterministic layer and result binding work before paying for model runs.

**Regression risk:** Reusing the implementation as its own oracle reproduces the same bug. Use independent manual/rational examples and alternative reference calculations. Do not claim an LLM is reliable because a mocked runner passed.

**Tests:** See the validation matrix below; include both accepted correct answers and plausible incorrect answers with valid provenance.

## Evaluation defects and mismatches, separately from agent failures

These require explicit versioning and preserved v8 results. None justifies loosening numeric tolerances, removing substantive requirements, or declaring old runs successful.

| Issue | Severity / affected architecture | Independent reason it is incorrect or misaligned | Fix, benefit, risk and regression test |
|---|---|---|---|
| Target period versus “latest” question | High / shared | Annual data and the business question do not identify Q2 as latest. Descriptive run-ID leakage supplies hints but is not a coherent task specification. | Version public questions with reporting dates only and remove evaluator metadata hints. Benefit: evaluates the requested investigation. Risk: accidentally revealing the mechanism; prohibit channel/cause hints. Test public task scope against expected period metadata. |
| Arbitrary issue ID treated as defect type | High / shared | Renaming a correctly evidenced issue must not change defect recall. `evaluate_data_quality` uses exact IDs. | General issue type plus scope; keep evidence/defect requirements. Test arbitrary IDs, wrong types, wrong dates and unsupported claims. |
| Negation-blind unsupported-claim regex | High / shared | “This is an observed association, not causal proof” and “does not prove a literal null effect” are cautions, yet forbidden token regexes reject them. Observed in canonical S r2 and no-effect M r2/S r1. | Deterministic scoped negation handling with adversarial affirmative/negative fixtures; preserve rejection of actual overclaims. Risk: broad exceptions allow “not only proves”; test it. |
| Chart required offline but not consistently in runtime task contract | High / shared | All 18 completed cells fail `task_completeness:chart`; `TaskCompletenessPolicy.require_chart=True`, while runtime gates chart creation principally on an explicit visualization request. | Preserve the existing requirement and make the generic deliverable explicit and enforced for both architectures. Do not simply drop the gate. Test requested/required chart inclusion, valid artifact and rendered reference. |
| Textual metric/statistical identity and assumption matching | High / shared | Equivalent bound estimands can fail because of unconstrained wording, while incomplete identity can conceal wrong arithmetic. Statistical assumptions use exact strings. | Public semantic operation/identity contract; evaluate meaning from structured fields and tool-bound inputs. Test equivalent wording and genuinely different estimands. Do not silently accept any “similar” metric or any statistical method. |
| Historical statistics counted as competing final answers | Medium / shared evaluator, mostly M exposure | A corrected prior answer is history, not necessarily a second asserted final estimate. | Evaluate selected final records, retain conflict checks. Test superseded versus unresolved contradictions. Latent in this run because identity matching often fails first. |

The numerical ground-truth calculations inspected are supported by the source data: the principal effects, data gaps, and experiment counts are present. I found no basis to blame the synthetic values or widen tolerances. The primary evaluator problem is the task/output boundary and attribution of failure, not a demonstrated global error in the ground-truth arithmetic.

Statistical method requirements also need to be explicit enough to distinguish a wrong answer from a valid different procedure. For example, a requested “effect size” does not uniquely specify Cohen's h. This should be settled as a general, versioned statistics contract before a new experiment, not adjusted after observing its results.

### E1 — Descriptive scenario IDs cross the model-visible boundary

**Severity:** High. **Architecture:** shared. **Evidentiary status: Directly demonstrated** as an accessible tool response; whether each model used the hint is not recoverable. **Affected runs:** all 60 default IDs embed the scenario name. [Benchmark `_default_run_id`](../src/benchmark/runner.py) concatenates it, and [tools `inspect_workspace`](../src/agents/tools.py) returns `run_config.run_id`. Examples disclose `cogs-q2-margin-deterioration`, `missing-reporting-day`, `no-effect-ab-experiment`, and `significant-but-immaterial-ab-effect`.

This is a benchmark-integrity defect, not evidence of a cause of poor numerical accuracy or completion. It can contaminate apparently successful qualitative reasoning and invalidates any claim that *all* scenario hints were inaccessible. **Fix:** keep immutable internal IDs, but omit them or expose opaque identifiers at model-facing boundaries, including error/path responses. Clarify task periods explicitly rather than rely on these hints. **Benefit:** measures discovery from legitimate task/data. **Risk:** breaking reference resolution or inadvertently removing legitimate task context. **Tests:** sentinel scenario names/hidden labels never appear in tool responses, prompts or errors; opaque references still resolve; both architectures retain identical legitimate inputs. Require a new benchmark/configuration declaration; preserve v8 untouched. This was the material High issue missing from the initial audit.

## Scenario-by-scenario comparison

Values below are evaluator targets or independent calculations from retained data, rounded for reading. Existing tolerances remain unchanged. Completion counts are M/S out of three each.

| Scenario | Required truth / data check | What agents actually did | Primary failure classification |
|---|---|---|---|
| Canonical Q2 profitability (0/2) | Meta conversion and acquired customers about -18%; spend +7%; CAC about +30.5%; 90-day LTV stable. Meta contribution $251,676.51 to -$16,971.70. | S r1/r2 identify Meta. r2 surfaces CAC/count successfully but omits spend/conversion comparisons and uses unmatched LTV scope; its LTV excludes nonbuyers. M repeatedly repairs useful analysis until blocked. | Output coverage/identity, synthesis, small real denominator error, evidence inspection and critique convergence |
| Channel mix (0/1) | Meta share about -0.0800 and Organic +0.0800; total customers +1.11%, within the stable-volume tolerance. | S r3 compares Q3–Q4 and reports about +0.0266 Meta/-0.0267 Organic under a different metric identity. M r1 follows a false floating-point economic anomaly into sensitivity work; all M block. | Task-period mismatch, investigation, false audit, orchestration |
| COGS deterioration (0/2) | Google COGS/net revenue 0.430965 to 0.550965, +0.12; customers +1.11%. Google profit $280,036.38 to $62,087.88. | S r1 identifies COGS/Google but emits overall ratio change around +0.0281, not Google's +0.12. S r3 analyzes November–December. M r3 times out on date profiling. | Segment coverage/synthesis, task period, execution reliability |
| Discount/refund deterioration (0/2) | Affiliate discount/gross revenue about +0.05; refund/gross revenue about +0.04; customers +1.12%. | S r2/r3 investigate November–December, not the affected Q2 comparison. M work finds Affiliate patterns but becomes entangled in cohort/calendar definitions; r2 times out on cross-joined dates. | Task period, metric semantics, repair convergence, execution |
| Meaningful A/B effect (2/1) | 400/2,000 control versus 600/2,000 treatment; +0.10, CI about [0.07334, 0.12666], p about 1.95e-13; meaningful versus 0.05 threshold. | Completed cells compute the effect. All miss expected structured identity. M r3 corrupts p to 1; M r1's final selected assessment instead concerns external generalization. M r2 and S r2/r3 fail provenance boundaries. | Output identity, statistical propagation/synthesis, provenance failure |
| Missing reporting day (0/2) | One marketing date missing: 2025-05-31; 364 dates and 1,820 channel-date rows. | S r2's grid query falsely reports complete coverage and self-PASSes. S r3 recognizes a gap but does not emit the required missing-day metric/recognized issue type. M detect limitations but remain blocked. | True audit computation error; output contract; decision framing; orchestration |
| No-effect A/B (3/1) | 500/2,000 in each arm; difference 0, p=1, CI about ±0.02684; no demonstrated improvement. | Completed cells correctly compute the effect but use unmatched identities. S r2/r3 fail source lineage for statistical findings; some completed reports are penalized for explicitly saying the result does not prove a null effect. | Output identity, evidence chain, evaluator negation defect, unnecessary critique |
| Partial latest day (0/0) | 2025-12-31 marketing coverage is 4/5=0.8; Affiliate missing; preceding dates complete. | M r2 gets the principal result, but reviews invent Paid Social and dispute result accessibility; final metric is 4 covered channels rather than coverage fraction. S r1 fails provenance; r2/r3 time out on cross-joins. | Evidence propagation, synthesis, critique error, execution |
| Retention deterioration (0/0) | Email second-order-within-90-days retention 0.98621 to 0.69033, about -30%; count/CAC stable; LTV $611.03 to $498.42. | M r1 initially uses any purchase, then repair counts orders as customers and introduces an unsupported cutoff. S investigations find deterioration but self-REVISE, including wrong statistical output and spend allocation issues. | Wrong grain/metric semantics, methodology, repair failure |
| Significant but immaterial A/B (1/1) | 7,500/25,000 versus 8,000/25,000; +0.02, CI about [0.011894, 0.028106], p about 1.33e-6; below 0.05 business threshold. | Both completed cells estimate +0.02 but miss expected identity. M r3 computes the right p then copies 1; Critic reproduces it correctly but PASSes. S r3 emits about 1e-13. Other cells include timeout, provenance and self-revision failures. | Statistical propagation/calculation, Critic blind spot, output identity, operational failures |

The business changes concern specific channels and cohorts. An overall profitability change, a channel's absolute profit change, and the hypothesized mechanism's relative change are different quantities. A stronger contract must preserve these distinctions rather than reward keyword mentions.

## Representative end-to-end traces

### T1: Correct core experiment computation, corrupted statistics, PASS, evaluator rejection

Cell: meaningful-ab-treatment-effect / M / r3. [Ledger](../.runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-meaningful-ab-treatment-effect-1_0-multi-agent-r3/state/analysis_ledger.json), [computed JSON](../.runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-meaningful-ab-treatment-effect-1_0-multi-agent-r3/working/experiment_treatment_effect.json).

The user asks whether treatment improves outcomes; visible definitions describe a binary randomized experiment and practical threshold. Audit establishes 4,000 participants, balanced arms and usable binary outcomes. Investigation/delegation computes arm rates, then the Statistician calculates the treatment-control contrast, interval, p-value and effect size in Python. The JSON has estimate 0.1, CI [0.0733416077, 0.1266583923], p=1.95049e-13, Cohen's h=0.2319843. Specialist structured output instead says p=1.0 while retaining the significant/practical conclusion. Lead preserves that assessment and emits correctly valued metrics under other identities. Critic can reproduce rates but does not receive the typed statistical record as a candidate field; validation PASSes. The report is produced, but the renderer does not systematically expose the assessment. Offline selection fails to match the experiment estimand, producing “missing numeric ground-truth finding.”

This chain contains **successful computation, numerical transcription corruption, incomplete critique, and output identity failure**. Calling it simply a failed statistical calculation would miss the repair point.

### T2: Expected-grid audit counts the wrong side of a LEFT JOIN

Cell: missing-reporting-day / S / r2. [Query](../.runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-missing-reporting-day-1_0-single-agent-r2/working/queries/audit_marketing_calendar_v2.sql), [report](../.runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-missing-reporting-day-1_0-single-agent-r2/outputs/report.md).

The user explicitly asks whether reporting supports a reliable Q2 comparison. The model builds a calendar/channel grid, LEFT JOINs marketing spend, and counts the grid's channel values. Replay returns 365 complete dates and zero incomplete dates because the count is independent of actual matches. Audit/planning adopt the false premise that coverage is complete. Findings and final report say the comparison is supported, self-critique PASSes, and no missing-day structured comparison is emitted. Evaluation correctly rejects the missing required defect/metric.

There is no handoff to blame here. A deterministic coverage operation and a fixture deleting an entire date would prevent this exact class of false assurance.

### T3: Critic catches an error; remediation introduces another

Cell: retention-q2-deterioration / M / r1. [First metric query](../.runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-retention-q2-deterioration-1_0-multi-agent-r1/working/queries/email_q1_q2_components_v2.sql), [repair query](../.runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-retention-q2-deterioration-1_0-multi-agent-r1/working/queries/email_fixed_maturity_metrics.sql), [ledger](../.runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-retention-q2-deterioration-1_0-multi-agent-r1/state/analysis_ledger.json).

The visible definition is a second order within 90 days. The initial Analyst counts distinct purchasers, and Critic correctly requests a retention correction. Remediation aggregates joined orders with `COUNT(*)` as acquired customers, inflating cohort denominators. Another query uses 2025-08-31 as a cutoff although the source extends through 2025-12-31, creating a maturity concern for fully observable Q1/Q2 cohorts. The loop shifts toward comparability and unavailable campaign/audience explanations instead of delivering the defined retention comparison. The run blocks; no official analytical score exists.

The failure is not lack of evidence that Email deteriorated. It is unstable metric execution during repair, compounded by scope expansion. A reusable cohort routine should establish one row per acquired customer before combining spend or computing rates.

### T4: Correct audit result becomes an evidence-access and critique loop

Cell: partial-latest-reporting-day / M / r2. [Ledger](../.runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-partial-latest-reporting-day-1_0-multi-agent-r2/state/analysis_ledger.json), [final result artifact](../.runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-partial-latest-reporting-day-1_0-multi-agent-r2/outputs/reporting_period_completeness.json).

Audit finds one incomplete marketing date. Lead formulates completeness findings and hypotheses. Critic cannot inspect several accepted query IDs, reruns coverage, and identifies the latest-day gap while incorrectly naming Paid Social. Repair persists more artifacts and structural checks. The final Critic reproduces principal bounds and recognizes Affiliate, yet REVISE remains over reference accessibility and whether the channel universe is configured or source-derived. Lead's final metrics give four covered channels rather than the derived 0.8 fraction. The run blocks after three reviews.

Some scope objections were valid: “all decisions are reliable” would be too broad, and configured versus observed coverage should be stated honestly. But repeated missing-output disputes and fabricated channel facts are avoidable system failures, not evidence that the task requires more specialists.

### T5: A sensible business diagnosis loses required comparisons

Cell: canonical-q2-profitability / S / r2. [Query](../.runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-canonical-q2-profitability-1_0-single-agent-r2/working/queries/q1_q2_profitability_by_channel.sql), [report](../.runs/phase2-task10-20260820-v8/workspaces/phase2-task10-20260820-v8-canonical-q2-profitability-1_0-single-agent-r2/outputs/report.md).

Audit is clean; analysis follows the Q1/Q2 acquisition-cohort definitions and identifies Meta's higher spend and lower acquisitions. SQL computes spend, CAC, profit and LTV; a separate volume query supplies acquisitions. Final prose reports the correct main mechanism and appropriate observational caution. The structured list contains CAC +0.305, customers -0.18, an absolute profit decline, and a nearly flat LTV comparison. It omits structured spend and conversion changes. CAC/count pass; other expected comparisons are missing or unmatched. The LTV query also ignores zero-order customers, as described above. The unsupported-claims regex rejects the phrase “not causal proof.”

This is a useful but incomplete analysis, a modest denominator error, and an independently incorrect evaluator language judgment—not a wholly failed investigation.

## B. Downstream symptoms

- **Near-zero numeric score:** principally failure to produce a matching final estimand; not an aggregate estimate of arithmetic accuracy.
- **Budget exhaustion and 57 M revisions:** consequences of repeated inspection/recomputation, unstable definitions and expanding critique. Raising all caps would likely buy more of the same behavior.
- **Expensive multi-agent runs:** repeated schema/audit inspection, SQL reproduction, specialist invocations and large shared context. Retained totals include 598 successful and 90 failed M SQL calls versus 153/25 S; M also has 246 successful and 33 failed Python calls versus 17/6 S. These counts are workload evidence, not a normalized architecture-quality comparison.
- **High provenance and capability scores:** indicate references resolve and required classes of work occurred. They do not mean the cited execution computes the stated number or uses the right denominator.
- **High-level correct narrative with failed benchmark:** sometimes a final-coverage problem, sometimes masking absent mechanism measurements, and sometimes helped by evaluator boundary defects. It must be inspected per case.

## C. Real infrastructure issues that do not explain the central analytical result

The existing immutability, offline evaluation, explicit failure taxonomy, model-usage recording, source protections, structured interfaces and lifecycle work are valuable. I found no evidence that mutable input data, benchmark overwrites, nondeterministic scenario generation, generic caching, or lost successful-run cost accounting caused the observed numerical collapse. Ledger locking/budget reservation and ordinary SQL connection cleanup already exist; another abstraction for these is not the priority.

Seven cells fail outright with evidence-provenance classifications, and additional blocked cells have provenance-related constraints. Some are legitimate: a Python calculation citing only hardcoded counts lacks a verifiable data derivation even if those counts were seen in a previous SQL response. The appropriate fix is a direct dependency on that executed result, not weakening lineage to accept arbitrary constants. A provider/API failure in immaterial S r1 is a separate operational incident; it does not explain repeated analytical failures.

There are secondary state/concurrency risks worth testing around cancellation, shared artifact discovery, and historical finding/assessment selection. These code-path risks justify targeted tests, not a speculative concurrency rewrite. Their contribution to a particular v8 failure is **Plausible but not yet demonstrated** unless separately evidenced above. I found no empirical reason to attribute the broad result to cache corruption or a random race.

The aggregate comparison report performs many small-sample tests (108 scenario/metric comparisons, three repetitions per scenario). Its “supported difference” labels should be treated as exploratory without a multiple-comparison plan. This reporting limitation cannot cause bad agent calculations; the descriptive cost/latency gap is large regardless.

Verification during this audit: **692 tests passed, three skipped, 17 deselected** with `pytest -m 'not live'`; the skipped Docker integrations could not access the local Docker socket. Ruff lint and formatting checks passed (171 Python files already formatted). These results support the infrastructure baseline while underscoring the analytical coverage gap. No production code or benchmark evidence was changed for this audit.

## Small deterministic metric/statistics layer

This should be a few operations over explicit column/table mappings, not a benchmark solver or a replacement for investigation. The LLM chooses the business question, source bindings, populations, periods and plausible mechanisms. Deterministic code performs the repeated arithmetic and preserves its output.

| Reusable operation | Inputs that must be explicit | Output / safeguards |
|---|---|---|
| Relation and coverage profile | Key/date columns; expected bounds; expected grid source or explicit values; sparse-event versus daily-grid meaning | Counts, uniqueness, nulls, date bounds, missing dates/channel-days; counts actual matches; no large fact-table cross-join |
| Economic reconciliation | Result/operand columns; documented rounding/currency precision | Material discrepancy counts and maxima; separates binary floating noise from economic violations |
| Period/cohort aggregate | Entity key; acquisition/event date; interval boundaries; maturity cutoff; dimension mapping | Entity-level facts before joins; includes zero-activity entities; exposes incomplete observation rather than silently redefining population |
| Components and ratios | Named numerator/denominator aggregates and their compatible scopes | Gross/net revenue, COGS, discounts/refunds, contribution profit/margin, CAC, ROAS, AOV, conversion, retention, LTV; ratio-of-sums versus mean-of-ratios explicitly distinguished |
| Contrast | Two calculated values with compatible scope; absolute/relative operation | Levels, difference, relative change, unit and component references; defined behavior for zero/negative baselines |
| Statistical procedure | Outcome/unit/arm mapping; design assumptions; estimand; confidence level; practical threshold | Initially two-proportion analysis and a justified continuous-outcome comparison; seeded bootstrap only when requested/appropriate; computed estimate, CI, p-value, effect size and interpretation inputs |

Examples of semantics that must remain configurable: whether contribution profit includes acquisition spend; whether revenue is gross or net; whether ROAS uses attributed revenue or incremental revenue; retention's return event and window; LTV observation versus extrapolation; order-level versus customer-level uncertainty. AOV uses orders as denominator, CAC acquired customers, conversion eligible sessions/participants, and retention an eligible cohort. These denominators are not interchangeable. Predictive LTV or causal marketing attribution is outside this proposed scope.

Start with the operations implicated by retained failures: date/grid profile, cohort aggregation/ratios, contrast, and binary experiment inference. Do not implement the entire business metric catalog first. No operation should import `scenarios`, evaluator rules, target answers, scenario IDs, or hidden tolerances. Both architectures get the same tools and public documentation. Independent evaluator calculations should remain separate from the production implementation to avoid a shared-bug oracle.

Generality is a design feasibility conclusion, not verification of an implemented layer. The independently executed schema-driven profiles need none of those dependencies; the remaining operations can accept arbitrary source bindings and explicit business definitions. Add import/dependency checks and renamed/reordered/date-shifted fixtures before accepting their implementation. Public unit/operation semantics are permissible; a mapping from benchmark scenario or expected-answer IDs to a calculation is not. The existing E1 metadata leak must not become an input to metric selection.

## Prioritized remediation and empirical acceptance

| Priority | Small intervention | How to determine whether it worked | Main risk |
|---|---|---|---|
| P0.1 | Persist/inspect execution results; return canonical result references | Offline retained-shaped fixtures resolve every accepted reference; Critic can retrieve values without rerunning; zero result/source confusion | Output size or stale alias resolution |
| P0.2 | Bind selected metric/statistical outputs to computed records; pass/render full selected statistics and limitations | Exact numerical equality from execution through specialist, Lead, candidate, report and evaluator; p-value corruption fixtures fail before finalization | Accidental dependence on benchmark identities |
| P0.3 | Per-relation date/profile operation plus real SQL cancellation | All four retained cross-join patterns have cheap profiling alternatives; expensive query exits within declared tool budget and leaves no worker/late writes | Overrestricting legitimate analysis |
| P1.1 | Add only cohort/ratio/contrast and binary-statistics operations; share guidance | Hand-calculated and metamorphic fixtures pass; correct segment/cohort comparisons appear with component values; false-clean and false-anomaly fixtures fail appropriately | Correct arithmetic over incorrectly mapped data |
| P1.2 | Stable objective-bound blockers and one in-budget finalization repair path | Seeded real errors remain blocked until repaired; optional causal follow-up does not consume all reviews; detected errors are actually corrected | Completion improves by suppressing critique |
| P1.3 | Version task/evaluator boundary corrections independently | Negation and issue-ID defects reproduced then fixed; same values/tolerances preserved; public date scope agrees with evaluator; chart requirement is fulfilled | Mixing evaluator improvement with model improvement |
| P2 | Reassess whether all five roles earn their cost | Paired small paid matrix shows incremental errors prevented/repaired per role, with all-cell completion, correctness, dollars and latency | Overinterpreting a tiny sample |

For each stage, retain a baseline and record the changed code/configuration/evaluator version. An improvement in contract match without any new correct calculation is a useful integration improvement, but label it that way. An improvement from evaluator negation handling is a measurement correction, not a smarter agent. A completion increase with more false claims is a regression.

Do not use higher model capability or larger caps to conceal defects during the first comparison. Once the execution/result path is reliable, model choice can be a separately declared experiment. The existing data does not establish that the model alone caused the failure, or that specialization will help under a repaired contract.

## Validation before another full 60-cell paid run

### 1. Deterministic and replay gate — no API credits

Use small, independently calculable datasets with arbitrary table/column/channel names and shifted dates. Exercise the production tools and actual persistence/rendering contracts, not just the evaluator's accepted schema.

- Cohort fixture: repeat orders, nonbuyers, boundary-day purchases, a dimension with multiple rows, and incomplete observation. Check customers counted once, correct zero inclusion, spend joined once, and explicit maturity treatment.
- Metric fixture: unequal segment volumes to distinguish aggregate/segment ratios and ratio-of-sums/mean-of-ratios; gross/net denominators; negative profit; zero denominator; absolute versus relative change.
- Coverage fixture: one whole missing date, one missing latest channel row, sparse event dates that are not reporting defects, and a complete control. Require exact missing scope and no false-clean result.
- Reconciliation fixture: realistic floating noise plus one actual material discrepancy. Require only the material discrepancy to be flagged.
- Experiment fixtures: meaningful effect, null effect, significant-but-small effect; swapped arms; unequal allocation; duplicate subjects; missing outcomes. Verify method assumptions, interval, p-value, effect size and practical decision independently.
- Propagation fixture: correct executed p-value followed by model-authored `1.0`; reject or replace it through the established computed-record binding. Include omitted metrics, wrong scope and stale corrected assessments.
- Critic fixture: plausible incorrect finding with valid provenance must fail; honest uncertainty with correct result must remain reportable; fabricated Critic facts/references must fail.
- Resource fixture: expensive query cancellation, no orphan workers, one terminal event and consistent budget; subsequent query succeeds.
- Evaluator fixture: valid equivalent semantic identities accepted; wrong denominator/window/arm rejected; arbitrary issue IDs; negated versus affirmative causal claims; required chart actually present.

Metamorphic checks: row permutation does not change results; splitting an order while preserving its order identity does not create customers; duplicating a dimension join is detected; changing currency scale scales money but not rates; renaming channels changes no arithmetic; swapping arms flips the effect sign; translating dates with the observation window preserves comparisons. Define each transformation's expected behavior carefully rather than assume arbitrary row duplication is harmless.

Replay selected safe retained calculations from all ten scenarios and compare their computed records with the original final records. Report separately: correctly computed and surfaced, correctly computed but lost, wrong computation, absent investigation, and no usable evidence. Do not execute retained pathological queries or rewrite frozen ledgers. Run offline evaluation for any new fixture/replay workspace under a new identity.

### 2. Preregistered small paid matrix — 20 cells

Use the same model/configuration and shared capabilities in both architectures; keep identical declared resource ceilings unless a separately versioned experiment changes them. Prepare all tasks, seeds, repetition counts, success gates and versions before observing outcomes.

| Scenario family | Repetitions per architecture | Purpose |
|---|---:|---|
| Canonical business root cause | 2 | Multi-table economics, channel mechanism, complete comparison delivery |
| Missing reporting day | 2 | False-clean protection, defect scope, useful qualified conclusion |
| Meaningful binary experiment | 2 | Numerical binding and valid significance/practical recommendation |
| Significant but immaterial experiment | 2 | Separates statistical significance from business action; catches p-value corruption |
| Retention business root cause | 1 | Sentinel for customer grain, repeat purchase, maturity and repair |
| Partial latest reporting day | 1 | Sentinel for date profiling, expected grid and operational completion |

Total: `(4 × 2 + 2 × 1) × 2 architectures = 20`. Null-effect and channel-mix/COGS/refund cases are mandatory offline fixtures; they remain in the subsequent full matrix. The small paid matrix is a screening gate, not sufficient evidence of architecture superiority.

Use separately versioned task definitions where period clarity is corrected; preserve data difficulty and hidden ground truth. Include predeclared innocuous renaming/date-shift variants in offline tests and freeze any paid variants before execution. Never inspect a variant outcome to decide whether to include it. Never rerun only failed or favorable cells.

Report all declared cells, including failures, with:

- Operational completion and cause of noncompletion.
- Original strict evaluation where applicable and separately versioned corrected evaluation; never silently replace v8 scores.
- Investigation coverage, correct computed estimands, correctly surfaced estimands, and value changes during propagation.
- Incorrect audit assurances, false anomaly claims, statistical contradictions, and Critic true/false objections.
- SQL/Python/specialist/review counts, known/unknown cost, latency, and timeout/cleanup outcomes.

Suggested engineering gate, fixed before running: at least 16/20 cells complete, at least 80% of all declared required estimands are correctly computed and surfaced with noncompleted cells counted as undelivered, zero material false-clean coverage conclusions, zero computed-to-final numerical corruption, and no unbounded SQL workers. Require both architectures' results to be reported separately so aggregate success cannot hide one failing architecture. These are release-screening thresholds, not statistical claims or evaluator tolerances. Failure should identify a specific stage to repair before paying for another 60 cells.

### Architecture decision after validation

Measure whether Auditor/Statistician/Critic contributions prevent or repair concrete errors that a shared-tool generalist misses. Do not reward a role for existing, producing a plausible narrative, or issuing more objections. If the five-agent system still spends much more and completes fewer tasks without better all-cell analytical delivery, prefer the single-agent baseline with the shared deterministic tools. Any leaner multi-agent design should then be a separately declared architecture experiment, not a redefinition of the existing five-agent result.

The immediate objective is a trustworthy data-to-calculation-to-report path. Once that path works, the repository can meaningfully test whether specialized reasoning improves investigation quality.

## Audit completion and implementation handoff

**Completion:** The original audit request is satisfied within the retained-evidence limits stated above. All ten scenarios and both architectures are covered; numerical computation is distinguished from missing investigation, propagation, identity and evaluator failures. The focused pass reconfirmed the numerical counts, nine experiment estimates, representative R1–R7 evidence and cross-join mechanism. It corrected the assertion of wholly absent period hints and added E1. No further demonstrated Critical/High omission was identified. The audit is ready to guide remediation; no remediation, paid calls or new benchmark cells were executed.

**Final order and dependencies:**

1. **P0.1 — Persist and inspect calculation results.** Add focused offline regression fixtures with this change. This is the dependency for reliable numerical binding and cheaper critique.
2. **P0.2 — Preserve/select computed metrics and statistics through validation and rendering.** Depends on P0.1; define a minimal general result identity now, and exercise one binary-experiment calculation through the complete path before expanding the catalog.
3. **P0.3 — Add schema-driven date profiling and tested SQL cancellation.** Can proceed independently of P0.2; must pass cleanup/budget tests before paid validation.
4. **P1.1 — Add cohort/ratio/contrast and binary-statistics operations.** Depends on the result contract; use independent analytical fixtures and share capabilities across architectures.
5. **P1.2 — Narrow blockers and support bounded finalization repair.** After reliable result inspection/binding, measure legitimate error detection as well as completion.
6. **P1.3 plus E1 — Correct task/evaluator boundaries and blind model-visible IDs.** Prepare independently from step 1; settle and freeze them before any new paid comparison. Do not defer metadata blinding until after performance testing.
7. **Validation, then architecture decision.** Run the offline gate, then the preregistered small matrix only when separately authorized; consider the full 60-cell experiment only after the screening gate passes.

**Behavior and version boundaries:** P0/P1.1/P1.2 alter production tools, outputs or agent behavior; record a new code/architecture configuration and immutable benchmark run ID for comparisons. Bump output contracts when wire/persisted schemas change. E1 changes model-visible benchmark metadata and requires a new benchmark/configuration declaration. Reporting-period clarifications change scenario/task versions. Semantic matching, issue classification, selected-final-statistics evaluation and negation handling change evaluator versions. Making the existing chart requirement explicit changes public task/agent configuration, not the numeric rubric. Declare applicable versions together before testing; never present corrected evaluation or clarified tasks as unchanged v8 results.

**Smallest first implementation tranche:** P0.1 only: bounded persisted SQL results, returned execution/result references, and consistent inspection of already-supported aliases, with serialization/truncation/failed-reference regressions. No new agent, metric catalog or retry loop. Its completion criterion is that a reviewer or Critic can retrieve the exact successful calculation output without rerunning it. Then implement P0.2 as the next reviewable tranche.

**Do not change:** frozen v8 manifests/workspaces/results; evaluator-only expected values or tolerances; substantive evaluation requirements; dataset difficulty; hidden-answer boundaries; equal tool access between architectures; model or budgets to mask current defects; lineage checks to accept unsupported constants; unfavorable-cell inclusion. Do not add scenario-specific metric mappings, blanket retries, orchestration abstractions, UI, AWS, predictive ML or other future-phase work.
