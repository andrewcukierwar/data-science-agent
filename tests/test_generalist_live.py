"""Opt-in live smoke coverage for the single-agent baseline."""

import os
from pathlib import Path

import pandas as pd
import pytest

from orchestration.generalist_runner import GeneralistRunner

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is not set; live tests are opt-in",
)
@pytest.mark.skipif(
    not os.getenv("OPENAI_DEFAULT_MODEL"),
    reason="OPENAI_DEFAULT_MODEL is not set; live tests are opt-in",
)
def test_generalist_live_smoke_uses_one_agent_lifecycle(
    tmp_path: Path,
    docker_image: str,
) -> None:
    input_source = tmp_path / "inputs"
    docs_source = tmp_path / "docs"
    input_source.mkdir()
    docs_source.mkdir()
    pd.DataFrame(
        {
            "order_id": ["O1", "O2", "O3"],
            "revenue": [100.0, 120.0, 80.0],
        }
    ).to_parquet(input_source / "orders.parquet", index=False)
    (docs_source / "business_definitions.md").write_text(
        "# Definitions\n\nRevenue is the sum of order revenue.\n",
        encoding="utf-8",
    )

    runner = GeneralistRunner(
        workspace_base_dir=tmp_path / "workspaces",
        model=os.environ["OPENAI_DEFAULT_MODEL"],
        model_provider="openai",
        docker_image=docker_image,
    )
    result = runner.run_sync(
        "run-generalist-live",
        "Summarize the observed order revenue and any data-quality limitations.",
        inputs_source=input_source,
        docs_source=docs_source,
    )

    assert result.ledger is not None
    assert result.ledger.specialist_results == []
    assert result.ledger.agent_events
    assert {event.agent_role for event in result.ledger.agent_events} == {"generalist"}
