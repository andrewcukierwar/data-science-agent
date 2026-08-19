"""Opt-in live Critic coverage for pass and known validation failures."""

import asyncio
import os
from pathlib import Path

import pytest

from agents import AgentRole, AgentRunConfig, AgentRunContext, run_critic
from orchestration.ledger import AnalysisLedger
from schemas.findings import ConfidenceLevel, Finding
from schemas.run_state import ArtifactKind
from schemas.validation import CriticCandidate, ValidationStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

pytestmark = pytest.mark.live


def _prepare_context(
    tmp_path: Path,
    *,
    case_name: str,
    docker_image: str,
) -> tuple[AgentRunContext, CriticCandidate]:
    source_inputs = tmp_path / "source-inputs"
    source_docs = tmp_path / "source-docs"
    source_inputs.mkdir()
    source_docs.mkdir()
    (source_inputs / "customers.csv").write_text(
        "customer_id,acquisition_date,acquisition_channel\n"
        + "\n".join(
            [
                *(f"M{index:02d},2025-01-{index:02d},Meta" for index in range(1, 11)),
                *(
                    f"O{index:02d},2025-01-{index:02d},Organic"
                    for index in range(1, 11)
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (source_inputs / "marketing_spend.csv").write_text(
        "date,channel,spend\n2025-01-31,Meta,100.0\n",
        encoding="utf-8",
    )
    (source_docs / "business_definitions.md").write_text(
        """# Business Definitions

- CAC is marketing spend divided by customers newly acquired through the
  channel during the reporting period.
- The reporting period is January 2025 for this fixture.
- The fixture contains no creative-level evidence, so channel comparisons are
  observational and cannot establish creative causality.
""",
        encoding="utf-8",
    )

    run_id = f"run-critic-live-{case_name}"
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        run_id,
        inputs_source=source_inputs,
        docs_source=source_docs,
    )
    objective = "Validate a candidate Meta acquisition analysis."
    ledger = AnalysisLedger(workspace, objective=objective)
    sql_service = DuckDBExecutionService(workspace, ledger)
    context = AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=sql_service,
        python_service=PythonExecutionService(
            workspace,
            ledger,
            image=docker_image,
        ),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id=run_id,
            agent_role=AgentRole.CRITIC,
            model=os.environ["OPENAI_DEFAULT_MODEL"],
        ),
    )

    customers_path = workspace.inputs / "customers.csv"
    spend_path = workspace.inputs / "marketing_spend.csv"
    if case_name == "unsupported_causal_claim":
        sql = (
            "SELECT 0.10 AS q1_conversion, 0.08 AS q2_conversion, "
            "'creative data unavailable' AS limitation"
        )
    else:
        sql = f"""
        SELECT
            (SELECT SUM(spend) FROM read_csv_auto('{spend_path}')) AS spend,
            COUNT(*) AS total_customers,
            COUNT(*) FILTER (WHERE acquisition_channel = 'Meta') AS new_customers,
            (SELECT SUM(spend) FROM read_csv_auto('{spend_path}'))
                / COUNT(*) FILTER (WHERE acquisition_channel = 'Meta') AS cac
        FROM read_csv_auto('{customers_path}')
        WHERE acquisition_date BETWEEN '2025-01-01' AND '2025-01-31'
        """
    query_id = f"Q-{case_name}"
    query_result = sql_service.execute(sql, query_id=query_id)
    assert query_result.success is True
    evidence_path = f"working/queries/{query_id}.sql"
    context.artifact_manager.register(
        evidence_path,
        artifact_id=f"artifact-{case_name}",
        kind=ArtifactKind.QUERY,
        description="Executed Critic fixture query",
    )

    if case_name == "correct_analysis":
        finding = Finding(
            id="F-CORRECT",
            statement="Meta CAC is $10, using $100 spend divided by 10 new customers.",
            metric="CAC",
            value=10.0,
            evidence_refs=[evidence_path],
            confidence=ConfidenceLevel.HIGH,
        )
        recommendations = [
            "Use this definition and calculation for Meta CAC monitoring."
        ]
    elif case_name == "incorrect_cac_denominator":
        finding = Finding(
            id="F-WRONG-DENOMINATOR",
            statement=(
                "Meta CAC is $5, calculated as $100 spend divided by 20 total "
                "customers."
            ),
            metric="CAC",
            value=5.0,
            evidence_refs=[evidence_path],
            confidence=ConfidenceLevel.HIGH,
        )
        recommendations = ["Increase Meta spend because CAC is low."]
    elif case_name == "unsupported_causal_claim":
        finding = Finding(
            id="F-CAUSAL",
            statement="Creative fatigue caused Meta conversion to decline in Q2.",
            evidence_refs=[evidence_path],
            confidence=ConfidenceLevel.LOW,
        )
        recommendations = ["Replace Meta creative immediately."]
    else:
        finding = Finding(
            id="F-WRONG-NUMBER",
            statement="Meta CAC is $12 based on the SQL evidence.",
            metric="CAC",
            value=12.0,
            evidence_refs=[evidence_path],
            confidence=ConfidenceLevel.HIGH,
        )
        recommendations = ["Optimize the budget using the reported CAC."]

    return context, CriticCandidate(
        objective=objective,
        answer="The candidate analysis explains the observed change.",
        findings=[finding],
        recommendations=recommendations,
        artifacts=[evidence_path],
        evidence_refs=[f"tool-{query_id}"],
    )


@pytest.mark.parametrize(
    "case_name,expected_status,expected_term",
    [
        ("correct_analysis", ValidationStatus.PASS, None),
        ("incorrect_cac_denominator", ValidationStatus.REVISE, "denominator"),
        ("unsupported_causal_claim", ValidationStatus.REVISE, "caus"),
        ("inconsistent_numerical_claim", ValidationStatus.REVISE, "numer"),
    ],
)
@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is not set; live tests are opt-in",
)
@pytest.mark.skipif(
    not os.getenv("OPENAI_DEFAULT_MODEL"),
    reason="OPENAI_DEFAULT_MODEL is not set; live tests are opt-in",
)
def test_critic_live_passes_or_revises_known_cases(
    tmp_path: Path,
    docker_image: str,
    case_name: str,
    expected_status: ValidationStatus,
    expected_term: str | None,
) -> None:
    context, candidate = _prepare_context(
        tmp_path,
        case_name=case_name,
        docker_image=docker_image,
    )

    result = asyncio.run(run_critic(context, candidate))

    assert result.status is expected_status
    assert context.ledger.validation_results[-1] == result
    # R17: a live agent call that recorded no usage is not a valid smoke run.
    assert context.ledger.usage.requests > 0
    assert context.ledger.usage_complete is True
    if expected_term is not None:
        issue_text = " ".join(
            issue.message
            + " "
            + (issue.category or "")
            + " "
            + (issue.recommendation or "")
            for issue in result.issues
        ).lower()
        assert expected_term in issue_text
