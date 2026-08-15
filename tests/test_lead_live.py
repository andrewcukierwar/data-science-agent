"""Opt-in live Lead coverage proving manager-style specialist delegation."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from agents import AgentRole, AgentRunConfig, AgentRunContext, run_lead
from orchestration.ledger import AnalysisLedger
from scenarios.generator import SyntheticEcommerceConfig
from scenarios.injection import generate_canonical_profitability_scenario
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is not set; live tests are opt-in",
)
@pytest.mark.skipif(
    not os.getenv("OPENAI_DEFAULT_MODEL"),
    reason="OPENAI_DEFAULT_MODEL is not set; live tests are opt-in",
)
def test_lead_live_delegates_computation_to_specialists(
    tmp_path: Path,
    docker_image: str,
) -> None:
    config = SyntheticEcommerceConfig(
        seed=42,
        num_customers=500,
        num_orders=2_000,
        num_sessions=4_000,
        num_products=4,
        period_days=365,
    )
    generated = generate_canonical_profitability_scenario(config).dataset.write(
        tmp_path / "generated"
    )
    inputs = tmp_path / "inputs"
    docs = tmp_path / "docs"
    inputs.mkdir()
    docs.mkdir()
    for name in ("customers", "orders", "sessions", "marketing_spend"):
        shutil.copy2(generated[name], inputs / generated[name].name)
    shutil.copy2(generated["business_definitions"], docs / "business_definitions.md")

    objective = (
        "Decompose the Q1-to-Q2 profitability change and identify which major "
        "component changed most."
    )
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-lead-live",
        inputs_source=inputs,
        docs_source=docs,
    )
    ledger = AnalysisLedger(workspace, objective=objective)
    context = AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(
            workspace,
            ledger,
            image=docker_image,
        ),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-lead-live",
            agent_role=AgentRole.LEAD,
            model=os.environ["OPENAI_DEFAULT_MODEL"],
        ),
    )

    result = asyncio.run(run_lead(context, objective))

    assert result.answer
    assert context.ledger.budget.specialist_invocations >= 1
    assert (
        context.ledger.budget.sql_executions >= 1
        or context.ledger.budget.python_executions >= 1
    )
    assert context.ledger.tool_events
    assert all(
        event.tool_name != "delegate_to_lead" for event in context.ledger.tool_events
    )
