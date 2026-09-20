"""Field-level references to retained deterministic execution output."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.common import NonEmptyString


class ComputedField(BaseModel):
    """One numerical claim field supplied by an exact JSON pointer."""

    model_config = ConfigDict(extra="forbid")

    field: NonEmptyString
    pointer: str = Field(pattern=r"^/")


class ComputationBinding(BaseModel):
    """SQL pointers address output.rows; Python pointers address JSON stdout."""

    model_config = ConfigDict(extra="forbid")

    tool_event_id: NonEmptyString
    source: Literal["sql_rows", "python_stdout_json"]
    fields: list[ComputedField] = Field(min_length=1)


class BoundNumericalClaim(BaseModel):
    """Legacy values remain readable; bound wire outputs may omit numbers."""

    numerical_fields: ClassVar[tuple[str, ...]] = ()
    result_id: NonEmptyString | None = None
    computation: ComputationBinding | None = None

    @model_validator(mode="after")
    def unbound_records_need_values(self) -> "BoundNumericalClaim":
        if self.computation is None:
            for field in self.numerical_fields:
                if getattr(self, field.split(".")[0]) is None:
                    raise ValueError(f"unbound record requires {field}")
        return self
