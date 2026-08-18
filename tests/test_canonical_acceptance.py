"""Deterministic checks for the canonical acceptance evaluator boundary."""

import asyncio
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
)
from orchestration.runner import AnalysisRunner
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from schemas.audit import AuditResult
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.hypotheses import Hypothesis, HypothesisStatus
from schemas.lead import LeadResult
from schemas.metrics import MetricComparison
from schemas.run_state import AgentEventStatus, ArtifactKind, ToolEvent, ToolEventStatus
from schemas.validation import ValidationResult, ValidationStatus
from tools.artifacts import ArtifactManager
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


def test_canonical_acceptance_rejects_incomplete_persisted_runs(
    tmp_path: Path,
) -> None:
    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return AuditResult(status="complete")

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
        return AuditResult(status="complete")

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
