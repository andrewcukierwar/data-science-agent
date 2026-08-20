"""Deterministic checks for the canonical acceptance evaluator boundary."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from evaluation.canonical import (
    CanonicalAcceptanceError,
    _canonical_numeric_ground_truth_failures,
    _has_asserted_primary_driver,
    _has_primary_channel_contribution,
    evaluate_canonical_run,
    evaluate_canonical_workspace,
)
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from schemas.audit import AuditObservation, AuditResult, TableAudit
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.hypotheses import Hypothesis, HypothesisStatus
from schemas.lead import LeadResult
from schemas.metrics import MetricComparison
from schemas.run_state import (
    AgentEventStatus,
    ArtifactKind,
    RunStatus,
    ToolEvent,
    ToolEventStatus,
)
from schemas.validation import ValidationResult, ValidationStatus
from tools.artifacts import ArtifactManager
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def _canonical_comparisons(**overrides: float) -> list[MetricComparison]:
    comparisons: list[MetricComparison] = []
    for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth:
        comparisons.append(
            MetricComparison(
                metric_key=metric.metric_key,
                dimensions=metric.dimensions,
                baseline_period=metric.baseline_period,
                comparison_period=metric.comparison_period,
                comparison_type=metric.comparison_type,
                value=overrides.get(metric.id, metric.expected_relative_change),
                unit=metric.value_unit,
                evidence_refs=["tool-evidence"],
            )
        )
    return comparisons


def test_canonical_numeric_ground_truth_accepts_structured_values() -> None:
    result = LeadResult(
        objective="Explain the change.",
        answer="The answer is supported.",
        findings=[
            Finding(
                id="model-local-finding-17",
                statement="A model-generated local finding ID is acceptable.",
                evidence_refs=["tool-evidence"],
                confidence=ConfidenceLevel.HIGH,
            )
        ],
        metric_comparisons=_canonical_comparisons(),
    )

    assert _canonical_numeric_ground_truth_failures(result) == []


def test_canonical_numeric_ground_truth_rejects_wrong_cac() -> None:
    failures = _canonical_numeric_ground_truth_failures(
        _canonical_comparisons(**{"meta-q2-cac": 0.90})
    )

    assert any("meta-q2-cac" in failure for failure in failures)
    assert any("outside" in failure for failure in failures)


def test_canonical_numeric_ground_truth_requires_the_declared_unit() -> None:
    comparisons = _canonical_comparisons()
    comparisons[0] = comparisons[0].model_copy(update={"unit": "percent"})

    failures = _canonical_numeric_ground_truth_failures(comparisons)

    assert any(
        "missing numeric ground-truth finding" in failure for failure in failures
    )


def _replace_ltv_comparisons(
    replacements: list[MetricComparison],
) -> list[MetricComparison]:
    return [
        item for item in _canonical_comparisons() if item.metric_key != "ltv"
    ] + replacements


def _ltv_comparison(
    value: float,
    dimensions: dict[str, str],
    evidence_ref: str,
) -> MetricComparison:
    return MetricComparison(
        metric_key="ltv",
        dimensions=dimensions,
        baseline_period="Q1 2025",
        comparison_period="Q2 2025",
        comparison_type="relative_change",
        value=value,
        unit="relative_change_fraction",
        evidence_refs=[evidence_ref],
    )


def test_exact_metric_dimensions_win_over_consistent_specific_match() -> None:
    comparisons = _replace_ltv_comparisons(
        [
            _ltv_comparison(0.0, {"channel": "Meta"}, "exact"),
            _ltv_comparison(
                0.0001,
                {"channel": "Meta", "cohort": "acquired customers"},
                "specific",
            ),
        ]
    )

    assert _canonical_numeric_ground_truth_failures(comparisons) == []


def test_consistent_compatible_metric_supersets_are_reconciled() -> None:
    comparisons = _replace_ltv_comparisons(
        [
            _ltv_comparison(
                0.0,
                {"channel": "Meta", "cohort": "acquired customers"},
                "cohort",
            ),
            _ltv_comparison(
                0.0001,
                {"channel": "Meta", "population": "new customers"},
                "population",
            ),
        ]
    )

    assert _canonical_numeric_ground_truth_failures(comparisons) == []


def test_conflicting_compatible_metric_matches_fail() -> None:
    comparisons = _replace_ltv_comparisons(
        [
            _ltv_comparison(
                0.0,
                {"channel": "Meta", "cohort": "acquired customers"},
                "cohort",
            ),
            _ltv_comparison(
                0.2,
                {"channel": "Meta", "population": "new customers"},
                "population",
            ),
        ]
    )

    failures = _canonical_numeric_ground_truth_failures(comparisons)

    assert any("materially conflicting" in failure for failure in failures)
    assert any("meta-q2-90-day-ltv" in failure for failure in failures)


def test_incorrect_exact_metric_match_is_not_replaced_by_superset() -> None:
    comparisons = _replace_ltv_comparisons(
        [
            _ltv_comparison(0.2, {"channel": "Meta"}, "incorrect-exact"),
            _ltv_comparison(
                0.0,
                {"channel": "Meta", "cohort": "acquired customers"},
                "correct-specific",
            ),
        ]
    )

    failures = _canonical_numeric_ground_truth_failures(comparisons)

    assert any("outside" in failure for failure in failures)
    assert any("meta-q2-90-day-ltv" in failure for failure in failures)


def test_missing_metric_still_fails_numeric_acceptance() -> None:
    failures = _canonical_numeric_ground_truth_failures(_replace_ltv_comparisons([]))

    assert any("missing" in failure for failure in failures)
    assert any("meta-q2-90-day-ltv" in failure for failure in failures)


def test_generic_metric_aliases_and_paraphrased_periods_match_identity() -> None:
    aliases = {
        "conversion_rate": "session conversion",
        "acquired_customers": "new customers",
        "marketing_spend": "spend",
        "cac": "customer acquisition cost",
        "ltv": "90-day LTV",
    }
    comparisons = [
        comparison.model_copy(
            update={
                "metric_key": aliases[comparison.metric_key],
                "dimensions": {"acquisition_channel": "META"},
                "baseline_period": "2025 Q1",
                "comparison_period": "Q2 2025",
            }
        )
        for comparison in _canonical_comparisons()
    ]

    assert _canonical_numeric_ground_truth_failures(comparisons) == []


def test_canonical_acceptance_requires_asserted_root_cause_not_speculation() -> None:
    speculative = (
        "Meta declined and conversion may be worth investigating as a possible "
        "explanation for the acquisition deterioration."
    )
    asserted = (
        "Meta was the largest profitability driver. Meta conversion fell and "
        "drove the acquisition deterioration."
    )

    assert not _has_asserted_primary_driver(speculative)
    assert _has_asserted_primary_driver(asserted)
    assert _has_primary_channel_contribution(asserted)


def test_evaluator_metadata_does_not_enter_lead_or_critic_prompt_text() -> None:
    from agents.critic import _candidate_prompt
    from agents.lead import LEAD_INSTRUCTIONS, _lead_input

    expected_ids = " ".join(
        metric.id for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth
    )
    assert expected_ids.split()[0] not in LEAD_INSTRUCTIONS
    assert expected_ids.split()[-1] not in _candidate_prompt(
        LeadResult(
            objective="Explain the change.",
            answer="Use generic metric comparisons.",
            metric_comparisons=[],
        )
    )
    lead_prompt = _lead_input("Explain the change.")
    assert all(
        metric.id not in lead_prompt
        for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth
    )
    assert all(
        str(metric.expected_relative_change) not in lead_prompt
        for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth
        if metric.expected_relative_change != 0
    )


def _audit_with_executed_provenance(context) -> AuditResult:  # noqa: ANN001
    """Return a production-shaped audit that cites its own executed check.

    A real Data Auditor establishes its table profile with an approved tool
    call, so the deterministic fixture does the same rather than asserting a
    completed status with nothing behind it.
    """

    inspection = context.sql_service.inspect_relations()
    reference = inspection.tool_event_id
    assert reference is not None
    return AuditResult(
        status="complete",
        tables=[
            TableAudit(
                table_name=relation.relation_name,
                row_count=relation.row_count or 0,
                evidence_refs=[reference],
            )
            for relation in inspection.relations
        ],
        limitations=[
            AuditObservation(
                statement="Only the registered input relations were inspected.",
                evidence_refs=[reference],
            )
        ],
    )


def test_canonical_acceptance_rejects_incomplete_persisted_runs(
    tmp_path: Path,
) -> None:
    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return _audit_with_executed_provenance(context)

    async def fake_lead(context, objective, *, business_context, audit, agent):  # noqa: ANN001
        return LeadResult(objective=objective, answer="Not enough evidence.")

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        context.consume_budget("critic_loops")
        return ValidationResult(status=ValidationStatus.PASS)

    result = asyncio.run(
        AnalysisRunner(
            workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
            auditor_runner=fake_auditor,
            lead_runner=fake_lead,
            critic_runner=fake_critic,
        ).run("canonical-incomplete", "Why did profitability decline?")
    )

    try:
        evaluate_canonical_run(result)
    except CanonicalAcceptanceError as error:
        assert "investigation plan" in str(error)
    else:
        raise AssertionError("incomplete run unexpectedly passed acceptance")


def test_complete_offline_fixture_passes_phase1_acceptance_without_api(
    tmp_path: Path,
) -> None:
    """Exercise the complete acceptance boundary with deterministic fake agents."""

    inputs_source = tmp_path / "inputs-source"
    inputs_source.mkdir()
    pd.DataFrame({"customer_id": ["C1"]}).to_parquet(
        inputs_source / "customers.parquet"
    )
    docs_source = tmp_path / "docs-source"
    docs_source.mkdir()
    (docs_source / "business_definitions.md").write_text(
        "# Business definitions\nUse reporting contribution profit.",
        encoding="utf-8",
    )

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return _audit_with_executed_provenance(context)

    async def fake_lead(context, objective, *, business_context, audit, agent):  # noqa: ANN001
        ledger = context.ledger
        ledger.update_investigation_plan(
            ["Audit the data", "Decompose profitability", "Validate the mechanism"]
        )
        ledger.upsert_hypothesis(
            Hypothesis(id="H1", statement="Acquisition efficiency deteriorated.")
        )

        query_path = context.workspace.working / "queries" / "canonical.sql"
        query_path.parent.mkdir(parents=True, exist_ok=True)
        query_path.write_text(
            "SELECT customer_id FROM customers LIMIT 1\n",
            encoding="utf-8",
        )
        script_path = context.workspace.working / "scripts" / "canonical.py"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(
            "import pandas as pd\n"
            "pd.read_parquet('/workspace/inputs/customers.parquet')\n",
            encoding="utf-8",
        )
        now = datetime.now(UTC)
        ledger.append_tool_event(
            ToolEvent(
                id="sql-evidence",
                tool_name="run_sql",
                status=ToolEventStatus.SUCCEEDED,
                started_at=now,
                completed_at=now,
                arguments={"query_path": "working/queries/canonical.sql"},
                artifact_refs=["working/queries/canonical.sql"],
            )
        )
        ledger.append_tool_event(
            ToolEvent(
                id="python-evidence",
                tool_name="run_python",
                status=ToolEventStatus.SUCCEEDED,
                started_at=now,
                completed_at=now,
                arguments={"script_path": "working/scripts/canonical.py"},
                artifact_refs=["working/scripts/canonical.py"],
            )
        )

        chart_path = context.workspace.outputs / "acquisition-funnel.png"
        chart_path.write_bytes(b"deterministic chart fixture")
        ArtifactManager(context.workspace, ledger).register(
            "outputs/acquisition-funnel.png",
            artifact_id="acquisition-funnel-chart",
            kind=ArtifactKind.CHART,
            media_type="image/png",
        )

        comparisons = _canonical_comparisons()
        comparisons = [
            item.model_copy(update={"evidence_refs": ["sql-evidence"]})
            for item in comparisons
        ]
        comparisons.append(
            MetricComparison(
                metric_key="sessions",
                dimensions={"channel": "Meta"},
                baseline_period="Q1 2025",
                comparison_period="Q2 2025",
                comparison_type="relative_change",
                value=-0.0019,
                unit="relative_change_fraction",
                evidence_refs=["sql-evidence"],
            )
        )
        ledger.record_specialist_result(
            "statistician",
            SpecialistResult(
                objective="Assess whether customer value changed.",
                metric_comparisons=[
                    item for item in comparisons if item.metric_key == "ltv"
                ],
                methods_used=["deterministic fixture"],
            ),
        )
        for role in ("analyst", "statistician"):
            ledger.record_agent_event(
                agent_name=role.title(),
                agent_role=role,
                status=AgentEventStatus.SUCCEEDED,
                model="test-model",
                objective=objective,
                output_type="SpecialistResult",
            )
        ledger.record_model_usage(
            SimpleNamespace(
                requests=5,
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                input_tokens_details=None,
                output_tokens_details=None,
            )
        )
        hypothesis = Hypothesis(
            id="H1",
            statement="Acquisition efficiency deteriorated.",
            status=HypothesisStatus.SUPPORTED,
            evidence_refs=["sql-evidence"],
            rationale="The acquisition funnel reconciles.",
        )
        answer = (
            "Meta was the largest material profitability driver. Meta marketing "
            "spend increased while sessions were stable; conversion declined and "
            "drove fewer acquired customers and higher CAC. Meta 90-day LTV was "
            "stable, and broad COGS and contribution margin were stable and did "
            "not drive the decline."
        )
        return LeadResult(
            objective=objective,
            answer=answer,
            findings=[
                Finding(
                    id="F1",
                    statement=answer,
                    metric="cac",
                    value=next(
                        item.value for item in comparisons if item.metric_key == "cac"
                    ),
                    value_unit="relative_change_fraction",
                    evidence_refs=["sql-evidence"],
                    confidence=ConfidenceLevel.HIGH,
                )
            ],
            metric_comparisons=comparisons,
            recommendations=[
                {
                    "id": "R1",
                    "statement": (
                        "Improve conversion while monitoring CAC and downstream LTV."
                    ),
                    "evidence_refs": ["sql-evidence"],
                    "confidence": "high",
                }
            ],
            hypotheses=[hypothesis],
            artifacts=["acquisition-funnel-chart"],
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        return ValidationResult(
            status=ValidationStatus.PASS,
            checked_finding_ids=[item.id for item in candidate.findings],
            summary="The deterministic candidate is complete and supported.",
        )

    result = asyncio.run(
        AnalysisRunner(
            workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
            model="test-model",
            auditor_runner=fake_auditor,
            lead_runner=fake_lead,
            critic_runner=fake_critic,
        ).run(
            "canonical-offline",
            CANONICAL_PROFITABILITY_SCENARIO.user_question,
            inputs_source=inputs_source,
            docs_source=docs_source,
        )
    )

    summary = evaluate_canonical_run(result)

    assert summary.status == "completed"
    assert summary.model_requests == 5


def test_completed_persisted_workspace_passes_without_executing_agents(
    tmp_path: Path,
) -> None:
    """Build ledger state directly and evaluate it without an agent lifecycle."""

    inputs_source = tmp_path / "persisted-inputs"
    inputs_source.mkdir()
    pd.DataFrame({"customer_id": ["C1"]}).to_parquet(
        inputs_source / "customers.parquet"
    )
    docs_source = tmp_path / "persisted-docs"
    docs_source.mkdir()
    (docs_source / "business_definitions.md").write_text(
        "# Business definitions\nUse reporting contribution profit.",
        encoding="utf-8",
    )
    workspace = WorkspaceManager(tmp_path / "persisted-workspaces").create_workspace(
        "canonical-persisted",
        inputs_source=inputs_source,
        docs_source=docs_source,
    )
    ledger = AnalysisLedger(
        workspace,
        objective=CANONICAL_PROFITABILITY_SCENARIO.user_question,
    )
    ledger.record_run_metadata(model="offline-fixture", model_provider="none")
    inspection = DuckDBExecutionService(workspace, ledger).inspect_relations()
    assert inspection.tool_event_id is not None
    ledger.record_audit(
        AuditResult(
            status="complete",
            tables=[
                TableAudit(
                    table_name=relation.relation_name,
                    row_count=relation.row_count or 0,
                    evidence_refs=[inspection.tool_event_id],
                )
                for relation in inspection.relations
            ],
            limitations=[
                AuditObservation(
                    statement="Only the registered input relations were inspected.",
                    evidence_refs=[inspection.tool_event_id],
                )
            ],
        )
    )
    ledger.update_investigation_plan(
        ["Audit inputs", "Decompose profit", "Validate recommendations"]
    )

    query_path = workspace.working / "queries" / "canonical.sql"
    query_path.write_text(
        "SELECT customer_id FROM customers LIMIT 1\n",
        encoding="utf-8",
    )
    script_path = workspace.working / "scripts" / "canonical.py"
    script_path.write_text(
        "import pandas as pd\npd.read_parquet('/workspace/inputs/customers.parquet')\n",
        encoding="utf-8",
    )
    now = datetime.now(UTC)
    ledger.append_tool_event(
        ToolEvent(
            id="persisted-sql",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=now,
            completed_at=now,
            arguments={"query_path": "working/queries/canonical.sql"},
            artifact_refs=["working/queries/canonical.sql"],
        )
    )
    ledger.append_tool_event(
        ToolEvent(
            id="persisted-python",
            tool_name="run_python",
            status=ToolEventStatus.SUCCEEDED,
            started_at=now,
            completed_at=now,
            arguments={"script_path": "working/scripts/canonical.py"},
            artifact_refs=["working/scripts/canonical.py"],
        )
    )

    evidence_ref = "working/queries/canonical.sql"
    hypothesis = Hypothesis(
        id="H1",
        statement="Acquisition efficiency drove the profitability decline.",
        status=HypothesisStatus.SUPPORTED,
        evidence_refs=[evidence_ref],
        rationale="The persisted funnel evidence supports the mechanism.",
    )
    ledger.upsert_hypothesis(hypothesis)
    finding_text = (
        "Meta was the largest material profitability driver: contribution profit "
        "fell $5,235.57 as spend rose, sessions stayed stable, conversion declined "
        "and drove fewer acquired customers and higher CAC. Meta 90-day LTV and "
        "broad COGS and contribution margin were stable, so neither was a material "
        "driver."
    )
    ledger.upsert_finding(
        Finding(
            id="F1",
            statement=finding_text,
            metric="cac",
            value=0.0,
            value_unit="relative_change_fraction",
            evidence_refs=[evidence_ref],
            confidence=ConfidenceLevel.HIGH,
        )
    )
    comparisons = [
        item.model_copy(update={"evidence_refs": [evidence_ref]})
        for item in _canonical_comparisons()
    ]
    comparisons.extend(
        [
            _ltv_comparison(
                0.0001,
                {"channel": "Meta", "cohort": "acquired customers"},
                evidence_ref,
            ),
            MetricComparison(
                metric_key="sessions",
                dimensions={"channel": "Meta"},
                baseline_period="Q1 2025",
                comparison_period="Q2 2025",
                comparison_type="relative_change",
                value=0.0,
                unit="relative_change_fraction",
                evidence_refs=[evidence_ref],
            ),
        ]
    )
    ledger.replace_metric_comparisons(comparisons)
    ledger.record_specialist_result(
        "statistician",
        SpecialistResult(
            objective="Assess downstream customer value.",
            metric_comparisons=[
                item for item in comparisons if item.metric_key == "ltv"
            ],
            methods_used=["persisted deterministic fixture"],
        ),
    )
    for role in ("data_auditor", "lead", "analyst", "statistician", "critic"):
        ledger.record_agent_event(
            agent_name=role.replace("_", " ").title(),
            agent_role=role,
            status=AgentEventStatus.SUCCEEDED,
            model="offline-fixture",
            objective=CANONICAL_PROFITABILITY_SCENARIO.user_question,
            output_type="persisted-fixture",
        )
    ledger.add_validation_result(
        ValidationResult(
            status=ValidationStatus.PASS,
            checked_finding_ids=["F1"],
            summary="Persisted evidence is complete and valid.",
        )
    )

    chart_path = workspace.outputs / "canonical-chart.png"
    chart_path.write_bytes(b"offline chart")
    ArtifactManager(workspace, ledger).register(
        "outputs/canonical-chart.png",
        artifact_id="canonical-chart",
        kind=ArtifactKind.CHART,
        media_type="image/png",
    )
    report_path = workspace.outputs / "report.md"
    report_path.write_text(
        "# Analysis Report\n\n"
        "## Executive Summary\n\n"
        f"{finding_text}\n\n"
        "## Findings\n\n"
        f"- {finding_text} _(evidence: {evidence_ref})_\n\n"
        "## Recommendations\n\n"
        "- Govern spend with CAC, conversion, contribution-profit, and LTV "
        f"guardrails. _(evidence: {evidence_ref})_\n",
        encoding="utf-8",
    )
    report = ArtifactManager(workspace, ledger).register(
        "outputs/report.md",
        artifact_id="final-report",
        kind=ArtifactKind.REPORT,
        media_type="text/markdown",
    )
    ledger.record_final_report(report)
    ledger.record_model_usage(
        SimpleNamespace(
            requests=5,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            input_tokens_details=None,
            output_tokens_details=None,
        )
    )
    ledger.record_elapsed(1.0)
    ledger.record_cost_estimate()
    ledger.update_budget(
        ledger.budget.model_copy(
            update={
                "sql_executions": 1,
                "python_executions": 1,
                "specialist_invocations": 2,
                "critic_loops": 1,
                "charts_created": 1,
            }
        )
    )
    ledger.set_status(RunStatus.COMPLETED)

    summary = evaluate_canonical_workspace(workspace.root)

    assert summary.status == "completed"
    assert summary.sql_events == 1
    assert summary.python_events == 1
    assert summary.chart_artifacts == 1

    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    environment["OPENAI_BASE_URL"] = "http://127.0.0.1:1"
    completed = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1]
                / "scripts"
                / "evaluate_canonical_workspace.py"
            ),
            str(workspace.root),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["run_id"] == "canonical-persisted"
