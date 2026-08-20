# Data Science AI Agent — Project Plan

## Plan Status

**Revision:** 2026-08-20 — Phase 2 Tasks 1–9, R1–R19, and R20–R23 are
implemented.
The deterministic portion of the reopened R6 gate and its benchmark-validity
review are complete. Fresh paid canaries were run: the single-agent architecture
passed, while the multi-agent architecture failed its executed-evidence gate.
The resulting audit/Lead provenance gaps are tracked as R20–R25. R20–R23 are
now closed: audit contract 2.0 makes every material audit claim
evidence-bearing, one persistence boundary refuses an unsupported completed
audit, the Lead receives a bounded typed audit evidence catalog, offline scoring
enforces the same provenance boundary at catalog evaluator version 1.2, one
shared hypothesis-evidence rule is enforced when the state transition is
requested, and a strict-schema-valid response whose citations do not resolve
gets one bounded tool-less correction attempt. R24–R25 remain open. Task 10
remains blocked before the paid matrix; no benchmark result is published.

The core product thesis and five-agent architecture remain unchanged. Phase 0
and the Phase 1 multi-agent MVP are complete. This revision scopes Phase 2 as a
versioned, reproducible evaluation program: expand the deterministic scenario
suite, build a fair single-agent baseline, and benchmark analytical reliability
before beginning product UI or cloud deployment.

---

## 1. Project Summary

### Working concept

Build an **autonomous multi-agent data science system** that investigates open-ended business questions using unfamiliar datasets, SQL, Python, statistics, and iterative hypothesis testing, then produces a validated, evidence-backed analysis.

The canonical user experience is:

> Upload several business datasets and documentation, then ask a question such as:
>
> **“Why did profitability decline in Q2, and what should the company do about it?”**

The system should autonomously:

1. Understand the question.
2. Audit the available data.
3. Form an investigation plan.
4. Run SQL and Python analyses.
5. Develop and test hypotheses.
6. Request statistical analysis where appropriate.
7. Investigate follow-up questions.
8. Challenge its own conclusions.
9. Correct problems if necessary.
10. Produce a professional report with charts, methodology, caveats, and reproducible evidence.

### Project thesis

The project should answer a broader technical question:

> **Can a team of specialized AI agents perform open-ended business data science more reliably than a single general-purpose agent?**

The product is the primary deliverable. Evaluation is built into the system so the multi-agent architecture can later be benchmarked against a single-agent baseline.

---

## 2. V1 Scope

V1 should focus on **business analytics**, not general-purpose data science.

### In scope

- Exploratory data analysis
- Data-quality analysis
- Metric decomposition
- Segmentation
- Marketing channel analysis
- Funnel analysis
- Cohort analysis
- Retention analysis
- LTV and CAC analysis
- Basic statistical testing
- Root-cause investigation
- Visualization
- Evidence-backed business recommendations

### Explicitly out of scope for V1

- Predictive ML model building
- Forecasting
- Causal ML
- External web research
- RAG / vector databases
- Arbitrary third-party database integrations
- Fully autonomous “do any data science task” functionality
- Kubernetes / EKS
- Complex distributed infrastructure

These are future expansion opportunities after the core system is reliable.

---

## 3. Core Multi-Agent Architecture

Use a **manager/orchestrator architecture**.

The Lead Data Scientist remains in control throughout the run and invokes specialist agents as needed.

```text
                           USER
                            |
                            v
                 +----------------------+
                 | LEAD DATA SCIENTIST  |
                 |    ORCHESTRATOR      |
                 +----------+-----------+
                            |
              +-------------+--------------+
              |             |              |
              v             v              v
        DATA AUDITOR     ANALYST       STATISTICIAN
           AGENT          AGENT            AGENT
              |             |              |
              +-------------+--------------+
                            |
                            v
                     CRITIC AGENT
                            |
                     +------+------+
                     |             |
                   PASS          REVISE
                     |             |
                     v             +--> Lead --> Specialist
               FINAL REPORT
```

### Design rules

1. **Only the Lead agent may delegate work.**
2. Specialist agents do not call each other directly.
3. Specialists return **typed structured outputs**, not long free-form conversations.
4. The Lead has no direct SQL/Python execution tools.
5. No final answer is produced before Critic validation.
6. Important quantitative claims must have executable evidence behind them.
7. Persist explicit plans, hypotheses, outputs, evidence, and tool events — not hidden chain-of-thought.

---

## 4. Agent Responsibilities

### 4.1 Lead Data Scientist Agent

The Lead is the system orchestrator.

#### Responsibilities

- Understand the user’s analytical objective.
- Read available business context.
- Create an initial investigation plan.
- Maintain and revise a hypothesis tree.
- Delegate tasks to specialist agents.
- Decide which findings require deeper investigation.
- Request statistical validation where appropriate.
- Integrate specialist findings.
- Send candidate conclusions to the Critic.
- Route failed validation back to the appropriate specialist.
- Produce the final user-facing analysis.

#### Important restriction

The Lead should **not** directly run SQL or Python. This forces meaningful specialist delegation.

#### Example reasoning artifact

```text
Profitability decline
|
+-- Revenue?
|   +-- Traffic
|   +-- Conversion
|   +-- AOV
|
+-- COGS?
|
+-- Marketing efficiency?
    +-- Spend
    +-- CAC
    +-- Conversion
    +-- Customer LTV
```

The hypothesis tree is an explicit planning artifact stored in the shared ledger.

---

### 4.2 Data Auditor Agent

The Data Auditor determines whether the available data can be trusted and understood.

#### Responsibilities

- Enumerate available files/tables.
- Inspect schemas and data types.
- Determine date coverage.
- Calculate row counts.
- Check missingness.
- Check duplicates.
- Identify suspicious gaps.
- Identify likely primary keys.
- Identify basic table relationships.
- Review business definitions.
- Detect obvious outliers and data-quality issues.
- Surface limitations before substantive analysis begins.

#### Example output

```text
DATA AUDIT

orders
Rows: 284,182
Date range: Jan 1 – Jun 30
Duplicates: 0.17%
Missing customer_id: 0.02%

marketing_spend
Rows: 905
Date range: Jan 1 – Jun 30

WARNING:
June 14 contains 92% fewer transactions
than surrounding Sundays.
```

The Data Auditor is especially important for benchmark scenarios in which an apparent business problem is actually a data-quality problem.

---

### 4.3 Analyst Agent

The Analyst is the primary computational workhorse.

#### Responsibilities

- Write and execute SQL.
- Perform Python/pandas analysis.
- Join datasets.
- Calculate business metrics.
- Decompose KPI changes.
- Perform segmentation.
- Analyze funnels.
- Analyze cohorts.
- Calculate CAC, LTV, retention, AOV, contribution profit, etc.
- Create charts.
- Investigate follow-up hypotheses assigned by the Lead.
- Return findings with evidence and caveats.

#### Example task

> Determine why Meta CAC increased in Q2.

#### Example output

```text
Finding:
Meta CAC increased 29%.

Evidence:
Spend:            +7%
Traffic:          +5%
Conversion rate: -17%
New customers:   -18%

Interpretation:
Conversion deterioration, rather than higher media cost,
explains most of the CAC increase.

Artifacts:
- outputs/charts/meta_cac_trend.png
- working/queries/meta_cac.sql

Caveat:
Campaign-level creative data is unavailable.
```

---

### 4.4 Statistician Agent

The Statistician specializes in formal statistical reasoning.

#### Responsibilities

- Select appropriate hypothesis tests.
- Check assumptions.
- Calculate confidence intervals.
- Calculate effect sizes.
- Run regression when useful.
- Distinguish statistical from practical significance.
- Identify sample-size limitations.
- Prevent overinterpretation of noisy differences.

#### Example task

> Determine whether the observed difference in 90-day LTV between Meta and Google customers is statistically credible.

#### Example output

```text
Difference: +5.1%

95% CI:
[-1.3%, +11.7%]

p = 0.14

Conclusion:
Evidence is insufficient to conclude that the cohorts
have meaningfully different 90-day LTV.
```

The Statistician should not become a generic second Analyst.

---

### 4.5 Critic / Validator Agent

The Critic independently challenges the proposed analysis before it reaches the user.

#### Responsibilities

- Reproduce important calculations where useful.
- Verify business definitions were followed.
- Check denominator choices.
- Check joins for accidental duplication.
- Check charts against reported values.
- Identify unsupported claims.
- Flag correlation/causation errors.
- Identify ignored data-quality issues.
- Identify important alternative explanations.
- Assess whether recommendations are actually supported.

#### Output contract

Either:

```text
PASS
```

or:

```text
REVISE

HIGH:
CAC calculation uses all customers rather than new customers.

MEDIUM:
The claim that creative fatigue caused conversion decline is unsupported.

LOW:
Chart title says revenue while the plotted metric is net revenue.
```

The system should allow a bounded number of remediation loops rather than iterating indefinitely.

---

## 5. Structured Agent Communication

Agents should communicate through Pydantic schemas instead of unstructured prose.

Example conceptual schemas:

```python
class Finding:
    id: str
    statement: str
    metric: str | None
    value: float | None
    evidence_refs: list[str]
    confidence: str
    caveats: list[str]


class SpecialistResult:
    objective: str
    findings: list[Finding]
    artifacts: list[str]
    methods_used: list[str]
    follow_up_questions: list[str]
    caveats: list[str]
```

The Lead should receive:

- Finding
- Evidence
- Caveats
- Artifacts
- Follow-up questions
- Confidence

rather than several pages of specialist narrative.

---

## 6. Shared Analysis Ledger

Each run should maintain a persistent structured `AnalysisLedger`.

Suggested contents:

```text
AnalysisLedger
|
+-- objective
+-- business_context
+-- audit
+-- investigation_plan
+-- hypotheses
|   +-- H001
|   +-- H002
|   +-- H003
+-- findings
+-- rejected_hypotheses
+-- open_questions
+-- artifacts
+-- validation_issues
+-- tool_events
+-- run_budget
```

Example hypothesis:

```text
H001

Statement:
"Q2 profitability declined because AOV fell."

Status:
REJECTED

Evidence:
Q1 AOV = $82.14
Q2 AOV = $83.02
```

Another:

```text
H002

Statement:
"Q2 profitability declined because paid-social
acquisition became less efficient."

Status:
SUPPORTED

Evidence:
Meta CAC +29%
Meta conversion -17%
Meta 90-day LTV -1.8%
```

### Ledger principle

Persist observable work products:

- plans;
- hypotheses;
- SQL;
- Python;
- results;
- evidence;
- artifacts;
- explicit conclusions;
- validation issues;
- tool calls.

Do not attempt to persist or display private model chain-of-thought.

---

## 7. Evidence Provenance

Every important quantitative finding should be traceable to executed work.

Example:

```text
Finding F017

Statement:
"Meta CAC increased 29.1% QoQ."

Evidence:
query_id: Q023
artifact: working/queries/meta_cac.sql
source_tables:
- marketing_spend
- customers
```

### Guiding rule

> The system should not make an important quantitative claim that it cannot trace back to executed analysis.

This improves:

- user trust;
- Critic validation;
- debugging;
- evaluation;
- reproducibility;
- demo quality.

---

## 8. Tool Design

Keep the callable tool surface small.

### Workspace tools

```text
inspect_workspace()
read_document()
```

### Computational tools

```text
run_sql()
run_python()
```

### Output tools

```text
save_artifact()
```

Avoid creating overly narrow tools such as:

```text
calculate_mean()
calculate_ltv()
run_ttest()
make_bar_chart()
```

Those would remove much of the interesting analytical decision-making.

### Tool permissions

| Agent | Workspace | SQL | Python | Save Output | Delegate |
|---|---:|---:|---:|---:|---:|
| Lead DS | Yes | No | No | Yes | Yes |
| Data Auditor | Yes | Yes | Yes | No | No |
| Analyst | Yes | Yes | Yes | Yes | No |
| Statistician | Yes | No | Yes | Yes | No |
| Critic | Yes | Yes | Yes | No | No |

---

## 9. Workspace Design

Each analysis run gets an isolated workspace.

```text
workspace/
|
+-- inputs/
|   +-- customers.parquet
|   +-- orders.parquet
|   +-- sessions.parquet
|   +-- marketing_spend.parquet
|
+-- docs/
|   +-- business_definitions.md
|
+-- working/
|   +-- queries/
|   +-- scripts/
|
+-- outputs/
|   +-- charts/
|   +-- report.md
|
+-- state/
|   +-- analysis_ledger.json
|
+-- logs/
    +-- run.jsonl
```

### Filesystem rules

