"""R13 regressions for strictly structured analytical agent output.

The retained Task 10 pilots failed with invalid final-output JSON while every
analytical agent still opted out of strict schema mode and re-parsed whatever
the model returned. These tests hold the replacement contract: every production
output type compiles through the installed SDK strict converter, no production
agent opts out, the typed dimension representation round-trips without changing
the estimand, and malformed output is an explicit model/schema failure.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from agents.exceptions import ModelBehaviorError, UserError
from agents.strict_schema import ensure_strict_json_schema
from pydantic import ValidationError

from agents import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    build_analyst_agent,
    build_critic_agent,
    build_data_auditor_agent,
    build_generalist_agent,
    build_lead_agent,
    build_statistician_agent,
    run_analyst,
    run_generalist,
    run_lead,
    run_statistician,
)
from agents.lead import _canonical_specialist_output
from agents.output_contract import (
    PRODUCTION_AGENT_OUTPUT_TYPES,
    AgentOutputContractError,
    require_strict_output,
    strict_output_type,
)
from evaluation.primitives import select_metric_candidates
from orchestration.ledger import AnalysisLedger
from scenarios.definitions.models import GroundTruthMetric
from schemas.audit import AuditResult, AuditStatus
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.metrics import (
    MetricComparison,
    MetricComparisonType,
    MetricDimension,
    metric_comparison_identity,
    normalize_metric_comparison,
    normalize_metric_dimensions,
)
from schemas.run_state import ToolEvent, ToolEventStatus
from schemas.statistics import StatisticalAssessment
from schemas.validation import ValidationResult, ValidationStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)

_AGENT_BUILDERS = {
    AgentRole.LEAD: build_lead_agent,
    AgentRole.GENERALIST: build_generalist_agent,
    AgentRole.DATA_AUDITOR: build_data_auditor_agent,
    AgentRole.ANALYST: build_analyst_agent,
    AgentRole.STATISTICIAN: build_statistician_agent,
    AgentRole.CRITIC: build_critic_agent,
}


def _comparison(**overrides: object) -> MetricComparison:
    payload: dict[str, object] = {
        "metric_key": "cac",
        "dimensions": [MetricDimension(name="channel", value="Meta")],
        "baseline_period": "Q1 2025",
        "comparison_period": "Q2 2025",
        "comparison_type": MetricComparisonType.RELATIVE_CHANGE,
        "value": 0.3,
        "unit": "relative_change_fraction",
        "evidence_refs": ["tool-sql"],
    }
    payload.update(overrides)
    return MetricComparison.model_validate(payload)


def _specialist_result() -> SpecialistResult:
    return SpecialistResult(
        objective="Compare the acquisition periods.",
        findings=[
            Finding(
                id="F1",
                statement="Meta CAC increased.",
                metric="cac",
                value=0.3,
                evidence_refs=["working/queries/Q001.sql"],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
        metric_comparisons=[_comparison()],
    )


def _context(tmp_path: Path, role: AgentRole, run_id: str) -> AgentRunContext:
    input_source = tmp_path / "input-source"
    input_source.mkdir(exist_ok=True)
    pd.DataFrame({"observed_value": [1]}).to_parquet(
        input_source / "customers.parquet",
        index=False,
    )
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        run_id,
        inputs_source=input_source,
    )
    query = workspace.working / "queries" / "Q001.sql"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.write_text("SELECT 1;\n", encoding="utf-8")
    ledger = AnalysisLedger(workspace, objective="Explain the observed change.")
    ledger.append_tool_event(
        ToolEvent(
            id="tool-sql",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q001"},
            output={"rows": [{"observed_value": 1}]},
            artifact_refs=["working/queries/Q001.sql"],
        )
    )
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id=run_id,
            agent_role=role,
            model="test-model",
        ),
    )


# --- every production output type compiles through the strict converter -----


@pytest.mark.parametrize(
    ("role", "output_type"),
    sorted(
        PRODUCTION_AGENT_OUTPUT_TYPES.items(),
        key=lambda item: item[0].value,
    ),
    ids=lambda value: value.value if isinstance(value, AgentRole) else value.__name__,
)
def test_every_production_output_type_compiles_strictly(
    role: AgentRole,
    output_type: type,
) -> None:
    schema = strict_output_type(output_type)

    assert schema.output_type is output_type
    assert schema.is_strict_json_schema() is True
    # The SDK converter is the authority; compile the raw model schema too so a
    # regression cannot be hidden by a cached AgentOutputSchema.
    ensure_strict_json_schema(output_type.model_json_schema())


@pytest.mark.parametrize(
    "role",
    sorted(_AGENT_BUILDERS, key=lambda item: item.value),
    ids=lambda role: role.value,
)
def test_no_production_agent_opts_out_of_strict_schema(role: AgentRole) -> None:
    agent = _AGENT_BUILDERS[role](model="test-model")

    assert agent.output_type.output_type is PRODUCTION_AGENT_OUTPUT_TYPES[role]
    assert agent.output_type.is_strict_json_schema() is True
    assert agent.output_type.json_schema()["additionalProperties"] is False


def test_strict_schema_declares_dimensions_as_typed_objects() -> None:
    schema = strict_output_type(SpecialistResult).json_schema()
    dimension = schema["$defs"]["MetricDimension"]

    assert dimension["additionalProperties"] is False
    assert sorted(dimension["required"]) == ["name", "value"]
    comparison = schema["$defs"]["MetricComparison"]
    assert comparison["properties"]["dimensions"]["type"] == "array"


def test_strict_output_type_rejects_an_open_ended_output() -> None:
    from pydantic import BaseModel, ConfigDict

    class OpenEnded(BaseModel):
        model_config = ConfigDict(extra="forbid")

        dimensions: dict[str, str]

    with pytest.raises(UserError, match="not valid"):
        strict_output_type(OpenEnded)


# --- dimension round-trip determinism --------------------------------------


def test_typed_dimensions_round_trip_without_changing_the_estimand() -> None:
    typed = _comparison(
        dimensions=[
            MetricDimension(name="channel", value="Meta"),
            MetricDimension(name="cohort", value="acquired customers"),
        ]
    )
    legacy = _comparison(
        dimensions={"acquisition_channel": "Meta", "cohort": "acquired customers"}
    )

    round_tripped = MetricComparison.model_validate_json(typed.model_dump_json())

    assert round_tripped == typed
    assert normalize_metric_comparison(round_tripped).dimensions == [
        MetricDimension(name="channel", value="Meta"),
        MetricDimension(name="cohort", value="acquired customers"),
    ]
    # The legacy persisted mapping form and the strict typed form describe the
    # same estimand, so the evaluator identity is unchanged.
    assert metric_comparison_identity(legacy) == metric_comparison_identity(typed)


def test_retained_workspaces_with_legacy_dimension_maps_still_load(
    tmp_path: Path,
) -> None:
    """Offline evaluation of retained pilot evidence must keep working."""

    context = _context(tmp_path, AgentRole.ANALYST, "run-legacy-dimensions")
    ledger = context.ledger
    ledger.upsert_metric_comparison(_comparison())

    state = json.loads(ledger.state_path.read_text(encoding="utf-8"))
    persisted = state["metric_comparisons"][0]
    assert persisted["dimensions"] == [{"name": "channel", "value": "Meta"}]
    persisted["dimensions"] = {"channel": "Meta"}
    ledger.state_path.write_text(json.dumps(state), encoding="utf-8")

    reloaded = AnalysisLedger(ledger.state_path)

    assert reloaded.metric_comparisons[0].dimensions == [
        MetricDimension(name="channel", value="Meta")
    ]
    assert metric_comparison_identity(
        reloaded.metric_comparisons[0]
    ) == metric_comparison_identity(_comparison())


def test_dimension_normalization_is_order_independent_and_deterministic() -> None:
    forward = normalize_metric_dimensions(
        [
            MetricDimension(name="Acquisition Channel", value=" Meta "),
            MetricDimension(name="device", value="mobile"),
        ]
    )
    reversed_order = normalize_metric_dimensions(
        [
            MetricDimension(name="device", value="mobile"),
            MetricDimension(name="acquisition_channel", value="Meta"),
        ]
    )

    assert forward == reversed_order
    assert forward == [
        MetricDimension(name="channel", value="Meta"),
        MetricDimension(name="device", value="mobile"),
    ]


def test_repeated_dimension_names_are_rejected_as_ambiguous() -> None:
    with pytest.raises(ValidationError, match="duplicate metric dimension name"):
        _comparison(
            dimensions=[
                MetricDimension(name="channel", value="Meta"),
                MetricDimension(name="Channel", value="Google"),
            ]
        )


def test_evaluator_selection_is_identical_for_both_dimension_forms() -> None:
    expected = GroundTruthMetric(
        id="GT1",
        description="Meta CAC change",
        comparison="q1_to_q2",
        metric_key="cac",
        dimensions={"channel": "Meta"},
        baseline_period="Q1 2025",
        comparison_period="Q2 2025",
        comparison_type=MetricComparisonType.RELATIVE_CHANGE,
        expected_relative_change=0.3,
        tolerance=0.01,
    )
    typed_expected = GroundTruthMetric(
        **{
            **expected.model_dump(),
            "dimensions": [{"name": "channel", "value": "Meta"}],
        }
    )

    candidates = select_metric_candidates([_comparison()], expected)
    typed_candidates = select_metric_candidates([_comparison()], typed_expected)

    assert [candidate.value for candidate in candidates] == [0.3]
    assert candidates == typed_candidates


def test_statistical_assessment_dimensions_round_trip(tmp_path: Path) -> None:
    payload = {
        "metric_key": "conversion_rate",
        "dimensions": [{"name": "experiment", "value": "checkout-v1"}],
        "baseline_period": "control",
        "comparison_period": "treatment",
        "method": "two-proportion z test",
        "unit_of_analysis": "session",
        "conclusion": "not_statistically_significant",
        "confidence_level": 0.95,
        "estimate": 0.0,
        "confidence_interval": {"lower": -0.1, "upper": 0.1},
        "p_value": 0.4,
        "effect_size": 0.0,
        "practical_significance_threshold": 0.01,
        "practically_significant": False,
        "assumptions_checked": ["random assignment"],
        "causal_interpretation": "causal_effect_supported",
        "evidence_refs": ["tool-sql"],
    }
    assessment = StatisticalAssessment.model_validate(payload)

    assert assessment.dimensions == [
        MetricDimension(name="experiment", value="checkout-v1")
    ]
    assert (
        StatisticalAssessment.model_validate_json(assessment.model_dump_json())
        == assessment
    )


# --- malformed output is an explicit model/schema failure -------------------


def test_valid_strict_fixture_parses_for_every_output_type() -> None:
    fixtures: dict[type, str] = {
        AuditResult: AuditResult(
            status=AuditStatus.COMPLETE, audited_at=_STAMP
        ).model_dump_json(),
        SpecialistResult: _specialist_result().model_dump_json(),
        ValidationResult: ValidationResult(
            status=ValidationStatus.PASS
        ).model_dump_json(),
    }
    for output_type, payload in fixtures.items():
        assert isinstance(output_type.model_validate_json(payload), output_type)


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("malformed", "{not json at all"),
        (
            "truncated",
            _specialist_result().model_dump_json()[:-25],
        ),
        (
            "extra_field",
            '{"objective": "o", "findings": [], "metric_comparisons": [], '
            '"statistical_assessments": [], "artifacts": [], "methods_used": [], '
            '"follow_up_questions": [], "caveats": [], "unexpected": 1}',
        ),
        (
            "open_ended_dimensions_with_duplicate_names",
            '{"objective": "o", "metric_comparisons": [{"metric_key": "cac", '
            '"dimensions": [{"name": "channel", "value": "Meta"}, '
            '{"name": "channel", "value": "Google"}], '
            '"baseline_period": "Q1", "comparison_period": "Q2", '
            '"comparison_type": "relative_change", "value": 0.3, '
            '"unit": "relative_change_fraction", "evidence_refs": ["tool-sql"]}]}',
        ),
    ],
)
def test_invalid_specialist_payloads_never_parse(label: str, payload: str) -> None:
    with pytest.raises(ValidationError):
        SpecialistResult.model_validate_json(payload)


@pytest.mark.parametrize(
    "output",
    ['{"objective": "o"}', None, {"objective": "o"}, 42],
)
def test_require_strict_output_raises_an_explicit_model_failure(
    output: object,
) -> None:
    with pytest.raises(AgentOutputContractError) as raised:
        require_strict_output(output, SpecialistResult, agent_name="Analyst")

    assert isinstance(raised.value, ModelBehaviorError)
    assert "SpecialistResult" in str(raised.value)


def test_specialist_output_extractor_fails_on_malformed_model_output() -> None:
    extract = _canonical_specialist_output(AgentRole.ANALYST, "Analyst")

    with pytest.raises(AgentOutputContractError):
        asyncio.run(extract(SimpleNamespace(final_output="{not json at all")))

    namespaced = asyncio.run(
        extract(SimpleNamespace(final_output=_specialist_result()))
    )
    assert "analyst:F1" in namespaced


@pytest.mark.parametrize(
    ("role", "run_id", "runner"),
    [
        (AgentRole.ANALYST, "run-strict-analyst", run_analyst),
        (AgentRole.STATISTICIAN, "run-strict-statistician", run_statistician),
    ],
    ids=("analyst", "statistician"),
)
def test_specialist_runs_reject_unparsed_final_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: AgentRole,
    run_id: str,
    runner: object,
) -> None:
    context = _context(tmp_path, role, run_id)

    async def fake_run(agent, objective, *, context, **kwargs):  # noqa: ANN001
        assert agent.output_type.is_strict_json_schema() is True
        return SimpleNamespace(final_output='{"objective": "truncated"')

    monkeypatch.setattr("agents.analyst.Runner.run", fake_run)
    monkeypatch.setattr("agents.statistician.Runner.run", fake_run)

    with pytest.raises(AgentOutputContractError):
        asyncio.run(runner(context, "Compare the periods."))


def test_lead_run_rejects_unparsed_final_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, AgentRole.LEAD, "run-strict-lead")

    async def fake_run(agent, prompt, *, context, **kwargs):  # noqa: ANN001
        assert agent.output_type.output_type is LeadResult
        return SimpleNamespace(final_output={"objective": "no answer field"})

    monkeypatch.setattr("agents.lead.Runner.run", fake_run)

    with pytest.raises(AgentOutputContractError):
        asyncio.run(run_lead(context, "Explain the observed change."))


def test_generalist_run_rejects_unparsed_final_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, AgentRole.GENERALIST, "run-strict-generalist")

    async def fake_run(agent, prompt, *, context, **kwargs):  # noqa: ANN001
        assert agent.output_type.output_type is GeneralistResult
        return SimpleNamespace(final_output="}{ truncated generalist output")

    monkeypatch.setattr("agents.generalist.Runner.run", fake_run)

    with pytest.raises(AgentOutputContractError):
        asyncio.run(run_generalist(context, "Explain the observed change."))
