"""Orchestration interfaces for analysis runs."""

from orchestration.budgets import (
    BudgetExhaustedError,
    BudgetResource,
    BudgetSnapshot,
    RunBudgetController,
    RunBudgetManager,
)
from orchestration.ledger import (
    AnalysisLedger,
    LedgerConflictError,
    LedgerError,
    ToolEventLedger,
    ToolEventSink,
)


def __getattr__(name: str):  # noqa: ANN001
    """Load the runner lazily to avoid the agents/runtime import cycle."""

    if name in {"AnalysisRunResult", "AnalysisRunner"}:
        from orchestration.runner import AnalysisRunner, AnalysisRunResult

        return {
            "AnalysisRunResult": AnalysisRunResult,
            "AnalysisRunner": AnalysisRunner,
        }[name]
    raise AttributeError(name)


__all__ = [
    "BudgetExhaustedError",
    "BudgetResource",
    "BudgetSnapshot",
    "AnalysisLedger",
    "AnalysisRunResult",
    "AnalysisRunner",
    "LedgerConflictError",
    "LedgerError",
    "RunBudgetController",
    "RunBudgetManager",
    "ToolEventLedger",
    "ToolEventSink",
]