- `inputs/` is read-only.
- `docs/` is read-only.
- Agents may write to `working/`.
- Approved artifacts may be written to `outputs/`.
- State is written through the orchestration layer.
- Tool executions should be logged.

---

## 10. Canonical Synthetic Ecommerce Dataset

Start with **one high-quality synthetic company** rather than many unrelated datasets.

Approximate scale:

```text
Customers:        ~50,000
Orders:           ~200,000–300,000
Sessions:         ~1–2 million
Marketing rows:   daily x channel
Period:           12 months
Channels:         5
Products:         ~20
```

### `customers`

Possible fields:

```text
customer_id
acquisition_date
acquisition_channel
region
device
```

### `orders`

```text
order_id
customer_id
order_date
product_id
quantity
gross_revenue
discount
refund
net_revenue
cogs
```

### `sessions`

```text
session_id
customer_id
session_date
channel
device
converted
```

### `marketing_spend`

```text
date
channel
spend
impressions
clicks
```

### `business_definitions.md`

Define:

- CAC
- new customer
- gross revenue
- net revenue
- gross contribution (`net revenue - COGS`)
- **reporting contribution profit (`net revenue - COGS - marketing spend`)**
- 30-day LTV
- 60-day LTV
- 90-day LTV
- conversion
- refunded-order treatment
- canceled-order treatment
- reporting timezone

For the canonical profitability scenario, **reporting contribution profit** is the primary profitability metric. Marketing spend therefore must be included when decomposing the Q1-to-Q2 profitability change. This removes ambiguity between order-level contribution and company-level profitability.

The agent should rely on explicit business definitions rather than infer semantics from column names.

---

## 11. Scenario Injection System

Generate a clean synthetic baseline, then programmatically inject known business or data-quality conditions.

This gives the evaluation suite known ground truth.

### Scenario A — Acquisition deterioration

Inject:

```text
Meta Q2 conversion: -18%
Meta spend:          +7%
Meta LTV:            approximately unchanged
```

Expected root cause:

> Rising Meta CAC caused primarily by conversion deterioration rather than declining customer quality.

### Scenario B — Retention deterioration

Inject:

```text
First-order metrics: approximately unchanged
Repeat rate:         -15%
90-day LTV:          -12%
```

Expected conclusion:

> Customer-retention deterioration is the primary issue.

### Scenario C — Data-quality trap

Inject a missing day or partial day in the orders feed.

Weak conclusion:

> Revenue suddenly collapsed.

Desired conclusion:

> The apparent decline is primarily a data-completeness issue.

### Scenario D — Margin deterioration

Revenue and acquisition performance remain stable while COGS rises.

Expected conclusion:

> Lower gross/contribution margin is the primary driver.

### Scenario E — A/B experiment

Inject a known treatment effect with known statistical properties.

The Statistician should reach the correct significance conclusion and avoid overstating the result.

---

## 12. Core Execution Loop

The application should enforce key workflow stages rather than relying only on prompts.

```text
START RUN
   |
   v
Create isolated workspace + ledger
   |
   v
AnalysisRunner receives objective
   |
   v
MANDATORY DATA AUDITOR PREFLIGHT
   |
   v
Persist AuditResult
   |
   v
Lead receives objective + audit
   |
   v
Lead creates investigation plan
   |
   v
Specialist delegation
   |
   v
Update Analysis Ledger
   |
   v
Open questions?
   |
   +-- YES --> further specialist work
   |
   +-- NO
        |
        v
Construct candidate findings
        |
        v
MANDATORY CRITIC
        |
        v
       PASS?
      /     \
    NO       YES
    |         |
    v         v
Remediation  Final report
    |
    +-------> Critic
```

### Workflow enforcement

The application-level `AnalysisRunner` must enforce the required lifecycle. Mandatory audit and Critic validation are **not** optional prompt conventions. The runner should own run status, budget enforcement, remediation-loop limits, failure handling, and final completion state.

### Validation loop

Allow approximately **two remediation cycles**.

If validation still fails, produce a constrained final report that explicitly discloses unresolved issues.

---

## 13. Budgets and Stopping Rules

Bound autonomy so agents do not investigate indefinitely.

Initial values can be tuned later.

Example limits:

```text
Max specialist invocations: 12
Max SQL executions:         20
Max Python executions:      15
Max Critic loops:            2
Max charts:                  4
```

Track:

- input tokens;
- output tokens;
- model calls;
- specialist calls;
- SQL executions;
- Python executions;
- elapsed time;
- estimated model cost;
- validation loops.

### Stopping principle

Stop when:

> Additional investigation is unlikely to materially change the primary conclusion within the remaining run budget.

---

## 14. Final User Deliverable

Use a consistent professional structure.

### Executive Summary

Two or three most important conclusions.

### Key Findings

Quantitative evidence supporting the conclusions.

### Recommended Actions

Actions that are actually supported by the available evidence.

### Supporting Visualizations

Prefer approximately 3–4 useful charts over many low-value visuals.

### Methodology

Concise description of relevant calculations, tests, and definitions.

### Caveats

Missing data, observational limitations, uncertainty, and unsupported hypotheses.

### Reproducibility

Allow the user to inspect:

- SQL;
- Python;
- charts;
- source tables;
- evidence references;
- Analysis Ledger;
- specialist assignments.

---

## 15. User Interface

Use **Streamlit for V1**.

The interface should expose the real agent system rather than becoming a separate frontend engineering project.

### View 1 — Analyze

User provides:

- CSV and/or Parquet files;
- business documentation;
- open-ended question.

### View 2 — Investigation

Show observable activity:

```text
✓ Data Auditor
  Audited four datasets

✓ Analyst
  Decomposed contribution profit

✓ Analyst
  Investigated channel CAC

✓ Statistician
  Evaluated LTV difference

✓ Critic
  Validation passed
```

### View 3 — Results

Show:

- executive summary;
- key findings;
- recommendations;
- charts;
- caveats.

### View 4 — Evidence

Show:

- Analysis Ledger;
- SQL;
- Python;
- artifacts;
- agent assignments;
- tool events;
- costs;
- latency.

Do not display private chain-of-thought. Display explicit plans, hypotheses, computations, evidence, and conclusions.

---

## 16. Recommended Repository Structure

```text
data-science-agent/
|
+-- src/
|   +-- agents/
|   |   +-- lead.py
|   |   +-- auditor.py
|   |   +-- analyst.py
|   |   +-- statistician.py
|   |   +-- critic.py
|   |
|   +-- tools/
|   |   +-- workspace.py
|   |   +-- sql.py
|   |   +-- python.py
|   |   +-- artifacts.py
|   |
|   +-- schemas/
|   |   +-- findings.py
|   |   +-- audit.py
|   |   +-- validation.py
|   |   +-- run_state.py
|   |
|   +-- orchestration/
|   |   +-- runner.py
|   |   +-- ledger.py
|   |   +-- budgets.py
|   |
|   +-- sandbox/
|   |   +-- executor.py
|   |
|   +-- evaluation/
|       +-- evaluator.py
|       +-- metrics.py
|
+-- skills/
|   +-- data_auditing.md
|   +-- business_analytics.md
|   +-- statistical_analysis.md
|
+-- scenarios/
|   +-- generator/
|   +-- definitions/
|
+-- evals/
|
+-- app/
|   +-- streamlit_app.py
|
+-- tests/
|
+-- AGENTS.md
+-- PROJECT_PLAN.md
+-- .env.example
+-- .gitignore
+-- Dockerfile
+-- pyproject.toml
+-- README.md
```

---

## 17. Technology Stack

### Core

- Python 3.12+
- OpenAI Agents SDK
- Pydantic

### Data / analytics

- DuckDB
- Parquet / PyArrow
- pandas
- NumPy
- SciPy
- statsmodels

### Visualization

- matplotlib
- Plotly

### Execution / isolation

- Docker locally

### Evaluation

- pytest
- custom deterministic evaluators

### Interface

- Streamlit

### Engineering

- uv
- Ruff
- Git
- GitHub Actions

### Cloud — later phase

- AWS ECS/Fargate
- Amazon ECR
- Amazon S3
- AWS Secrets Manager
- Amazon CloudWatch
- IAM

Do not introduce AWS into the critical path before the local MVP works.

---

## 18. Model Configuration

Do not hard-code the system around a single specific model.

Use configuration such as:

```text
OPENAI_DEFAULT_MODEL=<model-name>
```

This enables future comparisons across models without changing the architecture.

V1 only needs one working model/provider.

Multi-model comparisons are an evaluation enhancement, not a prerequisite.

---

## 19. Implementation Phases

# Phase 0 — Foundation

### Goal

Build deterministic infrastructure before agent intelligence.

### Current status

The deterministic foundation is complete: repository scaffolding, typed schemas,
workspace lifecycle, DuckDB execution, persistent Analysis Ledger, synthetic
ecommerce generation, artifact provenance, Docker-backed Python execution, and
the end-to-end acceptance path are implemented.

Phase 1: Multi-Agent MVP is complete. Phase 2: Evaluation and Reliability is now
underway on top of the completed deterministic foundation.

### Phase 0 completion status

- [x] Safe workspaces, artifacts, and persistent typed ledger
- [x] Bounded SQL and Docker-backed Python execution
- [x] Deterministic SQL → Python → artifact → ledger acceptance test
- [x] GitHub Actions checks for pytest and Ruff

### Build

- [x] Repository skeleton
- [x] `pyproject.toml` / uv environment
- [x] Pydantic schemas
- [x] Workspace manager
- [x] Analysis Ledger
- [x] DuckDB execution tool
- [x] First synthetic ecommerce dataset
- [x] Artifact manager with provenance
- [x] Docker-backed Python execution tool
- [x] Sandbox executor / Docker runtime
- [x] Bounded SQL result materialization and budget accounting
- [x] End-to-end deterministic Phase 0 integration test
- [x] CI for pytest + Ruff

### Phase 0 task record

#### Task 7 — Artifact management

Implement safe registration of workspace artifacts, enforce approved paths, persist relative references through `AnalysisLedger`, and add checksum/file-size provenance where useful.

#### Task 8 — Docker-backed Python execution

Implement `src/sandbox/executor.py` and `src/tools/python.py` so generated Python runs in a constrained Docker container with read-only inputs/docs, writable working/outputs, no network access, non-root execution, resource/time limits, typed results, and ledger events.

#### Task 9 — Execution hardening and integration acceptance

Cap DuckDB output materialization, expose truncation metadata, increment SQL/Python budget counters, and add a deterministic integration test covering the full foundation path.

#### Task 10 — CI and phase transition

Add GitHub Actions for deterministic checks. Once the Phase 0 acceptance test passes, update repo status documents to Phase 1. Live LLM tests must remain outside normal CI.

### Done when

The code can:

```text
create workspace
-> inspect/query data
-> execute SQL
-> execute sandboxed Python
-> save/register artifact
-> persist provenance + tool events + budgets in ledger
```

without any LLM/API calls.

The entire flow must be covered by a deterministic integration test, with pytest and Ruff passing.

---

# Phase 1 — Multi-Agent MVP

### Goal

One canonical scenario works extremely well locally through the full five-agent system.

### Completion status

Phase 1 is complete. The repository now contains the five typed agents, shared
runtime and deterministic tools, application-level lifecycle enforcement,
evidence and metric provenance, bounded remediation, offline persisted-workspace
acceptance, and a no-steering canonical workflow.

- [x] Deterministic canonical scenario and evaluator-only ground truth
- [x] Shared Agents SDK runtime with role-bound tools
- [x] Analyst, Data Auditor, Statistician, Critic, and Lead agents
- [x] Application-level `AnalysisRunner` lifecycle
- [x] Atomic budgets, task-local roles, and graceful constrained reporting
- [x] Structured evidence and metric compilation through final validation
- [x] Canonical live runner and zero-API offline workspace evaluator
- [x] Deterministic, Docker-backed, Ruff, and opt-in live test coverage

### Updated implementation order

1. Canonical profitability scenario + ground truth
2. Shared Agents SDK runtime and deterministic tool adapters
3. Analyst Agent
4. Data Auditor Agent
5. Statistician Agent
6. Critic / Validator Agent
7. Lead Data Scientist Agent
8. Application-level `AnalysisRunner` orchestration
9. Canonical end-to-end acceptance run

Do not connect the entire workflow before each specialist is independently testable. The shared runtime/tool layer should be implemented once and reused by all agents.

### Task 1 — Canonical profitability scenario

Create a deterministic scenario-injection layer on top of the clean baseline for:

> **Why did profitability decline in Q2, and what should the company do about it?**

