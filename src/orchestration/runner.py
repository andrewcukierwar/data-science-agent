"""Application-level Phase 1 analysis lifecycle orchestration."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agents import Agent, MaxTurnsExceeded
from agents.auditor import build_data_auditor_agent, run_data_auditor
from agents.critic import (
    build_critic_agent,
    persist_validation_result,
    run_critic,
)
from agents.lead import build_lead_agent, persist_lead_result, run_lead
from agents.runtime import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    normalize_agent_turn_limits,
)
from orchestration.budgets import BudgetExhaustedError, BudgetResource
from orchestration.ledger import AnalysisLedger
from schemas.audit import AuditResult, AuditStatus
from schemas.lead import LeadResult
from schemas.run_state import (
    AgentEventStatus,
    AnalysisRunState,
    Artifact,
    ArtifactKind,
    RunBudget,
    RunStatus,
)
from schemas.validation import CriticCandidate, ValidationResult, ValidationStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import Workspace, WorkspaceManager

AuditorRunner = Callable[..., Awaitable[AuditResult]]
LeadRunner = Callable[..., Awaitable[LeadResult]]
CriticRunner = Callable[..., Awaitable[ValidationResult]]


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
        input_cost_per_1k_tokens: float | None = None,
        output_cost_per_1k_tokens: float | None = None,
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
        if (input_cost_per_1k_tokens is None) != (output_cost_per_1k_tokens is None):
            raise ValueError("input and output cost rates must be provided together")
        self.input_cost_per_1k_tokens = input_cost_per_1k_tokens
        self.output_cost_per_1k_tokens = output_cost_per_1k_tokens
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
        constraint_reason: str | None = None
        active_agent: tuple[str, AgentRole, str] | None = None
        active_agent_recorded = False

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
            self._configure_ledger(ledger, run_id, business_context)
            ledger.set_status(RunStatus.RUNNING)

            audit_context, audit_agent = self._agent_context(
                run_workspace,
                ledger,
                AgentRole.DATA_AUDITOR,
            )
            active_agent = (audit_agent.name, AgentRole.DATA_AUDITOR, "preflight audit")
            audit_context.check_budget(BudgetResource.SPECIALIST_INVOCATIONS)
            audit_specialist_usage = ledger.budget.specialist_invocations
            audit = await self.auditor_runner(
                audit_context,
                self._audit_prompt(business_context),
                agent=audit_agent,
            )
            audit_context.assert_base_role(AgentRole.DATA_AUDITOR)
            self._ensure_budget_increment(
                audit_context,
                BudgetResource.SPECIALIST_INVOCATIONS,
                audit_specialist_usage,
            )
            if not isinstance(audit, AuditResult):
                audit = AuditResult.model_validate(audit)
            self._record_agent_success(ledger, active_agent, AuditResult)
            active_agent_recorded = True
            # Keep this explicit even though the existing specialist runner also
            # persists it; it makes the application lifecycle invariant clear.
            ledger.record_audit(audit)
            if audit.status is AuditStatus.BLOCKED:
                raise RuntimeError("mandatory data audit was blocked")

            lead_context, lead_agent = self._agent_context(
                run_workspace,
                ledger,
                AgentRole.LEAD,
            )
            active_agent = (lead_agent.name, AgentRole.LEAD, objective)
            active_agent_recorded = False
            lead_result = await self.lead_runner(
                lead_context,
                objective,
                business_context=business_context,
                audit=audit,
                agent=lead_agent,
            )
            lead_context.assert_base_role(AgentRole.LEAD)
            if not isinstance(lead_result, LeadResult):
                lead_result = LeadResult.model_validate(lead_result)
            self._record_agent_success(ledger, active_agent, LeadResult)
            active_agent_recorded = True
            lead_result = persist_lead_result(lead_result, lead_context)

            critic_context, critic_agent = self._agent_context(
                run_workspace,
                ledger,
                AgentRole.CRITIC,
            )
            critic_attempts = 0
            available_critic_loops = (
                ledger.budget.max_critic_loops - ledger.budget.critic_loops
            )
            critic_context.check_budget("critic_loops")
            while True:
                candidate = self._candidate(objective, lead_result)
                active_agent = (critic_agent.name, AgentRole.CRITIC, objective)
                active_agent_recorded = False
                critic_context.check_budget(BudgetResource.SPECIALIST_INVOCATIONS)
                critic_context.check_budget(BudgetResource.CRITIC_LOOPS)
                critic_specialist_usage = ledger.budget.specialist_invocations
                critic_loop_usage = ledger.budget.critic_loops
                validation_result = await self.critic_runner(
                    critic_context,
                    candidate,
                    agent=critic_agent,
                )
                critic_context.assert_base_role(AgentRole.CRITIC)
                self._ensure_budget_increment(
                    critic_context,
                    BudgetResource.SPECIALIST_INVOCATIONS,
                    critic_specialist_usage,
                )
                self._ensure_budget_increment(
                    critic_context,
                    BudgetResource.CRITIC_LOOPS,
                    critic_loop_usage,
                )
                if not isinstance(validation_result, ValidationResult):
                    validation_result = ValidationResult.model_validate(
                        validation_result
                    )
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

                if critic_attempts >= available_critic_loops:
                    constrained = True
                    constraint_reason = (
                        "Critic returned REVISE and the configured maximum of "
                        f"{available_critic_loops} critic loop(s) was reached."
                    )
                    break

                remediation_prompt = self._remediation_prompt(
                    objective,
                    lead_result,
                    validation_result,
                )
                active_agent = (lead_agent.name, AgentRole.LEAD, remediation_prompt)
                active_agent_recorded = False
                try:
                    remediated_lead_result = await self.lead_runner(
                        lead_context,
                        remediation_prompt,
                        business_context=business_context,
                        audit=audit,
                        agent=lead_agent,
                    )
                    lead_context.assert_base_role(AgentRole.LEAD)
                    if not isinstance(remediated_lead_result, LeadResult):
                        remediated_lead_result = LeadResult.model_validate(
                            remediated_lead_result
                        )
                    self._record_agent_success(ledger, active_agent, LeadResult)
                    active_agent_recorded = True
                    lead_result = persist_lead_result(
                        remediated_lead_result,
                        lead_context,
                    )
                except Exception as error:
                    # A usable candidate and Critic result already exist. Keep
                    # them and produce a constrained report when bounded
                    # remediation cannot complete, including its stop reason.
                    constrained = True
                    constraint_reason = self._remediation_failure_reason(error)
                    if active_agent is not None and not active_agent_recorded:
                        agent_name, role, agent_objective = active_agent
                        ledger.record_agent_event(
                            agent_name=agent_name,
                            agent_role=role.value,
                            status=AgentEventStatus.FAILED,
                            model=self.model,
                            objective=agent_objective,
                            error=constraint_reason,
                        )
                        active_agent_recorded = True
                    break

            report = self._write_report(
                run_workspace,
                ledger,
                objective,
                audit,
                lead_result,
                validation_result,
                constrained=constrained,
                constraint_reason=constraint_reason,
            )
            if constrained:
                ledger.set_status(RunStatus.BLOCKED)
            else:
                ledger.set_status(RunStatus.COMPLETED)
            return AnalysisRunResult(
                status=ledger.state.status,
                workspace=run_workspace,
                ledger=ledger,
                audit=audit,
                lead_result=lead_result,
                validation_result=validation_result,
                report=report,
                constrained=constrained,
            )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
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
                ledger.mark_failed(message)
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
            )
        finally:
            if ledger is not None:
                try:
                    ledger.record_cost_estimate(
                        input_cost_per_1k_tokens=self.input_cost_per_1k_tokens,
                        output_cost_per_1k_tokens=self.output_cost_per_1k_tokens,
                    )
                    ledger.record_elapsed(time.perf_counter() - started)
                except Exception:
                    # Do not mask a primary lifecycle or persistence error with a
                    # final metadata-write failure.
                    pass

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
    def _remediation_failure_reason(error: Exception) -> str:
        """Describe why an existing candidate could not be remediated."""

        if isinstance(error, BudgetExhaustedError):
            return f"Remediation stopped by budget exhaustion: {error}"
        if isinstance(error, MaxTurnsExceeded):
            return f"Remediation stopped by the Lead turn limit: {error}"
        return f"Remediation stopped by a bounded execution failure: {error}"

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
            agent_role=role,
            model=self.model,
            model_provider=self.model_provider,
            agent_turn_limits=self.agent_turn_limits,
        )
        context = AgentRunContext(
            workspace=workspace,
            ledger=ledger,
            sql_service=DuckDBExecutionService(workspace, ledger),
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
    def _candidate(objective: str, result: LeadResult) -> CriticCandidate:
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
        return CriticCandidate(
            objective=objective,
            findings=result.findings,
            recommendations=recommendations,
            artifacts=result.artifacts,
            evidence_refs=list(dict.fromkeys(evidence_refs)),
        )

    @staticmethod
    def _remediation_prompt(
        objective: str,
        result: LeadResult,
        validation: ValidationResult,
    ) -> str:
        return (
            "Remediate the candidate analysis for the original objective. Review "
            "each Critic issue, delegate bounded follow-up analysis when it is "
            "materially useful, update hypotheses and evidence, and return a "
            "complete replacement LeadResult. Do not merely describe a fix.\n\n"
            f"ORIGINAL_OBJECTIVE:\n{objective}\n\n"
            "CURRENT_CANDIDATE_JSON:\n"
            f"{result.model_dump_json(indent=2)}\n\n"
            "CRITIC_VALIDATION_JSON:\n"
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
                f"_(evidence: {', '.join(finding.evidence_refs)})_"
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
            lines.append(f"- Status: **{validation.status.value}**")
            if validation.summary:
                lines.append(f"- Summary: {validation.summary}")
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
        lines.extend(["", "## Reproducibility", ""])
        lines.extend(
            [
                f"- SQL executions: **{ledger.budget.sql_executions}**",
                f"- Python executions: **{ledger.budget.python_executions}**",
                f"- Specialist invocations: **{ledger.budget.specialist_invocations}**",
                f"- Critic loops: **{ledger.budget.critic_loops}**",
                f"- Model requests: **{ledger.usage.requests}**",
                f"- Total tokens: **{ledger.usage.total_tokens}**",
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
