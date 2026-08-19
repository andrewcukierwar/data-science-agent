"""Typed classification of why an analysis run did not complete.

Every non-completion carries an explicit machine-readable reason and a
human-readable detail. Classification happens where the originating condition
is known — the orchestration boundary that caught the exception or made the
constraint decision — rather than by matching substrings in an error message
later.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agents.exceptions import (
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelRefusalError,
    UserError,
)

from agents.output_contract import AgentOutputContractError
from orchestration.budgets import BudgetExhaustedError
from schemas.run_state import RunBlockReason


@dataclass(frozen=True, slots=True)
class RunConstraint:
    """One explicit non-completion cause with its observable explanation."""

    reason: RunBlockReason
    detail: str

    def with_prefix(self, prefix: str) -> RunConstraint:
        """Return the same reason with a caller-specific explanation."""

        return RunConstraint(reason=self.reason, detail=f"{prefix}: {self.detail}")


def classify_exception(error: BaseException) -> RunBlockReason:
    """Map an exception raised during orchestration to its typed reason.

    Only genuine run-budget exhaustion is classified as a budget outcome. A
    turn-limit stop is an agent bound, not the configured resource budget, and
    a structured-output violation is a schema failure.
    """

    if isinstance(error, BudgetExhaustedError):
        return RunBlockReason.BUDGET_EXHAUSTED
    if isinstance(error, AgentOutputContractError):
        return RunBlockReason.SCHEMA_FAILURE
    if isinstance(error, MaxTurnsExceeded):
        return RunBlockReason.AGENT_FAILURE
    if isinstance(error, ModelRefusalError):
        return RunBlockReason.PROVIDER_FAILURE
    if isinstance(error, ModelBehaviorError):
        return RunBlockReason.SCHEMA_FAILURE
    if isinstance(error, UserError):
        return RunBlockReason.WORKSPACE_FAILURE
    if isinstance(error, KeyboardInterrupt | asyncio.CancelledError):
        return RunBlockReason.INTERRUPTED
    if isinstance(error, TimeoutError):
        return RunBlockReason.TIMEOUT
    if isinstance(error, PermissionError):
        return RunBlockReason.TOOL_FAILURE
    if isinstance(error, FileNotFoundError | IsADirectoryError | NotADirectoryError):
        return RunBlockReason.WORKSPACE_FAILURE
    return RunBlockReason.OTHER


_REASON_DESCRIPTIONS: dict[RunBlockReason, str] = {
    RunBlockReason.BUDGET_EXHAUSTED: "budget exhaustion",
    RunBlockReason.VALIDATION_REVISION: "an unresolved self-critique revision",
    RunBlockReason.UNRESOLVED_FOLLOW_UP: "an unresolved analytical follow-up",
    RunBlockReason.AGENT_FAILURE: "the agent turn limit",
    RunBlockReason.SCHEMA_FAILURE: "a structured-output schema failure",
    RunBlockReason.TOOL_FAILURE: "a tool permission failure",
    RunBlockReason.PROVIDER_FAILURE: "a provider refusal",
    RunBlockReason.SANDBOX_FAILURE: "a sandbox failure",
    RunBlockReason.WORKSPACE_FAILURE: "a workspace failure",
    RunBlockReason.DATA_QUALITY: "a blocking data-quality condition",
    RunBlockReason.TIMEOUT: "a timeout",
    RunBlockReason.INTERRUPTED: "an interruption",
    RunBlockReason.OTHER: "a bounded execution failure",
}


def describe_reason(reason: RunBlockReason) -> str:
    """Return the human-readable phrase for one typed block reason."""

    return _REASON_DESCRIPTIONS[RunBlockReason(reason)]


def constraint_from_exception(
    error: BaseException,
    *,
    context: str,
) -> RunConstraint:
    """Build a typed constraint describing where and why execution stopped."""

    reason = classify_exception(error)
    return RunConstraint(
        reason=reason,
        detail=(
            f"{context} stopped by {describe_reason(reason)}: "
            f"{type(error).__name__}: {error}"
        ).strip(),
    )


__all__ = [
    "RunConstraint",
    "classify_exception",
    "constraint_from_exception",
    "describe_reason",
]
