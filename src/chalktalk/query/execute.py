"""Running a compiled query, with a timeout that actually stops it.

DuckDB has no statement timeout, so this arms a timer that calls `interrupt()`
on the connection. Without it a badly-shaped plan can hold the single process
indefinitely, which for a one-process self-run server means hanging the user's
whole session.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import duckdb

from chalktalk.config import Settings
from chalktalk.query.compile import CompiledQuery


class QueryTimeout(Exception):
    """The query ran past `settings.query_timeout_s` and was interrupted."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        super().__init__(f"query exceeded {seconds:g}s and was cancelled")


@dataclass
class RunResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    sample_columns: list[str] = field(default_factory=list)
    total_matched: int = 0
    timing_ms: dict[str, int] = field(default_factory=dict)


def _fetch(
    conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any], timeout: float
) -> tuple[list[dict[str, Any]], list[str], int]:
    timer = threading.Timer(timeout, conn.interrupt)
    timer.start()
    started = time.perf_counter()
    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description]
    except duckdb.InterruptException as exc:
        raise QueryTimeout(timeout) from exc
    finally:
        timer.cancel()
    elapsed = int((time.perf_counter() - started) * 1000)
    return [dict(zip(columns, row, strict=True)) for row in rows], columns, elapsed


def run(
    conn: duckdb.DuckDBPyConnection,
    compiled: CompiledQuery,
    settings: Settings,
    *,
    with_sample: bool = True,
) -> RunResult:
    """Execute the aggregate, the matched-row sample and the total."""
    timeout = settings.query_timeout_s
    result = RunResult()

    rows, _, elapsed = _fetch(conn, compiled.sql, compiled.params, timeout)
    result.rows = rows
    result.row_count = len(rows)
    result.timing_ms["main"] = elapsed
    limit = compiled.params[-1] if compiled.params else None
    result.truncated = isinstance(limit, int) and len(rows) == limit

    if with_sample:
        sample, columns, elapsed = _fetch(
            conn, compiled.sample_sql, compiled.sample_params, timeout
        )
        result.sample_rows = sample
        result.sample_columns = columns
        result.timing_ms["sample"] = elapsed

        total, _, elapsed = _fetch(conn, compiled.count_sql, compiled.count_params, timeout)
        result.total_matched = next(iter(total[0].values())) if total else 0
        result.timing_ms["count"] = elapsed

    return result
