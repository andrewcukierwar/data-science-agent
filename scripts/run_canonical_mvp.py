#!/usr/bin/env python3
"""Run and evaluate the canonical Phase 1 MVP scenario.

Exact command from the repository root::

    OPENAI_API_KEY=... OPENAI_DEFAULT_MODEL=... \
      uv run python scripts/run_canonical_mvp.py

Use ``--force`` only when intentionally replacing the exact run directory.
The scenario ground truth is loaded by the evaluator after the agent run and
is never included in the prompts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPOSITORY_ROOT / "src", REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from evaluation.canonical import (  # noqa: E402
    CanonicalAcceptanceError,
    evaluate_canonical_run,
)
from orchestration.runner import AnalysisRunner  # noqa: E402
from scenarios.generator import SyntheticEcommerceConfig  # noqa: E402
from scenarios.injection import generate_canonical_profitability_scenario  # noqa: E402
from tools.workspace import WorkspaceManager  # noqa: E402

OBJECTIVE = (
    "Why did profitability decline in Q2, and what should the company do about it?"
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-base-dir",
        type=Path,
        default=Path(".runs/canonical-mvp"),
        help="Directory containing the isolated run workspace.",
    )
    parser.add_argument(
        "--run-id",
        default="canonical-q2-mvp",
        help="Safe run identifier below --workspace-base-dir.",
    )
    parser.add_argument(
        "--docker-image",
        default="data-science-agent-python:latest",
        help="Docker image used by Python analysis tools.",
    )
    parser.add_argument(
        "--input-cost-per-1k-tokens",
        type=float,
        default=None,
        help="Optional provider-specific input price used only for an estimate.",
    )
    parser.add_argument(
        "--output-cost-per-1k-tokens",
        type=float,
        default=None,
        help="Optional provider-specific output price used only for an estimate.",
    )
    parser.add_argument(
        "--input-cost-per-1m",
        type=float,
        default=None,
        help="Optional uncached input price in USD per million tokens.",
    )
    parser.add_argument(
        "--cached-input-cost-per-1m",
        type=float,
        default=None,
        help="Optional cached input price in USD per million tokens.",
    )
    parser.add_argument(
        "--output-cost-per-1m",
        type=float,
        default=None,
        help="Optional output price in USD per million tokens.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Remove and recreate this exact run directory before execution.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = _arguments()
    model = os.getenv("OPENAI_DEFAULT_MODEL")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY must be set for the canonical live run")
    if not model:
        raise SystemExit("OPENAI_DEFAULT_MODEL must be set for the canonical live run")

    workspace_base_dir = args.workspace_base_dir.expanduser().resolve()
    manager = WorkspaceManager(workspace_base_dir)
    run_path = workspace_base_dir / args.run_id
    if run_path.exists():
        if not args.force:
            raise SystemExit(
                f"run directory already exists: {run_path}; use --force or --run-id"
            )
        manager.cleanup_workspace(args.run_id)

    config = SyntheticEcommerceConfig(
        seed=42,
        num_customers=1_000,
        num_orders=4_000,
        num_sessions=8_000,
        num_products=4,
        period_days=365,
    )
    with tempfile.TemporaryDirectory(prefix="canonical-q2-sources-") as temp_dir:
        generated_dir = Path(temp_dir) / "generated"
        generated = generate_canonical_profitability_scenario(config).dataset.write(
            generated_dir
        )
        inputs = Path(temp_dir) / "inputs"
        docs = Path(temp_dir) / "docs"
        inputs.mkdir()
        docs.mkdir()
        for name in ("customers", "orders", "sessions", "marketing_spend"):
            shutil.copy2(generated[name], inputs / generated[name].name)
        shutil.copy2(
            generated["business_definitions"],
            docs / "business_definitions.md",
        )

        runner = AnalysisRunner(
            workspace_manager=manager,
            model=model,
            docker_image=args.docker_image,
            input_cost_per_1k_tokens=args.input_cost_per_1k_tokens,
            output_cost_per_1k_tokens=args.output_cost_per_1k_tokens,
            input_cost_per_1m=args.input_cost_per_1m,
            cached_input_cost_per_1m=args.cached_input_cost_per_1m,
            output_cost_per_1m=args.output_cost_per_1m,
        )
        result = runner.run_sync(
            args.run_id,
            OBJECTIVE,
            inputs_source=inputs,
            docs_source=docs,
        )

    try:
        summary = evaluate_canonical_run(result)
    except CanonicalAcceptanceError as error:
        print(f"CANONICAL ACCEPTANCE FAILED: {error}", file=sys.stderr)
        if result.workspace is not None:
            print(f"Workspace: {result.workspace.root}", file=sys.stderr)
        return 1

    print(json.dumps(summary.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
