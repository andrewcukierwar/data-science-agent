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
from orchestration.pricing import (
    MODEL_PRICING,
    calculate_cost_breakdown,
    pricing_for_model,
    resolve_model_pricing,
)


def __getattr__(name: str):  # noqa: ANN001
    """Load the runner lazily to avoid the agents/runtime import cycle."""

    if name in {
        "AnalysisRunResult",
        "AnalysisRunner",
        "GeneralistRunResult",
        "GeneralistRunner",
    }:
        from orchestration.generalist_runner import (
            GeneralistRunner,
            GeneralistRunResult,
        )
        from orchestration.runner import AnalysisRunner, AnalysisRunResult

        return {
            "AnalysisRunResult": AnalysisRunResult,
            "AnalysisRunner": AnalysisRunner,
            "GeneralistRunResult": GeneralistRunResult,
            "GeneralistRunner": GeneralistRunner,
        }[name]
    raise AttributeError(name)


__all__ = [
    "BudgetExhaustedError",
    "BudgetResource",
    "BudgetSnapshot",
    "MODEL_PRICING",
    "calculate_cost_breakdown",
    "AnalysisLedger",
    "AnalysisRunResult",
    "AnalysisRunner",
    "GeneralistRunResult",
    "GeneralistRunner",
    "LedgerConflictError",
    "LedgerError",
    "RunBudgetController",
    "RunBudgetManager",
    "pricing_for_model",
    "resolve_model_pricing",
    "ToolEventLedger",
    "ToolEventSink",
]
