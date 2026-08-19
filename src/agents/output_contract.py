"""Strict structured-output contract shared by every production agent.

Every analytical agent declares a typed Pydantic output and the Agents SDK
enforces it as a strict JSON Schema. Strict mode removes the permissive
final-output path that silently re-parsed whatever the model returned: a
response that is malformed, truncated, or carries undeclared fields is a model
failure, not something the application repairs after the fact.
"""

from __future__ import annotations

from types import MappingProxyType

from pydantic import BaseModel

from agents import AgentOutputSchema
from agents.exceptions import ModelBehaviorError
from agents.runtime import AgentRole
from schemas.audit import AuditResult
from schemas.findings import SpecialistResult
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.validation import ValidationResult

STRUCTURED_DIMENSION_GUIDANCE = (
    "Segment dimensions are a list of typed {name, value} objects, for example "
    '[{"name": "channel", "value": "Meta"}]. Use an empty list for an '
    "unsegmented measurement and never repeat a dimension name in one "
    "measurement."
)


class AgentOutputContractError(ModelBehaviorError):
    """Raised when a model response does not satisfy its strict output type.

    Subclassing the SDK's ``ModelBehaviorError`` keeps schema violations inside
    the model-failure taxonomy instead of being reported as an application bug
    or, worse, being coerced into a partially valid result.
    """

    code = "agent_output_contract"

    def __init__(
        self,
        agent_name: str,
        output_type: type[BaseModel],
        output: object,
    ) -> None:
        self.agent_name = agent_name
        self.output_type = output_type
        self.observed_type = type(output).__name__
        super().__init__(
            f"{agent_name} did not return a valid {output_type.__name__}; "
            f"observed {self.observed_type} instead"
        )


def strict_output_type[OutputT: BaseModel](
    output_type: type[OutputT],
) -> AgentOutputSchema:
    """Build a strict output schema and fail immediately if it cannot compile.

    Compiling the schema at agent-construction time turns an incompatible
    output type into a deterministic local error rather than a paid request
    that the provider rejects.
    """

    schema = AgentOutputSchema(output_type)
    schema.json_schema()
    if not schema.is_strict_json_schema():
        raise ValueError(f"{output_type.__name__} must use a strict JSON schema")
    return schema


def require_strict_output[OutputT: BaseModel](
    output: object,
    output_type: type[OutputT],
    *,
    agent_name: str,
) -> OutputT:
    """Return the model output only when it satisfies the strict contract."""

    if not isinstance(output, output_type):
        raise AgentOutputContractError(agent_name, output_type, output)
    return output


PRODUCTION_AGENT_OUTPUT_TYPES: MappingProxyType[AgentRole, type[BaseModel]] = (
    MappingProxyType(
        {
            AgentRole.LEAD: LeadResult,
            AgentRole.GENERALIST: GeneralistResult,
            AgentRole.DATA_AUDITOR: AuditResult,
            AgentRole.ANALYST: SpecialistResult,
            AgentRole.STATISTICIAN: SpecialistResult,
            AgentRole.CRITIC: ValidationResult,
        }
    )
)


__all__ = [
    "PRODUCTION_AGENT_OUTPUT_TYPES",
    "STRUCTURED_DIMENSION_GUIDANCE",
    "AgentOutputContractError",
    "require_strict_output",
    "strict_output_type",
]