The canonical scenario should have a known primary story: **Meta acquisition efficiency deteriorates primarily because conversion declines, while acquired-customer LTV remains approximately stable**. Include at least one plausible but false alternative hypothesis that the agents should reject.

Scenario ground truth must be represented in typed metadata and used only by tests/evaluators, never exposed to agent prompts.

The scenario should require:

- data-quality validation;
- reporting-contribution-profit decomposition;
- channel analysis;
- CAC;
- conversion;
- LTV;
- statistical validation;
- a defensible recommendation.

### Task 2 — Agent runtime and tool adapters

Create a typed per-run context containing the workspace, ledger, execution services, artifact manager, and model/run configuration. Expose the deterministic tools through OpenAI Agents SDK function tools while enforcing each agent's permission boundaries and `RunBudget`. Tool outputs sent back to models must be concise and structured.

### Task 3 — Analyst Agent

Implement the Analyst first. It returns `SpecialistResult`, cannot delegate, uses approved SQL/Python/artifact tools, and must attach evidence references to material quantitative findings. Replace the placeholder `skills/business_analytics.md` with concise procedural guidance.

### Task 4 — Data Auditor Agent

Implement typed preflight auditing over schemas, dates, missingness, duplicates, likely keys/relationships, temporal gaps, anomalies, and business definitions. Persist `AuditResult` into the ledger.

### Task 5 — Statistician Agent

Implement the statistical specialist for hypothesis tests, confidence intervals, effect sizes, assumptions, practical significance, and causal-claim restraint. Replace the placeholder statistical skill document with real guidance.

### Task 6 — Critic / Validator Agent

Implement independent validation of candidate findings and recommendations. It should reproduce material evidence as needed, detect definition/denominator/join errors, flag unsupported causal claims, and return typed `ValidationResult` / `ValidationIssue` structures.

### Task 7 — Lead Data Scientist Agent

Implement the manager/orchestrator agent. The Lead owns the objective and final answer, maintains explicit plans/hypotheses/open questions, invokes specialists as tools, and **must not** receive raw SQL or Python tools.

### Task 8 — `AnalysisRunner` orchestration

Enforce the application workflow in ordinary code rather than relying solely on agent prompts:

1. Create/open workspace and ledger.
2. Mark run `RUNNING`.
3. Run mandatory Data Auditor preflight.
4. Persist the audit.
5. Invoke Lead with objective, context, and audit.
6. Allow bounded specialist delegation.
7. Persist plans, hypotheses, findings, evidence, artifacts, and usage.
8. Run mandatory Critic validation.
9. Route `REVISE` issues back through Lead for bounded remediation.
10. Re-run Critic up to `max_critic_loops`.
11. Persist final report and mark `COMPLETED`, or expose unresolved validation issues after the limit.
12. Mark unrecoverable runs `FAILED`.

Capture model identity, SDK usage/token metadata, elapsed time, and relevant run configuration in persistent state.

### Task 9 — Canonical end-to-end acceptance

Run the entire system from only raw scenario data, `business_definitions.md`, and the canonical user question. No human steering and no ground-truth leakage into prompts are permitted.

### Done when

The canonical run autonomously generates and persists:

- completed Data Audit;
- investigation plan;
- hypothesis history;
- SQL evidence;
- Python evidence;
- relevant charts;
- findings with evidence provenance;
- Statistician output where appropriate;
- Critic validation;
- final report;
- complete Analysis Ledger;
- tool/agent execution trace;
- model usage, cost/latency metadata where available.

No manual steering should be required during the run.

### Canonical live acceptance command

From the repository root, run the full no-steering acceptance with:

```bash
OPENAI_API_KEY=... OPENAI_DEFAULT_MODEL=... uv run python scripts/run_canonical_mvp.py
```

The script generates the seeded canonical inputs, creates the isolated
workspace, runs the five-agent lifecycle, reloads the ledger, and evaluates the
persisted evidence and evaluator-only scenario ground truth. Docker must be
available for the Python specialist tools.

---

# Phase 2 — Evaluation and Reliability

### Goal

Turn the project from a demo into an empirically evaluated AI system.

Expand from one scenario to approximately **10 deterministic scenarios**, then
compare the five-agent architecture with a fair single-agent baseline using a
versioned and reproducible benchmark.

Phase 2 improves evaluation breadth and reliability. It must not introduce
Streamlit, AWS, predictive ML, external web research, a second agent framework,
or unrelated infrastructure.

### Phase 2 operating principles

1. **Ground truth stays downstream.** Scenario answers, expected values, and
   tolerances are available only to deterministic generators/evaluators, never
   to agent prompts, model-visible documents, or tools.
2. **Persisted state is authoritative.** Every score must be reproducible offline
   from an immutable workspace, ledger, report, artifacts, and a versioned
   evaluation manifest.
3. **Score before rerunning.** Add deterministic evaluator fixtures and inspect
   exact failures before paying for another model run.
4. **Compare architectures fairly.** Use the same raw inputs, documentation,
   question, model configuration, deterministic execution tools, and
   output/evidence contracts for single-agent and multi-agent runs.
5. **Separate reliability dimensions.** Operational completion, analytical
   correctness, evidence quality, cost, and latency are distinct outcomes.
6. **Preserve raw results.** Benchmark runs use unique immutable IDs. Never use
   `--force` to overwrite benchmark evidence.
7. **Declare the experiment first.** Freeze scenario/evaluator versions,
   repetitions, model, budgets, and aggregation rules before running the paid
   benchmark. Do not cherry-pick or invent results.

### Evaluation scenario contract

Each evaluation scenario should define, with typed schemas:

- stable scenario ID, version, name, seed/configuration, and user question;
- deterministic source-data and business-document generation;
- injected business conditions and data-quality conditions;
- expected primary driver and important secondary findings;
- known non-drivers and tempting false hypotheses;
- expected data-quality and statistical conclusions;
- generic structured metric identities, values, units, and tolerances;
- required evidence/provenance and task-completeness checks;
- evaluator version and compatibility information.

Scenario ground truth must be derivable from the generated model-visible data,
not only from comparison with a hidden clean baseline. Clean-baseline invariants
and injected relationships should be tested independently.

### Target scenario catalog

The exact mix may evolve when generator constraints are discovered, but Phase 2
should cover approximately this breadth without leaving V1 business analytics:

| # | Scenario | Primary capability |
| ---: | --- | --- |
| 1 | Acquisition conversion deterioration | Profit/CAC/funnel decomposition and stable-LTV non-driver |
| 2 | Retention deterioration | Repeat behavior, cohorts, and 90-day LTV |
| 3 | Missing orders day | Detect data incompleteness before claiming business decline |
| 4 | Partial latest reporting day | Temporal coverage and reporting-cutoff discipline |
| 5 | COGS/margin deterioration | Revenue-versus-margin profit decomposition |
| 6 | Discount/refund deterioration | Net-revenue bridge and order-economics diagnosis |
| 7 | Meaningful A/B treatment effect | Correct test, interval, effect size, and practical significance |
| 8 | No-effect/non-significant experiment | Avoid false positives and causal overstatement |
| 9 | Statistically significant but immaterial effect | Distinguish statistical from practical significance |
| 10 | Channel/mix confounding trap | Reject a tempting segment attribution or causal non-driver |

Every scenario does not need every specialist. The expected behavior includes
appropriate stopping and avoiding unnecessary tool/specialist work.

### Metrics

#### Numerical correctness

Do material structured values match evaluator-only ground truth within the
declared tolerance? Also report expected-metric coverage and materially
conflicting duplicate measurements.

#### Root-cause identification

Did the final validated answer assert and support the injected primary mechanism,
rather than merely mention it as something to investigate? Did it reject known
non-drivers?

#### Data-quality detection

Did it identify injected defects without inventing problems in clean data?
Report both recall and false-positive behavior.

#### Statistical correctness

Did it choose an appropriate analysis, respect assumptions, report uncertainty
and effect size, distinguish practical significance, and reach the expected
inferential conclusion?

#### Evidence grounding

Are material quantitative claims tied to approved executed analysis or
source-derived artifacts with valid lineage? Are cited values reproducible?

#### Unsupported claims

Did the answer make unsupported causal, numerical, or recommendation claims?
Did it preserve uncertainty and observational limitations?

#### Overall task success

Did the run complete the required lifecycle, receive final Critic `PASS`, answer
the primary objective, provide supported actions, and satisfy all hard scenario
acceptance gates?

#### Operational reliability and efficiency

Track separately from analytical quality:

- completion / blocked / failed status and failure taxonomy;
- agent, tool, schema, budget, timeout, sandbox, and provider failures;
- model requests and input/cached/output/reasoning tokens;
- estimated cost and elapsed time;
- SQL, Python, specialist, Critic-loop, and chart usage;
- remediation and follow-up frequency.

Do not hide analytical failures inside an aggregate operational-success number,
or vice versa.

### Phase 2 implementation status as of 2026-08-19

Tasks 1–9 below are implemented and covered by deterministic tests. The latest
full local verification completed with 508 passed and 16 opt-in live tests
deselected; Ruff lint and formatting checks passed. This status describes the
implementation, not an architecture-performance result.

R1–R19 are implemented and verified with deterministic architecture-equivalence,
capability, evidence, workspace-identity, evaluator-error, aggregation,
pilot-binding, attempt-reconciliation, scenario-document, and exclusive-output
regressions. The final R6 preflight passed, including Docker-backed integration
tests and the complete 10 × 2 × 3 dry-run. A subsequent review of the retained
paid-pilot failures found seven additional gaps, now tracked as R13–R19. R13 is
complete: every production agent output type compiles through the installed
Agents SDK strict-schema converter, no analytical agent opts out of strict mode,
invalid final output is an explicit model/schema failure, and both live
architecture canaries passed on 2026-08-19. R14 is complete: usage is recorded
at the response boundary and survives parsing, turn-limit, and lifecycle
failures, and incomplete usage can no longer be published as a known `$0.00`.
R15 is complete: the single-agent runner opens, attributes, and closes typed
attempts for every exit, so both architectures publish the same attempt
protocol. R16 is complete: an interrupted cell is retained as a cancelled
operational record with its partial accounting and can be resumed into a new
append-only attempt. R17 is complete: one shared
outcome-sensitive smoke gate is used by the live tests, the canaries, and
deterministic failure fixtures, and it rejects all four retained pilots.
R18 is complete: every non-completion persists a typed reason and detail, and
only genuine budget exhaustion is categorized as budget. R19 is complete: the
pilot is a declared per-architecture set with a stratified, ranged estimate
bound to the frozen manifest. The post-R19 deterministic preflight and
benchmark-validity review are complete. Fresh paid canaries ran on 2026-08-20:
the single-agent architecture passed, while the multi-agent architecture failed
because Lead hypothesis `H2` cited `completed_data_audit` rather than executed
evidence. A focused review opened R20–R25. R20–R23
are implemented; R24–R25 remain open, and R6 must be rerun after all of them and
therefore remains open.
Task 10 was attempted with four versioned manifests, but no paid cost pilot
completed and the declared matrix was not started. Failure-only offline
rescores and aggregate reports are retained under
`.runs/phase2-task10-20260819/`; no aggregate architecture comparison has been
published. Existing canonical MVP workspaces are legacy acceptance artifacts
rather than Phase 2 benchmark records. The live handoff and exact environment
constraints are recorded in `docs/phase2-status.md`.

| Task | Status | Implemented outcome |
| ---: | --- | --- |
| 1 | Complete | Strict versioned scenario, evaluator, run-record, and manifest contracts with compatibility errors and model-visible/evaluator-only separation |
| 2 | Complete | Deterministic composable evaluator primitives plus single-workspace and manifest offline CLIs |
| 3 | Complete | Versioned scenario catalog, deterministic source writing, evaluator lookup, and shared invariants while retaining the clean baseline |
| 4 | Complete | Retention, COGS/margin, and discount/refund root-cause scenarios with wrong-definition rejection tests |
| 5 | Complete | Missing-day, partial-day, and three basic statistical scenarios with seeded sampling and data-quality calibration |
| 6 | Complete | Channel-mix confounding scenario and catalog-wide correct/adversarial fixture regression suite |
| 7 | Complete | Bounded generalist architecture sharing runtime, provenance, tools, and report contracts without specialist delegation |
| 8 | Complete | Immutable resumable matrix runner, paid opt-in, cost pilot gate, failure isolation, and offline rescoring |
| 9 | Complete | Deterministic denominator-preserving aggregation, uncertainty, paired comparisons, cost/latency, and failure reporting |
| 10 | Attempted / blocked | Four versioned pilot attempts were retained, but none completed the pilot gate; R24–R25 and a fresh final R6 preflight must close before a new 60-cell matrix is started |

