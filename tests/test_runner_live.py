"""Opt-in live end-to-end AnalysisRunner coverage."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from orchestration.runner import AnalysisRunner
from scenarios.generator import SyntheticEcommerceConfig
from scenarios.injection import generate_canonical_profitability_scenario
from schemas.run_state import RunStatus
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
def test_analysis_runner_live_canonical_end_to_end(
    tmp_path: Path,
    docker_image: str,
) -> None:
    config = SyntheticEcommerceConfig(
        seed=42,
        num_customers=1_000,
        num_orders=4_000,
        num_sessions=8_000,
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
        "Why did profitability decline in Q2, and what should the company do about it?"
    )
    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        model=os.environ["OPENAI_DEFAULT_MODEL"],
        docker_image=docker_image,
    )
    result = asyncio.run(
        runner.run(
            "run-e2e-live",
            objective,
            inputs_source=inputs,
            docs_source=docs,
            business_context=(
                "Use reporting contribution profit: net revenue minus COGS and "
                "marketing spend."
            ),
        )
    )

    assert result.error is None, result.error
    assert result.status in {RunStatus.COMPLETED, RunStatus.BLOCKED}
    assert result.audit is not None
    assert result.lead_result is not None
    assert result.validation_result is not None
    assert result.report is not None
    assert result.ledger is not None
    assert result.ledger.state.final_report == result.report
    assert result.ledger.state.status is result.status
    assert result.ledger.state.model == os.environ["OPENAI_DEFAULT_MODEL"]
    assert result.ledger.usage.requests > 0
    assert result.ledger.state.elapsed_seconds is not None
    assert result.ledger.tool_events
    assert (result.workspace.outputs / "report.md").exists()
