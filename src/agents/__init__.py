"""Shared Agents SDK runtime contracts and deterministic tool adapters.

The project keeps placeholder modules under ``agents`` for the planned
specialists, while the OpenAI Agents SDK uses the same top-level import name.
The small compatibility loader below exposes the installed SDK in this package
without making the local specialist placeholders disappear.
"""

import importlib.metadata
from pathlib import Path


def _load_openai_agents_sdk() -> None:
    """Load the installed SDK into this compatible top-level package."""

    try:
        distribution = importlib.metadata.distribution("openai-agents")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ImportError("the openai-agents package is required") from exc
    sdk_init = Path(distribution.locate_file("agents/__init__.py"))

    local_package = Path(__file__).parent
    __path__[:] = [str(sdk_init.parent), str(local_package)]
    source = sdk_init.read_text(encoding="utf-8")
    exec(compile(source, str(sdk_init), "exec"), globals(), globals())


_load_openai_agents_sdk()

from agents.analyst import (  # noqa: E402
    ANALYST_INSTRUCTIONS,
    ANALYST_OBJECTIVE,
    AnalystEvidenceError,
    build_analyst_agent,
    create_analyst_agent,
    run_analyst,
    validate_analyst_result,
)
from agents.runtime import (  # noqa: E402
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    PermissionDeniedError,
    ToolError,
    ToolResponse,
    allowed_tools_for_role,
)
from agents.tools import (  # noqa: E402
    DocumentContents,
    WorkspaceFileInfo,
    WorkspaceInspection,
    build_agent,
    build_agent_from_config,
    inspect_workspace,
    read_document,
    run_python,
    run_sql,
    save_artifact,
    tools_for_role,
)

__all__ = [
    "AgentRole",
    "Agent",
    "AgentRunConfig",
    "AgentRunContext",
    "ANALYST_INSTRUCTIONS",
    "ANALYST_OBJECTIVE",
    "AnalystEvidenceError",
    "DocumentContents",
    "FunctionTool",
    "PermissionDeniedError",
    "ToolError",
    "ToolOutputText",
    "ToolResponse",
    "RunContextWrapper",
    "WorkspaceFileInfo",
    "WorkspaceInspection",
    "allowed_tools_for_role",
    "build_agent",
    "build_agent_from_config",
    "build_analyst_agent",
    "create_analyst_agent",
    "inspect_workspace",
    "function_tool",
    "read_document",
    "run_analyst",
    "run_python",
    "run_sql",
    "save_artifact",
    "tools_for_role",
    "validate_analyst_result",
]