### Phase 2 implementation order

#### Task 1 — Versioned evaluation contracts and manifests

Define typed, architecture-neutral schemas for scenario metadata, evaluator
results, per-run benchmark records, and aggregate benchmark manifests. A run
record should include scenario/evaluator version, architecture, model/provider,
run configuration, budgets/turn limits, code revision where available, seed,
workspace path, lifecycle outcome, score breakdown, usage, cost, and latency.

Acceptance:

- schemas reject incomplete or ambiguous benchmark records;
- evaluator-only fields cannot enter model-visible scenario context;
- old canonical persisted workspaces remain evaluable or fail with an explicit
  version-compatibility message.

#### Task 2 — Generic offline evaluation engine

Extract canonical-specific mechanics into reusable deterministic evaluators for
lifecycle, numerical comparisons, root-cause/non-driver semantics, data quality,
statistics, provenance, unsupported claims, and task completeness. Compose
scenario-specific rules from these primitives rather than cloning the canonical
evaluator.

Provide an offline CLI that evaluates one persisted workspace and a batch CLI
that evaluates a manifest. Neither may load `.env`, invoke agents, or make API
calls.

Acceptance:

- deterministic fixtures cover pass, missing, incorrect, conflicting, stale,
  and unsupported-claim cases;
- final report, Critic, and evaluator consume the same canonical final metric
  set;
- repeated offline evaluation of unchanged state is byte-for-byte stable where
  practical and semantically identical otherwise.

#### Task 3 — Scenario framework and catalog invariants

Generalize scenario registration/generation without replacing the clean
ecommerce baseline. Add catalog discovery, versioning, deterministic source
writing, evaluator lookup, and common invariant checks for keys, dates, metric
identities, documented nulls, economic identities, and observable ground truth.

Acceptance:

- the canonical scenario migrates to the generic contract without changing its
  model-visible inputs or answer;
- the clean baseline remains independently testable;
- scenario IDs/versions uniquely resolve generator and evaluator definitions.

#### Task 4 — Business root-cause scenarios

Implement retention deterioration, COGS/margin deterioration, and
discount/refund deterioration. Each should include plausible non-drivers,
coherent cross-table economics, deterministic tolerances, and tests proving the
intended mechanism is observable at the configured evaluation scale.

Acceptance:

- the three scenarios pass generator/invariant tests;
- their evaluators reject the correct metric with the wrong population, date
  basis, denominator, or observation window;
- no prompt or business document reveals the injected conclusion.

#### Task 5 — Data-quality and statistical scenarios

Implement missing-day and partial-day data-quality traps plus statistically
meaningful, no-effect, and statistically-significant-but-immaterial experiment
scenarios. Add experiment fields only as required by the typed scenario and keep
the work within V1 basic statistical testing.

Acceptance:

- Auditor correctness includes both injected-defect recall and clean-data false
  positives;
- statistical evaluators check conclusion, confidence interval/effect size,
  practical significance, assumptions, and causal restraint;
- seeded test fixtures have stable known sampling properties.

#### Task 6 — Scenario breadth and evaluator calibration

Add the final confounding/mix scenario and calibrate the full suite with
hand-authored correct and adversarial persisted fixtures before live runs.
Fixtures should include plausible wrong denominators, grain-multiplying joins,
period leakage, unsupported causality, evidence-free numbers, and incomplete
but keyword-rich answers.

Acceptance:

- approximately 10 scenarios are registered and deterministically generatable;
- a fully correct fixture passes each evaluator;
- each targeted analytical defect fails for the intended reason;
- evaluator changes are regression-tested across the entire scenario catalog.

#### Task 7 — Fair single-agent baseline

Implement one generalist analysis agent using the existing Agents SDK,
`AgentRunContext`, workspaces, DuckDB/Python/artifact services, budgets, evidence
schemas, final metric compiler, and report contract. It owns audit, analysis,
statistics, validation/self-critique, and synthesis itself and cannot invoke the
five specialist agents.

For comparison, hold constant:

- raw scenario data, documentation, and question;
- model/provider and model parameters;
- SQL/Python/chart limits and sandbox/security boundaries;
- structured findings, metric, evidence, report, and evaluator contracts.

Architecture-specific calls and token use should be measured, not disguised by
forcing identical internal workflows. Any budget differences must be declared in
the benchmark manifest.

Acceptance:

- construction/permission tests prove the baseline cannot call specialists;
- deterministic tests require the same provenance and bounded execution rules;
- opt-in live smoke tests remain outside normal CI.

#### Task 8 — Resumable benchmark runner

Build an explicit benchmark matrix runner over scenario x architecture x
repetition. It should create unique immutable run IDs, persist the experiment
manifest before execution, resume without rerunning completed cells, isolate
provider/operational failures, and support offline rescoring after evaluator
changes without rerunning agents.

Use at least three repetitions per scenario/architecture for the first declared
benchmark unless cost calibration justifies and documents another count. Run a
small cost-estimation pilot before launching the full matrix.

Acceptance:

- deterministic fake-run tests cover resume, interruption, duplicate IDs,
  failed cells, and offline rescore;
- no benchmark command overwrites an existing workspace;
- paid execution requires an explicit opt-in flag and API credentials, while
  dry-run/plan/offline modes require neither.

#### Task 9 — Aggregation, uncertainty, and reporting

Aggregate per-run metrics by scenario and architecture while retaining every raw
record. Report denominators, completion rates, score distributions, paired
differences where runs share scenarios/configuration, and uncertainty intervals
appropriate to the sample size. Include cost/latency and failure taxonomy; do
not collapse everything into one opaque score.

Acceptance:

- aggregation is deterministic and tested on known records;
- missing/failed runs cannot silently disappear from denominators;
- reports clearly distinguish descriptive results from statistically supported
  architecture differences;
- output can feed README tables later without manually transcribing values.

### Phase 2 Pre-Benchmark Remediation: R1–R6

#### R1 — Make the evaluator architecture-neutral [P0]

Remove role-presence requirements from scoring. Replace them with
capability/output requirements: was the data audited, were required metrics
calculated, was statistical analysis performed when required, was critique or
validation performed, etc.

Most importantly, add regression tests showing that semantically equivalent
outputs from the single-agent and multi-agent architectures receive the same
evaluation result. This is the highest-priority fix.

#### R2 — Harden evidence provenance [P1]

Only successful tool executions and successfully materialized/verified
artifacts should establish evidence. A failed query should never support a
finding merely because some unrelated query succeeded elsewhere in the run.

Add adversarial tests for:

- citing a failed SQL execution;
- citing a failed Python execution;
- citing an artifact from a failed event;
- having an unrelated successful execution present.

#### R3 — Cryptographically/deterministically bind workspaces to scenarios [P1]

Persist enough identity with every workspace to prove what generated it: at a
minimum scenario ID/version, seed, expected source paths and hashes, and
preferably the benchmark/code revision. Offline evaluation should refuse to
score a workspace if this identity does not match the manifest.

This is particularly important because the entire Phase 2 design relies on
immutable persisted workspaces and offline rescoring.

#### R4 — Separate evaluator errors from analytical failures [P1]

An evaluator crash is not evidence that an agent gave a bad answer. Introduce
an actual evaluator-error state and propagate that through the contracts and
aggregation layer.

The denominator semantics matter here: preserve the run in
operational/reliability counts, but exclude evaluator failures from
analytical-quality denominators. Do not silently discard them either.

#### R5 — Harden benchmark-run execution semantics [P2/P2/P3]

Combine the interrupted-resume, pricing-gate, and model-default findings
because they concern benchmark execution rather than analytical validity:

- make resumed attempts use clean state or explicit attempt IDs with cumulative
  cost/latency accounting;
- require known model pricing or an explicit `unknown-cost` acknowledgement
  before proceeding beyond the pilot;
- remove the misleading `configured-model` CLI default and require/pass the
  intended model explicitly.

The last issue is visible in the current CLI: `--model` defaults to
`"configured-model"`, while the README planning example does not specify
`--model`.

#### R6 — Fix scenario-document integrity + full preflight — Gate Reopened

Fix the false model-visible “clean/no injection” statement and add tests
ensuring injected scenarios never retain baseline-only assertions. Then
perform a complete deterministic benchmark preflight:

- all unit/integration tests;
- Ruff;
- architecture-neutral evaluator fixtures;
- corrupted/mismatched-workspace tests;
- failed-evidence tests;
- evaluator-exception tests;
- interrupted-resume tests;
- unknown-pricing tests;
- dry-run of all 10 × 2 × 3 declared cells;
- another Sol High code review focused specifically on benchmark validity.

R6 is the final pre-benchmark gate. Its complete preflight must be rerun after
all later remediation, including R13–R19 and R20–R25; an earlier green test run
does not close R6.

Status: the deterministic preflight and benchmark-validity review were rerun at
this revision after R13–R19. The fresh paid single-agent canary passed on
2026-08-20, but the multi-agent canary failed the production evidence gate. R20
and R21 have since closed the audit-provenance half of that failure at runtime
and offline, R22 closed the hypothesis-transition half, and R23 added the one
bounded correction attempt. R24–R25 must close before the complete R6 preflight
and both live canaries are rerun, so R6 remains open.

Scenario-document integrity is now enforced in code rather than only in a test.
The shared generated document is inherited unchanged by the clean baseline and
by every scenario injected on top of it, so any sentence asserting injection
status is true for at most one of them and is a false premise for the rest.
`scenarios/invariants.py` therefore declares `BASELINE_ONLY_DOCUMENT_CLAIMS`
and raises `document:injection-status-claim` from `check_dataset_invariants`,
which every scenario suite runs through `generate_validated`. The regression in
`tests/test_scenario_catalog.py` is parametrized from `discover_scenarios()`
rather than a hard-coded list, so a newly registered scenario cannot skip it,
and it also asserts the model-visible context contract. A negative fixture
proves each declared claim fails validation. Reintroducing the exact historical
sentence into the generator was verified to fail seven ecommerce-family
scenarios and the baseline check; the three experiment scenarios use a separate
document and are correctly unaffected.

Rerun at this revision — passed:

- full deterministic suite: 508 passed, 16 opt-in live tests deselected;
- Ruff lint (`All checks passed!`) and format (148 files already formatted);
- architecture-neutral evaluator fixtures (equivalence and tool-mix);
- corrupted/mismatched-workspace and source-tamper refusals;
- failed-evidence adversarial fixtures;
- evaluator-exception isolation and denominator preservation;
- interrupted-resume regressions across benchmark, Generalist, and ledger;
- unknown-pricing and unknown-cost acknowledgement regressions;
- scenario-document integrity regressions (all ten registered scenarios);
- the R17 outcome gate, including its calibration against all four retained
  Task 10 pilot workspaces;
- Docker-backed integration tests, executing real containers;
- dry-run of the complete declaration: 60 cells, 60 unique run IDs, 60 unique
  workspace paths, 10 scenarios × 2 architectures × 3 repetitions, with the
  R19 pilot set partitioning 30 cells per architecture;
- every retained `.runs/` artifact still loads at this revision (10 manifests,
  18 ledger states), and offline rescore and report still run against retained
  benchmark evidence.

The benchmark-validity review also found and closed residual risks that the
earlier preflight did not exercise:

- a failed pilot observation can no longer be retried within the same manifest
  until a favorable result is selected;
- pilot strata must form an exact, non-overlapping partition of every declared
  architecture/scenario cell;
- new manifests bind the exact Git revision and, for a dirty tree, a canonical
  working-tree digest, and execution refuses a changed repository state;
- mandatory blocked data audits retain the `data_quality` failure category in
  both architectures;
- interruption-like `BaseException` exits mark unreconciled usage incomplete;
- incomplete usage cannot authorize a pilot report or be treated as known
  zero cost; and
- a non-completed pilot stratum stops later paid strata instead of spending
  further before refusal.

Rerun at this revision — still open:

- the multi-agent canary in `tests/test_strict_output_canary_live.py`. The
  2026-08-20 provider-backed run reached Docker and the configured model but
  failed with `LeadEvidenceError: lead outputs cite no executed evidence:
  hypothesis:H2`; the Lead cited `completed_data_audit` rather than a successful
  execution or verified artifact. The single-agent canary and the deterministic
  canary-coverage assertion passed in the same run (`1 failed, 2 passed`).

The earlier 369-test preflight remains historical evidence only.

### Phase 2 Follow-up Review Remediation: R7–R12

The post-R5 code review identified residual gaps not covered by the original
regression fixtures. These tasks refine R1, R3, R4, and R5 and add explicit
immutability requirements for offline outputs.

#### R7 — Make tool use capability-driven rather than mandatory [P0]

