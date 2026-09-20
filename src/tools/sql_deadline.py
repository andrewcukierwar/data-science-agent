"""Cooperative native DuckDB deadlines, without abandoned query workers."""

import math
import threading
import time
from contextlib import contextmanager

import duckdb

DEFAULT_SQL_TIMEOUT_SECONDS = 30.0
MIN_SQL_TIMEOUT_SECONDS = 0.05
MAX_SQL_TIMEOUT_SECONDS = 120.0


def validate_sql_timeout(value: float) -> float:
    """Use one validation rule at service, runtime, and manifest boundaries."""

    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not MIN_SQL_TIMEOUT_SECONDS <= value <= MAX_SQL_TIMEOUT_SECONDS
    ):
        raise ValueError("sql_timeout_seconds must be a finite number from 0.05 to 120")
    return float(value)


class SQLTimeoutError(TimeoutError):
    """The deadline expired; no result from this execution may be published."""


class SQLCancelledError(RuntimeError):
    """The enclosing invocation was cancelled; its computation is drained."""


def check_sql_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SQLCancelledError("SQL cancelled by enclosing invocation")


@contextmanager
def bounded_connection(
    timeout_seconds: float, cancel_event: threading.Event | None = None
):
    """Interrupt native work on the same connection; close before returning.

    The calling SDK thread owns all execute/fetch calls. The sole helper is a
    daemon watchdog, which never touches results or the ledger. Repeated
    interrupts cover the gaps between setup, profile statements and fetches:
    an interrupt on an idle connection alone does not cancel its next query.
    This is cooperative cancellation, not a hard OS process deadline.
    """

    deadline = time.monotonic() + timeout_seconds
    check_sql_cancelled(cancel_event)
    connection = duckdb.connect(database=":memory:")
    finished = threading.Event()
    expired = threading.Event()

    def interrupt_on_deadline():
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                break
            if finished.wait(min(0.01, max(0, deadline - time.monotonic()))):
                return
        if time.monotonic() >= deadline:
            expired.set()
        while not finished.is_set():
            connection.interrupt()
            if finished.wait(0.01):
                return

    watchdog = threading.Thread(
        target=interrupt_on_deadline, name="duckdb-deadline", daemon=True
    )
    try:
        watchdog.start()
    except BaseException:
        connection.close()
        raise
    try:
        try:
            yield connection
        finally:
            # Only this thread finalizes. The watchdog cannot interrupt a
            # closed handle, and no helper survives the tool's terminal event.
            finished.set()
            watchdog.join()
            connection.close()
        check_sql_cancelled(cancel_event)
        if expired.is_set() or time.monotonic() >= deadline:
            raise SQLTimeoutError(f"SQL timed out after {timeout_seconds:g} seconds")
    except Exception as exc:
        check_sql_cancelled(cancel_event)
        if expired.is_set() or time.monotonic() >= deadline:
            raise SQLTimeoutError(
                f"SQL timed out after {timeout_seconds:g} seconds"
            ) from exc
        raise
