"""Strict-output encoding for arbitrary retained JSON evidence values.

Provider schemas cannot express objects with arbitrary keys in strict mode, so
object members are represented as typed key/value entries on the wire. The
retained evidence record still serializes to the original JSON value.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JsonNull(_Node):
    kind: Literal["null"]
    value: None


class JsonBoolean(_Node):
    kind: Literal["boolean"]
    value: bool


class JsonInteger(_Node):
    kind: Literal["integer"]
    value: int


class JsonNumber(_Node):
    kind: Literal["number"]
    value: float = Field(allow_inf_nan=False)


class JsonString(_Node):
    kind: Literal["string"]
    value: str


type JsonEvidenceNode = (
    JsonNull
    | JsonBoolean
    | JsonInteger
    | JsonNumber
    | JsonString
    | JsonArray
    | JsonObject
)


class JsonArray(_Node):
    kind: Literal["array"]
    items: list[JsonEvidenceNode]


class JsonObjectEntry(_Node):
    key: str
    value: JsonEvidenceNode


class JsonObject(_Node):
    kind: Literal["object"]
    entries: list[JsonObjectEntry]

    @model_validator(mode="after")
    def unique_keys(self) -> JsonObject:
        keys = [entry.key for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("evidence object contains duplicate keys")
        return self


_NODE_TYPES = (
    JsonNull,
    JsonBoolean,
    JsonInteger,
    JsonNumber,
    JsonString,
    JsonArray,
    JsonObject,
)


def encode_json_evidence(value: object) -> JsonEvidenceNode:
    """Encode a native JSON value without coercing types or stringifying data."""

    if isinstance(value, _NODE_TYPES):
        return value
    if value is None:
        return JsonNull(kind="null", value=None)
    if isinstance(value, bool):
        return JsonBoolean(kind="boolean", value=value)
    if isinstance(value, int):
        return JsonInteger(kind="integer", value=value)
    if isinstance(value, float) and math.isfinite(value):
        return JsonNumber(kind="number", value=value)
    if isinstance(value, str):
        return JsonString(kind="string", value=value)
    if isinstance(value, list):
        return JsonArray(
            kind="array", items=[encode_json_evidence(item) for item in value]
        )
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return JsonObject(
            kind="object",
            entries=[
                JsonObjectEntry(key=key, value=encode_json_evidence(item))
                for key, item in value.items()
            ],
        )
    raise ValueError("evidence value must be a finite JSON value")


def decode_json_evidence(node: JsonEvidenceNode) -> object:
    """Recover the exact JSON value represented by a provider-facing node."""

    if isinstance(node, JsonArray):
        return [decode_json_evidence(item) for item in node.items]
    if isinstance(node, JsonObject):
        return {entry.key: decode_json_evidence(entry.value) for entry in node.entries}
    return node.value
