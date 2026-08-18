# 0008: Deterministic CI and opt-in live runs

- Status: Accepted
- Date: 2026-08-17
- Phase: Phase 0/1

## Context

Normal CI must be repeatable, credential-free, and capable of exercising local
execution boundaries. Paid model calls are nondeterministic and must not be
required to merge ordinary code changes.

## Decision

On pushes and pull requests to `main`, CI:

- installs `uv`;
- uses the repository Python version;
- runs `uv sync --frozen`;
- runs deterministic pytest, including Docker-backed tests where the runner
  supports Docker;
- runs `uv run ruff check .`;
- runs `uv run ruff format --check .`.

`actions/setup-python` does not use `cache: uv`; `astral-sh/setup-uv` owns uv
caching. The unsupported duplicate cache configuration previously caused CI to
fail before tests executed.

Tests that make live OpenAI calls are explicitly marked `live`, opt-in, and
excluded from normal CI. CI does not require `OPENAI_API_KEY`.

The paid canonical command is manual only:

```bash
uv run python scripts/run_canonical_mvp.py --force
```

It requires an intentional API-enabled environment, Docker, and
`OPENAI_API_KEY` plus `OPENAI_DEFAULT_MODEL`. The Codex restricted sandbox may
block `api.openai.com`; when the user explicitly requests a live run, execute it
outside that restricted network only after the required approval. Never weaken
the analysis Docker container's no-network setting to solve host API access.

## Consequences

- Deterministic failures are actionable without spending API credit.
- Offline canonical evaluation is preferred before any paid rerun.
- `--force` deletes and replaces the exact run directory; use a unique
  `--run-id` or archive the workspace when historical preservation matters.
- API connection, organization, or credit failures are operational failures and
  should not be misdiagnosed as analytical code failures.

## Verification

See `.github/workflows/ci.yml`, pytest markers/configuration in `pyproject.toml`,
live test modules under `tests/`, and the two canonical scripts under `scripts/`.
