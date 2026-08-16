"""Shared schema typing primitives."""

from typing import Annotated

from pydantic import Field

NonEmptyString = Annotated[str, Field(min_length=1)]

__all__ = ["NonEmptyString"]
