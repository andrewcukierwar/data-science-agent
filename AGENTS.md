# Repository Guidance

See `PROJECT_PLAN.md` for the full architecture, implementation plan, and roadmap.

## Current Phase

Phase 2: Evaluation and Reliability.

Phase 1 is complete. The current objective is to expand the deterministic
scenario suite, build a fair single-agent baseline, and benchmark reliability.
Do not implement Phase 3 UI, Phase 4 AWS, predictive ML, or other future phases
unless explicitly requested.

## Architecture Rules

- The Lead agent orchestrates but does not execute SQL or Python directly.
- Specialist agents do not delegate to other specialists.
- Agent interfaces should use typed Pydantic schemas.
- Quantitative findings must retain evidence provenance.
- Persist explicit plans, hypotheses, tool outputs, and evidence — not hidden chain-of-thought.
- Input datasets must remain read-only during analysis runs.
- Prefer simple, deterministic infrastructure over unnecessary abstractions.

## Phase 2 Evaluation Rules

- Keep scenario ground truth and tolerances evaluator-only; never expose them to
  agent prompts, model-visible documents, or tools.
- Evaluate persisted workspaces offline without OpenAI/API calls.
- Version scenario definitions, evaluator rules, architecture configuration,
  model identity, and budgets for every benchmark run.
- Use immutable benchmark run IDs and preserve raw run records; do not overwrite
  benchmark evidence with `--force`.
- Compare single-agent and multi-agent systems on the same scenario inputs,
  model configuration, deterministic tools, and output/evidence contracts.
- Separate operational failures from analytical-quality failures and report
  both.
- Do not invent, cherry-pick, or publish benchmark results before running the
  declared experiment.
- Add deterministic evaluator fixtures before spending API credits on a live
  benchmark.

## Engineering

- Use Python 3.12+.
- Use `uv` for dependency management.
- Use Ruff for linting and formatting.
- Use pytest for testing.
- Add or update tests for behavioral changes.
- Never commit `.env`, API keys, credentials, or other secrets.
- Do not introduce AWS until explicitly requested.

## Before Marking Work Complete

1. Run relevant tests.
2. Run Ruff.
3. Review changes for unnecessary scope expansion.
4. Summarize what changed and note any unresolved issues.
