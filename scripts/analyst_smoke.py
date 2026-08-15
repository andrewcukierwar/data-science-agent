"""Manual smoke test for the Analyst on the canonical profitability subtask.

Run with an existing local Docker image and explicit OpenAI configuration:

    OPENAI_API_KEY=... OPENAI_DEFAULT_MODEL=... \
        uv run python scripts/analyst_smoke.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(1, str(REPOSITORY_ROOT))

from agents import (  # noqa: E402
    ANALYST_OBJECTIVE,
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    run_analyst,
)
from orchestration.ledger import AnalysisLedger  # noqa: E402
from scenarios.generator import SyntheticEcommerceConfig  # noqa: E402
from scenarios.injection import (  # noqa: E402
    generate_canonical_profitability_scenario,
)
from tools.artifacts import ArtifactManager  # noqa: E402
from tools.python import PythonExecutionService  # noqa: E402
from tools.sql import DuckDBExecutionService  # noqa: E402
from tools.workspace import WorkspaceManager  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="analyst-smoke")
    parser.add_argument(
        "--workspace-base",
        type=Path,
        default=Path(tempfile.gettempdir()) / "data-science-agent-analyst-smoke",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--customers", type=int, default=5_000)
    parser.add_argument("--orders", type=int, default=25_000)
    parser.add_argument("--sessions", type=int, default=50_000)
    parser.add_argument(
        "--image",
        default="data-science-agent-python:latest",
        help="Docker image used by the Python execution service.",
    )
    return parser


def _prepare_sources(
    staging: Path, *, seed: int, customers: int, orders: int, sessions: int
) -> tuple[Path, Path]:
    config = SyntheticEcommerceConfig(
        seed=seed,
        num_customers=customers,
        num_orders=orders,
        num_sessions=sessions,
        num_products=4,
        period_days=365,
    )
    scenario = generate_canonical_profitability_scenario(config)
    generated = scenario.dataset.write(staging)
    inputs = staging / "inputs"
    docs = staging / "docs"
    inputs.mkdir()
    docs.mkdir()
    for name in ("customers", "orders", "sessions", "marketing_spend"):
        shutil.copy2(generated[name], inputs / generated[name].name)
    shutil.copy2(generated["business_definitions"], docs / "business_definitions.md")
    return inputs, docs


async def _run(args: argparse.Namespace, model: str) -> None:
    with tempfile.TemporaryDirectory(prefix="analyst-smoke-") as staging_name:
        inputs, docs = _prepare_sources(
            Path(staging_name),
            seed=args.seed,
            customers=args.customers,
            orders=args.orders,
            sessions=args.sessions,
        )
        workspace = WorkspaceManager(args.workspace_base).create_workspace(
            args.run_id,
            inputs_source=inputs,
            docs_source=docs,
        )
    ledger = AnalysisLedger(
        workspace,
        objective=ANALYST_OBJECTIVE,
    )
    sql_service = DuckDBExecutionService(workspace, ledger)
    python_service = PythonExecutionService(
        workspace,
        ledger,
        image=args.image,
    )
    context = AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=sql_service,
        python_service=python_service,
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id=args.run_id,
            agent_role=AgentRole.ANALYST,
            model=model,
        ),
    )
    result = await run_analyst(context, ANALYST_OBJECTIVE)
    print(f"Workspace: {workspace.root}")
    print(result.model_dump_json(indent=2))


def main() -> None:
    load_dotenv()
    args = _parser().parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        _parser().error("OPENAI_API_KEY is required for the live smoke test")
    model = os.getenv("OPENAI_DEFAULT_MODEL")
    if not model:
        _parser().error("OPENAI_DEFAULT_MODEL is required for the live smoke test")
    asyncio.run(_run(args, model))


if __name__ == "__main__":
    main()
