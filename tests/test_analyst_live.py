"""Opt-in live Analyst integration coverage.

Run explicitly with:

    OPENAI_API_KEY=... OPENAI_DEFAULT_MODEL=... uv run pytest -m live
"""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from agents import AgentRole, AgentRunConfig, AgentRunContext, run_analyst
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
def test_analyst_live_canonical_profitability_subtask(
    tmp_path: Path,
    docker_image: str,
) -> None:
    config = SyntheticEcommerceConfig(
        seed=42,
        num_customers=2_000,
        num_orders=8_000,
        num_sessions=16_000,
        num_products=4,
        period_days=365,
    )
    scenario = generate_canonical_profitability_scenario(config).dataset
    generated = scenario.write(tmp_path / "generated")
    inputs = tmp_path / "inputs"
    docs = tmp_path / "docs"
    inputs.mkdir()
    docs.mkdir()
    for name in ("customers", "orders", "sessions", "marketing_spend"):
        shutil.copy2(generated[name], inputs / generated[name].name)
    shutil.copy2(generated["business_definitions"], docs / "business_definitions.md")

    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-analyst-live",
        inputs_source=inputs,
        docs_source=docs,
    )
    ledger = AnalysisLedger(
        workspace,
        objective=(
            "Decompose the Q1-to-Q2 profitability change and identify which "
            "major component changed most."
        ),
    )
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
            run_id="run-analyst-live",
            agent_role=AgentRole.ANALYST,
            model=os.environ["OPENAI_DEFAULT_MODEL"],
        ),
    )

    result = asyncio.run(
        run_analyst(
            context,
            "Decompose the Q1-to-Q2 profitability change and identify which "
            "major component changed most.",
        )
    )

    assert result.objective
    assert result.findings or result.metric_comparisons
    assert context.ledger.budget.specialist_invocations == 1
    # R17: a live agent call that recorded no usage is not a valid smoke run.
    assert context.ledger.usage.requests > 0
    assert context.ledger.usage.total_tokens > 0
    assert context.ledger.usage_complete is True
