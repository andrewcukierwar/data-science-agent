"""Typed local context and shared runtime contracts for analysis agents."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
    DATA_AUDITOR = "data_auditor"
    ANALYST = "analyst"
    STATISTICIAN = "statistician"
    CRITIC = "critic"


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
    AgentRole.DATA_AUDITOR: frozenset(
        {"inspect_workspace", "read_document", "run_sql", "run_python"}
    ),
    AgentRole.ANALYST: frozenset(
        {
            "inspect_workspace",
            "read_document",
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
            "run_sql",
            "run_python",
            "inspect_evidence",
        }
    ),
}


def allowed_tools_for_role(role: AgentRole | str) -> frozenset[str]:
    """Return the immutable tool permission set for an agent role."""

    return _TOOL_PERMISSIONS[AgentRole(role)]


class AgentRunConfig(BaseModel):
    """Model and bounded-output configuration for one agent run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    agent_role: AgentRole
    model: str = Field(default="configured-model", min_length=1)
    model_provider: str = Field(default="openai", min_length=1)
    max_workspace_files: int = Field(default=100, ge=1, le=1_000)
    max_result_rows: int = Field(default=100, ge=1, le=1_000)
    max_text_chars: int = Field(default=4_000, ge=256, le=100_000)
    max_document_chars: int = Field(default=16_000, ge=256, le=1_000_000)
    max_agent_turns: int = Field(default=10, ge=1, le=50)


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
    budget_manager: RunBudgetManager = field(init=False, repr=False)
    _role_stack: list[AgentRole] = field(default_factory=list, init=False, repr=False)

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

    @property
    def agent_role(self) -> AgentRole:
        """Role controlling this context's available tools."""

        return self._role_stack[-1] if self._role_stack else self.run_config.agent_role

    def enter_nested_role(self, role: AgentRole | str) -> None:
        """Activate a specialist permission boundary for a nested agent tool."""

        self._role_stack.append(AgentRole(role))

    def exit_nested_role(self, role: AgentRole | str) -> None:
        """Restore the parent permission boundary after a nested agent tool."""

        expected_role = AgentRole(role)
        if not self._role_stack or self._role_stack[-1] is not expected_role:
            raise RuntimeError("nested agent role stack is out of order")
        self._role_stack.pop()

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
        """Consume one counted operation after successful preparation."""

        return self.budget_manager.consume(resource)

    def record_specialist_invocation(self) -> BudgetSnapshot:
        """Consume one specialist-invocation budget unit."""

        return self.consume_budget(BudgetResource.SPECIALIST_INVOCATIONS)

    def record_critic_loop(self) -> BudgetSnapshot:
        """Consume one critic-loop budget unit."""

        return self.consume_budget(BudgetResource.CRITIC_LOOPS)

    def record_sdk_usage(self, usage: object) -> None:
        """Persist one Agents SDK usage snapshot for this run."""

        self.ledger.record_model_usage(usage)
