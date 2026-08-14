"""Smoke tests for the Phase 0 repository scaffold."""

from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).parents[1]

PYTHON_PACKAGES = (
    "agents",
    "tools",
    "schemas",
    "orchestration",
    "sandbox",
    "evaluation",
)

PLACEHOLDER_MODULES = (
    "agents.lead",
    "agents.auditor",
    "agents.analyst",
    "agents.statistician",
    "agents.critic",
    "tools.workspace",
    "tools.sql",
    "tools.python",
    "tools.artifacts",
    "schemas.findings",
    "schemas.audit",
    "schemas.validation",
    "schemas.run_state",
    "orchestration.runner",
    "orchestration.ledger",
    "orchestration.budgets",
    "sandbox.executor",
    "evaluation.evaluator",
    "evaluation.metrics",
)


def test_source_packages_have_init_modules() -> None:
    for package in PYTHON_PACKAGES:
        assert (ROOT / "src" / package / "__init__.py").is_file()


def test_placeholder_modules_are_importable() -> None:
    for module in PLACEHOLDER_MODULES:
        assert import_module(module).__doc__
