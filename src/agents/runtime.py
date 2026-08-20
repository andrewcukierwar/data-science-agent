"""Typed local context and shared runtime contracts for analysis agents."""

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestration.budgets import (
    BudgetResource,
    BudgetSnapshot,
    RunBudgetManager,
)
from orchestration.ledger import AnalysisLedger
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import Workspace


class AgentRole(StrEnum):
    """Roles whose tool surfaces are constrained by the project plan."""

    LEAD = "lead"
    GENERALIST = "generalist"
    DATA_AUDITOR = "data_auditor"
    ANALYST = "analyst"
    STATISTICIAN = "statistician"
    CRITIC = "critic"


DEFAULT_AGENT_TURN_LIMITS: Mapping[AgentRole, int] = MappingProxyType(
    {
        AgentRole.LEAD: 16,
        # Keep the default primary-agent turn cap aligned with Lead. A
        # benchmark may explicitly override this architecture's cap, but the
        # difference must remain visible in its run configuration.
        AgentRole.GENERALIST: 16,
        AgentRole.DATA_AUDITOR: 12,
        AgentRole.ANALYST: 10,
        AgentRole.STATISTICIAN: 10,
        AgentRole.CRITIC: 8,
    }
)


def normalize_agent_turn_limits(
    limits: Mapping[AgentRole | str, int] | None = None,
) -> dict[AgentRole, int]:
    """Merge and validate configurable role-specific turn limits."""

    normalized = dict(DEFAULT_AGENT_TURN_LIMITS)
    if limits is None:
        return normalized
    if not isinstance(limits, Mapping):
        raise ValueError("agent_turn_limits must be a mapping by agent role")
    for role, limit in limits.items():
        normalized_role = AgentRole(role)
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 50
        ):
            raise ValueError(
                f"turn limit for {normalized_role.value} must be an integer "
                "between 1 and 50"
            )
        normalized[normalized_role] = limit
    return normalized


_TOOL_PERMISSIONS: dict[AgentRole, frozenset[str]] = {
    AgentRole.LEAD: frozenset(
        {
            "inspect_workspace",
            "read_document",
            "save_artifact",
            "update_investigation_plan",
            "record_hypothesis",
            "record_open_question",
        }
    ),
    # The baseline owns every deterministic primitive used by the five-agent
    # architecture, plus the observable planning state tools.  This is a
    # capability union, not a delegation surface: no specialist-as-tool or
    # handoff is registered for this role.
    AgentRole.GENERALIST: frozenset(
        {
            "inspect_workspace",
            "read_document",
            "inspect_relations",
            "run_sql",
            "run_python",
            "save_artifact",
            "inspect_evidence",
            "update_investigation_plan",
            "record_hypothesis",
            "record_open_question",
        }
    ),
    AgentRole.DATA_AUDITOR: frozenset(
        {
            "inspect_workspace",
            "read_document",
            "inspect_relations",
            "run_sql",
            "run_python",
        }
    ),
    AgentRole.ANALYST: frozenset(
        {
            "inspect_workspace",
            "read_document",
            "inspect_relations",
            "run_sql",
            "run_python",
            "save_artifact",
        }
    ),
    AgentRole.STATISTICIAN: frozenset({"read_document", "run_python"}),
    AgentRole.CRITIC: frozenset(
        {
            "inspect_workspace",
            "read_document",
            "inspect_relations",
            "run_sql",
            "run_python",
            "inspect_evidence",
        }
    ),
}


def allowed_tools_for_role(role: AgentRole | str) -> frozenset[str]:
    """Return the immutable tool permission set for an agent role."""

    return _TOOL_PERMISSIONS[AgentRole(role)]


# A strict-schema-valid candidate whose citations do not resolve may receive one
# explicit correction attempt. The upper bound lives beside the configuration
# field that validates it, so no caller can turn a bounded correction into
# repeated resampling, and the benchmark can freeze the configured value in a
# manifest without importing the correction machinery.
DEFAULT_EVIDENCE_CORRECTION_ATTEMPTS = 1
MAX_EVIDENCE_CORRECTION_ATTEMPTS = 1


