"""Explicit analytical choices and retained computed records (contract 1.0)."""

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.common import NonEmptyString


class AnalyticalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Period(AnalyticalModel):
    """Calendar dates, start included and end excluded."""

    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("period end must follow start")
        return self


class Window(AnalyticalModel):
    """Days from each entity's cohort date; start included, end excluded."""

    start_day: int = Field(ge=0)
    end_day: int = Field(gt=0)
    observed_until: date  # exclusive source availability boundary, never inferred
    maturity: Literal["require_complete", "include_partial", "exclude_incomplete"]

    @model_validator(mode="after")
    def ordered(self):
        if self.end_day <= self.start_day:
            raise ValueError("window end must follow start")
        return self


class TableInput(AnalyticalModel):
    """A complete retained SQL execution, never model-supplied rows."""

    tool_event_id: NonEmptyString
    relation: NonEmptyString  # public source role/name, retained with the mapping


class NumericPolicy(AnalyticalModel):
    """Exact rational arithmetic; explicit projection to legacy float claims."""

    projection: Literal["exact_only", "nearest_binary64"]


class Scope(AnalyticalModel):
    population: NonEmptyString
    grain: NonEmptyString
    period: Period
    dimensions: dict[str, str] = Field(default_factory=dict)
    window: Window | None = None
    semantics: dict[str, Any] = Field(default_factory=dict)


class Request(AnalyticalModel):
    population: NonEmptyString
    grain: NonEmptyString
    period: Period
    numeric: NumericPolicy
    date_policy: Literal["date_only", "naive_date", "utc_date"] = "date_only"


class CoverageRequest(Request):
    operation: Literal["coverage"] = "coverage"
    source: TableInput
    temporal_field: NonEmptyString
    cadence: Literal["daily"] | None = None
    expected_dates: tuple[date, ...] | None = None
    dimension_field: str | None = None
    expected_dimensions: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def expectations(self):
        if self.expected_dates is not None:
            if self.cadence is not None or not self.expected_dates:
                raise ValueError("use explicit dates or cadence, not both")
            if len(set(self.expected_dates)) != len(self.expected_dates):
                raise ValueError("duplicate expected dates")
            if any(
                not self.period.start <= d < self.period.end
                for d in self.expected_dates
            ):
                raise ValueError("expected date outside period")
        if self.expected_dimensions is not None:
            if not self.dimension_field or not self.expected_dimensions:
                raise ValueError("expected dimension values require a dimension field")
            if len(set(self.expected_dimensions)) != len(self.expected_dimensions):
                raise ValueError("duplicate expected dimensions")
        if (
            (self.cadence or self.expected_dates)
            and self.dimension_field
            and self.expected_dimensions is None
        ):
            raise ValueError("expected grid requires explicit dimension values")
        return self


class Measure(AnalyticalModel):
    """Entity-local components, evaluated before cross-entity arithmetic."""

    kind: Literal[
        "entity_count", "entity_value", "event_count", "event_sum", "event_at_least"
    ]
    semantic_key: NonEmptyString
    unit: NonEmptyString
    column: str | None = None
    threshold: int | None = Field(default=None, ge=1)
    nulls: Literal["error", "zero"] = "error"

    @model_validator(mode="after")
    def shape(self):
        if (self.kind in {"entity_value", "event_sum"}) != (self.column is not None):
            raise ValueError("value/sum measures require a column; counts do not")
        if (self.kind == "event_at_least") != (self.threshold is not None):
            raise ValueError("event_at_least requires a threshold")
        return self


