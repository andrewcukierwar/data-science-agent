"""Opt-in live strict-output canaries, one per benchmark architecture.

R13 requires one bounded live canary per architecture to complete its top-level
strict output contract before any paid benchmark pilot is attempted. The
retained Task 10 pilots all failed on invalid final-output JSON, so these are
the smallest live runs that prove the strict contract actually holds against a
real provider rather than only against deterministic fixtures.

They are intentionally minimal: a tiny dataset, one question, and assertions on
the strict top-level output types plus the usage that proves a real request was
made. Broader outcome assertions belong to the R17 preflight.
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from agents.output_contract import PRODUCTION_AGENT_OUTPUT_TYPES
from agents.runtime import AgentRole
from benchmark.preflight import assert_run_outcome
from orchestration.generalist_runner import GeneralistRunner
from orchestration.runner import AnalysisRunner
from schemas.audit import AuditResult
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.validation import ValidationResult
from tools.workspace import WorkspaceManager

pytestmark = pytest.mark.live

_CANARY_OBJECTIVE = (
    "Summarize the observed order revenue by channel and state any "
    "data-quality limitations."
)

requires_live_credentials = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") or not os.getenv("OPENAI_DEFAULT_MODEL"),
    reason=(
        "OPENAI_API_KEY and OPENAI_DEFAULT_MODEL are required; live tests are opt-in"
    ),
)


def _canary_inputs(tmp_path: Path) -> tuple[Path, Path]:
    inputs_source = tmp_path / "inputs"
    docs_source = tmp_path / "docs"
    inputs_source.mkdir()
    docs_source.mkdir()
    pd.DataFrame(
        {
            "order_id": ["O1", "O2", "O3", "O4"],
            "channel": ["Meta", "Meta", "Email", "Email"],
            "revenue": [100.0, 120.0, 80.0, 60.0],
        }
    ).to_parquet(inputs_source / "orders.parquet", index=False)
    (docs_source / "business_definitions.md").write_text(
        "# Definitions\n\nRevenue is the sum of order revenue by channel.\n",
        encoding="utf-8",
    )
    return inputs_source, docs_source


def _assert_strict_contract_completed(ledger: object) -> None:
    """Require evidence that a real strict-output request completed."""

    assert ledger is not None
    assert ledger.usage.requests > 0
    assert ledger.state.elapsed_seconds is not None


@requires_live_credentials
def test_multi_agent_live_strict_output_canary(
    tmp_path: Path,
    docker_image: str,
) -> None:
    inputs_source, docs_source = _canary_inputs(tmp_path)
    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        model=os.environ["OPENAI_DEFAULT_MODEL"],
        docker_image=docker_image,
    )

    result = runner.run_sync(
        "canary-strict-multi-agent",
        _CANARY_OBJECTIVE,
        inputs_source=inputs_source,
        docs_source=docs_source,
    )

    assert result.error is None, result.error
    # The top-level strict output contract of the multi-agent architecture.
    assert isinstance(result.audit, AuditResult)
    assert isinstance(result.lead_result, LeadResult)
    assert isinstance(result.validation_result, ValidationResult)
    _assert_strict_contract_completed(result.ledger)
    # R17: the canary must also satisfy the outcome gate a paid pilot requires.
    assert_run_outcome(result, architecture="multi-agent")


@requires_live_credentials
def test_single_agent_live_strict_output_canary(
    tmp_path: Path,
    docker_image: str,
) -> None:
    inputs_source, docs_source = _canary_inputs(tmp_path)
    runner = GeneralistRunner(
        workspace_base_dir=tmp_path / "workspaces",
        model=os.environ["OPENAI_DEFAULT_MODEL"],
        model_provider="openai",
        docker_image=docker_image,
    )

    result = runner.run_sync(
        "canary-strict-single-agent",
        _CANARY_OBJECTIVE,
        inputs_source=inputs_source,
        docs_source=docs_source,
    )

    assert result.error is None, result.error
    # The single agent returns exactly one strict top-level output containing
    # the same three typed components the multi-agent lifecycle persists.
    assert isinstance(result.generalist_result, GeneralistResult)
    assert isinstance(result.generalist_result.audit, AuditResult)
    assert isinstance(result.generalist_result.candidate, LeadResult)
    assert isinstance(result.generalist_result.validation, ValidationResult)
    assert result.ledger is not None
    assert result.ledger.specialist_results == []
    _assert_strict_contract_completed(result.ledger)
    # R17: the canary must also satisfy the outcome gate a paid pilot requires.
    assert_run_outcome(result, architecture="single-agent")


@requires_live_credentials
def test_live_canaries_cover_every_production_output_type() -> None:
    """Keep the canary set aligned with the production output contracts."""

    covered = {
        PRODUCTION_AGENT_OUTPUT_TYPES[role]
        for role in (
            AgentRole.LEAD,
            AgentRole.GENERALIST,
            AgentRole.DATA_AUDITOR,
            AgentRole.CRITIC,
        )
    }

    assert covered == {
        LeadResult,
        GeneralistResult,
        AuditResult,
        ValidationResult,
    }


def test_canary_does_not_invent_a_visualization_requirement() -> None:
    assert AnalysisRunner._objective_requests_visualization(_CANARY_OBJECTIVE) is False
    assert (
        AnalysisRunner._objective_requests_visualization(
            "Summarize revenue and create a chart by channel."
        )
        is True
    )
