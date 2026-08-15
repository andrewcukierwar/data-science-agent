"""Evaluator for the canonical Phase 1 no-steering acceptance run.

This module is deliberately downstream of the agents. It may use the scenario
definition as evaluator-only ground truth, but no value from it is supplied to
agent prompts.
"""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass

from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunResult
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from schemas.run_state import AgentEventStatus, ArtifactKind, RunStatus
from schemas.validation import ValidationStatus
from tools.artifacts import ArtifactManager


class CanonicalAcceptanceError(AssertionError):
    """Raised when a canonical run does not satisfy the MVP contract."""


@dataclass(frozen=True, slots=True)
class CanonicalAcceptanceSummary:
    """Small machine-readable summary printed by the manual acceptance script."""

    run_id: str
    workspace: str
    status: str
    checks: tuple[str, ...]
    sql_events: int
    python_events: int
    chart_artifacts: int
    agent_events: int
    specialist_results: int
    model_requests: int
    total_tokens: int
    elapsed_seconds: float
    estimated_cost_usd: float | None

    def as_dict(self) -> dict[str, object]:
        """Return JSON-serializable acceptance metadata."""

        return {
            "run_id": self.run_id,
            "workspace": self.workspace,
            "status": self.status,
            "checks": list(self.checks),
            "sql_events": self.sql_events,
            "python_events": self.python_events,
            "chart_artifacts": self.chart_artifacts,
            "agent_events": self.agent_events,
            "specialist_results": self.specialist_results,
            "model_requests": self.model_requests,
            "total_tokens": self.total_tokens,
            "elapsed_seconds": self.elapsed_seconds,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


def evaluate_canonical_run(
    result: AnalysisRunResult,
) -> CanonicalAcceptanceSummary:
    """Verify the persisted workspace and the evaluator-only business outcome."""

    failures: list[str] = []
    if result.workspace is None:
        raise CanonicalAcceptanceError("run did not create a workspace")
    workspace = result.workspace
    try:
        ledger = AnalysisLedger(workspace)
    except Exception as exc:
        raise CanonicalAcceptanceError(
            f"analysis ledger could not be reloaded: {exc}"
        ) from exc
    state = ledger.state

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    require(state.status is RunStatus.COMPLETED, "run did not complete successfully")
    require(state.error is None, "completed run retains an error state")
    for read_only_directory in (workspace.inputs, workspace.docs):
        for path in read_only_directory.rglob("*"):
            if path.is_file():
                require(
                    not (
                        path.stat().st_mode
                        & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
                    ),
                    "read-only source became writable: "
                    f"{path.relative_to(workspace.root)}",
                )
    require(state.audit is not None, "completed Data Audit is missing")
    if state.audit is not None:
        require(
            state.audit.status.value == "complete",
            f"Data Audit status is {state.audit.status.value}, not complete",
        )
    require(bool(state.investigation_plan), "investigation plan is missing")
    require(bool(state.hypotheses), "current hypothesis state is missing")
    require(bool(state.hypothesis_history), "hypothesis history is missing")
    require(
        state.run_budget.sql_executions <= state.run_budget.max_sql_executions,
        "SQL budget was exceeded",
    )
    require(
        state.run_budget.python_executions <= state.run_budget.max_python_executions,
        "Python budget was exceeded",
    )
    require(
        state.run_budget.specialist_invocations
        <= state.run_budget.max_specialist_invocations,
        "specialist budget was exceeded",
    )
    require(
        state.run_budget.critic_loops <= state.run_budget.max_critic_loops,
        "critic-loop budget was exceeded",
    )
    require(
        state.run_budget.charts_created <= state.run_budget.max_charts,
        "chart budget was exceeded",
    )

    successful_sql = [
        event
        for event in state.tool_events
        if event.tool_name == "run_sql" and event.status.value == "succeeded"
    ]
    successful_python = [
        event
        for event in state.tool_events
        if event.tool_name == "run_python" and event.status.value == "succeeded"
    ]
    require(bool(successful_sql), "successful SQL evidence is missing")
    require(bool(successful_python), "successful Python evidence is missing")
    for event in [*successful_sql, *successful_python]:
        for reference in event.artifact_refs:
            require(
                (workspace.root / reference).is_file(),
                f"tool evidence path is missing: {reference}",
            )

    chart_artifacts = [
        artifact for artifact in state.artifacts if artifact.kind is ArtifactKind.CHART
    ]
    require(bool(chart_artifacts), "no chart artifact was registered")
    require(bool(state.findings), "no findings were persisted")
    require(
        result.lead_result is not None and bool(result.lead_result.findings),
        "Lead did not return final findings",
    )
    require(
        result.lead_result is not None and bool(result.lead_result.recommendations),
        "Lead did not return final recommendations",
    )

    evidence_refs = {event.id for event in state.tool_events}
    evidence_refs.update(
        reference for event in state.tool_events for reference in event.artifact_refs
    )
    evidence_refs.update(artifact.id for artifact in state.artifacts)
    evidence_refs.update(artifact.path for artifact in state.artifacts)
    for finding in state.findings:
        require(
            bool(finding.evidence_refs),
            f"finding has no evidence_refs: {finding.id}",
        )
        require(
            all(reference in evidence_refs for reference in finding.evidence_refs),
            f"finding cites unexecuted evidence: {finding.id}",
        )
    for hypothesis in state.hypotheses:
        if hypothesis.status.value != "open":
            require(
                any(
                    reference in evidence_refs for reference in hypothesis.evidence_refs
                ),
                f"resolved hypothesis cites no executed evidence: {hypothesis.id}",
            )
    if result.lead_result is not None:
        for recommendation in result.lead_result.recommendations:
            require(
                any(
                    reference in evidence_refs
                    for reference in recommendation.evidence_refs
                ),
                f"recommendation cites no executed evidence: {recommendation.id}",
            )

    specialist_roles = {record.agent_role for record in state.specialist_results}
    require(
        "statistician" in specialist_roles,
        "Statistician typed output is missing for the canonical LTV question",
    )

    agent_roles = {
        event.agent_role
        for event in state.agent_events
        if event.status is AgentEventStatus.SUCCEEDED
    }
    for role in ("data_auditor", "lead", "analyst", "statistician", "critic"):
        require(role in agent_roles, f"successful {role} trace is missing")
    require(bool(state.agent_events), "agent execution trace is missing")
    require(bool(state.tool_events), "tool execution trace is missing")

    require(bool(state.validation_results), "Critic validation is missing")
    if state.validation_results:
        require(
            state.validation_results[-1].status is ValidationStatus.PASS,
            "final Critic validation did not pass",
        )
    require(state.final_report is not None, "final report record is missing")
    if state.final_report is not None:
        require(
            state.final_report.kind is ArtifactKind.REPORT,
            "final report is not registered as a report artifact",
        )
        require(
            (workspace.root / state.final_report.path).is_file(),
            "final report file is missing",
        )

    require(state.usage.requests > 0, "model request usage is missing")
    require(state.usage.total_tokens > 0, "model token usage is missing")
    require(state.elapsed_seconds is not None, "elapsed-time metadata is missing")
    require(state.cost_estimation_note is not None, "cost metadata is missing")
    for artifact in state.artifacts:
        try:
            require(
                ArtifactManager(workspace, ledger).verify_artifact(artifact.id),
                f"artifact provenance mismatch: {artifact.id}",
            )
        except (OSError, ValueError, KeyError) as exc:
            failures.append(f"artifact provenance could not be verified: {exc}")

    report_text = ""
    if state.final_report is not None:
        report_text = (workspace.root / state.final_report.path).read_text(
            encoding="utf-8"
        )
    analysis_text = " ".join(
        [
            report_text,
            result.lead_result.answer if result.lead_result is not None else "",
            *[finding.statement for finding in state.findings],
        ]
    ).lower()
    expected_primary = CANONICAL_PROFITABILITY_SCENARIO.expected_primary_driver.lower()
    expected_primary_terms = tuple(
        term for term in ("meta", "conversion") if term in expected_primary
    )
    require(
        all(term in analysis_text for term in expected_primary_terms),
        "final analysis does not identify Meta conversion deterioration",
    )
    require("cac" in analysis_text, "final analysis does not discuss CAC")
    require(
        "ltv" in analysis_text
        and bool(
            re.search(r"ltv.{0,80}(stable|unchanged|approximately)", analysis_text)
        ),
        "final analysis does not characterize acquired-customer LTV as stable",
    )
    # The evaluator may inspect the known scenario conclusion; it is never
    # included in the agent prompts. Keep this check tolerant of paraphrase.
    require(
        any(term in analysis_text for term in ("efficiency", "conversion"))
        and bool(expected_primary_terms),
        "canonical primary-driver evaluation could not be applied",
    )

    if failures:
        raise CanonicalAcceptanceError("; ".join(failures))

    elapsed = state.elapsed_seconds or 0.0
    return CanonicalAcceptanceSummary(
        run_id=state.run_id,
        workspace=str(workspace.root),
        status=state.status.value,
        checks=(
            "audit",
            "plan_and_hypothesis_history",
            "sql_and_python_evidence",
            "charts",
            "provenance",
            "specialist_outputs",
            "critic",
            "final_report",
            "trace_and_usage",
            "canonical_ground_truth",
        ),
        sql_events=len(successful_sql),
        python_events=len(successful_python),
        chart_artifacts=len(chart_artifacts),
        agent_events=len(state.agent_events),
        specialist_results=len(state.specialist_results),
        model_requests=state.usage.requests,
        total_tokens=state.usage.total_tokens,
        elapsed_seconds=elapsed,
        estimated_cost_usd=state.estimated_cost_usd,
    )
