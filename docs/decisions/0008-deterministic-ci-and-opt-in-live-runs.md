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

The Phase 2 benchmark runner additionally requires `--allow-paid`, requires the
environment model to exactly match the model frozen in the manifest, and never
loads `.env`. Planning, dry-run, evaluation, rescoring, and reporting remain
credential-free. A cost-estimation pilot must be persisted before the full
matrix can resume.

When Codex runs as a VS Code extension, variables exported in an integrated
terminal are not retroactively inherited by the already-running extension host.
Quit VS Code completely and launch `code /path/to/repository` from a shell where
the variables are already exported, then start a new agent conversation. Check
only whether the key is set; never print it into logs or chat.

On the 2026-08-18 development-machine check, Docker Desktop and its
`desktop-linux` daemon were healthy. The Docker socket was owned by the current
user, and `docker info` succeeded outside the restricted agent sandbox. The
earlier permission-denied result was therefore a sandbox access restriction,
not a Docker daemon, context, or Unix ownership failure.

## Consequences

- Deterministic failures are actionable without spending API credit.
- Offline canonical evaluation is preferred before any paid rerun.
- `--force` deletes and replaces the exact run directory; use a unique
  `--run-id` or archive the workspace when historical preservation matters.
- API connection, organization, or credit failures are operational failures and
  should not be misdiagnosed as analytical code failures.
- Extension-host environment inheritance and Docker-socket sandbox denial are
  operational setup issues, not evaluator or analytical failures.

## Verification

See `.github/workflows/ci.yml`, pytest markers/configuration in `pyproject.toml`,
live test modules under `tests/`, and the two canonical scripts under `scripts/`.
