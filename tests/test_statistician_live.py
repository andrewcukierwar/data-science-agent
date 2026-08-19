"""Opt-in live Statistician coverage on deterministic synthetic examples."""

import asyncio
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents import AgentRole, AgentRunConfig, AgentRunContext, run_statistician
from orchestration.ledger import AnalysisLedger
from scenarios.generator import SyntheticEcommerceConfig
from scenarios.injection import generate_canonical_profitability_scenario
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

pytestmark = pytest.mark.live


def _context(
    tmp_path: Path,
    *,
    run_id: str,
    objective: str,
    docker_image: str,
    inputs: Path,
    docs: Path,
) -> AgentRunContext:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        run_id,
        inputs_source=inputs,
        docs_source=docs,
    )
    ledger = AnalysisLedger(workspace, objective=objective)
    return AgentRunContext(
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
            run_id=run_id,
            agent_role=AgentRole.STATISTICIAN,
            model=os.environ["OPENAI_DEFAULT_MODEL"],
        ),
    )


@pytest.mark.parametrize(
    "example_name,expected_phrase",
    [
        ("significant_difference", "significant"),
        ("non_significant_difference", "not statistically significant"),
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
def test_statistician_live_known_significance_examples(
    tmp_path: Path,
    docker_image: str,
    example_name: str,
    expected_phrase: str,
) -> None:
    rng = np.random.default_rng(2026)
    rows = []
    for name, shift in (
        ("significant_difference", 0.8),
        ("non_significant_difference", 0.05),
    ):
        for group, group_shift in (("control", 0.0), ("treatment", shift)):
            rows.extend(
                {
                    "example": name,
                    "group": group,
                    "value": float(value + group_shift),
                }
                for value in rng.normal(0.0, 1.0, 400)
            )

    inputs = tmp_path / "inputs"
    docs = tmp_path / "docs"
    inputs.mkdir()
    docs.mkdir()
    pd.DataFrame(rows).to_csv(inputs / "statistical_examples.csv", index=False)
    (docs / "definitions.md").write_text(
        """# Statistical Example Definitions

- Each row is one independent observation.
- Compare control and treatment within the requested `example`.
- Use a two-sided alpha of 0.05 and treat an absolute mean difference of 0.2
  as the practical-significance threshold.
""",
        encoding="utf-8",
    )
    objective = (
        f"Use /workspace/inputs/statistical_examples.csv and read "
        f"docs/definitions.md. Assess `{example_name}` with an "
        "appropriate two-sided test, confidence interval, effect size, and "
        "practical-significance conclusion. Return one finding for this example."
    )
    context = _context(
        tmp_path,
        run_id=f"run-stat-live-{example_name}",
        objective=objective,
        docker_image=docker_image,
        inputs=inputs,
        docs=docs,
    )

    result = asyncio.run(run_statistician(context, objective))
    text = " ".join(
        [finding.statement for finding in result.findings]
        + result.caveats
        + result.follow_up_questions
    ).lower()

    assert result.findings
    assert expected_phrase in text
    assert context.ledger.findings == result.findings
    assert context.ledger.budget.python_executions >= 1
    # R17: a live agent call that recorded no usage is not a valid smoke run.
    assert context.ledger.usage.requests > 0
    assert context.ledger.usage_complete is True


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is not set; live tests are opt-in",
)
@pytest.mark.skipif(
    not os.getenv("OPENAI_DEFAULT_MODEL"),
    reason="OPENAI_DEFAULT_MODEL is not set; live tests are opt-in",
)
def test_statistician_live_canonical_meta_ltv_assessment(
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
    dataset = generate_canonical_profitability_scenario(config).dataset
    generated = dataset.write(tmp_path / "generated")
    inputs = tmp_path / "inputs"
    docs = tmp_path / "docs"
    inputs.mkdir()
    docs.mkdir()
    for name in ("customers", "orders", "sessions", "marketing_spend"):
        shutil.copy2(generated[name], inputs / generated[name].name)
    shutil.copy2(generated["business_definitions"], docs / "business_definitions.md")

    objective = (
        "Read docs/business_definitions.md. Using only Python and the "
        "parquet files in /workspace/inputs, assess whether Q1 versus Q2 Meta "
        "acquired-customer 90-day LTV changed meaningfully. Use customer-level "
        "cohort values, a two-sided 95% confidence interval and appropriate "
        "effect-size/test reasoning, with 5% as the practical-change threshold. "
        "Do not infer causality from this observational comparison."
    )
    context = _context(
        tmp_path,
        run_id="run-stat-live-canonical-ltv",
        objective=objective,
        docker_image=docker_image,
        inputs=inputs,
        docs=docs,
    )

    result = asyncio.run(run_statistician(context, objective))
    text = " ".join(
        [finding.statement for finding in result.findings]
        + result.caveats
        + result.follow_up_questions
    ).lower()

    assert result.findings
    assert "ltv" in text or "lifetime value" in text
    assert context.ledger.findings == result.findings
    assert context.ledger.budget.python_executions >= 1
    # R17: a live agent call that recorded no usage is not a valid smoke run.
    assert context.ledger.usage.requests > 0
    assert context.ledger.usage_complete is True
