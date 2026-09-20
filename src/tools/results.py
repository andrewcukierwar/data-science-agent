"""JSON representation shared by persisted and model-visible calculation results."""

import json
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

EXECUTION_RESULT_CONTRACT_VERSION = "1.0"
MAX_SQL_RESULT_BYTES = 1_000_000


def json_result_value(value: Any) -> Any:
    """Encode DuckDB values without precision loss or non-standard JSON numbers.

    Column types accompany SQL rows. Decimal and temporal values use exact
    strings; binary, intervals, non-finite floats, and non-string-keyed maps
    use explicit tags. Unknown values fail capture instead of using repr().
    """

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"type": "float", "value": str(value)}
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex()}
    if isinstance(value, timedelta):
        return {
            "type": "timedelta",
            "days": value.days,
            "seconds": value.seconds,
            "microseconds": value.microseconds,
        }
    if isinstance(value, (list, tuple)):
        return [json_result_value(item) for item in value]
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return {key: json_result_value(value[key]) for key in sorted(value)}
        entries = [
            [json_result_value(key), json_result_value(item)]
            for key, item in value.items()
        ]
        return {
            "type": "map",
            "entries": sorted(entries, key=lambda item: result_json(item[0])),
        }
    raise TypeError(f"unsupported calculation result type: {type(value).__name__}")


def result_json(value: Any) -> str:
    """Stable, strict JSON for result sizing and lossless inspection pages."""

    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
