"""Application-level Phase 1 analysis lifecycle orchestration."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from agents import Agent, MaxTurnsExceeded
from agents.audit_evidence import persist_audit_result
from agents.auditor import build_data_auditor_agent, run_data_auditor
from agents.critic import (
    build_critic_agent,
    deterministic_candidate_validation,
    persist_validation_result,
    run_critic,
)
from agents.finalization import (
    build_validation_catalog,
    is_essential_impossible,
    metric_definition_change_targets,
    objective_requests_visualization,
    preserve_unrelated_candidate,
    rejected_review_result,
    repair_class_for,
    structured_metrics_required,
    validate_review,
)
from agents.generalist import build_generalist_agent
from agents.lead import build_lead_agent, persist_lead_result, run_lead
from agents.runtime import (
    DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    normalize_agent_turn_limits,
)
from orchestration.block_reasons import (
    BlockedAuditError,
    RunConstraint,
    constraint_from_exception,
)
from orchestration.budgets import BudgetExhaustedError, BudgetResource
from orchestration.ledger import AnalysisLedger
from orchestration.pricing import resolve_model_pricing
from schemas.audit import AuditResult, AuditStatus
from schemas.lead import LeadResult
from schemas.run_state import (
    AgentEventStatus,
    AnalysisRunState,
    Artifact,
    ArtifactKind,
    AttemptStatus,
    RunBlockReason,
    RunBudget,
    RunStatus,
)
from schemas.validation import (
    CriticCandidate,
    FinalizationRepairRecord,
    RepairStatus,
    ValidationResult,
    ValidationStatus,
)
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.sql_deadline import DEFAULT_SQL_TIMEOUT_SECONDS, validate_sql_timeout
from tools.workspace import Workspace, WorkspaceManager

AuditorRunner = Callable[..., Awaitable[AuditResult]]
LeadRunner = Callable[..., Awaitable[LeadResult]]
CriticRunner = Callable[..., Awaitable[ValidationResult]]
MAX_LEAD_FOLLOW_UP_CYCLES = 2
MAX_FINALIZATION_REPAIRS = 1
# Compatibility name for callers that asserted the former completion-pass bound.
MAX_LEAD_COMPLETION_PASSES = MAX_FINALIZATION_REPAIRS


def _critic_allows_metric_definition_change(
    validation: ValidationResult,
) -> bool:
    """Return whether Critic explicitly requested an estimand correction."""

    return bool(metric_definition_change_targets(validation))


@dataclass(slots=True)
class AnalysisRunResult:
    """Typed application result containing the persisted run products."""

    status: RunStatus
    workspace: Workspace | None
    ledger: AnalysisLedger | None
    audit: AuditResult | None = None
    lead_result: LeadResult | None = None
    validation_result: ValidationResult | None = None
    report: Artifact | None = None
    constrained: bool = False
    error: str | None = None
    block_reason: RunBlockReason | None = None
    block_detail: str | None = None

    @property
    def state(self) -> AnalysisRunState | None:
        """Return the current typed ledger state when a ledger exists."""

        return self.ledger.state if self.ledger is not None else None


class AnalysisRunner:
    """Enforce the mandatory audit, Lead, Critic, and report lifecycle."""

    def __init__(
        self,
        workspace_manager: WorkspaceManager | None = None,
        *,
        workspace_base_dir: str | Path | None = None,
        model: str = "configured-model",
        model_provider: str = "openai",
        docker_image: str = "data-science-agent-python:latest",
        budget: RunBudget | None = None,
        agent_turn_limits: Mapping[AgentRole | str, int] | None = None,
        agent_run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,
        sql_timeout_seconds: float = DEFAULT_SQL_TIMEOUT_SECONDS,
        input_cost_per_1k_tokens: float | None = None,
        cached_input_cost_per_1k_tokens: float | None = None,
        output_cost_per_1k_tokens: float | None = None,
        input_cost_per_1m: float | None = None,
        cached_input_cost_per_1m: float | None = None,
        output_cost_per_1m: float | None = None,
        auditor_runner: AuditorRunner | None = None,
        lead_runner: LeadRunner | None = None,
        critic_runner: CriticRunner | None = None,
    ) -> None:
        if workspace_manager is not None and workspace_base_dir is not None:
            raise ValueError(
                "provide workspace_manager or workspace_base_dir, not both"
            )
        self.workspace_manager = workspace_manager or WorkspaceManager(
            workspace_base_dir or Path("workspaces")
        )
        self.model = model
        self.model_provider = model_provider
        self.docker_image = docker_image
        self.budget = budget
        self.agent_turn_limits = normalize_agent_turn_limits(agent_turn_limits)
        if not 0 < agent_run_timeout_seconds <= 3_600:
            raise ValueError(
                "agent_run_timeout_seconds must be greater than 0 and at most 3600"
            )
        self.agent_run_timeout_seconds = float(agent_run_timeout_seconds)
        self.sql_timeout_seconds = validate_sql_timeout(sql_timeout_seconds)
        legacy_rates = (
            input_cost_per_1k_tokens,
            cached_input_cost_per_1k_tokens,
            output_cost_per_1k_tokens,
        )
        modern_rates = (input_cost_per_1m, cached_input_cost_per_1m, output_cost_per_1m)
        if any(rate is not None for rate in legacy_rates) and any(
            rate is not None for rate in modern_rates
        ):
            raise ValueError("use either per-1k or per-1m cost overrides, not both")
        if any(rate is not None for rate in legacy_rates):
            if input_cost_per_1k_tokens is None or output_cost_per_1k_tokens is None:
                raise ValueError(
                    "input and output cost rates must be provided together"
                )
            cached_rate = (
                input_cost_per_1k_tokens
                if cached_input_cost_per_1k_tokens is None
                else cached_input_cost_per_1k_tokens
            )
            self.model_pricing = resolve_model_pricing(
                self.model,
                input_per_1m=input_cost_per_1k_tokens * 1_000,
                cached_input_per_1m=cached_rate * 1_000,
                output_per_1m=output_cost_per_1k_tokens * 1_000,
            )
        else:
            self.model_pricing = resolve_model_pricing(
                self.model,
                input_per_1m=input_cost_per_1m,
                cached_input_per_1m=cached_input_cost_per_1m,
                output_per_1m=output_cost_per_1m,
            )
        self.auditor_runner = auditor_runner or run_data_auditor
        self.lead_runner = lead_runner or run_lead
        self.critic_runner = critic_runner or run_critic

    async def run(
        self,
        run_id: str,
        objective: str,
        *,
        inputs_source: str | Path | None = None,
        docs_source: str | Path | None = None,
        business_context: str | None = None,
        workspace: Workspace | None = None,
    ) -> AnalysisRunResult:
        """Run one bounded analysis and persist every lifecycle transition."""

        started = time.perf_counter()
        ledger: AnalysisLedger | None = None
        run_workspace: Workspace | None = workspace
        audit: AuditResult | None = None
        lead_result: LeadResult | None = None
        validation_result: ValidationResult | None = None
        report: Artifact | None = None
        constrained = False
        constraint: RunConstraint | None = None
        active_agent: tuple[str, AgentRole, str] | None = None
        active_agent_recorded = False
        runtime_metadata_finalized = False
        attempt_terminal_status: AttemptStatus | None = None
        attempt_terminal_error: str | None = None
        attempt_finalized = False

        try:
            run_workspace = run_workspace or self._open_or_create_workspace(
                run_id,
                inputs_source=inputs_source,
                docs_source=docs_source,
            )
            ledger = AnalysisLedger(
                run_workspace,
                run_id=run_id,
                objective=objective,
                business_context=business_context,
            )
            ledger.begin_attempt()
            self._configure_ledger(ledger, run_id, business_context)
            ledger.set_status(RunStatus.RUNNING)

            audit_context, audit_agent = self._agent_context(
                run_workspace,
                ledger,
                AgentRole.DATA_AUDITOR,
            )
            active_agent = (audit_agent.name, AgentRole.DATA_AUDITOR, "preflight audit")
            audit = await self.auditor_runner(
                audit_context,
                self._audit_prompt(business_context),
                agent=audit_agent,
            )
            audit_context.assert_base_role(AgentRole.DATA_AUDITOR)
            if not isinstance(audit, AuditResult):
                audit = AuditResult.model_validate(audit)
            self._record_agent_success(ledger, active_agent, AuditResult)
            active_agent_recorded = True
            # Keep this explicit even though the existing specialist runner also
            # persists it; it makes the application lifecycle invariant clear.
            # Persisting through the shared boundary also means an injected
            # auditor runner cannot bypass audit-provenance validation.
            audit = persist_audit_result(audit, audit_context)
            if audit.status is AuditStatus.BLOCKED:
                raise BlockedAuditError("mandatory data audit was blocked")

            lead_context, lead_agent = self._agent_context(
                run_workspace,
                ledger,
                AgentRole.LEAD,
            )
            lead_follow_up_cycles = 0

            async def run_lead_candidate(
                lead_objective: str,
                *,
                allow_follow_up: bool = True,
                prior_result: LeadResult | None = None,
                allowed_definition_change_targets: frozenset[str] = frozenset(),
                repair_validation: ValidationResult | None = None,
            ) -> tuple[LeadResult, str | None]:
                """Run Lead and optionally exhaust objective-critical follow-up."""

                nonlocal active_agent
                nonlocal active_agent_recorded
                nonlocal lead_follow_up_cycles

                active_agent = (lead_agent.name, AgentRole.LEAD, lead_objective)
                active_agent_recorded = False
                candidate = await self.lead_runner(
                    lead_context,
                    lead_objective,
                    business_context=business_context,
                    audit=audit,
                    agent=lead_agent,
                )
                lead_context.assert_base_role(AgentRole.LEAD)
                if not isinstance(candidate, LeadResult):
                    candidate = LeadResult.model_validate(candidate)
                if prior_result is not None and repair_validation is not None:
                    candidate = preserve_unrelated_candidate(
                        prior_result,
                        candidate,
                        repair_validation,
                    )
                self._record_agent_success(ledger, active_agent, LeadResult)
                active_agent_recorded = True
                candidate = persist_lead_result(
                    candidate,
                    lead_context,
                    prior_result=prior_result,
                    allowed_definition_change_targets=(
                        allowed_definition_change_targets
                    ),
                )

                if not allow_follow_up:
                    return candidate, None

                while candidate.follow_up_analysis:
                    if lead_follow_up_cycles >= MAX_LEAD_FOLLOW_UP_CYCLES:
                        return (
                            candidate,
                            RunConstraint(
                                reason=RunBlockReason.UNRESOLVED_FOLLOW_UP,
                                detail=(
                                    "Lead requested additional "
                                    "objective-critical analysis after the "
                                    "configured continuation limit."
                                ),
                            ),
                        )

                    lead_follow_up_cycles += 1
                    continuation_prompt = self._follow_up_prompt(
                        objective,
                        candidate,
                        ledger,
                        cycle=lead_follow_up_cycles,
                    )
                    active_agent = (
                        lead_agent.name,
                        AgentRole.LEAD,
                        continuation_prompt,
                    )
                    active_agent_recorded = False
                    try:
                        continued = await self.lead_runner(
                            lead_context,
                            continuation_prompt,
                            business_context=business_context,
                            audit=audit,
                            agent=lead_agent,
                        )
                        lead_context.assert_base_role(AgentRole.LEAD)
                        if not isinstance(continued, LeadResult):
                            continued = LeadResult.model_validate(continued)
                        self._record_agent_success(
                            ledger,
                            active_agent,
                            LeadResult,
                        )
                        active_agent_recorded = True
                        candidate = persist_lead_result(
                            continued,
                            lead_context,
                            prior_result=candidate,
                        )
                    except Exception as error:
                        follow_up = self._follow_up_failure_reason(error)
                        if active_agent is not None and not active_agent_recorded:
                            agent_name, role, agent_objective = active_agent
                            ledger.record_agent_event(
                                agent_name=agent_name,
                                agent_role=role.value,
                                status=AgentEventStatus.FAILED,
                                model=self.model,
                                objective=agent_objective,
                                error=follow_up.detail,
                            )
                            active_agent_recorded = True
                        return candidate, follow_up

                return candidate, None

            lead_result, follow_up_constraint = await run_lead_candidate(objective)
            if follow_up_constraint is not None:
                constrained = True
                constraint = follow_up_constraint

            completion_allowed = follow_up_constraint is None

            critic_context, critic_agent = self._agent_context(
                run_workspace,
                ledger,
                AgentRole.CRITIC,
            )
            critic_attempts = 0
            finalization_repairs = 0
            available_critic_loops = (
                ledger.budget.max_critic_loops - ledger.budget.critic_loops
            )
            while True:
                candidate = self._candidate(
                    objective,
                    lead_result,
                    require_visualization=self._objective_requests_visualization(
                        objective
                    ),
                )
                preflight = deterministic_candidate_validation(candidate, lead_context)
                if preflight is not None:
                    catalog = build_validation_catalog(candidate)
                    if (
                        not ledger.validation_candidates
                        or ledger.validation_candidates[-1] != candidate
                    ):
                        ledger.add_validation_snapshot(candidate, catalog)
                    if preflight not in ledger.validation_results:
                        persist_validation_result(
                            preflight, ledger, allow_issue_updates=True
                        )
                    if (
                        completion_allowed
                        and finalization_repairs < MAX_FINALIZATION_REPAIRS
                        and not is_essential_impossible(preflight)
                    ):
                        finalization_repairs += 1
                        prior_selected_ids = list(lead_result.selected_result_ids)
                        preserved_selection = {
                            name: deepcopy(getattr(ledger.state, name))
                            for name in (
                                "findings",
                                "metric_comparisons",
                                "statistical_assessments",
                                "statistical_assessment_history",
                                "hypotheses",
                                "hypothesis_history",
                                "open_questions",
                            )
                        }
                        lead_context.begin_finalization_repair(
                            repair_class_for(preflight)
                        )
                        try:
                            (
                                lead_result,
                                completion_constraint,
                            ) = await run_lead_candidate(
                                self._completion_prompt(
                                    objective,
                                    lead_result,
                                    preflight,
                                ),
                                allow_follow_up=False,
                                prior_result=lead_result,
                                repair_validation=preflight,
                            )
                            if completion_constraint is not None:
                                constrained = True
                                constraint = completion_constraint
                            ledger.add_finalization_repair(
                                FinalizationRepairRecord(
                                    attempt_id=(
                                        f"{ledger.state.attempt_id}:finalization-repair-1"
                                    ),
                                    blocker_ids=[
                                        item.id or "unassigned"
                                        for item in preflight.blockers
                                    ],
                                    status=RepairStatus.SUCCEEDED,
                                    prior_selected_result_ids=prior_selected_ids,
                                    repaired_selected_result_ids=list(
                                        lead_result.selected_result_ids
                                    ),
                                )
                            )
                            continue
                        except Exception as error:
                            for name, value in preserved_selection.items():
                                setattr(ledger.state, name, value)
                            ledger.save()
                            constrained = True
                            constraint = constraint_from_exception(
                                error,
                                context="Lead completion pass",
                            )
                            ledger.add_finalization_repair(
                                FinalizationRepairRecord(
                                    attempt_id=(
                                        f"{ledger.state.attempt_id}:finalization-repair-1"
                                    ),
                                    blocker_ids=[
                                        item.id or "unassigned"
                                        for item in preflight.blockers
                                    ],
                                    status=RepairStatus.FAILED,
                                    prior_selected_result_ids=prior_selected_ids,
                                    stop_reason=constraint.detail,
                                )
                            )
                        finally:
                            lead_context.end_finalization_repair()
                    validation_result = preflight
                    constrained = True
                    if constraint is None:
                        constraint = RunConstraint(
                            reason=RunBlockReason.VALIDATION_REVISION,
                            detail=(
                                "Deterministic finalization checks still require "
                                "revision."
                            ),
                        )
                    break
                active_agent = (critic_agent.name, AgentRole.CRITIC, objective)
                active_agent_recorded = False
                try:
                    critic_context.check_budget(BudgetResource.CRITIC_LOOPS)
                    critic_loop_usage = ledger.budget.critic_loops
                    validation_result = await self.critic_runner(
                        critic_context,
                        candidate,
                        agent=critic_agent,
                    )
                    critic_context.assert_base_role(AgentRole.CRITIC)
                    self._ensure_budget_increment(
                        critic_context,
                        BudgetResource.CRITIC_LOOPS,
                        critic_loop_usage,
                    )
                except (BudgetExhaustedError, MaxTurnsExceeded) as error:
                    # A prior Critic result is still the last valid review. If
                    # a bounded re-review cannot start or finish, preserve it
                    # and render a constrained report instead of converting a
                    # recoverable remediation stop into a failed run.
                    constrained = True
                    constraint = self._critic_failure_reason(error)
                    if active_agent is not None and not active_agent_recorded:
                        agent_name, role, agent_objective = active_agent
                        ledger.record_agent_event(
                            agent_name=agent_name,
                            agent_role=role.value,
                            status=AgentEventStatus.FAILED,
                            model=self.model,
                            objective=agent_objective,
                            error=constraint.detail,
                        )
                        active_agent_recorded = True
                    break
                except Exception as error:
                    validation_result = rejected_review_result(error)
                    catalog = build_validation_catalog(candidate)
                    ledger.add_validation_snapshot(candidate, catalog)
                    persist_validation_result(
                        validation_result,
                        critic_context.ledger,
                        allow_issue_updates=True,
                    )
                    constrained = True
                    constraint = RunConstraint(
                        reason=RunBlockReason.VALIDATION_REVISION,
                        detail=validation_result.summary
                        or "The runtime review failed validation.",
                    )
                    if active_agent is not None and not active_agent_recorded:
                        agent_name, role, agent_objective = active_agent
                        ledger.record_agent_event(
                            agent_name=agent_name,
                            agent_role=role.value,
                            status=AgentEventStatus.FAILED,
                            model=self.model,
                            objective=agent_objective,
                            error=constraint.detail,
                        )
                        active_agent_recorded = True
                    break
                try:
                    if not isinstance(validation_result, ValidationResult):
                        validation_result = ValidationResult.model_validate(
                            validation_result
                        )
                    catalog = build_validation_catalog(candidate)
                    validation_result = validate_review(
                        validation_result, candidate, ledger, catalog=catalog
                    )
                except Exception as error:
                    # A malformed or ungrounded review is not a candidate
                    # defect and cannot trigger a repair. Preserve the valid
                    # candidate, record the rejected boundary, and stop closed.
                    validation_result = rejected_review_result(error)
                    catalog = build_validation_catalog(candidate)
                    ledger.add_validation_snapshot(candidate, catalog)
                    persist_validation_result(
                        validation_result,
                        critic_context.ledger,
                        allow_issue_updates=True,
                    )
                    constrained = True
                    constraint = RunConstraint(
                        reason=RunBlockReason.VALIDATION_REVISION,
                        detail=validation_result.summary
                        or "The runtime review failed validation.",
                    )
                    if active_agent is not None and not active_agent_recorded:
                        agent_name, role, agent_objective = active_agent
                        ledger.record_agent_event(
                            agent_name=agent_name,
                            agent_role=role.value,
                            status=AgentEventStatus.FAILED,
                            model=self.model,
                            objective=agent_objective,
                            error=constraint.detail,
                        )
                        active_agent_recorded = True
                    break
                if (
                    not ledger.validation_candidates
                    or ledger.validation_candidates[-1] != candidate
                ):
                    ledger.add_validation_snapshot(candidate, catalog)
                self._record_agent_success(ledger, active_agent, ValidationResult)
                active_agent_recorded = True
                if validation_result not in critic_context.ledger.validation_results:
                    persist_validation_result(
                        validation_result,
                        critic_context.ledger,
                        allow_issue_updates=True,
                    )
                critic_attempts += 1
                if validation_result.status is ValidationStatus.PASS:
                    break

                if finalization_repairs >= MAX_FINALIZATION_REPAIRS:
                    constrained = True
                    constraint = RunConstraint(
                        reason=RunBlockReason.VALIDATION_REVISION,
                        detail=(
                            "The single finalization repair allowance was exhausted."
                        ),
                    )
                    break

                if critic_attempts >= available_critic_loops:
                    constrained = True
                    constraint = RunConstraint(
                        reason=RunBlockReason.VALIDATION_REVISION,
                        detail=(
                            "Critic returned REVISE and the configured maximum "
                            f"of {available_critic_loops} critic loop(s) was "
                            "reached."
                        ),
                    )
                    break

                remediation_prompt = self._remediation_prompt(
                    objective,
                    lead_result,
                    validation_result,
                    business_context=business_context,
                )
                try:
                    finalization_repairs += 1
                    prior_selected_ids = list(lead_result.selected_result_ids)
                    preserved_selection = {
                        name: deepcopy(getattr(ledger.state, name))
                        for name in (
                            "findings",
                            "metric_comparisons",
                            "statistical_assessments",
                            "statistical_assessment_history",
                            "hypotheses",
                            "hypothesis_history",
                            "open_questions",
                        )
                    }
                    lead_context.begin_finalization_repair(
                        repair_class_for(validation_result)
                    )
                    (
                        remediated_lead_result,
                        follow_up_constraint,
                    ) = await run_lead_candidate(
                        remediation_prompt,
                        allow_follow_up=False,
                        prior_result=lead_result,
                        allowed_definition_change_targets=(
                            metric_definition_change_targets(validation_result)
                        ),
                        repair_validation=validation_result,
                    )
                    lead_result = remediated_lead_result
                    ledger.add_finalization_repair(
                        FinalizationRepairRecord(
                            attempt_id=(
                                f"{ledger.state.attempt_id}:finalization-repair-1"
                            ),
                            blocker_ids=[
                                item.id or "unassigned"
                                for item in validation_result.blockers
                            ],
                            status=RepairStatus.SUCCEEDED,
                            prior_selected_result_ids=prior_selected_ids,
                            repaired_selected_result_ids=list(
                                lead_result.selected_result_ids
                            ),
                        )
                    )
                    if follow_up_constraint is not None:
                        constrained = True
                        constraint = follow_up_constraint
                except Exception as error:
                    for name, value in preserved_selection.items():
                        setattr(ledger.state, name, value)
                    ledger.save()
                    # A usable candidate and Critic result already exist. Keep
                    # them and produce a constrained report when bounded
                    # remediation cannot complete, including its stop reason.
                    constrained = True
                    constraint = self._remediation_failure_reason(error)
                    ledger.add_finalization_repair(
                        FinalizationRepairRecord(
                            attempt_id=(
                                f"{ledger.state.attempt_id}:finalization-repair-1"
                            ),
                            blocker_ids=[
                                item.id or "unassigned"
                                for item in validation_result.blockers
                            ],
                            status=RepairStatus.FAILED,
                            prior_selected_result_ids=prior_selected_ids,
                            stop_reason=constraint.detail,
                        )
                    )
                    if active_agent is not None and not active_agent_recorded:
                        agent_name, role, agent_objective = active_agent
                        ledger.record_agent_event(
                            agent_name=agent_name,
                            agent_role=role.value,
                            status=AgentEventStatus.FAILED,
                            model=self.model,
                            objective=agent_objective,
                            error=constraint.detail,
                        )
                        active_agent_recorded = True
                    break
                finally:
                    lead_context.end_finalization_repair()

            self._finalize_runtime_metadata(ledger, started)
            runtime_metadata_finalized = True
            report = self._write_report(
                run_workspace,
                ledger,
                objective,
                audit,
                lead_result,
                validation_result,
                constrained=constrained,
                constraint_reason=constraint.detail if constraint else None,
            )
            if constrained:
                blocking = constraint or RunConstraint(
                    reason=RunBlockReason.OTHER,
                    detail="The analysis was constrained without a stated cause.",
                )
                ledger.mark_blocked(blocking.reason, blocking.detail)
                attempt_terminal_status = AttemptStatus.BLOCKED
            else:
                ledger.set_status(RunStatus.COMPLETED)
                attempt_terminal_status = AttemptStatus.COMPLETED
            return AnalysisRunResult(
                status=ledger.state.status,
                workspace=run_workspace,
                ledger=ledger,
                audit=audit,
                lead_result=lead_result,
                validation_result=validation_result,
                report=report,
                constrained=constrained,
                block_reason=ledger.state.block_reason,
                block_detail=ledger.state.block_detail,
            )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            constraint = constraint_from_exception(error, context="The analysis")
            if ledger is not None:
                if active_agent is not None and not active_agent_recorded:
                    agent_name, role, agent_objective = active_agent
                    ledger.record_agent_event(
                        agent_name=agent_name,
                        agent_role=role.value,
                        status=AgentEventStatus.FAILED,
                        model=self.model,
                        objective=agent_objective,
                        error=message,
                    )
                attempt_terminal_status = AttemptStatus.FAILED
                attempt_terminal_error = message
                ledger.mark_failed(
                    message,
                    reason=constraint.reason,
                    detail=constraint.detail,
                )
            return AnalysisRunResult(
                status=RunStatus.FAILED,
                workspace=run_workspace,
                ledger=ledger,
                audit=audit,
                lead_result=lead_result,
                validation_result=validation_result,
                report=report,
                constrained=constrained,
                error=message,
                block_reason=constraint.reason,
                block_detail=constraint.detail,
            )
        finally:
            if ledger is not None and not runtime_metadata_finalized:
                try:
                    self._finalize_runtime_metadata(ledger, started)
                except Exception:
                    # Do not mask a primary lifecycle or persistence error with a
                    # final metadata-write failure.
                    pass
            if ledger is not None and not attempt_finalized:
                pending = sys.exc_info()[1]
                if attempt_terminal_status is None and pending is not None:
                    attempt_terminal_status = AttemptStatus.INTERRUPTED
                    attempt_terminal_error = f"{type(pending).__name__}: {pending}"
                    # Reconcile the workspace's top-level status with the
                    # interrupted attempt so the cell is an observed outcome
                    # rather than a workspace stuck in ``running``.
                    try:
                        ledger.mark_cancelled(attempt_terminal_error)
                    except Exception:
                        pass
                if attempt_terminal_status is not None:
                    try:
                        ledger.finish_attempt(
                            attempt_terminal_status,
                            error=attempt_terminal_error,
                        )
                        attempt_finalized = True
                    except Exception:
                        # Preserve the primary lifecycle result if terminal
                        # attempt publication itself encounters an I/O error.
                        pass

    def _finalize_runtime_metadata(
        self,
        ledger: AnalysisLedger,
        started: float,
    ) -> None:
        """Persist final usage/cost and elapsed time before report rendering."""

        ledger.record_cost_estimate(
            pricing=self.model_pricing,
            pricing_model=self.model,
        )
        ledger.record_elapsed(time.perf_counter() - started)

    def _record_agent_success(
        self,
        ledger: AnalysisLedger,
        invocation: tuple[str, AgentRole, str] | None,
        output_type: type[object],
    ) -> None:
        """Persist a bounded trace entry without storing raw model output."""

        if invocation is None:
            return
        agent_name, role, objective = invocation
        ledger.record_agent_event(
            agent_name=agent_name,
            agent_role=role.value,
            status=AgentEventStatus.SUCCEEDED,
            model=self.model,
            objective=objective,
            output_type=output_type.__name__,
        )

    @staticmethod
    def _remediation_failure_reason(error: Exception) -> RunConstraint:
        """Classify why an existing candidate could not be remediated."""

        return constraint_from_exception(error, context="Remediation")

    @staticmethod
    def _critic_failure_reason(error: Exception) -> RunConstraint:
        """Classify why a later Critic review could not be completed."""

        return constraint_from_exception(error, context="Critic re-review")

    @staticmethod
    def _follow_up_failure_reason(error: Exception) -> RunConstraint:
        """Classify why an objective-critical Lead continuation stopped."""

        return constraint_from_exception(error, context="Lead follow-up")

    @staticmethod
    def _ensure_budget_increment(
        context: AgentRunContext,
        resource: BudgetResource,
        previous_usage: int,
    ) -> None:
        """Keep injected runners subject to the same observable budgets."""

        if getattr(context.ledger.budget, resource.value) == previous_usage:
            context.consume_budget(resource)

    def run_sync(self, *args: object, **kwargs: object) -> AnalysisRunResult:
        """Synchronous convenience wrapper for CLI/manual callers."""

        return asyncio.run(self.run(*args, **kwargs))

    def _open_or_create_workspace(
        self,
        run_id: str,
        *,
        inputs_source: str | Path | None,
        docs_source: str | Path | None,
    ) -> Workspace:
        root = self.workspace_manager.base_dir / run_id
        if root.exists():
            if inputs_source is not None or docs_source is not None:
                raise ValueError(
                    "source directories cannot be supplied when opening an existing run"
                )
            return self.workspace_manager.open_workspace(run_id)
        return self.workspace_manager.create_workspace(
            run_id,
            inputs_source=inputs_source,
            docs_source=docs_source,
        )

    def _configure_ledger(
        self,
        ledger: AnalysisLedger,
        run_id: str,
        business_context: str | None,
    ) -> None:
        """Apply run configuration without resetting observed usage on resume."""

        if ledger.state.run_id != run_id:
            raise ValueError("workspace ledger run_id does not match requested run")
        if business_context and ledger.state.business_context is None:
            ledger.set_business_context(business_context)
        if self.budget is not None:
            values = ledger.budget.model_dump()
            configured = self.budget.model_dump()
            for field_name in (
                "max_specialist_invocations",
                "max_sql_executions",
                "max_python_executions",
                "max_critic_loops",
                "max_charts",
            ):
                values[field_name] = configured[field_name]
            ledger.update_budget(RunBudget.model_validate(values))
        ledger.record_run_metadata(
            model=self.model,
            model_provider=self.model_provider,
        )

    def _agent_context(
        self,
        workspace: Workspace,
        ledger: AnalysisLedger,
        role: AgentRole,
    ) -> tuple[AgentRunContext, Agent[AgentRunContext]]:
        config = AgentRunConfig(
            run_id=ledger.state.run_id,
            attempt_id=ledger.state.attempt_id,
            agent_role=role,
            model=self.model,
            model_provider=self.model_provider,
            agent_turn_limits=self.agent_turn_limits,
            agent_run_timeout_seconds=self.agent_run_timeout_seconds,
            sql_timeout_seconds=self.sql_timeout_seconds,
        )
        context = AgentRunContext(
            workspace=workspace,
            ledger=ledger,
            sql_service=DuckDBExecutionService(
                workspace, ledger, sql_timeout_seconds=self.sql_timeout_seconds
            ),
            python_service=PythonExecutionService(
                workspace,
                ledger,
                image=self.docker_image,
            ),
            artifact_manager=ArtifactManager(workspace, ledger),
            run_config=config,
        )
        if role is AgentRole.DATA_AUDITOR:
            return context, build_data_auditor_agent(config)
        if role is AgentRole.GENERALIST:
            return context, build_generalist_agent(config)
        if role is AgentRole.LEAD:
            return context, build_lead_agent(config)
        if role is AgentRole.CRITIC:
            return context, build_critic_agent(config)
        raise ValueError(f"unsupported AnalysisRunner role: {role.value}")

    @staticmethod
    def _audit_prompt(business_context: str | None) -> str:
        if not business_context:
            return (
                "Perform the mandatory preflight audit. Inspect all available "
                "workspace data and business definitions."
            )
        return (
            "Perform the mandatory preflight audit. Inspect all available "
            "workspace data and business definitions. Business context:\n"
            + business_context
        )

    @staticmethod
    def _candidate(
        objective: str,
        result: LeadResult,
        *,
        require_visualization: bool = False,
    ) -> CriticCandidate:
        recommendations = [
            f"{item.id}: {item.statement} "
            f"[evidence_refs: {', '.join(item.evidence_refs)}]"
            for item in result.recommendations
        ]
        evidence_refs: list[str] = []
        for finding in result.findings:
            evidence_refs.extend(finding.evidence_refs)
        for recommendation in result.recommendations:
            evidence_refs.extend(recommendation.evidence_refs)
        for hypothesis in result.hypotheses:
            evidence_refs.extend(hypothesis.evidence_refs)
        for comparison in [*result.metric_comparisons, *result.statistical_assessments]:
            evidence_refs.extend(comparison.evidence_refs)
        return CriticCandidate(
            objective=objective,
            answer=result.answer,
            findings=result.findings,
            metric_comparisons=result.metric_comparisons,
            statistical_assessments=result.statistical_assessments,
            caveats=result.caveats,
            metric_conflicts=result.metric_conflicts,
            recommendations=recommendations,
            hypotheses=result.hypotheses,
            open_questions=result.open_questions,
            follow_up_analysis=result.follow_up_analysis,
            follow_up_rationale=result.follow_up_rationale,
            artifacts=result.artifacts,
            evidence_refs=list(dict.fromkeys(evidence_refs)),
            structured_metrics_required=structured_metrics_required(result),
            visualization_requested=require_visualization,
        )

    @staticmethod
    def _objective_requests_visualization(objective: str) -> bool:
        """Return whether the user explicitly requested a visual deliverable."""

        return objective_requests_visualization(objective)

    @staticmethod
    def _follow_up_prompt(
        objective: str,
        result: LeadResult,
        ledger: AnalysisLedger,
        *,
        cycle: int,
    ) -> str:
        """Build a bounded continuation request for material open questions."""

        questions = list(
            dict.fromkeys([*result.open_questions, *ledger.state.open_questions])
        )
        return (
            "Continue the investigation now before finalizing the candidate. You "
            "previously marked follow_up_analysis=true because a material question "
            "remains. Delegate the bounded analysis to the appropriate specialist "
            "using the available data and tools, update hypotheses and evidence, "
            "and return a complete replacement LeadResult. Do not merely restate "
            "that more work would be useful. If the question is not answerable "
            "from the available data, explain that limitation and set "
            "follow_up_analysis=false; otherwise complete the analysis within "
            "this bounded continuation.\n\n"
            f"FOLLOW_UP_CYCLE: {cycle}/{MAX_LEAD_FOLLOW_UP_CYCLES}\n"
            f"ORIGINAL_OBJECTIVE:\n{objective}\n\n"
            "UNRESOLVED_QUESTIONS_JSON:\n"
            f"{questions!r}\n\n"
            "PREVIOUS_LEAD_RESULT_JSON:\n"
            f"{result.model_dump_json(indent=2)}"
        )

    @staticmethod
    def _remediation_prompt(
        objective: str,
        result: LeadResult,
        validation: ValidationResult,
        *,
        business_context: str | None = None,
    ) -> str:
        metric_contexts = [
            item.definition_context.model_dump(exclude_none=True)
            if item.definition_context is not None
            else None
            for item in result.metric_comparisons
        ]
        return (
            "Remediate only the named blockers for the immutable original "
            "objective. Delegate computation only when a named blocker has "
            "repair_class=computation, and give the specialist a defect-specific "
            "task naming that blocker and target. Do not rerun the audit, reopen "
            "the broader investigation, or pursue unrelated decomposition, causal, "
            "segmentation, or validation work. Return a complete replacement "
            "LeadResult; do not merely describe a fix. "
            "Preserve every existing metric population, date basis, observation "
            "window, numerator, denominator, and definition reference unless the "
            "Critic names that exact metric target in a wrong_grain or "
            "wrong_denominator blocker. Every other metric definition is immutable. If "
            "you compute a different valid estimand, retain it as a distinct "
            "comparison with its own definition_context. Reuse exact specialist "
            "MetricComparison objects rather than reconstructing values from prose. "
            "This is a bounded remediation with no further continuation cycle: "
            "complete any materially useful follow-up inside this call and return "
            "follow_up_analysis=false, recording a question the available data "
            "cannot answer as an open question or caveat instead.\n\n"
            f"ORIGINAL_OBJECTIVE:\n{objective}\n\n"
            "BUSINESS_CONTEXT:\n"
            f"{business_context or 'Read the approved business definitions.'}\n\n"
            "EXISTING_METRIC_DEFINITION_CONTEXTS_JSON:\n"
            f"{metric_contexts!r}\n\n"
            "CURRENT_CANDIDATE_JSON:\n"
            f"{result.model_dump_json(indent=2)}\n\n"
            "CRITIC_VALIDATION_JSON:\n"
            f"{validation.model_dump_json(indent=2)}"
        )

    @staticmethod
    def _completion_prompt(
        objective: str,
        result: LeadResult,
        validation: ValidationResult,
    ) -> str:
        """Request one bounded completion pass before initial Critic review."""

        return (
            "Complete the candidate before it is sent to the Critic. Address the "
            "specific completeness blockers below using only defect-specific "
            "bounded specialist tasks. Do not rerun the audit or perform unrelated "
            "analysis. "
            "Carry forward the exact structured MetricComparison objects and their "
            "definition_context; do not reconstruct values from prose. If a chart "
            "is requested, ask the Analyst to create and save one useful chart, "
            "then copy the exact returned artifact reference. Return a complete "
            "replacement LeadResult with follow_up_analysis=false.\n\n"
            f"ORIGINAL_OBJECTIVE:\n{objective}\n\n"
            "CURRENT_CANDIDATE_JSON:\n"
            f"{result.model_dump_json(indent=2)}\n\n"
            "COMPLETENESS_ISSUES_JSON:\n"
            f"{validation.model_dump_json(indent=2)}"
        )

    def _write_report(
        self,
        workspace: Workspace,
        ledger: AnalysisLedger,
        objective: str,
        audit: AuditResult,
        lead_result: LeadResult,
        validation: ValidationResult | None,
        *,
        constrained: bool,
        constraint_reason: str | None,
    ) -> Artifact:
        """Render a concise deterministic Markdown report and register it."""

        report_path = workspace.outputs / "report.md"
        report_path.write_text(
            self._render_report(
                objective,
                audit,
                lead_result,
                validation,
                constrained=constrained,
                constraint_reason=constraint_reason,
                ledger=ledger,
            ),
            encoding="utf-8",
        )
        manager = ArtifactManager(workspace, ledger)
        artifact = manager.register(
            "outputs/report.md",
            artifact_id="final-report",
            kind=ArtifactKind.REPORT,
            media_type="text/markdown",
            description=(
                "Constrained final analysis report"
                if constrained
                else "Validated final analysis report"
            ),
            overwrite=True,
        )
        return ledger.record_final_report(artifact)

    @staticmethod
    def _render_report(
        objective: str,
        audit: AuditResult,
        lead_result: LeadResult,
        validation: ValidationResult | None,
        *,
        constrained: bool,
        constraint_reason: str | None,
        ledger: AnalysisLedger,
    ) -> str:
        from agents.result_binding import (
            resolve_outputs,
            validate_metric_selection,
            validate_statistical_selection,
        )

        lead_result = resolve_outputs(lead_result, ledger)
        validate_statistical_selection(lead_result.statistical_assessments)
        validate_metric_selection(lead_result.metric_comparisons)
        title = "Constrained Analysis Report" if constrained else "Analysis Report"
        lines = [
            f"# {title}",
            "",
            f"**Objective:** {objective}",
            "",
            "## Executive Summary",
            "",
            lead_result.answer,
            "",
            "## Findings",
            "",
        ]
        if lead_result.findings:
            lines.extend(
                f"- **{finding.id}:** {finding.statement} "
                + (
                    f" Value: {finding.value} {finding.value_unit or ''}. "
                    if finding.value is not None
                    else ""
                )
                + f"_(evidence: {', '.join(finding.evidence_refs)})_"
                for finding in lead_result.findings
            )
        else:
            lines.append("- No findings were returned.")
        lines.extend(["", "## Recommendations", ""])
        if lead_result.recommendations:
            lines.extend(
                f"- **{recommendation.id}:** {recommendation.statement} "
                f"_(evidence: {', '.join(recommendation.evidence_refs)})_"
                for recommendation in lead_result.recommendations
            )
        else:
            lines.append("- No recommendations were returned.")
        lines.extend(["", "## Key Metric Comparisons", ""])
        for item in lead_result.metric_comparisons:
            dimensions = ", ".join(f"{d.name}={d.value}" for d in item.dimensions)
            lines.append(
                f"- **{item.metric_key}:** {item.value} {item.unit} "
                f"({item.baseline_period} to {item.comparison_period}; "
                f"{item.comparison_type.value}; {dimensions or 'all segments'}; "
                f"evidence: {', '.join(item.evidence_refs)})"
            )
            if item.definition_context:
                lines.append(
                    f"  - Definition: {item.definition_context.model_dump_json()}"
                )
            lines.append(f"  - Result: {item.result_id or 'legacy unbound'}")
        if not lead_result.metric_comparisons:
            lines.append("- No structured metric comparisons were returned.")
        lines.extend(["", "## Selected Statistical Assessments", ""])
        for item in lead_result.statistical_assessments:
            interval = item.confidence_interval
            dimensions = ", ".join(f"{d.name}={d.value}" for d in item.dimensions)
            lines.extend(
                [
                    f"- **{item.metric_key}** "
                    f"({item.baseline_period} to {item.comparison_period})",
                    f"  - Result: {item.result_id or 'legacy unbound'}",
                    f"  - Dimensions: {dimensions or 'all segments'}",
                    f"  - Method: {item.method}; "
                    f"unit of analysis: {item.unit_of_analysis}",
                    f"  - Estimate: {item.estimate}; "
                    f"confidence level: {item.confidence_level}; "
                    f"confidence interval: [{interval.lower}, {interval.upper}]",
                    f"  - p-value: {item.p_value}; effect size: {item.effect_size}",
                    "  - Practical threshold: "
                    f"{item.practical_significance_threshold}; "
                    f"practically significant: {item.practically_significant}",
                    f"  - Conclusion: {item.conclusion.value}; "
                    f"causal interpretation: {item.causal_interpretation.value}",
                    f"  - Assumptions checked: {'; '.join(item.assumptions_checked)}",
                    f"  - Evidence: {', '.join(item.evidence_refs)}",
                ]
            )
            if item.definition_context:
                lines.append(
                    f"  - Definition: {item.definition_context.model_dump_json()}"
                )
            lines.extend(f"  - Caveat: {caveat}" for caveat in item.caveats)
        if not lead_result.statistical_assessments:
            lines.append("- No selected statistical assessments.")
        lines.extend(["", "## Caveats and Scope", ""])
        lines.extend(f"- {caveat}" for caveat in lead_result.caveats)
        for item in [*lead_result.findings, *lead_result.recommendations]:
            lines.extend(f"- {item.id}: {caveat}" for caveat in item.caveats)
        from schemas.audit import audit_claims

        lines.extend(["", "## Audit Details", ""])
        for claim in audit_claims(audit):
            lines.append(
                f"- {claim.statement} (evidence: "
                f"{', '.join(claim.evidence_refs) or 'unbound legacy observation'})"
            )
        for table in audit.tables:
            if table.date_range:
                lines.append(
                    f"- {table.table_name} date coverage: "
                    f"{table.date_range.start} to {table.date_range.end}"
                )
            lines.extend(
                f"- {table.table_name}.{item.column} missingness: {item.rate}"
                for item in table.missingness
            )
            lines.extend(
                f"- {table.table_name}: {item}" for item in table.relationships
            )
        for issue in audit.issues:
            if issue.recommendation:
                lines.append(f"- {issue.id}: {issue.recommendation}")
        listed_chart_refs = set(lead_result.artifacts)
        listed_charts = [
            artifact
            for artifact in ledger.artifacts
            if artifact.kind is ArtifactKind.CHART
            and (artifact.id in listed_chart_refs or artifact.path in listed_chart_refs)
        ]
        lines.extend(["", "## Supporting Visualizations", ""])
        if listed_charts:
            lines.extend(
                f"- [{artifact.id}]({artifact.path})"
                + (f" — {artifact.description}" if artifact.description else "")
                for artifact in listed_charts
            )
        else:
            lines.append("- No Lead-listed chart artifacts.")
        lines.extend(
            [
                "",
                "## Data Audit",
                "",
                f"- Status: **{audit.status.value}**",
                f"- Tables audited: **{len(audit.tables)}**",
                f"- Data-quality issues: **{len(audit.issues)}**",
                "",
                "## Validation",
                "",
            ]
        )
        if validation is None:
            lines.append("- Critic validation was not completed.")
        else:
            if constrained:
                lines.append(
                    "- Status: **constrained; candidate is not finally validated**"
                )
            else:
                lines.append(f"- Status: **{validation.status.value}**")
            if validation.summary:
                lines.append(f"- Summary: {validation.summary}")
            if validation.blockers:
                lines.append("- Active blockers:")
                lines.extend(
                    f"  - **{blocker.category.value} {blocker.id}:** {blocker.message}"
                    for blocker in validation.blockers
                )
            if validation.limitations:
                lines.append("- Limitations:")
                lines.extend(
                    f"  - **{item.category.value}:** {item.message}"
                    for item in validation.limitations
                )
            if validation.issues:
                lines.extend(
                    f"- **{issue.severity.value.upper()} {issue.id}:** {issue.message}"
                    for issue in validation.issues
                )
        if constrained:
            lines.extend(
                [
                    "",
                    "> Remediation stop: "
                    f"{constraint_reason or 'no further remediation was available.'}",
                    "",
                    "> This report is constrained because validation issues remained "
                    "after the configured remediation limit. Treat recommendations "
                    "as provisional and resolve the listed issues before acting.",
                ]
            )
        breakdown = ledger.state.cost_breakdown
        if breakdown is None:
            cost_lines = ["- Pricing breakdown: **not configured**"]
        else:
            cost_lines = [
                f"- Pricing model: **{breakdown.pricing_model}**",
                f"- Uncached input tokens: **{breakdown.uncached_input_tokens}**",
                f"- Cached input tokens: **{breakdown.cached_tokens}**",
                "- Uncached input cost (USD): "
                f"**{breakdown.uncached_input_cost_usd:.6f}**",
                f"- Cached input cost (USD): **{breakdown.cached_input_cost_usd:.6f}**",
                f"- Output cost (USD): **{breakdown.output_cost_usd:.6f}**",
            ]
        lines.extend(["", "## Reproducibility", ""])
        lines.extend(
            [
                f"- SQL executions: **{ledger.budget.sql_executions}**",
                f"- Python executions: **{ledger.budget.python_executions}**",
                f"- Specialist invocations: **{ledger.budget.specialist_invocations}**",
                f"- Critic loops: **{ledger.budget.critic_loops}**",
                f"- Model requests: **{ledger.usage.requests}**",
                f"- Total tokens: **{ledger.usage.total_tokens}**",
                *cost_lines,
                f"- Elapsed seconds: **{ledger.state.elapsed_seconds or 0:.3f}**",
                (
                    "- Estimated model cost (USD): "
                    f"**{ledger.state.estimated_cost_usd:.6f}**"
                    if ledger.state.estimated_cost_usd is not None
                    else "- Estimated model cost (USD): **not configured**"
                ),
                "- Evidence and artifacts are recorded in "
                "`state/analysis_ledger.json`.",
            ]
        )
        return "\n".join(lines) + "\n"


__all__ = ["AnalysisRunResult", "AnalysisRunner"]