class EntityAggregateRequest(Request):
    operation: Literal["entity_aggregate"] = "entity_aggregate"
    entities: TableInput
    entity_key: NonEmptyString
    cohort_date: NonEmptyString
    events: TableInput | None = None
    event_key: str | None = None
    event_entity_key: str | None = None
    event_date: str | None = None
    window: Window | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)  # semantic name -> column
    include_zero_activity: bool
    numerator: Measure
    denominator: Measure | None = None
    aggregation: Literal["sum", "mean", "ratio_of_sums", "mean_of_ratios"]
    zero_denominator: Literal["undefined", "error", "exclude"] = "undefined"
    unit: NonEmptyString

    @model_validator(mode="after")
    def choices(self):
        event_fields = (
            self.event_key,
            self.event_entity_key,
            self.event_date,
            self.window,
        )
        if self.events is not None and any(v is None for v in event_fields):
            raise ValueError("events require keys, date and observation window")
        if self.events is None and any(v is not None for v in event_fields):
            raise ValueError("event mappings require an event source")
        if self.events is None and not self.include_zero_activity:
            raise ValueError("without events, include_zero_activity must be true")
        if self.events is None and any(
            m and m.kind.startswith("event_")
            for m in (self.numerator, self.denominator)
        ):
            raise ValueError("event measure requires events")
        if self.aggregation in {"ratio_of_sums", "mean_of_ratios"}:
            if self.denominator is None:
                raise ValueError("ratio requires explicit denominator")
        elif self.denominator is not None:
            raise ValueError("sum/mean must not supply denominator")
        if self.zero_denominator == "exclude" and self.aggregation != "mean_of_ratios":
            raise ValueError("exclude zero denominators only for mean_of_ratios")
        return self


class QuantityRef(AnalyticalModel):
    tool_event_id: NonEmptyString
    result_index: int = Field(default=0, ge=0)
    quantity: NonEmptyString = "value"


class ContrastRequest(AnalyticalModel):
    operation: Literal["contrast"] = "contrast"
    baseline: QuantityRef
    comparison: QuantityRef | None = None
    comparison_type: Literal["level", "absolute_difference", "relative_change"]
    numeric: NumericPolicy

    @model_validator(mode="after")
    def operands(self):
        if (self.comparison_type == "level") != (self.comparison is None):
            raise ValueError("level takes one quantity; differences take two")
        return self


class RatioRequest(AnalyticalModel):
    """Ratio of independently aggregated compatible totals; no fact joins."""

    operation: Literal["ratio"] = "ratio"
    numerator: QuantityRef
    denominator: QuantityRef
    unit: NonEmptyString
    semantic_key: NonEmptyString
    numeric: NumericPolicy
    zero_denominator: Literal["undefined", "error"] = "undefined"


class ReconciliationRequest(Request):
    operation: Literal["reconciliation"] = "reconciliation"
    source: TableInput
    row_key: NonEmptyString
    temporal_field: NonEmptyString
    result_column: NonEmptyString
    components: dict[str, Decimal] = Field(min_length=1)  # column -> signed coefficient
    intercept: Decimal = Decimal(0)
    unit: NonEmptyString
    absolute_tolerance: Decimal = Field(ge=0)
    relative_tolerance: Decimal = Field(ge=0)


class BinaryExperimentRequest(Request):
    operation: Literal["binary_experiment"] = "binary_experiment"
    source: TableInput
    subject_id: NonEmptyString
    assignment: NonEmptyString
    temporal_field: NonEmptyString
    control: NonEmptyString
    treatment: NonEmptyString
    outcome: NonEmptyString
    confidence_level: float = Field(gt=0, lt=1)
    practical_threshold: Decimal = Field(ge=0, le=1)
    design_assumptions: tuple[NonEmptyString, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def direction(self):
        if self.control == self.treatment:
            raise ValueError("control and treatment must differ")
        return self


class ExactNumber(AnalyticalModel):
    numerator: str = Field(pattern=r"^-?\d+$")
    denominator: str = Field(pattern=r"^[1-9]\d*$")


class Quantity(AnalyticalModel):
    value: int | float | bool | None
    unit: NonEmptyString
    exact: ExactNumber | None = None
    projection_error: ExactNumber | None = None
    representation: Literal[
        "exact", "nearest_binary64", "statistical_binary64", "boolean", "undefined"
    ]
    reason: str | None = None


class AnalyticalResult(AnalyticalModel):
    scope: Scope
    method: NonEmptyString
    quantities: dict[str, Quantity]
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] = Field(default_factory=dict)


class InputBinding(AnalyticalModel):
    tool_event_id: NonEmptyString
    output_digest: str


class AnalyticalRecord(AnalyticalModel):
    analytical_contract_version: Literal["1.0"] = "1.0"
    tool_event_id: NonEmptyString
    result_id: NonEmptyString
    operation: NonEmptyString
    request: dict[str, Any]
    inputs: tuple[InputBinding, ...]
    results: tuple[AnalyticalResult, ...]
