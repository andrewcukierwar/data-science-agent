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
    AnalystArtifactError,
    AnalystEvidenceError,
    build_analyst_agent,
    create_analyst_agent,
    persist_analyst_result,
    run_analyst,
    validate_analyst_result,
)
from agents.auditor import (  # noqa: E402
    AUDITOR_INSTRUCTIONS,
    AUDITOR_OBJECTIVE,
    DATA_AUDITOR_INSTRUCTIONS,
    DATA_AUDITOR_OBJECTIVE,
    build_auditor_agent,
    build_data_auditor_agent,
    create_data_auditor_agent,
    run_auditor,
    run_data_auditor,
)
from agents.critic import (  # noqa: E402
    CRITIC_INSTRUCTIONS,
    CRITIC_OBJECTIVE,
    VALIDATOR_INSTRUCTIONS,
    VALIDATOR_OBJECTIVE,
    CriticPersistenceError,
    build_critic_agent,
    build_validator_agent,
    candidate_completeness_validation,
    create_critic_agent,
    create_validator_agent,
    persist_validation_result,
    run_critic,
    run_validator,
)
from agents.generalist import (  # noqa: E402
    GENERALIST_INSTRUCTIONS,
    GENERALIST_OBJECTIVE,
    build_generalist_agent,
    create_generalist_agent,
    persist_generalist_result,
    run_generalist,
)
from agents.lead import (  # noqa: E402
    LEAD_INSTRUCTIONS,
    LEAD_OBJECTIVE,
    LeadEvidenceError,
    build_lead_agent,
    create_lead_agent,
    persist_lead_result,
    record_hypothesis,
    record_open_question,
    run_lead,
    update_investigation_plan,
    validate_lead_result,
)
from agents.runtime import (  # noqa: E402
    DEFAULT_AGENT_TURN_LIMITS,
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    PermissionDeniedError,
    ToolError,
    ToolResponse,
    allowed_tools_for_role,
    normalize_agent_turn_limits,
)
from agents.statistician import (  # noqa: E402
    STATISTICIAN_INSTRUCTIONS,
    STATISTICIAN_OBJECTIVE,
    StatisticianArtifactError,
    StatisticianEvidenceError,
    build_statistician_agent,
    create_statistician_agent,
    persist_statistician_result,
    run_statistician,
    validate_statistician_result,
)
from agents.tools import (  # noqa: E402
    DocumentContents,
    EvidenceInspection,
    WorkspaceFileInfo,
    WorkspaceInspection,
    build_agent,
    build_agent_from_config,
    inspect_evidence,
    inspect_relations,
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
    "DEFAULT_AGENT_TURN_LIMITS",
    "ANALYST_INSTRUCTIONS",
    "ANALYST_OBJECTIVE",
    "AnalystArtifactError",
    "AnalystEvidenceError",
    "LEAD_INSTRUCTIONS",
    "LEAD_OBJECTIVE",
    "LeadEvidenceError",
    "AUDITOR_INSTRUCTIONS",
    "AUDITOR_OBJECTIVE",
    "DATA_AUDITOR_INSTRUCTIONS",
    "DATA_AUDITOR_OBJECTIVE",
    "CRITIC_INSTRUCTIONS",
    "CRITIC_OBJECTIVE",
    "GENERALIST_INSTRUCTIONS",
    "GENERALIST_OBJECTIVE",
    "CriticPersistenceError",
    "candidate_completeness_validation",
    "VALIDATOR_INSTRUCTIONS",
    "VALIDATOR_OBJECTIVE",
    "DocumentContents",
    "EvidenceInspection",
    "inspect_relations",
    "FunctionTool",
    "PermissionDeniedError",
    "ToolError",
    "ToolOutputText",
    "ToolResponse",
    "RunContextWrapper",
    "WorkspaceFileInfo",
    "WorkspaceInspection",
    "allowed_tools_for_role",
    "normalize_agent_turn_limits",
    "build_agent",
    "build_agent_from_config",
    "build_analyst_agent",
    "build_auditor_agent",
    "build_data_auditor_agent",
    "build_critic_agent",
    "build_generalist_agent",
    "build_lead_agent",
    "build_validator_agent",
    "create_analyst_agent",
    "persist_analyst_result",
    "create_data_auditor_agent",
    "create_critic_agent",
    "create_generalist_agent",
    "create_lead_agent",
    "persist_lead_result",
    "persist_generalist_result",
    "create_validator_agent",
    "inspect_workspace",
    "inspect_evidence",
    "function_tool",
    "read_document",
    "record_hypothesis",
    "record_open_question",
    "run_lead",
    "run_analyst",
    "run_auditor",
    "run_data_auditor",
    "run_critic",
    "run_generalist",
    "run_validator",
    "STATISTICIAN_INSTRUCTIONS",
    "STATISTICIAN_OBJECTIVE",
    "StatisticianArtifactError",
    "StatisticianEvidenceError",
    "run_python",
    "run_sql",
    "save_artifact",
    "build_statistician_agent",
    "create_statistician_agent",
    "persist_statistician_result",
    "persist_validation_result",
    "tools_for_role",
    "update_investigation_plan",
    "validate_analyst_result",
    "validate_lead_result",
    "run_statistician",
    "validate_statistician_result",
]