Status: implemented and verified in the evaluator and catalog rules. The final
R6 preflight passed; the catalog evaluator version is `1.1` so this semantic change
cannot be confused with earlier `1.0` scoring.

Remove unconditional SQL- and Python-presence checks from generic provenance
scoring. Scenario rules should declare the analytical capabilities and typed
outputs they require; the evaluator should not prescribe which tool produced a
valid result when the tool choice is not part of the estimand.

Acceptance:

- a non-statistical scenario can pass with verified SQL-only evidence when all
  required outputs and provenance are present;
- adding an unnecessary successful Python execution does not change the result;
- a statistical scenario requires its typed statistical assessment and evidence
  without requiring a Statistician role or a particular producer architecture;
- equivalence fixtures vary role traces, specialist-result placement, and
  SQL/Python tool mixes while holding semantic outputs constant;
- genuinely missing required capabilities still fail through a named,
  scenario-specific check.

#### R8 — Enforce workspace identity at every offline evaluation boundary [P1]

Status: implemented and verified in the offline engine, standalone workspace
CLI, manifest rescore API, and benchmark runner.

Make persisted identity authoritative wherever it exists. The supplied or
resolved evaluator rules must match the workspace scenario ID, scenario
version, and evaluator version. Manifest rescoring must compare the complete
expected identity for every record, including non-completed records, before
evaluation or error classification.

Acceptance:

- standalone evaluation refuses a workspace whose persisted scenario or
  evaluator version differs from the selected rules;
- the standalone CLI derives rules from persisted identity or requires an
  explicit selection that is verified against it;
- manifest rescore checks manifest ID, run ID, scenario/version, evaluator
  version, architecture, repetition, seed, source hashes, and code revision for
  every persisted record;
- missing, corrupt, mismatched, or source-tampered benchmark identities produce
  an explicit refusal rather than an analytical failure or `not_evaluated`;
- legacy unbound workspaces, if retained, use an explicit diagnostic-only path
  and cannot enter a benchmark manifest.

#### R9 — Consolidate offline rescoring and make it aggregation-safe [P1]

Status: implemented and verified through the shared manifest-rescore engine,
benchmark API, and both offline CLIs.

Use one canonical manifest-rescore implementation for the Python API and both
CLIs. Isolate evaluator exceptions per record, retain operational outcomes,
update evaluator versions deliberately, and recompute all aggregates and
architecture comparisons from the new run results before writing output.

Acceptance:

- one evaluator exception does not abort scoring of unrelated records;
- a completed run with an evaluator exception receives `error`, no score, and
  remains in operational/reliability counts;
- non-completed runs retain their lifecycle outcome and are not converted into
  analytical failures;
- analytical-quality denominators exclude evaluator errors while the failure
  taxonomy reports them;
- rescored aggregates and paired comparisons are recomputed and cannot retain
  values from the input manifest;
- tests exercise a non-empty multi-record manifest, including one evaluator
  crash and previously populated aggregates.

#### R10 — Bind the cost pilot to its recorded benchmark cell [P2] — Implemented

Status: implemented and verified in the benchmark runner and manifest
contracts.

Treat the pilot report as a derived view of an immutable run record, not an
independent source of truth. Bind it to the manifest/model/configuration and a
canonical digest of the referenced run record, then re-derive and compare usage,
latency, pricing availability, and cost at the full-run gate.

Acceptance:

- changing pilot cost, usage, latency, run ID, model identity, matrix size, or
  record digest causes the full run to refuse execution;
- unknown cost can proceed only through an explicit acknowledgement tied to the
  exact manifest and pilot record;
- known pricing requires a matching pricing model and recorded cost breakdown;
- adversarial tests prove that replacing `null` cost with `0.0` cannot bypass
  the acknowledgement.

#### R11 — Persist append-only attempt history and reconcile totals [P2] — Implemented

Status: implemented and verified in the run-state schema, persistent ledger,
orchestration lifecycle, and benchmark record contract.

Replace the single overwritten attempt ID with typed, append-only attempt
records. Each attempt should retain its identity, timestamps, terminal outcome,
usage delta, cost availability/delta, and elapsed time while the run record
retains reconciled cumulative totals.

Acceptance:

- resuming a workspace appends a new attempt without changing prior attempt
  records;
- agent and tool events can be attributed to the attempt that produced them;
- cumulative usage, known cost, and elapsed time equal the sum of attempt-level
  values, with unknown cost represented explicitly rather than as zero;
- interrupted-before-record and interrupted-after-partial-write fixtures recover
  without double counting;
- the final benchmark record exposes or references the full attempt history,
  not only the latest attempt ID.

#### R12 — Make every offline output non-destructive and atomic [P2] — Implemented

Status: implemented and verified through the shared exclusive atomic writer,
canonical rescore/report entry points, and legacy manifest CLI delegation.

All offline evaluation, rescore, and report writers must refuse the input path
and existing output files. Consolidate exclusive/atomic writing behavior and
retire or delegate legacy CLI paths so documentation cannot direct users to a
less-safe implementation.

Acceptance:

- input and output resolving to the same path are rejected before evaluation;
- an existing output is never overwritten implicitly;
- writes validate the full payload before an atomic publication operation that
  fails if the destination already exists, without modifying the source
  manifest;
- the legacy manifest-evaluation CLI delegates to the canonical R9 path or is
  removed from supported benchmark documentation;
- regression tests cover identical paths, symlink/relative aliases, existing
  outputs, evaluator failure, and successful exclusive creation.

### Phase 2 Post-Pilot Remediation: R13–R19

The retained Task 10 pilot workspaces exposed live execution and accounting
gaps that deterministic R1–R12 fixtures did not detect. R13–R19 are required
before Task 10 can resume. Completing them reopens R6: run the complete final
preflight again after all seven tasks are implemented.

#### R13 — Make every analytical agent output strictly structured [P0] — Complete

Status: complete. Both opt-in live canaries passed on 2026-08-19
(`uv run pytest -m live tests/test_strict_output_canary_live.py`, 3 passed in
74.01 s), so every acceptance criterion is met. They must be rerun inside the
reopened R6 preflight after R14–R19.

Segment dimensions are now a typed `MetricDimension` list (`name`/`value`)
instead of an open-ended JSON object, so `MetricObservation`,
`MetricComparison`, `MetricConflict`, `StatisticalAssessment`,
`StatisticalExpectation`, and evaluator ground truth all share one
strict-compatible representation. Every production agent (Generalist, Lead,
Analyst, Statistician, Data Auditor, Critic) builds its output through
`agents.output_contract.strict_output_type`, which compiles the strict schema
at agent-construction time; no agent passes `strict_json_schema=False`. The
permissive re-parse of `final_output` is replaced by `require_strict_output`,
which raises `AgentOutputContractError` (a `ModelBehaviorError`), and the Lead's
specialist-output extractor no longer returns raw text when validation fails.
Persisted pre-R13 workspaces that stored the mapping form still load through a
documented compatibility coercion, so retained pilot evidence remains
evaluable. `tests/test_strict_agent_outputs.py` holds the deterministic
regressions and `tests/test_strict_output_canary_live.py` holds the opt-in
per-architecture live canaries.

Replace output fields that require open-ended JSON object keys with
strict-schema-compatible typed representations, then enable strict JSON Schema
for the Generalist, Lead, Analyst, and Statistician. Preserve the analytical
meaning and deterministic evaluator behavior of dimensions while removing the
permissive final-output parsing path that failed the retained pilots.

Acceptance:

- every production agent output type compiles through the installed Agents SDK
  strict-schema converter;
- no production analytical agent opts out with `strict_json_schema=False`;
- dimension names and values round-trip deterministically through the new typed
  representation without changing the estimand seen by the evaluator;
- malformed, truncated, and extra-field outputs fail as explicit model/schema
  failures, while valid strict fixtures parse successfully;
- one opt-in live canary for each architecture completes its top-level strict
  output contract before a paid benchmark pilot is attempted.

#### R14 — Persist usage and cost across failed model calls [P0] — Implemented

Status: implemented.

`agents.model_usage` records usage at the provider response boundary through
`ModelUsageHooks.on_llm_end`, and every agent run goes through
`run_agent_with_usage`, which reconciles what was recorded against the run's
authoritative cumulative usage on both the success and the exception path.
`AgentsException` carries that total on `run_data.context_wrapper`, so an
invalid-JSON final output or a turn-limit failure keeps every token the
provider reported. Reconciliation records only the remainder, and remainders
are clamped at zero, so no response is counted twice or removed. Nested
specialist runs share the parent's accumulator and are additionally recorded by
`_NestedSpecialistHooks`.

When no authoritative total is available, the ledger marks usage incomplete
(`AnalysisRunState.usage_complete` and `AttemptRecord.usage_complete`) and
`record_cost_estimate` refuses to publish a cost: the run and attempt cost
become `unavailable` with an explicit note rather than a confident `$0.00`.
Benchmark records surface the same facts through `UsageSummary.complete` and
the ledger's cost note. Incompleteness reuses the existing verified
`unavailable` representation rather than adding a third cost-availability
state, so the R10 unknown-cost gate and aggregation keep their semantics.
`tests/test_model_usage_accounting.py` holds the regressions.

Record model usage incrementally at the response boundary instead of only after
`Runner.run()` returns. Parsing errors, max-turn failures, and later lifecycle
errors must retain the usage already returned by the provider. If usage cannot
be reconciled, represent cost as partial or unavailable rather than known zero.

Acceptance:

- successful and failed model responses contribute exactly once to cumulative
  and attempt-level usage;
- a final-output parsing exception retains the usage of the response that
  triggered it;
- a max-turn run retains every completed request before the limit;
- known pricing produces a reconciled cost only when the corresponding usage is
  complete; incomplete usage cannot be published as `$0.00`;
- failure-path fixtures cover Generalist, Lead, and specialist invocations and
  prove attempt totals equal the sum of recorded response deltas.

#### R15 — Give the single-agent runner a complete attempt lifecycle [P1] — Implemented

Status: implemented.

`GeneralistRunner.run` now follows the same append-only attempt protocol as the
multi-agent runner: `begin_attempt()` opens one attempt after the ledger is
constructed and before `_agent_context`, so the run configuration carries the
attempt ID and every agent event, tool event, usage delta, and cost is
attributed to it. Completed and blocked exits finish the attempt as
`COMPLETED`/`BLOCKED`, the failure handler finishes it as `FAILED` with the same
message the run state records, and the `finally` block closes a `BaseException`
exit (for example `KeyboardInterrupt`) as `INTERRUPTED`. Runtime metadata is
finalized before the attempt is closed, so the terminal record carries usage,
cost availability, and elapsed time. Resume reuses the ledger's existing
append-only semantics, so a new attempt is appended without altering prior
records or recounting their deltas.

`tests/test_generalist_attempt_lifecycle.py` drives the real runner with only
the SDK boundary stubbed, and covers attempt-before-execution, all four
terminal exits, resume after failure and after interruption, and an
attempt-protocol equivalence check against `AnalysisRunner`. A benchmark-level
regression in `tests/test_benchmark_runner.py` runs the production
`GeneralistRunner` through the matrix and asserts non-null attempt identity and
a reconciled attempt history.

Bring `GeneralistRunner` under the same append-only attempt protocol as the
multi-agent runner. Every start, completion, block, failure, interruption, and
resume must create or finish the appropriate typed attempt record and attribute
agent/tool events to it.

Acceptance:

- a new single-agent run begins one attempt before agent execution;
- completed, blocked, failed, and interrupted exits finish that attempt with
  matching terminal status, timing, usage, cost availability, and error;
- resuming a single-agent workspace appends a new attempt without modifying the
  prior record or double-counting its totals;
- single-agent benchmark records expose non-null attempt identity and full
  attempt history;
- tests exercise the real `GeneralistRunner` lifecycle rather than relying only
  on a fake benchmark executor that manages attempts itself.

#### R16 — Retain interrupted benchmark cells and resume them safely [P1] — Implemented

Status: implemented.

Interrupting a declared cell now materializes a cancelled run record through
`BenchmarkRunner._interrupted_record` and persists it, with the manifest still
`running`, before the manifest is marked `aborted`. The record carries
`LifecycleStatus.CANCELLED` with the new `FailureCategory.INTERRUPTED`, the
workspace path, the attempt history, and whatever partial usage, cost
availability, and latency the workspace persisted. Because a record now exists,
existing aggregation counts the cell as an observed operational failure
(`lifecycle:interrupted`) instead of inflating `missing_repetitions`.

