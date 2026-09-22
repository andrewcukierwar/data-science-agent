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
    "measurement. For every metric comparison, statistical assessment, and "
    "Finding.value (when numerical), supply "
    "computation={tool_event_id, source, fields:[{field, pointer}]}. Use the exact "
    "execution ID. source=sql_rows addresses retained rows (e.g. /0/0); "
    "source=python_stdout_json addresses one complete JSON document printed on "
    "stdout (e.g. /p_value). Every numerical field must have its own pointer, "
    "including confidence_interval.lower/upper, confidence_level, effect_size, "
    "practical_significance_threshold and practically_significant for statistics. "
    "Set numerical fields and result_id to null on first output: application code "
    "copies computed values and assigns a stable result_id. Qualitative findings "
    "may leave value and computation null. Never retype numbers. "
    "Calculations must still derive from approved inputs. Lead can select full "
    "persisted specialist metrics/statistics using selected_result_ids, without "
    "recreating their fields. Only explicitly selected statistics are final; "
    "omit/reject superseded results. Retain material caveats and definition context. "
    "A run_analytical analytical_record_id identifies computation evidence; it is "
    "not a claim result_id and must not be placed in selected_result_ids."
)


DETERMINISTIC_ANALYTICAL_GUIDANCE = """Deterministic analytical workflow:

1. Inspect relation schemas and business definitions before choosing an operation.
2. Use narrow SQL projections and legitimate filters to create clean, complete
   retained sources with canonical tool_event_id references. Preserve the intended
   population. Never treat a truncated/sample result as complete, raise capture
   caps, or recombine hidden partitions. If the complete source cannot fit, disclose
   the limitation and use bounded SQL/Python that preserves provenance.
3. State the source, grain, date/cohort field, population, period, observation
   window, dimensions, numerator, denominator, aggregation, expected grid/cadence,
   reconciliation tolerances, or experiment arms/outcome/practical threshold as
   applicable, then call the role-approved run_analytical operation. Prefer it over
   manual arithmetic when it supports the requested calculation; retain SQL/Python
   for exploration, source construction, and unsupported calculations.
4. Inspect every returned scope, quantity, warning, and detail before using it.
   Entity counts must come from the entity-first operation, not post-join row counts.
   Include zero-activity entities when the estimand requires them. Missing dates
   require an explicit expected date/grid or cadence; sparse event data alone does
   not establish a reporting gap.
5. For a MetricComparison, StatisticalAssessment, or numerical Finding, bind every
   numerical field to the returned analytical_record /value pointer. Leave model
   numerical fields and result_id null. Application persistence hydrates the exact
   values and assigns a separate specialist claim result_id; Lead selects those
   persisted claim IDs, and the generalist uses the same finalizer."""


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
    "DETERMINISTIC_ANALYTICAL_GUIDANCE",
    "PRODUCTION_AGENT_OUTPUT_TYPES",
    "STRUCTURED_DIMENSION_GUIDANCE",
    "AgentOutputContractError",
    "require_strict_output",
    "strict_output_type",
]
