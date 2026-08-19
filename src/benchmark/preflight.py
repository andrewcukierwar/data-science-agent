"""Outcome-sensitive smoke checks gating a paid benchmark matrix.

The Task 10 preflight was green while every retained pilot failed. Its live
smoke tests asserted permissive configuration and artifact presence — a ledger
exists, some agent events were recorded — which a run that produced invalid
JSON, lost its usage, or dropped an interruption can still satisfy.

These checks assert the outcomes the benchmark actually requires: the
architecture completed, its report is persisted and readable, its usage is
either real or explicitly unavailable, its cost is explicit rather than a
silent zero, and its attempt history reconciles to the run totals with no
attempt left running. They are shared by the opt-in live smoke tests and by
deterministic failure fixtures, so the same gate that authorizes a paid pilot
is the one proven to reject broken runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from schemas.run_state import (
    AttemptStatus,
    ModelUsage,
    RunStatus,
)


class PreflightError(AssertionError):
    """Raised when a run does not satisfy the benchmark smoke contract."""


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    """One deterministic assertion about an observed run outcome."""

    check_id: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Every smoke check observed for one run."""

    architecture: str
    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        """Whether every check passed."""

        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[PreflightCheck, ...]:
        """The checks that did not pass."""

        return tuple(check for check in self.checks if not check.passed)

    def raise_for_failures(self) -> PreflightReport:
        """Raise a readable error naming every failed check."""

        if self.passed:
            return self
        detail = "; ".join(
            f"{check.check_id}: {check.detail}" for check in self.failures
        )
        raise PreflightError(
            f"{self.architecture} smoke run did not satisfy the benchmark "
            f"preflight ({detail})"
        )


def _check(check_id: str, passed: bool, detail: str) -> PreflightCheck:
    return PreflightCheck(check_id=check_id, passed=bool(passed), detail=detail)


def _lifecycle_checks(result: Any, state: Any) -> list[PreflightCheck]:
    status = getattr(result, "status", None)
    error = getattr(result, "error", None)
    state_status = getattr(state, "status", None)
    return [
        _check(
            "lifecycle:completed",
            status is RunStatus.COMPLETED,
            f"run status is {getattr(status, 'value', status)}",
        ),
        _check(
            "lifecycle:no_error",
            error is None,
            f"run error is {error!r}",
        ),
        _check(
            "lifecycle:state_matches_result",
            state_status is status,
            "persisted status "
            f"{getattr(state_status, 'value', state_status)} does not match "
            f"the returned status {getattr(status, 'value', status)}",
        ),
    ]


def _report_checks(result: Any, state: Any) -> list[PreflightCheck]:
    report = getattr(result, "report", None)
    persisted = getattr(state, "final_report", None)
    workspace = getattr(result, "workspace", None)
    workspace_root = getattr(workspace, "root", None)
    report_path = getattr(report, "path", None)
    readable = False
    if workspace_root is not None and report_path:
        candidate = Path(workspace_root) / report_path
        readable = candidate.is_file() and candidate.stat().st_size > 0
    return [
        _check(
            "report:returned",
            report is not None,
            "the run returned no report artifact",
        ),
        _check(
            "report:persisted",
            report is not None and persisted == report,
            "the persisted final report does not match the returned artifact",
        ),
        _check(
            "report:readable",
            readable,
            f"report file is missing or empty at {report_path!r}",
        ),
    ]


def _usage_checks(state: Any) -> list[PreflightCheck]:
    usage = getattr(state, "usage", None) or ModelUsage()
    complete = bool(getattr(state, "usage_complete", True))
    note = getattr(state, "usage_incompleteness_note", None)
    # Nonzero usage, or usage explicitly published as incomplete. A completed
    # run that silently recorded zero requests lost its accounting.
    accounted = (usage.requests > 0 and usage.total_tokens > 0) or (
        not complete and bool(note)
    )
    return [
        _check(
            "usage:accounted",
            accounted,
            f"usage is {usage.requests} requests / {usage.total_tokens} tokens "
            f"with usage_complete={complete} and no incompleteness note"
            if complete
            else f"usage is incomplete without an explanatory note: {note!r}",
        ),
        _check(
            "usage:consistent",
            usage.total_tokens == usage.input_tokens + usage.output_tokens,
            "total tokens do not equal input plus output tokens",
        ),
    ]


