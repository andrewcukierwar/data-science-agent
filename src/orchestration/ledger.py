"""Persistent, typed analysis ledger operations."""

import json
import os
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from schemas.audit import AuditResult
from schemas.findings import Finding
from schemas.run_state import (
    AnalysisRunState,
    Artifact,
    Hypothesis,
    HypothesisStatus,
    ModelUsage,
    RunBudget,
    RunStatus,
    ToolEvent,
)
from schemas.validation import ValidationIssue, ValidationResult

_LEDGER_FILENAME = "analysis_ledger.json"
_USAGE_FIELDS = frozenset(
    {
        "specialist_invocations",
        "sql_executions",
        "python_executions",
        "critic_loops",
        "charts_created",
    }
)


class ToolEventLedger(Protocol):
    """Minimal ledger boundary required by tool execution services."""

    def append_tool_event(self, event: ToolEvent) -> None:
        """Persist one structured tool event."""


ToolEventSink = ToolEventLedger


class LedgerError(RuntimeError):
    """Base error for persistent ledger failures."""


class LedgerConflictError(LedgerError):
    """Raised when an operation would create a duplicate ledger identifier."""


class AnalysisLedger(ToolEventLedger):
    """Persist and update one analysis run's observable work products.

    ``workspace_or_state_path`` may be a workspace object, a state directory,
    or a direct path to ``analysis_ledger.json``. A new ledger requires an
    objective; existing ledgers are loaded from disk and ignore it.
    """

    def __init__(
        self,
        workspace_or_state_path: object | str | Path,
        *,
        run_id: str | None = None,
        objective: str | None = None,
        business_context: str | None = None,
    ) -> None:
        self.state_path = self._resolve_state_path(workspace_or_state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            self._state = self._load_from_disk()
        else:
            inferred_run_id = run_id or self._infer_run_id(workspace_or_state_path)
            if not inferred_run_id:
                raise ValueError("run_id is required when creating a new ledger")
            if not objective:
                raise ValueError("objective is required when creating a new ledger")
            self._state = AnalysisRunState(
                run_id=inferred_run_id,
                objective=objective,
                business_context=business_context,
            )
            self.save()

    @property
    def state(self) -> AnalysisRunState:
        """Current typed state held by this ledger instance."""

        return self._state

    @property
    def hypotheses(self) -> list[Hypothesis]:
        """Tracked investigation hypotheses."""

        return self._state.hypotheses

    @property
    def findings(self) -> list[Finding]:
        """Recorded analytical findings."""

        return self._state.findings

    @property
    def artifacts(self) -> list[Artifact]:
        """Recorded artifact references."""

        return self._state.artifacts

    @property
    def tool_events(self) -> list[ToolEvent]:
        """Recorded tool events."""

        return self._state.tool_events

    @property
    def validation_issues(self) -> list[ValidationIssue]:
        """Recorded Critic validation issues."""

        return self._state.validation_issues

    @property
    def validation_results(self) -> list[ValidationResult]:
        """Recorded typed Critic validation results."""

        return self._state.validation_results

    @property
    def budget(self) -> RunBudget:
        """Current run budget and usage counters."""

        return self._state.run_budget

    @property
    def usage(self) -> ModelUsage:
        """Aggregated Agents SDK request and token usage."""

        return self._state.usage

    @property
    def audit(self) -> AuditResult | None:
        """The persisted preflight audit, when one has been recorded."""

        return self._state.audit

    def save(self) -> None:
        """Atomically persist the current typed state to JSON."""

        self._state.updated_at = datetime.now(UTC)
        temporary_path = self.state_path.with_name(
            f".{self.state_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary_path.write_text(
                self._state.model_dump_json(indent=2), encoding="utf-8"
            )
            os.replace(temporary_path, self.state_path)
        except OSError as exc:
            temporary_path.unlink(missing_ok=True)
            raise LedgerError(f"could not persist ledger: {self.state_path}") from exc

    def refresh(self) -> AnalysisRunState:
        """Reload and return the typed state from disk."""

        self._state = self._load_from_disk()
        return self._state

    def set_status(self, status: RunStatus | str) -> RunStatus:
        """Persist the observable lifecycle status for the run."""

        self._state.status = RunStatus(status)
        self.save()
        return self._state.status

    def record_run_metadata(
        self,
        *,
        model: str | None = None,
        model_provider: str | None = None,
    ) -> None:
        """Persist model identity used by the application runner."""

        if model is not None:
            self._state.model = model
        if model_provider is not None:
            self._state.model_provider = model_provider
        self.save()

    def set_business_context(self, business_context: str) -> None:
        """Persist business context supplied when opening an existing run."""

        normalized = business_context.strip()
        if not normalized:
            raise ValueError("business_context must be non-empty")
        self._state.business_context = normalized
        self.save()

    def record_model_usage(self, usage: Any) -> ModelUsage:
        """Add one Agents SDK usage snapshot to the persisted run totals."""

        if usage is None:
            return self.usage

        def _integer(name: str) -> int:
            value = getattr(usage, name, 0)
            return int(value or 0)

        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        increment = ModelUsage(
            requests=_integer("requests"),
            input_tokens=_integer("input_tokens"),
            output_tokens=_integer("output_tokens"),
            total_tokens=_integer("total_tokens"),
            cached_tokens=int(getattr(input_details, "cached_tokens", 0) or 0),
            reasoning_tokens=int(getattr(output_details, "reasoning_tokens", 0) or 0),
        )
        self._state.usage = ModelUsage(
            requests=self.usage.requests + increment.requests,
            input_tokens=self.usage.input_tokens + increment.input_tokens,
            output_tokens=self.usage.output_tokens + increment.output_tokens,
            total_tokens=self.usage.total_tokens + increment.total_tokens,
            cached_tokens=self.usage.cached_tokens + increment.cached_tokens,
            reasoning_tokens=self.usage.reasoning_tokens + increment.reasoning_tokens,
        )
        self.save()
        return self.usage

    def record_elapsed(self, elapsed_seconds: float) -> float:
        """Persist total wall-clock duration for the run."""

        if elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be non-negative")
        self._state.elapsed_seconds = elapsed_seconds
        self.save()
        return elapsed_seconds

    def record_final_report(self, artifact: Artifact) -> Artifact:
        """Persist the registered final report artifact."""

        self._state.final_report = artifact
        self.save()
        return artifact

    def mark_failed(self, error: str | Exception) -> None:
        """Persist a failed status and concise observable error state."""

        message = str(error).strip() or error.__class__.__name__
        self._state.status = RunStatus.FAILED
        self._state.error = message
        self.save()

    def add_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
        """Append a hypothesis with a unique identifier."""

        self._ensure_unique(
            "hypothesis", hypothesis.id, (item.id for item in self.hypotheses)
        )
        self._state.hypotheses.append(hypothesis)
        self._sync_rejected_hypotheses()
        self.save()
        return hypothesis

    def update_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
        """Replace an existing hypothesis and persist its new status/evidence."""

        for index, current in enumerate(self.hypotheses):
            if current.id == hypothesis.id:
                self._state.hypotheses[index] = hypothesis
                self._sync_rejected_hypotheses()
                self.save()
                return hypothesis
        raise KeyError(f"unknown hypothesis: {hypothesis.id}")

    def upsert_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
        """Create a hypothesis or update its existing status and evidence."""

        if any(current.id == hypothesis.id for current in self.hypotheses):
            return self.update_hypothesis(hypothesis)
        return self.add_hypothesis(hypothesis)

    def update_investigation_plan(self, steps: Iterable[str]) -> list[str]:
        """Replace the explicit investigation plan and persist it."""

        normalized = [step.strip() for step in steps]
        if not normalized or any(not step for step in normalized):
            raise ValueError("investigation plan must contain non-empty steps")
        self._state.investigation_plan = normalized
        self.save()
        return list(self._state.investigation_plan)

    def add_open_question(self, question: str) -> str:
        """Record a material unanswered question once."""

        normalized = question.strip()
        if not normalized:
            raise ValueError("open question must be non-empty")
        if normalized not in self._state.open_questions:
            self._state.open_questions.append(normalized)
            self.save()
        return normalized

    def add_finding(self, finding: Finding) -> Finding:
        """Append a finding with a unique identifier."""

        self._ensure_unique("finding", finding.id, (item.id for item in self.findings))
        self._state.findings.append(finding)
        self.save()
        return finding

    def add_artifact(self, artifact: Artifact) -> Artifact:
        """Append an artifact reference with a unique identifier."""

        self._ensure_unique(
            "artifact", artifact.id, (item.id for item in self.artifacts)
        )
        self._state.artifacts.append(artifact)
        self.save()
        return artifact

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        """Return an artifact by identifier, or ``None`` when absent."""

        return next(
            (artifact for artifact in self.artifacts if artifact.id == artifact_id),
            None,
        )

    def update_artifact(self, artifact: Artifact) -> Artifact:
        """Replace an existing artifact record and persist its provenance."""

        for index, current in enumerate(self.artifacts):
            if current.id == artifact.id:
                self._state.artifacts[index] = artifact
                self.save()
                return artifact
        raise KeyError(f"unknown artifact: {artifact.id}")

    def append_tool_event(self, event: ToolEvent) -> None:
        """Append a structured tool event with a unique identifier."""

        self._ensure_unique(
            "tool event", event.id, (item.id for item in self.tool_events)
        )
        self._state.tool_events.append(event)
        self.save()

    def record_tool_event(self, event: ToolEvent) -> None:
        """Compatibility alias for append_tool_event."""

        self.append_tool_event(event)

    def add_validation_issue(self, issue: ValidationIssue) -> ValidationIssue:
        """Append a Critic issue with a unique identifier."""

        self._ensure_unique(
            "validation issue", issue.id, (item.id for item in self.validation_issues)
        )
        self._state.validation_issues.append(issue)
        self.save()
        return issue

    def update_validation_issue(self, issue: ValidationIssue) -> ValidationIssue:
        """Replace a previously observed issue during remediation."""

        for index, current in enumerate(self.validation_issues):
            if current.id == issue.id:
                self._state.validation_issues[index] = issue
                self.save()
                return issue
        raise KeyError(f"unknown validation issue: {issue.id}")

    def upsert_validation_issue(self, issue: ValidationIssue) -> ValidationIssue:
        """Create an issue or retain its latest remediation-cycle details."""

        if any(current.id == issue.id for current in self.validation_issues):
            return self.update_validation_issue(issue)
        return self.add_validation_issue(issue)

    def add_validation_result(self, result: ValidationResult) -> ValidationResult:
        """Append a typed Critic result."""

        self._state.validation_results.append(result)
        self.save()
        return result

    def record_audit(self, audit: AuditResult) -> AuditResult:
        """Persist the current Data Auditor result for this run."""

        self._state.audit = audit
        self.save()
        return audit

    def update_budget(self, budget: RunBudget) -> RunBudget:
        """Replace the typed budget and persist it."""

        self._state.run_budget = budget
        self.save()
        return budget

    def increment_budget(self, **usage: int) -> RunBudget:
        """Increment observed usage counters without changing configured limits."""

        unknown_fields = set(usage) - _USAGE_FIELDS
        if unknown_fields:
            raise ValueError(f"unknown budget usage fields: {sorted(unknown_fields)}")
        if any(not isinstance(amount, int) or amount < 0 for amount in usage.values()):
            raise ValueError("budget increments must be non-negative integers")

        updates = self.budget.model_dump()
        for field_name, amount in usage.items():
            updates[field_name] += amount
        return self.update_budget(RunBudget.model_validate(updates))

    # Descriptive aliases keep ledger operations easy to discover at call sites.
    record_finding = add_finding
    record_artifact = add_artifact
    record_validation_issue = add_validation_issue
    record_validation_result = add_validation_result
    add_audit = record_audit
    update_audit = record_audit

    def _load_from_disk(self) -> AnalysisRunState:
        try:
            return AnalysisRunState.model_validate_json(
                self.state_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise LedgerError(f"could not load ledger: {self.state_path}") from exc

    def _sync_rejected_hypotheses(self) -> None:
        self._state.rejected_hypotheses = [
            hypothesis.id
            for hypothesis in self.hypotheses
            if hypothesis.status is HypothesisStatus.REJECTED
        ]

    @staticmethod
    def _ensure_unique(
        entity_name: str,
        entity_id: str,
        existing_ids: Iterable[str],
    ) -> None:
        if entity_id in existing_ids:
            raise LedgerConflictError(f"duplicate {entity_name} id: {entity_id}")

    @staticmethod
    def _resolve_state_path(workspace_or_state_path: object | str | Path) -> Path:
        state_directory = getattr(workspace_or_state_path, "state", None)
        if state_directory is not None:
            return Path(state_directory) / _LEDGER_FILENAME

        path = Path(workspace_or_state_path)
        if path.suffix.lower() == ".json":
            return path.expanduser().resolve()
        return (path.expanduser() / _LEDGER_FILENAME).resolve()

    @staticmethod
    def _infer_run_id(workspace_or_state_path: object | str | Path) -> str | None:
        root = getattr(workspace_or_state_path, "root", None)
        if root is not None:
            return Path(root).name
        path = Path(workspace_or_state_path)
        if path.suffix.lower() == ".json":
            return path.parent.parent.name or None
        if path.name == "state":
            return path.parent.name or None
        return path.name or None
