"""Deterministic model-pricing and cost-breakdown tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestration.ledger import AnalysisLedger
from orchestration.pricing import (
    MODEL_PRICING,
    calculate_cost_breakdown,
    resolve_model_pricing,
)
from orchestration.runner import AnalysisRunner
from schemas.run_state import ModelUsage
from tools.workspace import WorkspaceManager


def test_luna_pricing_is_centralized() -> None:
    pricing = MODEL_PRICING["gpt-5.6-luna"]

    assert pricing.input_per_1m == 0.20
    assert pricing.cached_input_per_1m == 0.02
    assert pricing.output_per_1m == 1.20
    assert resolve_model_pricing("gpt-5.6-luna") == pricing


def test_cost_formula_separates_cached_and_uncached_input() -> None:
    breakdown = calculate_cost_breakdown(
        ModelUsage(
            input_tokens=108_000,
            cached_tokens=39_500,
            output_tokens=32_333,
            total_tokens=140_333,
        ),
        MODEL_PRICING["gpt-5.6-luna"],
        pricing_model="gpt-5.6-luna",
    )

    assert breakdown.uncached_input_tokens == 68_500
    assert breakdown.uncached_input_cost_usd == pytest.approx(0.0137)
    assert breakdown.cached_input_cost_usd == pytest.approx(0.00079)
    assert breakdown.output_cost_usd == pytest.approx(0.0387996)
    assert breakdown.estimated_cost_usd == pytest.approx(0.0532896)


def test_cost_breakdown_persists_and_reloads(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("pricing")
    ledger = AnalysisLedger(workspace, objective="Estimate model cost.")
    ledger.record_run_metadata(model="gpt-5.6-luna")
    ledger.record_model_usage(
        SimpleNamespace(
            requests=1,
            input_tokens=1_000,
            output_tokens=100,
            total_tokens=1_100,
            input_tokens_details=SimpleNamespace(cached_tokens=200),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        )
    )
    ledger.record_cost_estimate(
        pricing=MODEL_PRICING["gpt-5.6-luna"],
        pricing_model="gpt-5.6-luna",
    )

    restored = AnalysisLedger(ledger.state_path)

    assert restored.state.cost_breakdown is not None
    assert restored.state.cost_breakdown.pricing_model == "gpt-5.6-luna"
    assert restored.state.cost_breakdown.uncached_input_tokens == 800
    assert restored.state.cost_breakdown.uncached_input_cost_usd == pytest.approx(
        0.00016
    )
    assert restored.state.cost_breakdown.cached_input_cost_usd == pytest.approx(
        0.000004
    )
    assert restored.state.estimated_cost_usd == pytest.approx(
        restored.state.cost_breakdown.estimated_cost_usd
    )


def test_ledger_accepts_direct_per_million_rate_overrides(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("direct")
    ledger = AnalysisLedger(workspace, objective="Estimate model cost.")
    ledger.record_model_usage(
        SimpleNamespace(
            requests=1,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            input_tokens_details=SimpleNamespace(cached_tokens=25),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        )
    )

    ledger.record_cost_estimate(
        input_cost_per_1m=0.20,
        cached_input_cost_per_1m=0.02,
        output_cost_per_1m=1.20,
        pricing_model="gpt-5.6-luna",
    )

    assert ledger.state.cost_breakdown is not None
    assert ledger.state.cost_breakdown.pricing_model == "gpt-5.6-luna"
    assert ledger.state.estimated_cost_usd == pytest.approx(0.0000755)


def test_runner_selects_registry_pricing_and_allows_per_million_overrides(
    tmp_path: Path,
) -> None:
    registered = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "registered"),
        model="gpt-5.6-luna",
    )
    overridden = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "overridden"),
        model="gpt-5.6-luna",
        input_cost_per_1m=0.40,
        cached_input_cost_per_1m=0.04,
        output_cost_per_1m=2.40,
    )

    assert registered.model_pricing == MODEL_PRICING["gpt-5.6-luna"]
    assert overridden.model_pricing is not None
    assert overridden.model_pricing.input_per_1m == 0.40
    assert overridden.model_pricing.cached_input_per_1m == 0.04
    assert overridden.model_pricing.output_per_1m == 2.40


def test_unknown_model_without_pricing_keeps_estimate_unavailable(
    tmp_path: Path,
) -> None:
    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "unknown"),
        model="provider-model-not-in-registry",
    )

    assert runner.model_pricing is None


def test_legacy_rates_preserve_existing_single_input_rate_behavior(
    tmp_path: Path,
) -> None:
    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "legacy"),
        model="test-model",
        input_cost_per_1k_tokens=1.0,
        output_cost_per_1k_tokens=2.0,
    )

    assert runner.model_pricing is not None
    assert runner.model_pricing.input_per_1m == 1_000
    assert runner.model_pricing.cached_input_per_1m == 1_000
    assert runner.model_pricing.output_per_1m == 2_000