Both runners reconcile the workspace's top-level status with its interrupted
attempt through the new `RunStatus.CANCELLED` and `AnalysisLedger.mark_cancelled`,
so an interrupted workspace no longer advertises `running` forever. An explicit
`resume=True` retries only cancelled cells — completed, failed, and blocked
records are real observations and are never silently re-run — and the retry
appends a new attempt to the same immutable cell, leaving the interrupted
attempt in the history verbatim. `run_pilot` refuses to publish a cost pilot
built from an interrupted cell, which would otherwise scale a partial
observation across the whole matrix.

`FailureCategory.INTERRUPTED` is shared with R18's taxonomy work; R16 needs it
so the retained record is machine-readable as an interruption rather than an
`other` failure. `tests/test_benchmark_interruption.py` holds the regressions,
covering interruption both before agent execution and after partial
persistence.

Materialize an operational run record when a declared cell is interrupted
instead of allowing it to disappear as a missing observation. Reconcile the
workspace's top-level status with its interrupted attempt, preserve partial
usage/cost/latency, and permit an explicit resume to append a new attempt for
the same immutable cell.

Acceptance:

- interrupting a cell writes a cancelled/interrupted lifecycle record before
  marking the manifest aborted;
- the record retains the workspace, attempt history, partial usage, cost
  availability, latency, and interruption reason;
- aggregate denominators count the cell as an observed operational failure, not
  a missing repetition;
- resume retries the interrupted cell through a new append-only attempt while
  leaving prior attempt evidence unchanged;
- interruption before agent execution and after partial persistence are both
  covered without losing or double-counting evidence.

#### R17 — Make the preflight sensitive to benchmark outcomes [P1] — Implemented

Status: implemented. The post-R19 deterministic R6 rerun and validity review
are complete; fresh provider-backed canaries remain an R6 release-gate item.

`benchmark/preflight.py` holds one shared, outcome-sensitive smoke gate:
completion and error state, a report that is persisted *and* readable on disk,
usage that is nonzero or explicitly unavailable, cost that is explicit rather
than a silent zero, and an attempt history that reconciles to the run totals
with no attempt left running. It also asserts the architecture's role
boundary. The same gate is called by both live lifecycle smoke tests, by both
live canaries, and by deterministic failure fixtures, so the assertions that
authorize a paid pilot are the ones proven to reject broken runs.

`tests/test_preflight_smoke_gate.py` drives the production runners to real
outcomes and proves that invalid JSON, lost usage (a completed run claiming
complete usage with zero tokens), a dropped interruption (an attempt left
running), a missing attempt history, unreconciled attempt usage, and a missing
report file all fail the gate, while a completed run and an honestly
unavailable usage total pass. It also asserts, deterministically, that one
bounded live canary per architecture still exists and uses the gate, so canary
coverage cannot regress silently in normal CI.

The gate is calibrated against real evidence rather than invented thresholds: a
regression runs it against all four retained Task 10 pilot workspaces and
requires every one to fail, with the single-agent pilots additionally failing
the usage and attempt-history checks that R14 and R15 fixed. Live specialist
smoke tests now also assert recorded, complete usage.

Closure evidence: Ruff, the Docker-backed integrations, the adversarial
fixtures, and the 60-cell dry-run pass at this revision. The provider-backed
canaries that consume this gate still require a fresh paid run under R6.

Replace regressions that assert permissive configuration or mere artifact
presence with assertions on the outcomes Task 10 actually requires. The live
smoke path must fail unless the architecture completes, emits its report,
records usage, and publishes a valid attempt history.

Acceptance:

- tests no longer require non-strict output mode and instead verify strict
  schema compilation for every production output type;
- Generalist and multi-agent lifecycle tests assert completion/error state,
  report persistence, nonzero or explicitly unavailable usage, and reconciled
  attempt history;
- deterministic failure fixtures prove invalid JSON, lost usage, and dropped
  interruptions cannot satisfy the smoke assertions;
- the opt-in live preflight runs one bounded canary per architecture before any
  matrix pilot;
- after all later remediation, including R20–R25, the full R6 suite, Ruff,
  Docker-backed integrations, adversarial fixtures, 60-cell dry-run, and both
  live architecture canaries all pass again.

R13 already contributes the strict-schema compilation checks and the two opt-in
live canaries this task must run; R17 adds the remaining outcome assertions.

#### R18 — Preserve explicit blocked reasons and accurate failure taxonomy [P1] — Implemented

Status: implemented.

Orchestration now persists a typed `RunBlockReason` and a human-readable
`block_detail` on the run state for every non-completion, through
`AnalysisLedger.mark_blocked`, `mark_failed`, and `mark_cancelled`. Both
architectures name the originating condition where it is known:
`orchestration/block_reasons.py` classifies exceptions by type — only
`BudgetExhaustedError` is budget exhaustion, `MaxTurnsExceeded` is an agent
bound, and a structured-output violation is a schema failure — while the
constraint decisions themselves are stated directly (an unresolved Critic
revision is `validation_revision`, an unresolved objective-critical
continuation is `unresolved_follow_up`, a blocked mandatory audit is
`data_quality`, and an interruption is `interrupted`).

`BenchmarkRunner._coerce_result` reads that persisted reason and maps it through
`category_for_block_reason`, replacing the previous behavior of hard-coding
`FailureCategory.BUDGET` for every blocked run and guessing every other category
from substrings in an error message. Prose inference survives only as the
compatibility path for pre-R18 workspaces that persisted no reason.
`FailureCategory` gains `validation`, `unresolved_follow_up`, and `data_quality`
so analytical constraints are not reported as operational faults.

Blocked and cancelled records keep `EvaluatorStatus.NOT_EVALUATED` rather than
`FAIL`, so they remain observed operational outcomes and are never converted
into analytical evaluator failures, while still counting in the operational
denominators. `tests/test_failure_taxonomy.py` covers the major block paths for
both architectures and asserts that the aggregate taxonomy reproduces the
per-record categories exactly.

Do not classify every blocked analysis as a budget failure. Persist a
machine-readable block reason from orchestration and map budget exhaustion,
agent/schema failure, unresolved analytical follow-up, validation revision,
and user/provider interruption to accurate operational categories.

Acceptance:

- every non-completed run has an explicit lifecycle status, category, and
  human-readable reason derived from the originating condition;
- genuine budget exhaustion is categorized as budget, while self-critique,
  unresolved follow-up, schema failure, and interruption are not;
- blocked and cancelled runs remain visible in operational denominators and are
  not silently converted into analytical evaluator failures;
- aggregate failure taxonomy reproduces the per-record categories exactly;
- fixtures cover the major block paths for both architectures.

#### R19 — Calibrate the paid pilot across architectures and workload classes [P2] — Implemented

Status: implemented.

Planning now freezes a `PilotSetDeclaration` into the manifest: one stratum per
declared architecture by default, with explicit workload strata available by
naming scenario IDs. The manifest validator requires every declared
architecture to be represented and the strata to partition the declared matrix.
`run_pilot` measures one cell per stratum and writes a
`BenchmarkPilotSetReport` (version 2.0) that retains every per-pilot
observation, bound to its immutable run record by the existing R10 digest rule.

The estimate is a stratified sum, not a single-cell extrapolation: each stratum
contributes mean-per-cell × its planned cells, with an explicit low/high range
from the observed per-stratum minimum and maximum, and the scaling method is
named in the report. If any stratum's cost is unknown, the whole matrix cost is
published as unavailable rather than silently understated.

The full-run gate verifies every declared stratum, refuses a pilot whose record
is missing, did not complete, belongs to another stratum, or whose usage, cost,
or latency no longer reconciles with the immutable record, and recomputes the
matrix estimate from the retained observations. Two fingerprints enforce
re-planning: `canonical_manifest_declaration_digest` covers model identity, turn
budgets, matrix size, and the declared pilot set, and
`output_schema_fingerprint` covers the production agent output contracts, so any
such change requires a new manifest version before paid execution. Mutable
execution state — records, aggregates, status, and the per-scenario source
identities R8 verifies separately — is excluded so a matrix run cannot
invalidate its own pilot. Unknown-cost acknowledgement now binds every affected
pilot record digest, not just one. `tests/test_pilot_set_calibration.py` holds
the regressions.

Replace a single first-cell linear extrapolation with a declared pilot set that
can expose material architecture and workload differences. Bind every pilot
record to the immutable manifest using the existing R10 identity/digest rules,
then derive a transparent range or stratified estimate for the remaining
matrix. Turn budgets may be changed only in a new manifest version after pilot
evidence is reviewed.

Acceptance:

- the pilot set contains at least one declared cell for each architecture and
  identifies any additional workload strata used for estimation;
- the full-run gate verifies every required pilot record and refuses failed,
  missing, mismatched, or unreconciled pilot evidence;
- estimated cost and latency state the scaling method and retain per-pilot
  observations rather than presenting one cell as representative of all 60;
- unknown-cost acknowledgement is bound to each affected pilot record and the
  exact manifest;
- any model, schema, turn-budget, matrix-size, or pilot-set change requires a
  new manifest/version before paid execution.

### Phase 2 Live-Canary Provenance Remediation: R20–R25

The post-R19 live R6 rerun passed the single-agent canary but failed the
multi-agent canary after the model returned valid strict JSON. The Data Auditor
had executed its checks successfully, but its typed audit output did not carry
canonical evidence references for table observations or limitations. The Lead
was told to treat that audit as evidence, could not discover the auditor's tool
references, and resolved hypothesis `H2` using the invented reference
`completed_data_audit`. The production evidence gate correctly rejected it.

R20–R25 close the cross-agent provenance contract rather than weakening that
gate or retrying until a favorable model output appears. All six tasks must be
implemented before another complete R6 preflight or Task 10 manifest. R20–R23
are implemented; R24–R25 remain open.

#### R20 — Preserve typed audit provenance across architecture boundaries [P0] — Implemented

Make every material audit observation that can influence a candidate answer
carry canonical provenance. Replace provenance-free warning/limitation strings
where necessary with typed evidence-bearing observations, and expose those
references unchanged when the persisted audit is supplied to the Lead. The
multi-agent architecture must give the Lead the same usable provenance that the
single-agent architecture retains from its own tool calls without granting the
Lead SQL/Python execution or access to internal state.

Acceptance:

- material audit issues, table observations, warnings, and limitations used
  downstream carry exact successful tool-event, query/script, or verified
  artifact references;
- completed audits cannot persist material claims with missing, failed,
  ambiguous, or fabricated provenance;
- the Lead receives a bounded typed audit evidence catalog and never needs a
  pseudo-reference such as `completed_data_audit`;
- semantically equivalent single-agent and multi-agent audit outputs expose
  equivalent claim-level provenance;
- strict output schemas, fingerprints, persisted contracts, and compatibility
  handling are versioned for the audit-contract change.

Implemented. Audit contract `2.0` makes every material audit claim
evidence-bearing: table warnings and run limitations are typed
`AuditObservation` objects with a `statement` and `evidence_refs`, and
`TableAudit` carries references for the row count, date coverage, duplicate
rate, and missingness it asserts. `agents.audit_evidence.audit_claims`
enumerates claims with positional, collision-free IDs, so two claims cannot
share an ID even when a model repeats a table name or issue ID.

Both architectures persist through one boundary, `persist_audit_result`, used
by the multi-agent runner, the generalist persistence path, and the
nested-auditor hook. It canonicalizes each claim against the ledger with the
same resolver that validates Lead output and refuses to persist a non-blocked
audit whose material claims have missing, failed, ambiguous, or fabricated
provenance. A blocked audit stays exempt so it is still reported under its own
blocked-audit condition.

The Lead receives a bounded typed `AuditEvidenceCatalog` under
`DATA_AUDIT_EVIDENCE_CATALOG_JSON`: one entry per resolving claim, its canonical
references, and the flattened citable set. Unresolved claims are omitted, a
`claim_id` is explicitly a label rather than a reference, and the Lead gains no
execution tool or internal-state access. The `COMPLETED_DATA_AUDIT_JSON`
heading that produced the invented `completed_data_audit` citation is gone, and
a regression pins that the production gate still rejects that exact reference.
`inspect_relations` now returns its persisted `tool_event_id`, so a row count
established by a successful tool call has something to cite.

Versioning: persisted state is written at `CURRENT_STATE_SCHEMA_VERSION =
"1.1"`, accepted alongside `legacy` and `1.0`; `output_schema_fingerprint()`
covers `AuditResult` and `GeneralistResult`, so the change invalidates any
existing pilot estimate and forces a new manifest version before paid
execution; and contract `1.0` payloads still load with statements preserved and
`evidence_refs` explicitly empty, so retained workspaces stay readable without
gaining invented provenance. Decision record
`docs/decisions/0009-audit-provenance-across-architectures.md` holds the
rationale and `tests/test_audit_provenance.py` holds the regressions.