class AgentRunConfig(BaseModel):
    """Model and bounded-output configuration for one agent run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    attempt_id: str | None = Field(default=None, min_length=1)
    agent_role: AgentRole
    model: str = Field(default="configured-model", min_length=1)
    model_provider: str = Field(default="openai", min_length=1)
    max_workspace_files: int = Field(default=100, ge=1, le=1_000)
    max_result_rows: int = Field(default=100, ge=1, le=1_000)
    max_text_chars: int = Field(default=4_000, ge=256, le=100_000)
    max_document_chars: int = Field(default=16_000, ge=256, le=1_000_000)
    agent_turn_limits: dict[AgentRole, int] = Field(
        default_factory=lambda: dict(DEFAULT_AGENT_TURN_LIMITS)
    )
    evidence_correction_attempts: int = Field(
        default=DEFAULT_EVIDENCE_CORRECTION_ATTEMPTS,
        ge=0,
        le=MAX_EVIDENCE_CORRECTION_ATTEMPTS,
    )

    @field_validator("agent_turn_limits", mode="before")
    @classmethod
    def validate_agent_turn_limits(
        cls,
        value: Mapping[AgentRole | str, int] | None,
    ) -> dict[AgentRole, int]:
        """Accept partial overrides while retaining safe role defaults."""

        return normalize_agent_turn_limits(value)

    def turn_limit_for(self, role: AgentRole | str) -> int:
        """Return the configured SDK turn limit for one agent role."""

        return self.agent_turn_limits[AgentRole(role)]

    @property
    def turn_limit(self) -> int:
        """Return this context's role-specific SDK turn limit."""

        return self.turn_limit_for(self.agent_role)


class PermissionDeniedError(PermissionError):
    """Raised when an agent role attempts a tool outside its boundary."""

    code = "permission_denied"

    def __init__(self, role: AgentRole, tool_name: str) -> None:
        self.role = role
        self.tool_name = tool_name
        super().__init__(
            f"role '{role.value}' is not permitted to use tool '{tool_name}'"
        )


