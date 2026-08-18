# Data Science AI Agent — Project Plan

## Plan Status

**Revision:** 2026-08-17 — Phase 1 completion / Phase 2 evaluation sequencing.

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

#### Task 10 — Execute and publish the Phase 2 benchmark

Freeze a benchmark manifest, run the declared single-agent and five-agent
matrix, evaluate every persisted workspace offline, inspect failures without
changing rules mid-experiment, and publish the actual results and limitations.
If a defect requires code/evaluator changes, version the benchmark and rerun the
affected declared matrix rather than silently patching scores.

Acceptance:

- raw manifests, run records, evaluator results, and aggregate report are
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