def _cost_checks(state: Any) -> list[PreflightCheck]:
    breakdown = getattr(state, "cost_breakdown", None)
    estimated = getattr(state, "estimated_cost_usd", None)
    note = getattr(state, "cost_estimation_note", None)
    complete = bool(getattr(state, "usage_complete", True))
    explicit = (breakdown is not None and estimated is not None) or (
        breakdown is None and estimated is None and bool(note)
    )
    return [
        _check(
            "cost:explicit",
            explicit,
            "cost is neither a known breakdown nor an explained unavailability",
        ),
        _check(
            "cost:not_known_over_incomplete_usage",
            complete or breakdown is None,
            "a cost breakdown was published over incomplete usage",
        ),
    ]


def _attempt_checks(state: Any) -> list[PreflightCheck]:
    history: Sequence[Any] = tuple(getattr(state, "attempt_history", ()) or ())
    attempt_id = getattr(state, "attempt_id", None)
    usage = getattr(state, "usage", None) or ModelUsage()
    elapsed = getattr(state, "elapsed_seconds", None)
    running = [item for item in history if item.status is AttemptStatus.RUNNING]
    reconciled = bool(history) and all(
        getattr(usage, name) == sum(getattr(item.usage_delta, name) for item in history)
        for name in ModelUsage.model_fields
    )
    elapsed_reconciled = (
        bool(history)
        and elapsed is not None
        and (abs(sum(item.elapsed_seconds for item in history) - elapsed) <= 1e-6)
    )
    return [
        _check(
            "attempts:recorded",
            bool(history),
            "the run published no attempt history",
        ),
        _check(
            "attempts:identity",
            bool(history) and attempt_id == history[-1].attempt_id,
            f"attempt_id {attempt_id!r} does not match the latest attempt",
        ),
        _check(
            "attempts:terminal",
            not running,
            f"{len(running)} attempt(s) were left running; an interruption was "
            "dropped instead of being closed",
        ),
        _check(
            "attempts:usage_reconciled",
            reconciled,
            "run usage does not equal the sum of attempt usage deltas",
        ),
        _check(
            "attempts:elapsed_reconciled",
            elapsed_reconciled,
            "run elapsed time does not equal the sum of attempt elapsed times",
        ),
    ]


def _architecture_checks(
    architecture: str,
    ledger: Any,
) -> list[PreflightCheck]:
    roles = {event.agent_role for event in getattr(ledger, "agent_events", []) or []}
    specialists = tuple(getattr(ledger, "specialist_results", ()) or ())
    if architecture == "single-agent":
        return [
            _check(
                "architecture:single_agent_only",
                roles == {"generalist"},
                f"observed agent roles {sorted(roles)}",
            ),
            _check(
                "architecture:no_specialists",
                not specialists,
                f"{len(specialists)} specialist result(s) were recorded",
            ),
        ]
    return [
        _check(
            "architecture:multi_agent_roles",
            {"lead", "critic"} <= roles,
            f"observed agent roles {sorted(roles)}",
        )
    ]


def check_run_outcome(
    result: Any,
    *,
    architecture: str,
) -> PreflightReport:
    """Return every smoke check for one completed architecture run."""

    ledger = getattr(result, "ledger", None)
    if ledger is None:
        return PreflightReport(
            architecture=architecture,
            checks=(
                _check(
                    "lifecycle:ledger",
                    False,
                    "the run produced no ledger, so no outcome is observable",
                ),
            ),
        )
    state = ledger.state
    checks = [
        *_lifecycle_checks(result, state),
        *_report_checks(result, state),
        *_usage_checks(state),
        *_cost_checks(state),
        *_attempt_checks(state),
        *_architecture_checks(architecture, ledger),
    ]
    return PreflightReport(architecture=architecture, checks=tuple(checks))


def assert_run_outcome(result: Any, *, architecture: str) -> PreflightReport:
    """Raise unless the run satisfies every benchmark smoke check."""

    return check_run_outcome(result, architecture=architecture).raise_for_failures()


__all__ = [
    "PreflightCheck",
    "PreflightError",
    "PreflightReport",
    "assert_run_outcome",
    "check_run_outcome",
]
