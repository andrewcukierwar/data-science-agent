"""Application lifecycle for the fair single-agent baseline."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

from agents.critic import persist_validation_result
from agents.generalist import persist_generalist_result, run_generalist
from agents.runtime import AgentRole
from orchestration.block_reasons import RunConstraint, constraint_from_exception
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from schemas.audit import AuditResult, AuditStatus
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.run_state import (
    AgentEventStatus,
    AnalysisRunState,
    Artifact,
    AttemptStatus,
    RunBlockReason,
    RunStatus,
)
from schemas.validation import ValidationResult, ValidationStatus
from tools.workspace import Workspace


class BlockedAuditError(RuntimeError):
    """Raised when the generalist's mandatory data audit is blocked."""


@dataclass(slots=True)
class GeneralistRunResult:
    """Typed persisted products from one generalist lifecycle."""

    status: RunStatus
    workspace: Workspace | None
    ledger: AnalysisLedger | None
    generalist_result: GeneralistResult | None = None
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
        """Return the current typed ledger state when available."""

        return self.ledger.state if self.ledger is not None else None


class GeneralistRunner(AnalysisRunner):
    """Run exactly one generalist agent through the shared lifecycle.

    Inheriting the application runner is intentional: it reuses workspace
    opening, run configuration, pricing, bounded report rendering, and runtime
    metadata.  The overridden lifecycle below creates only the GENERALIST
    context and never calls the inherited auditor, Lead, or Critic runners.
    """

    architecture = "single-agent"

    def _agent_context(
        self,
        workspace: Workspace,
        ledger: AnalysisLedger,
        role: AgentRole,
    ):
        """Refuse construction of any non-generalist agent in this runner."""

        if role is not AgentRole.GENERALIST:
            raise ValueError("GeneralistRunner can construct only a generalist")
        return super()._agent_context(workspace, ledger, role)

    async def run(
        self,
        run_id: str,
        objective: str,
        *,
        inputs_source: str | Path | None = None,
        docs_source: str | Path | None = None,
        business_context: str | None = None,
        workspace: Workspace | None = None,
    ) -> GeneralistRunResult:
        """Run one bounded generalist request and persist its report."""

        started = time.perf_counter()
        ledger: AnalysisLedger | None = None
        run_workspace = workspace
        generalist_result: GeneralistResult | None = None
        audit: AuditResult | None = None
        lead_result: LeadResult | None = None
        validation_result: ValidationResult | None = None
        report: Artifact | None = None
        constrained = False
        constraint: RunConstraint | None = None
        active_agent: tuple[str, str] | None = None
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
            # The single agent runs under the same append-only attempt protocol
            # as the multi-agent lifecycle: one attempt is open before any agent
            # executes, so every agent event, tool event, usage delta, and cost
            # is attributed to it.
            ledger.begin_attempt()
            self._configure_ledger(ledger, run_id, business_context)
            ledger.set_status(RunStatus.RUNNING)

            context, agent = self._agent_context(
                run_workspace,
                ledger,
                AgentRole.GENERALIST,
            )
            active_agent = (agent.name, objective)
            generalist_result = await run_generalist(
                context,
                objective,
                business_context=business_context,
                agent=agent,
            )
            context.assert_base_role(AgentRole.GENERALIST)
            if not isinstance(generalist_result, GeneralistResult):
                generalist_result = GeneralistResult.model_validate(generalist_result)
            # Direct ``run_generalist`` callers get the same persistence behavior
            # as the existing agent runners.  The application boundary also
            # accepts injected deterministic runners, so fill any missing
            # persistence without appending duplicate validation records.
            if (
                ledger.audit != generalist_result.audit
                or ledger.metric_comparisons
                != generalist_result.candidate.metric_comparisons
                or ledger.statistical_assessments
                != generalist_result.candidate.statistical_assessments
                or any(
                    finding not in ledger.findings
                    for finding in generalist_result.candidate.findings
                )
            ):
                generalist_result = persist_generalist_result(
                    generalist_result,
                    context,
                )
            elif generalist_result.validation not in ledger.validation_results:
                persist_validation_result(
                    generalist_result.validation,
                    ledger,
                    allow_issue_updates=True,
                )
            audit = generalist_result.audit
            lead_result = generalist_result.candidate
            validation_result = generalist_result.validation
            self._record_agent_success(ledger, active_agent, GeneralistResult)
            active_agent_recorded = True

            if audit.status is AuditStatus.BLOCKED:
                raise BlockedAuditError("generalist data audit was blocked")
            # Name the originating condition rather than defaulting every
            # constrained single-agent run to a budget failure.
            if validation_result.status is ValidationStatus.REVISE:
                constraint = RunConstraint(
                    reason=RunBlockReason.VALIDATION_REVISION,
                    detail="The generalist self-critique returned REVISE.",
                )
            elif lead_result.follow_up_analysis:
                constraint = RunConstraint(
                    reason=RunBlockReason.UNRESOLVED_FOLLOW_UP,
                    detail=(
                        "The generalist left objective-critical follow-up unresolved."
                    ),
                )
            constrained = constraint is not None

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
            if constraint is not None:
                ledger.mark_blocked(constraint.reason, constraint.detail)
                attempt_terminal_status = AttemptStatus.BLOCKED
            else:
                ledger.set_status(RunStatus.COMPLETED)
                attempt_terminal_status = AttemptStatus.COMPLETED
            return GeneralistRunResult(
                status=ledger.state.status,
                workspace=run_workspace,
                ledger=ledger,
                generalist_result=generalist_result,
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
            constraint = (
                RunConstraint(
                    reason=RunBlockReason.DATA_QUALITY,
                    detail=(
                        "The mandatory data audit was blocked by data-quality "
                        f"conditions: {error}"
                    ),
                )
                if isinstance(error, BlockedAuditError)
                else constraint_from_exception(error, context="The analysis")
            )
            if ledger is not None:
                if active_agent is not None and not active_agent_recorded:
                    agent_name, agent_objective = active_agent
                    ledger.record_agent_event(
                        agent_name=agent_name,
                        agent_role=AgentRole.GENERALIST.value,
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
            return GeneralistRunResult(
                status=RunStatus.FAILED,
                workspace=run_workspace,
                ledger=ledger,
                generalist_result=generalist_result,
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
                    # Preserve the primary lifecycle error if final metadata
                    # persistence itself is unavailable.
                    pass
            if ledger is not None and not attempt_finalized:
                # Usage, cost availability, and elapsed time are written above
                # while the attempt is still running, so the terminal record
                # carries them.
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

    def _record_agent_success(
        self,
        ledger: AnalysisLedger,
        invocation: tuple[str, str] | None,
        output_type: type[object],
    ) -> None:
        """Record the single generalist invocation without specialist traces."""

        if invocation is None:
            return
        agent_name, objective = invocation
        ledger.record_agent_event(
            agent_name=agent_name,
            agent_role=AgentRole.GENERALIST.value,
            status=AgentEventStatus.SUCCEEDED,
            model=self.model,
            objective=objective,
            output_type=output_type.__name__,
        )

    def run_sync(self, *args: object, **kwargs: object) -> GeneralistRunResult:
        """Synchronous convenience wrapper for CLI/manual callers."""

        import asyncio

        return asyncio.run(self.run(*args, **kwargs))


__all__ = ["GeneralistRunResult", "GeneralistRunner"]
