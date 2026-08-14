# Data Science Agent

Foundation for an evidence-backed, multi-agent business analytics system.

The repository is currently in Phase 0: deterministic project infrastructure and
package boundaries are being established before agent behavior is implemented.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The planned architecture and implementation sequence are documented in
[`PROJECT_PLAN.md`](PROJECT_PLAN.md).
