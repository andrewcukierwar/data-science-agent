# Repository Guidance

See `PROJECT_PLAN.md` for the full architecture, implementation plan, and roadmap.

## Current Phase

Phase 1: Multi-Agent MVP.

Do not implement future phases unless explicitly requested.

## Architecture Rules

- The Lead agent orchestrates but does not execute SQL or Python directly.
- Specialist agents do not delegate to other specialists.
- Agent interfaces should use typed Pydantic schemas.
- Quantitative findings must retain evidence provenance.
- Persist explicit plans, hypotheses, tool outputs, and evidence — not hidden chain-of-thought.
- Input datasets must remain read-only during analysis runs.
- Prefer simple, deterministic infrastructure over unnecessary abstractions.

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
