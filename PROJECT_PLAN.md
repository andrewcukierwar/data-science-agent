# Data Science AI Agent — Project Plan

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
- contribution profit
- 30-day LTV
- 60-day LTV
- 90-day LTV
- conversion
- refunded-order treatment
- canceled-order treatment
- reporting timezone

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
Create isolated workspace
   |
   v
Lead receives objective
   |
   v
MANDATORY DATA AUDIT
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
OPENAI_MODEL=<model-name>
```

This enables future comparisons across models without changing the architecture.

V1 only needs one working model/provider.

Multi-model comparisons are an evaluation enhancement, not a prerequisite.

---

## 19. Implementation Phases

# Phase 0 — Foundation

### Goal

Build deterministic infrastructure before agent intelligence.

### Build

- Repository skeleton
- `pyproject.toml`
- uv environment
- Pydantic schemas
- Workspace manager
- Analysis Ledger
- DuckDB execution tool
- Python execution tool
- Artifact manager
- Execution logging
- Unit tests
- Docker environment
- First synthetic ecommerce dataset

### Done when

The code can:

```text
create workspace
-> inspect data
-> execute SQL
-> execute Python
-> save artifact
-> record events/results in ledger
```

without any LLM agents involved.

---

# Phase 1 — Multi-Agent MVP

### Goal

One canonical scenario works extremely well locally.

### Implementation order

1. Analyst
2. Data Auditor
3. Statistician
4. Critic
5. Lead

Do not connect all five agents before each specialist can be tested independently.

### Canonical scenario

> **Why did profitability decline in Q2?**

Engineer the synthetic dataset so the answer requires:

- data-quality validation;
- contribution-profit decomposition;
- channel analysis;
- CAC;
- conversion;
- LTV;
- statistical validation;
- a defensible recommendation.

### Done when

From only raw data, documentation, and the question, the system autonomously generates:

- validated report;
- charts;
- SQL;
- Python;
- Analysis Ledger;
- execution trace.

No manual steering should be required during the run.

---

# Phase 2 — Evaluation and Reliability

### Goal

Turn the project from a demo into an empirically evaluated AI system.

Expand from one scenario to approximately **10 deterministic scenarios**.

### Metrics

#### Numerical correctness

Do reported values match ground truth within tolerance?

#### Root-cause identification

Did the system identify the injected primary driver?

#### Data-quality detection

Did it identify injected defects?

#### Statistical correctness

Did it reach the correct inferential conclusion?

#### Evidence grounding

Are material quantitative claims tied to executed analysis?

#### Unsupported claims

Did the agent claim more than the data establishes?

#### Overall task success

Did it solve the analytical problem?

### Key experiment

Build a **single-agent baseline** and compare it with the five-agent architecture.

Example future result format:

| Architecture | Task Success | Numerical Accuracy | Unsupported Claims |
|---|---:|---:|---:|
| Single Agent | TBD | TBD | TBD |
| Multi-Agent | TBD | TBD | TBD |

Do not invent results before running the benchmark.

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
OPENAI_MODEL=
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

Only after this foundation works should agent implementation begin.

---

# 27. First Milestone

The first milestone should **not** be “the AI answers a question.”

It should be:

> **A deterministic analytical workspace can be created, queried, modified safely, logged, and tested without any agents.**

Acceptance criteria:

- [ ] Repository created.
- [ ] Python environment reproducible.
- [ ] Tests run successfully.
- [ ] Ruff passes.
- [ ] Workspace lifecycle works.
- [ ] Synthetic dataset can be generated from a fixed seed.
- [ ] DuckDB can query the generated Parquet files.
- [ ] Python execution can analyze approved workspace data.
- [ ] Artifacts can be saved.
- [ ] Analysis Ledger persists structured events.
- [ ] No API keys are committed.

Once this milestone is complete, start the Analyst agent.

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

Recommended sequence:

1. Create a GitHub repository named `data-science-agent`.
2. Clone it locally.
3. Open the repository in VS Code.
4. Install/configure Codex for your preferred workflow.
5. Add this `PROJECT_PLAN.md`.
6. Create a concise `AGENTS.md`.
7. Initialize the project with `uv`.
8. Add `.gitignore` and `.env.example`.
9. Make the first clean commit.
10. Ask Codex to implement **Phase 0 / Task 1 only**.
11. Review the diff manually.
12. Run tests/lint.
13. Commit.
14. Move to Task 2.

Do not create AWS infrastructure yet.
Do not build Streamlit yet.
Do not build all five agents yet.

The immediate objective is a clean, understandable foundation that makes the interesting agent work easy to add later.
