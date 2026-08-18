"""Persistent, typed analysis ledger operations."""

import json
import os
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from pydantic import ValidationError

from orchestration.pricing import calculate_cost_breakdown
from schemas.audit import AuditResult
from schemas.findings import Finding
from schemas.metrics import (
    MetricComparison,
    deduplicate_metric_comparisons,
    metric_comparison_identity,
    normalize_metric_comparison,
)
from schemas.run_state import (
    AgentEvent,
    AgentEventStatus,
    AnalysisRunState,
    Artifact,
    Hypothesis,
    HypothesisStatus,
    ModelPricing,
    ModelUsage,
    RunBudget,
    RunStatus,
    SpecialistResultRecord,
    ToolEvent,
)
from schemas.statistics import StatisticalAssessment
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
_BUDGET_LIMIT_FIELDS = {
    "specialist_invocations": "max_specialist_invocations",
    "sql_executions": "max_sql_executions",
    "python_executions": "max_python_executions",
    "critic_loops": "max_critic_loops",
    "charts_created": "max_charts",
}


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
        self._budget_lock = RLock()
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
    def hypothesis_history(self) -> list[Hypothesis]:
        """All persisted hypothesis versions in update order."""

        return self._state.hypothesis_history

    @property
    def findings(self) -> list[Finding]:
        """Recorded analytical findings."""

        return self._state.findings

    @property
    def metric_comparisons(self) -> list[MetricComparison]:
        """Structured metric comparisons supporting the Lead answer."""

        return self._state.metric_comparisons

    @property
    def statistical_assessments(self) -> list[StatisticalAssessment]:
        """Typed statistical outputs independent of the producing architecture."""

        return self._state.statistical_assessments

    @property
    def artifacts(self) -> list[Artifact]:
        """Recorded artifact references."""

        return self._state.artifacts

    @property
    def tool_events(self) -> list[ToolEvent]:
        """Recorded tool events."""

        return self._state.tool_events

    @property
    def agent_events(self) -> list[AgentEvent]:
        """Recorded concise agent invocation trace."""

        return self._state.agent_events

    @property
    def validation_issues(self) -> list[ValidationIssue]:
        """Recorded Critic validation issues."""

        return self._state.validation_issues

    @property
    def validation_results(self) -> list[ValidationResult]:
        """Recorded typed Critic validation results."""

        return self._state.validation_results

    @property
    def specialist_results(self) -> list[SpecialistResultRecord]:
        """Typed specialist outputs retained for later audit and evaluation."""

        return self._state.specialist_results

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

        with self._budget_lock:
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
                raise LedgerError(
                    f"could not persist ledger: {self.state_path}"
                ) from exc

    @property
    def budget_lock(self) -> RLock:
        """Return the run-level lock coordinating counted reservations."""

        return self._budget_lock

    def refresh(self) -> AnalysisRunState:
        """Reload and return the typed state from disk."""

        self._state = self._load_from_disk()
        return self._state

    def set_status(self, status: RunStatus | str) -> RunStatus:
        """Persist the observable lifecycle status for the run."""

        self._state.status = RunStatus(status)
        if self._state.status is RunStatus.RUNNING:
            self._state.error = None
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

    def record_cost_estimate(
        self,
        *,
        input_cost_per_1k_tokens: float | None = None,
        cached_input_cost_per_1k_tokens: float | None = None,
        output_cost_per_1k_tokens: float | None = None,
        input_cost_per_1m: float | None = None,
        cached_input_cost_per_1m: float | None = None,
        output_cost_per_1m: float | None = None,
        pricing: ModelPricing | None = None,
        pricing_model: str | None = None,
    ) -> float | None:
        """Persist an optional cached/uncached model cost breakdown.

        The per-1k arguments remain as a compatibility path for existing
        callers. When the cached rate is omitted on that path, cached input is
        charged at the legacy input rate.
        """

        legacy_rates = (
            input_cost_per_1k_tokens,
            cached_input_cost_per_1k_tokens,
            output_cost_per_1k_tokens,
        )
        modern_rates = (input_cost_per_1m, cached_input_cost_per_1m, output_cost_per_1m)
        if pricing is not None and (
            any(rate is not None for rate in legacy_rates)
            or any(rate is not None for rate in modern_rates)
        ):
            raise ValueError("provide pricing or token rates, not both")

        if pricing is None and any(rate is not None for rate in modern_rates):
            if any(rate is None for rate in modern_rates):
                raise ValueError(
                    "input, cached input, and output cost rates must be provided"
                    " together"
                )
            pricing = ModelPricing(
                input_per_1m=input_cost_per_1m,
                cached_input_per_1m=cached_input_cost_per_1m,
                output_per_1m=output_cost_per_1m,
            )

        if pricing is None and any(rate is not None for rate in legacy_rates):
            if (
                input_cost_per_1k_tokens is not None
                and output_cost_per_1k_tokens is not None
            ):
                cached_rate = (
                    input_cost_per_1k_tokens
                    if cached_input_cost_per_1k_tokens is None
                    else cached_input_cost_per_1k_tokens
                )
                pricing = ModelPricing(
                    input_per_1m=input_cost_per_1k_tokens * 1_000,
                    cached_input_per_1m=cached_rate * 1_000,
                    output_per_1m=output_cost_per_1k_tokens * 1_000,
                )

        if pricing is None:
            self._state.cost_breakdown = None
            self._state.estimated_cost_usd = None
            self._state.cost_estimation_note = (
                "Cost estimate unavailable: model pricing rates were not configured"
                f" for {self._state.model or 'the configured model'}."
            )
        else:
            breakdown = calculate_cost_breakdown(
                self.usage,
                pricing,
                pricing_model=pricing_model or self._state.model or "configured-model",
            )
            self._state.cost_breakdown = breakdown
            self._state.estimated_cost_usd = breakdown.estimated_cost_usd
            self._state.cost_estimation_note = (
                "Estimated from configured uncached-input, cached-input, and output "
                "token rates; provider billing may differ."
            )
        self.save()
        return self._state.estimated_cost_usd

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
        self._state.hypothesis_history.append(hypothesis)
        self._sync_rejected_hypotheses()
        self.save()
        return hypothesis

    def update_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
        """Replace an existing hypothesis and persist its new status/evidence."""

        for index, current in enumerate(self.hypotheses):
            if current.id == hypothesis.id:
                if current == hypothesis:
                    return current
                self._state.hypotheses[index] = hypothesis
                self._state.hypothesis_history.append(hypothesis)
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

    def upsert_finding(self, finding: Finding) -> Finding:
        """Create a finding or replace its latest persisted version."""

        for index, current in enumerate(self.findings):
            if current.id == finding.id:
                if current == finding:
                    return current
                self._state.findings[index] = finding
                self.save()
                return finding
        return self.add_finding(finding)

    def upsert_metric_comparison(
        self,
        comparison: MetricComparison,
    ) -> MetricComparison:
        """Create or replace a comparison with the same generic identity."""

        comparison = normalize_metric_comparison(comparison)
        identity = metric_comparison_identity(comparison)
        for index, current in enumerate(self.metric_comparisons):
            if metric_comparison_identity(current) == identity:
                if current == comparison:
                    return current
                self._state.metric_comparisons[index] = comparison
                self.save()
                return comparison
        self._state.metric_comparisons.append(comparison)
        self.save()
        return comparison

    def replace_metric_comparisons(
        self,
        comparisons: list[MetricComparison],
    ) -> list[MetricComparison]:
        """Persist the canonical final metric set as one source of truth."""

        normalized = [normalize_metric_comparison(item) for item in comparisons]
        if self._state.metric_comparisons == normalized:
            return self._state.metric_comparisons
        self._state.metric_comparisons = normalized
        self.save()
        return self._state.metric_comparisons

    def replace_statistical_assessments(
        self,
        assessments: list[StatisticalAssessment],
    ) -> list[StatisticalAssessment]:
        """Persist the canonical typed statistical output for this run."""

        unique: list[StatisticalAssessment] = []
        for assessment in assessments:
            if assessment not in unique:
                unique.append(assessment)
        if self._state.statistical_assessments == unique:
            return self._state.statistical_assessments
        self._state.statistical_assessments = unique
        self.save()
        return self._state.statistical_assessments

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

    def append_agent_event(self, event: AgentEvent) -> None:
        """Append an agent invocation trace entry with a unique identifier."""

        self._ensure_unique(
            "agent event", event.id, (item.id for item in self.agent_events)
        )
        self._state.agent_events.append(event)
        self.save()

    def record_agent_event(
        self,
        *,
        agent_name: str,
        agent_role: str,
        status: AgentEventStatus,
        model: str | None = None,
        objective: str | None = None,
        output_type: str | None = None,
        error: str | None = None,
    ) -> AgentEvent:
        """Create and persist a concise agent invocation trace entry."""

        completed_at = datetime.now(UTC)
        event = AgentEvent(
            id=f"agent-{uuid.uuid4().hex}",
            agent_name=agent_name,
            agent_role=agent_role,
            status=status,
            started_at=completed_at,
            completed_at=completed_at,
            model=model,
            objective=objective,
            output_type=output_type,
            error=error,
        )
        self.append_agent_event(event)
        return event

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

    def record_specialist_result(
        self,
        agent_role: str,
        result: object,
    ) -> SpecialistResultRecord:
        """Persist a typed specialist result without raw model transcript data."""

        from schemas.findings import SpecialistResult

        record = SpecialistResultRecord(
            agent_role=agent_role,
            result=(
                result
                if isinstance(result, SpecialistResult)
                else SpecialistResult.model_validate(result)
            ),
        )
        record = record.model_copy(
            update={
                "result": record.result.model_copy(
                    update={
                        "metric_comparisons": deduplicate_metric_comparisons(
                            record.result.metric_comparisons
                        )
                    }
                )
            }
        )
        self._state.specialist_results.append(record)
        self.save()
        return record

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
        """Increment usage while preserving every configured hard limit."""

        unknown_fields = set(usage) - _USAGE_FIELDS
        if unknown_fields:
            raise ValueError(f"unknown budget usage fields: {sorted(unknown_fields)}")
        if any(not isinstance(amount, int) or amount < 0 for amount in usage.values()):
            raise ValueError("budget increments must be non-negative integers")

        with self._budget_lock:
            updates = self.budget.model_dump()
            for field_name, amount in usage.items():
                self._validate_budget_increment(updates, field_name, amount)
                updates[field_name] += amount
            self._state.run_budget = RunBudget.model_validate(updates)
            self.save()
            return self.budget

    def reserve_budget(self, resource: str) -> RunBudget:
        """Atomically reserve one unit of a counted run resource."""

        return self.reserve_budgets([resource])

    def reserve_budgets(self, resources: Iterable[str]) -> RunBudget:
        """Atomically reserve one unit for each requested resource.

        All capacity checks happen before any usage is persisted, so a grouped
        reservation such as Critic specialist invocation plus loop count cannot
        partially consume the run budget.
        """

        normalized = [
            resource.value if hasattr(resource, "value") else str(resource)
            for resource in resources
        ]
        unknown_fields = set(normalized) - _USAGE_FIELDS
        if unknown_fields:
            raise ValueError(f"unknown budget resources: {sorted(unknown_fields)}")

        with self._budget_lock:
            updates = self.budget.model_dump()
            counts: dict[str, int] = {}
            for resource in normalized:
                counts[resource] = counts.get(resource, 0) + 1
            for field_name, amount in counts.items():
                self._validate_budget_increment(updates, field_name, amount)
            for field_name, amount in counts.items():
                updates[field_name] += amount
            self._state.run_budget = RunBudget.model_validate(updates)
            self.save()
            return self.budget

    @staticmethod
    def _validate_budget_increment(
        budget_values: dict[str, Any],
        field_name: str,
        amount: int,
    ) -> None:
        limit_field = _BUDGET_LIMIT_FIELDS[field_name]
        current = budget_values[field_name]
        limit = budget_values[limit_field]
        if current + amount <= limit:
            return
        from orchestration.budgets import BudgetExhaustedError, BudgetSnapshot

        snapshot = BudgetSnapshot(
            resource=field_name,
            used=current,
            limit=limit,
            remaining=max(limit - current, 0),
        )
        raise BudgetExhaustedError(snapshot)

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