class ToolError(BaseModel):
    """Concise model-visible error information from a deterministic tool."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ToolResponse(BaseModel):
    """Uniform structured response returned by every runtime function tool."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1)
    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: ToolError | None = None

    @classmethod
    def ok(cls, tool_name: str, data: dict[str, Any]) -> "ToolResponse":
        """Build a successful tool response."""

        return cls(tool_name=tool_name, success=True, data=data)

    @classmethod
    def failed(
        cls,
        tool_name: str,
        code: str,
        message: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> "ToolResponse":
        """Build a concise structured failure response."""

        return cls(
            tool_name=tool_name,
            success=False,
            data=data or {},
            error=ToolError(code=code, message=message),
        )


@dataclass(slots=True)
class AgentRunContext:
    """All local dependencies and configuration for one SDK agent run.

    The context is passed to the Agents SDK ``Runner`` and is never sent to
    the model. Every deterministic tool resolves its dependencies from this
    object rather than from module-level state.
    """

    workspace: Workspace
    ledger: AnalysisLedger
    sql_service: DuckDBExecutionService
    python_service: PythonExecutionService
    artifact_manager: ArtifactManager
    run_config: AgentRunConfig
    # Set for the duration of one agent run so response-boundary hooks can
    # persist provider usage as it arrives. See ``agents.model_usage``.
    usage_recorder: Any | None = field(default=None, init=False, repr=False)
    budget_manager: RunBudgetManager = field(init=False, repr=False)
    _role_stack: ContextVar[tuple[AgentRole, ...]] = field(
        default_factory=lambda: ContextVar("agent_role_stack", default=()),
        init=False,
        repr=False,
    )
    _tool_role: ContextVar[AgentRole | None] = field(
        default_factory=lambda: ContextVar("agent_tool_role", default=None),
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        """Reject cross-run dependencies that could break isolation."""

        self.budget_manager = RunBudgetManager(self.ledger)
        workspace_root = self.workspace.root.resolve()
        for service_name in ("sql_service", "python_service", "artifact_manager"):
            service = getattr(self, service_name)
            if service.workspace.root.resolve() != workspace_root:
                raise ValueError(f"{service_name} is bound to a different workspace")
            service_ledger = getattr(service, "ledger", None)
            if service_ledger is not self.ledger:
                raise ValueError(f"{service_name} must use this run's ledger")
        if self.run_config.run_id != self.ledger.state.run_id:
            raise ValueError("run_config.run_id must match the ledger run_id")
        if (
            self.run_config.attempt_id is not None
            and self.run_config.attempt_id != self.ledger.state.attempt_id
        ):
            raise ValueError("run_config.attempt_id must match the ledger attempt_id")

    @property
    def agent_role(self) -> AgentRole:
        """Role controlling this context's available tools."""

        stack = self._role_stack.get()
        if stack:
            return stack[-1]
        return self._tool_role.get() or self.run_config.agent_role

    def bind_tool_agent(self, agent: object | None) -> None:
        """Bind a tool invocation task to the SDK agent executing it.

        The Agents SDK invokes function tools in child tasks. Their context
        variables inherit the parent task before nested-agent hooks run, so the
        hook's role scope alone cannot identify the nested agent there. The SDK
        supplies the active public agent on ``ToolContext``; binding its stable
        application role keeps permissions correct without shared mutable role
        state.
        """

        name = getattr(agent, "name", None)
        role_by_name = {
            "Lead Data Scientist": AgentRole.LEAD,
            "Generalist Data Scientist": AgentRole.GENERALIST,
            "Data Auditor": AgentRole.DATA_AUDITOR,
            "Analyst": AgentRole.ANALYST,
            "Statistician": AgentRole.STATISTICIAN,
            "Critic": AgentRole.CRITIC,
        }
        role = role_by_name.get(name)
        if role is not None:
            self._tool_role.set(role)

    def enter_nested_role(self, role: AgentRole | str) -> None:
        """Activate a specialist permission boundary for a nested agent tool."""

        stack = self._role_stack.get()
        self._role_stack.set((*stack, AgentRole(role)))

    def exit_nested_role(self, role: AgentRole | str) -> None:
        """Restore the parent permission boundary after a nested agent tool."""

        expected_role = AgentRole(role)
        stack = self._role_stack.get()
        if not stack:
            return
        if stack[-1] is not expected_role:
            raise RuntimeError("nested agent role stack is out of order")
        self._role_stack.set(stack[:-1])

    def assert_base_role(self, role: AgentRole | str | None = None) -> None:
        """Assert that no nested role remains active at a lifecycle boundary."""

        expected_role = (
            AgentRole(role) if role is not None else self.run_config.agent_role
        )
        if self._role_stack.get() or self.agent_role is not expected_role:
            raise RuntimeError(
                f"agent context is not restored to {expected_role.value}"
            )

    def allowed_tools(self) -> frozenset[str]:
        """Return the role's permitted tool names."""

        return allowed_tools_for_role(self.agent_role)

    def require_permission(self, tool_name: str) -> None:
        """Raise if the current role cannot call ``tool_name``."""

        if tool_name not in self.allowed_tools():
            raise PermissionDeniedError(self.agent_role, tool_name)

    def check_budget(self, resource: BudgetResource | str) -> BudgetSnapshot:
        """Check capacity before a service performs a counted operation."""

        return self.budget_manager.check(resource)

    def consume_budget(self, resource: BudgetResource | str) -> BudgetSnapshot:
        """Atomically reserve one counted operation before work begins."""

        return self.budget_manager.consume(resource)

    def consume_budgets(
        self,
        *resources: BudgetResource | str,
    ) -> tuple[BudgetSnapshot, ...]:
        """Atomically reserve several counted operations."""

        return self.budget_manager.consume_many(*resources)

    def record_specialist_invocation(self) -> BudgetSnapshot:
        """Consume one specialist-invocation budget unit."""

        return self.consume_budget(BudgetResource.SPECIALIST_INVOCATIONS)

    def record_critic_loop(self) -> BudgetSnapshot:
        """Consume one critic-loop budget unit."""

        return self.consume_budget(BudgetResource.CRITIC_LOOPS)

    def record_sdk_usage(self, usage: object) -> None:
        """Persist one Agents SDK usage snapshot for this run."""

        self.ledger.record_model_usage(usage)