#### R21 — Enforce audit provenance in capability and offline scoring [P0] — Implemented

Extend the evaluator's data-audit capability and provenance checks so a
completed `AuditResult` or expected issue ID is insufficient by itself. Audit
claims used to satisfy scenario requirements must resolve through the same
successful-execution and verified-artifact boundary as findings, metrics,
hypotheses, and statistical assessments.

Acceptance:

- data-audit capability passes only when the required typed audit outputs and
  their provenance are present;
- required issue IDs backed only by failed SQL, failed Python, failed artifacts,
  missing files, or fabricated references fail offline evaluation;
- an unrelated successful execution cannot rescue an unsupported audit claim;
- clean-audit evidence proves the performed checks without requiring a
  particular tool mix or producer role;
- architecture-equivalence fixtures give semantically identical audits the same
  result, and the evaluator version is advanced deliberately.

Implemented. `evaluation.primitives.resolve_audit_claims` resolves every
material audit claim against the same successful-execution and verified-artifact
boundary used for findings, metrics, hypotheses, and statistical assessments.
The claim projection moved from `agents.audit_evidence` into `schemas.audit` so
the evaluator resolves identical claim IDs without importing anything that
executes agents, and `evaluate_workspace` resolves executed references once and
shares that one set with the audit, capability, and provenance checks.

`capability:data_audit` now requires a completed audit that states at least one
material claim with no unsupported claim, and names the unsupported claim IDs
when it fails. Each required issue ID is scored twice: the existing presence
check plus `data_quality:required_provenance:{id}`, so an expected defect
asserted from failed SQL, a failed script, a deleted artifact, a missing file,
or an invented reference fails while its presence check still passes — the
distinction stays visible instead of scoring as recall.
`data_quality:claim_provenance` covers every material claim, and
`data_quality:clean_audit_evidence` requires a clean audit to demonstrate a
performed check through a supported table profile or limitation.

The clean-audit rule preserves R1 and R7: references from `run_sql`,
`run_python`, `inspect_relations`, or a verified artifact satisfy it equally,
and no check inspects the producing role. Architecture-equivalence fixtures
assert that five-role and single-role workspaces holding the same audit produce
byte-identical check tuples in both the passing and failing direction.

The catalog evaluator version advanced deliberately from `1.1` to `1.2`. Two
consequences are intended, not worked around: offline rescore of the retained
`1.1`-bound Task 10 manifests is refused unless rules pinned to `1.1` are
supplied explicitly, and the retained Phase-1 canonical workspace — whose audit
predates contract 2.0 — goes from 4 to 7 offline failures because its claims
correctly resolve as unsupported. `tests/test_audit_provenance_scoring.py`
holds the regressions.

#### R22 — Align hypothesis evidence contracts and validate state transitions [P1] — Implemented

Make the model-visible instructions, `Hypothesis` contract, state tools, final
Lead validation, and offline evaluation agree on one rule: an open hypothesis
may have no evidence, but every supported, rejected, or inconclusive hypothesis
must cite canonical executed evidence. Validate this when the state transition
is requested rather than accepting poisoned state and failing only after the
final model response.

Acceptance:

- Lead and Generalist instructions explicitly require exact evidence references
  for every resolved hypothesis, including qualitative audit hypotheses;
- `record_hypothesis` refuses an invalid resolved transition before mutating the
  current hypothesis or append-only history and returns an actionable typed
  error;
- open hypotheses remain usable without manufactured evidence;
- resume cannot inherit an invalid resolved hypothesis from a rejected state
  transition;
- deterministic tests cover open-to-supported, rejected, and inconclusive
  transitions using direct, aliased, missing, and failed references.

Implemented. `schemas.hypotheses.hypothesis_requires_evidence` is the one
predicate the state tool, final Lead validation, and offline evaluation all
call, so the four boundaries cannot drift on which transitions need provenance.
The rule is stated in the `Hypothesis` `status` and `evidence_refs` field
descriptions — which the strict output schema carries to the provider, unlike a
Pydantic validator, which would appear in no schema and would misfile a
correctable provenance problem as a strict-schema failure — and in the Lead and
Generalist instructions, including for qualitative and data-quality hypotheses
resolved from the audit.

`record_hypothesis` resolves citations before the ledger is touched. A refused
resolution leaves the current hypothesis, the append-only history, the
`rejected_hypotheses` index, and the persisted file unchanged, so a resumed run
reads the pre-transition state. The refusal is a typed
`invalid_hypothesis_transition` tool error carrying the hypothesis ID, requested
status, unresolved and resolved references, a bounded list of available
references, and an explicit remedy. An accepted resolution persists its
canonical references; an open hypothesis is left untouched, references included,
because dropping one it still intends to use would be the silent rewrite this
contract prevents.

Offline evaluation now checks the append-only history as well as the current
hypothesis list, so revising a claim cannot erase that it was once asserted
without support. The `Hypothesis` field descriptions are part of the strict
output schema, so `output_schema_fingerprint()` changed again and a new manifest
is required before paid execution. Decision record
`docs/decisions/0010-hypothesis-evidence-rule.md` holds the rationale and
`tests/test_hypothesis_transitions.py` holds the regressions.

#### R23 — Add bounded correction for semantic provenance failures [P1] — Implemented

Treat a strict-schema-valid Lead response with invalid evidence references as a
bounded semantic contract failure that may receive one explicit correction
attempt. The correction must identify the invalid output fields and the
canonical references already available; it must not rerun specialists, alter
claims silently, or weaken provenance validation.

Acceptance:

- one configured correction attempt is available after strict output succeeds
  but Lead provenance validation fails;
- the correction prompt contains the invalid field IDs and a bounded canonical
  evidence catalog, without evaluator-only data or hidden state;
- correction reuses existing executions and does not spend additional SQL,
  Python, specialist, or Critic budget;
- both model calls, usage, latency, and terminal outcomes remain observable and
  attributable to the active attempt;
- a second invalid response terminates with an explicit provenance failure; no
  output is silently rewritten or repeatedly retried.

Implemented. `AgentRunConfig.evidence_correction_attempts` is validated
`ge=0, le=1` and the correction agent runs with `max_turns=1`, so the bound is
structural rather than conventional. That agent has no tools at all — no SQL, no
Python, no specialist delegation, no Critic — so it reuses the run's existing
executions and spends no additional resource budget; the regression asserts
every budget counter and the tool-event count are unchanged across a corrected
run.

`LeadEvidenceError` now carries typed `invalid_fields`, and the correction
request contains those field IDs, the validator's message, the previous output
verbatim, and a bounded `EvidenceCorrectionCatalog` of executed tool-event IDs
and query/script paths, persisted specialist findings with their canonical
references, and the R20 audit evidence catalog. Every entry derives from the
run's own executed evidence; no scenario ground truth, evaluator rule, or
orchestration internal reaches the prompt.

The application never edits a citation. The corrected response passes through
the identical validating persistence boundary that rejected the first one, and a
second invalid response raises the provenance failure and ends the run. Both
model calls stay observable as agent events with real timing, bound to the
active attempt, with usage from both accumulating through the normal
response-boundary accounting.

Two deliberate scope decisions: the single-agent baseline gets the same
allowance through `run_generalist`, because giving one architecture a second
attempt at valid provenance would be measured by the benchmark as an
architecture difference; and `AuditEvidenceError` stays terminal, since the
audit is a preflight the rest of the run builds on. The configured allowance is
frozen into the manifest's `run_configuration.parameters` and therefore its
declaration digest. Decision record
`docs/decisions/0011-bounded-evidence-correction.md` holds the rationale and
`tests/test_evidence_correction.py` holds the regressions.

#### R24 — Make citation resolution lossless and consistent [P1]

Use one citation-resolution contract at runtime, Critic validation, offline
evaluation, and rescoring. Never discard an unresolved citation merely because
another citation resolves, and do not let `any(valid_reference)` conceal a
failed, fabricated, or unrelated reference.

Acceptance:

- canonicalization returns resolved and unresolved references explicitly rather
  than silently dropping invalid entries;
- every cited reference required for a material claim must resolve to successful
  execution or a verified artifact at all evaluation boundaries;
- mixed valid/failed, valid/fabricated, ambiguous alias, cyclic alias, and
  unrelated-success fixtures fail consistently;
- direct and uniquely aliased specialist references canonicalize
  deterministically without changing claim meaning;
- runtime validation and offline rescoring produce the same provenance result
  for the same persisted workspace.

#### R25 — Classify provenance failures and close the live regression gap [P2]

Give semantic evidence failures an explicit operational taxonomy and retain the
2026-08-20 canary failure as a deterministic regression. Replace keyword-only
and empty-audit mocks with a production-shaped audit-to-Lead fixture, then rerun
the complete R6 gate after R20–R24.

Acceptance:

- `LeadEvidenceError` and equivalent semantic citation failures map to a named
  run reason and benchmark failure category rather than `other`;
- the category propagates through attempt history, benchmark records,
  aggregation, failure reports, and canonical offline rescore;
- a deterministic fixture reproduces the provenance-free audit handoff and the
  `completed_data_audit` failure without a provider call;
- lifecycle tests use evidence-bearing audits and resolved hypotheses rather
  than empty audits or unchecked synthetic references;
- after R20–R24, all deterministic tests, Ruff, Docker integrations, adversarial
  provenance suites, the 60-cell dry-run, retained-artifact checks, and both
  paid live architecture canaries pass in one fresh R6 preflight.

#### Task 10 — Execute and publish the Phase 2 benchmark

Status (2026-08-19): attempted but blocked before the paid matrix. The
retained manifests are under `.runs/phase2-task10-20260819/`:

- `phase2-task10-20260819-luna-v1`: one failed multi-agent pilot record,
  `ModelBehaviorError: Invalid JSON when parsing model output`; the persisted
  28,825 tokens and `$0.00372068` omit the failed Lead response and therefore
  are incomplete, not a trustworthy pilot cost.
- `phase2-task10-20260819-luna-v2`: interrupted after partial multi-agent
  progress. Its workspace retains an interrupted attempt with usage and cost,
  but the manifest incorrectly omitted the declared cell.
- `phase2-task10-20260819-luna-v3`: one failed single-agent pilot record with
  the same invalid-JSON error; the failed Runner call lost provider usage and
  was incorrectly represented as known zero cost.
- `phase2-task10-20260819-gpt55-v1`: one failed single-agent pilot record with
  the same invalid-JSON error, lost usage, and unavailable pricing.

Each plan declared 60 cells (ten scenarios × two architectures × three
repetitions), but no plan passed the pilot gate, so the full matrix was not
executed. The failed records were rescored offline with the corrected R9
semantics and published as failure-only aggregate reports. Non-completed
records remain operational failures, are `not_evaluated`, and have no
analytical score; missing cells are not treated as observations. These are
retained attempt artifacts rather than benchmark results.

After R1–R25 are complete, including a new final R6 preflight after R20–R25,
freeze a benchmark manifest, run the declared
single-agent and five-agent matrix, evaluate every persisted workspace offline,
inspect failures without changing rules mid-experiment, and publish the actual
results and limitations.
If a defect requires code/evaluator changes, version the benchmark and rerun the
affected declared matrix rather than silently patching scores.

Acceptance:

- raw manifests, run records, evaluator results, pilot reports, and aggregate report are
  retained;
- README/project documentation reports real task success, numerical accuracy,
  unsupported claims, operational reliability, cost, and latency;
- conclusions acknowledge sample size, model specificity, evaluator limitations,
  and failed runs;
- no result is invented or selected only because it favors the multi-agent
  system.

### Key experiment

Build a **single-agent baseline** and compare it with the five-agent architecture.

Example future result format:

| Architecture | Task Success | Numerical Accuracy | Unsupported Claims |
|---|---:|---:|---:|
| Single Agent | TBD | TBD | TBD |
| Multi-Agent | TBD | TBD | TBD |

Do not invent results before running the benchmark.

### Phase 2 done when

- R1–R25 pre-benchmark remediation is complete and the reopened R6 regression
  suite is green;
- approximately 10 deterministic, versioned scenarios cover root cause, data
  quality, statistical reasoning, evidence grounding, and non-driver rejection;
- every scenario has tested evaluator-only ground truth and an offline evaluator;
- the single-agent baseline shares the deterministic runtime, security,
  provenance, and final output contracts;
- a frozen, resumable benchmark compares both architectures across declared
  repetitions using the same model/configuration;
- raw run records and aggregate metrics include analytical quality,
  operational reliability, cost, latency, and failure taxonomy;
- real benchmark results and limitations are documented without cherry-picking;
- deterministic pytest, Docker integration tests, Ruff, and CI remain green;
- no Phase 3 UI or Phase 4 AWS work is required for Phase 2 completion.

---

# Phase 3 — Product Polish

### Goal

Expose the working agent system through a clean interactive interface.

### Add

- file upload;
- business documentation input;
- question input;
- investigation progress;
- results page;
- chart display;
- evidence explorer;
- agent activity;
- cost/latency metrics;
- evaluation dashboard.

Avoid spending significant time on frontend work before Phase 2 works.

---

# Phase 4 — AWS Deployment

### Goal

Gain legitimate AWS experience by deploying infrastructure that serves an actual purpose.

Suggested architecture:

```text
                Web App
                   |
                   v
               ECS Service
                   |
               launch run
                   v
           ECS/Fargate Task
           +--------------+
           | Agent system |
           | DuckDB       |
           | Python       |
           | Workspace    |
           +------+-------+
                  |
                  v
                  S3
          results / artifacts
```

Use:

- ECR for container images
- ECS/Fargate for application/run execution
- S3 for datasets and artifacts
- Secrets Manager for API credentials
- CloudWatch for logs
- IAM for least-privilege permissions

### Important design choice

Local development may use Docker-backed isolation.

On AWS, prefer **one entire analysis run per ephemeral Fargate task** rather than nested Docker containers inside Fargate.

---

# Phase 5 — Predictive ML Expansion

Only start this after the analytics system and evaluation harness are reliable.

Potential sixth specialist:

## Machine Learning Scientist Agent

Responsibilities:

- define target;
- identify leakage;
- construct train/test split;
- establish baseline;
- preprocess features;
- compare models;
- evaluate;
- interpret;
- save model artifacts.

Future example tasks:

> Which customers are likely to churn?

> Predict 90-day customer LTV.

Later expansions may include:

- forecasting;
- causal inference;
- model-provider comparisons;
- external research;
- additional specialist agents.

---

## 20. Resume-Ready Threshold

The project does **not** need every future phase before being added to a resume.

Consider it resume-ready when it has:

- the five-agent architecture;
- a genuine autonomous end-to-end analysis;
- approximately 10 deterministic evaluation scenarios;
- a single-agent vs. multi-agent benchmark;
- a polished README with architecture and real results;
- a usable demo.

AWS is a valuable enhancement but should not delay reaching this threshold.

---

## 21. Recommended Build Order

Follow this order unless implementation reveals a strong reason to change it:

```text
Repository setup
    ->
Schemas / infrastructure
    ->
Synthetic data generator
    ->
Analyst
    ->
Data Auditor
    ->
Statistician
    ->
Critic
    ->
Lead
    ->
Canonical scenario
    ->
Evaluation framework
    ->
Additional scenarios
    ->
Single-agent baseline
    ->
Single vs. multi-agent benchmark
    ->
Streamlit UI
    ->
AWS deployment
```

---

# 22. GitHub / Development Workflow

## Repository name

Recommended:

```text
data-science-agent
```

Why:

- short;
- descriptive;
- professional;
- understandable on a resume;
- does not lock the project into a particular vendor/model;
- still works if the architecture evolves.

Alternative names:

```text
multi-agent-data-scientist
autonomous-data-scientist
ds-agent-lab
```

Prefer `data-science-agent` if available.

## Repository visibility

Either approach is fine:

- **Private initially, public at MVP:** easiest if you want to experiment freely.
- **Public from day one:** fine if you are comfortable with rough early commits.

For a portfolio project, make it public before linking it on the resume.

## Version-control habits

- Commit small coherent changes.
- Do not let Codex generate one giant initial commit.
- Use feature branches for meaningful work.
- Review every diff before merging.
- Keep API keys and local data secrets out of Git.
- Use `.env.example`, never commit `.env`.
- Keep generated large datasets/artifacts out of Git unless they are intentionally small fixtures.
- Prefer reproducible generation scripts over checking in huge synthetic datasets.

---

# 23. VS Code + Codex Workflow

Use a **hybrid workflow** rather than choosing one tool exclusively.

## VS Code should be the primary local workspace

Use VS Code for:

- browsing the repository;
- manually reading code;
- debugging;
- running tests;
- inspecting diffs;
- editing architecture-sensitive code;
- understanding what Codex changed.

## Use Codex aggressively inside that workflow

Use the Codex IDE integration or desktop/ChatGPT Codex experience for:

- scaffolding;
- implementing well-defined modules;
- writing tests;
- refactoring;
- debugging;
- code review;
- repetitive plumbing;
- parallelizable tasks.

### Guiding principle

> Codex is the implementation accelerator; you remain the architect.

This project is partly intended to make you better at agent engineering, so do not optimize away your own understanding of the codebase.

---

# 24. `AGENTS.md`

Create an `AGENTS.md` near the beginning of the project.

Keep it concise initially.

It should tell Codex:

- what the project is;
- repo layout;
- current implementation phase;
- commands to install/run/test/lint;
- architectural rules;
- things it must not do;
- definition of done.

Example initial content:

```markdown
# Repository Guidance

## Project

This repository implements a multi-agent autonomous data science system.
See `PROJECT_PLAN.md` for the full architecture and roadmap.

## Current Phase

Phase 0: deterministic infrastructure.

Do not implement later project phases unless explicitly requested.

## Architecture Rules

- The Lead agent orchestrates but does not execute SQL/Python directly.
- Specialist agents do not delegate to other specialists.
- Agent interfaces should use typed Pydantic schemas.
- Quantitative findings must retain evidence provenance.
- Persist explicit plans, hypotheses, tool outputs, and evidence — not hidden chain-of-thought.
- Keep input datasets read-only during analysis runs.
- Prefer simple deterministic infrastructure over unnecessary abstractions.

## Engineering

- Python 3.12+
- Use `uv` for dependency management.
- Use Ruff for linting/formatting.
- Use pytest for tests.
- Add or update tests for behavioral changes.
- Do not commit `.env` or API keys.
- Do not introduce AWS until the local MVP is complete.

## Before Marking Work Complete

1. Run the relevant tests.
2. Run Ruff.
3. Review changed files for scope creep.
4. Summarize what changed and any unresolved issues.
```

Update `AGENTS.md` when repeated Codex mistakes reveal a useful persistent rule.

Do not stuff the entire `PROJECT_PLAN.md` into `AGENTS.md`.

---

# 25. Initial Local Setup

A reasonable starting sequence:

```bash
git clone <repo-url>
cd data-science-agent

uv init --python 3.12

uv add openai-agents pydantic duckdb pyarrow pandas numpy scipy statsmodels matplotlib plotly streamlit python-dotenv

uv add --dev pytest ruff
```

Then create:

```text
src/
tests/
scenarios/
skills/
evals/
app/
PROJECT_PLAN.md
AGENTS.md
.env.example
.gitignore
```

Create `.env.example` containing only placeholder names such as:

```text
OPENAI_API_KEY=
OPENAI_DEFAULT_MODEL=
```

Do not put a real API key in the repository.

---

# 26. The First Codex Tasks

Do not ask Codex to “build the project.”

Give it bounded tasks.

## Task 1 — Repository foundation

Objective:

> Set up the Python project structure, dependencies, linting, test configuration, and placeholder packages described in `PROJECT_PLAN.md`. Do not implement agent logic yet.

## Task 2 — Domain schemas

Objective:

> Implement the initial Pydantic schemas for findings, audit results, validation results, hypotheses, tool events, and analysis run state. Add unit tests.

## Task 3 — Workspace lifecycle

Objective:

> Implement creation and cleanup of per-run workspaces with read-only `inputs` and `docs`, writable `working` and `outputs`, and state/log directories. Add tests.

## Task 4 — DuckDB execution

Objective:

> Implement a DuckDB execution service that can query approved workspace data, save query files, capture results/errors, and emit structured tool events to the ledger. Add tests.

## Task 5 — Analysis Ledger

Objective:

> Implement persistent ledger operations for hypotheses, findings, artifacts, tool events, validation issues, and budgets. Use typed schemas and add tests.

## Task 6 — Synthetic data generator

Objective:

> Build a deterministic synthetic ecommerce dataset generator according to `PROJECT_PLAN.md`, initially without scenario injection. Include configurable seed and dataset size.

## Task 7 — Artifact management

Objective:

> Implement safe artifact registration, provenance, path validation, and ledger persistence for analysis outputs.

## Task 8 — Docker-backed Python execution

Objective:

> Implement constrained Docker-backed execution for agent-generated Python, including read-only inputs/docs, writable working/outputs, no network, time/resource limits, typed results, and tool-event persistence.

## Task 9 — Execution hardening + integration test

Objective:

> Bound SQL result materialization and add a deterministic integration test for workspace → SQL → Python → artifact → ledger.

## Task 10 — CI + phase transition

Objective:

> Add deterministic GitHub Actions checks and, only after the Phase 0 acceptance criteria pass, update repository status to Phase 1.

Only after Tasks 7–10 and the Phase 0 integration acceptance test pass should agent implementation begin.

---

# 27. First Milestone

The first milestone should **not** be “the AI answers a question.”

It should be:

> **A deterministic analytical workspace can be created, queried, modified safely, logged, and tested without any agents.**

Acceptance criteria:

- [x] Repository created.
- [x] Python environment and lockfile established.
- [x] Workspace lifecycle implemented and tested.
- [x] Synthetic dataset can be generated from a fixed seed.
- [x] DuckDB can query approved workspace data and persist tool events.
- [x] Analysis Ledger persists typed structured state.
- [x] No API keys or `.env` secrets are committed.
- [x] Artifact manager is implemented and tested.
- [x] Python execution can analyze approved workspace data inside the Docker sandbox.
- [x] Docker sandbox prevents input mutation, host filesystem escape, and network access.
- [x] DuckDB result materialization is bounded and SQL/Python usage updates budgets.
- [x] Deterministic Phase 0 integration test covers SQL → Python → artifact → ledger.
- [x] Full pytest suite passes.
- [x] Ruff check and format check pass.
- [x] GitHub Actions CI runs deterministic checks on `main`.

This Phase 0 milestone and the subsequent Phase 1 multi-agent MVP are complete.
Begin **Phase 2 Task 1: versioned evaluation contracts and manifests**, followed
by the generic offline evaluator. Do not start by running a large paid benchmark
or by adding scenarios without stable evaluator contracts.

---

# 28. General Project Principles

### 1. Build vertically, but not chaotically

Get one path working well before expanding breadth.

### 2. Keep deterministic pieces deterministic

LLMs should decide *what to investigate*.

Ordinary code should handle:

- file permissions;
- ledger persistence;
- evaluation calculations;
- schema validation;
- budgets;
- known metrics;
- test execution.

### 3. Test agents independently

Do not debug five agents simultaneously.

### 4. Evaluate from the beginning

Every synthetic scenario should eventually have explicit ground truth.

### 5. Do not optimize for technology keywords

Only add a technology when it serves a real architectural purpose.

### 6. Keep AWS out of the critical path initially

Use AWS after the local system proves worthwhile.

### 7. Preserve reproducibility

Synthetic data should be seedable. Agent configs should be recorded. Runs should record models and relevant parameters.

### 8. Review Codex output

Never merge large generated changes solely because tests pass.

### 9. Maintain an architectural decision log

For non-obvious decisions, add a short `docs/decisions/` note explaining:

- decision;
- alternatives;
- rationale;
- consequences.

Examples:

- manager pattern vs handoffs;
- DuckDB vs Postgres;
- one Lead + specialists vs peer-to-peer agents;
- local Docker vs cloud execution.

### 10. Avoid premature UI work

A beautiful interface around a weak agent is not the goal.

---

# 29. Immediate Next Actions

Current recommended sequence:

1. Keep deterministic pytest, Ruff, and Docker-backed acceptance checks green in CI.
2. Implement **Phase 2 Task 1 — versioned evaluation contracts and manifests**.
3. Implement **Phase 2 Task 2 — generic zero-API offline evaluation engine**.
4. Migrate the canonical scenario to the generic contract without changing its
   visible data, prompts, or acceptance semantics.
5. Add and deterministically validate business, data-quality, and statistical
   scenarios before making paid runs.
6. Implement the fair single-agent baseline on the existing execution and
   evidence foundation.
7. Build and dry-run the resumable benchmark manifest/runner.
8. Freeze the experiment, run a cost pilot, then execute and publish the real
   comparison.

Do not build Streamlit or AWS infrastructure during Phase 2. The priority is
measuring analytical correctness and reliability across scenarios with a fair,
reproducible benchmark.
