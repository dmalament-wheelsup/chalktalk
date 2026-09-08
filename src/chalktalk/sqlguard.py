"""The raw-SQL escape hatch, and the fence around it.

Defence in depth, and it is worth being clear which layer actually holds. The
real boundary is `open_ro` from phase 1: read-only, `enable_external_access=false`,
`lock_configuration=true`, set in that order so nothing can re-enable them. This
module is the second fence — it rejects statements that have no business being
here before DuckDB ever sees them, so the failure is a clear message rather than
a permission error.

The hatch exists because a tool that cannot answer an unanticipated question is
worse than one that can. Every call is logged, and recurring raw queries are the
roadmap for what should become a first-class attribute.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import duckdb


class SqlRejected(Exception):
    """The statement is not something this tool will run."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class SqlTimeout(Exception):
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        super().__init__(f"query exceeded {seconds:g}s and was cancelled")


#: Anything that writes, loads, configures or reaches outside the database.
FORBIDDEN = (
    "ATTACH", "COPY", "EXPORT", "IMPORT", "INSTALL", "LOAD", "PRAGMA", "SET",
    "RESET", "CREATE", "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CALL",
    "CHECKPOINT", "VACUUM", "DETACH", "GRANT", "REVOKE",
)  # fmt: skip

_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(FORBIDDEN) + r")\b", re.IGNORECASE)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:[^']|'')*'")


@dataclass
class RawResult:
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    timing_ms: int = 0


def _strip_comments(sql: str) -> str:
    return _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", sql))


def validate(sql: str) -> str:
    """The cleaned single statement, or a refusal saying why."""
    if not sql or not sql.strip():
        raise SqlRejected("no SQL was given")

    cleaned = _strip_comments(sql).strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if not cleaned:
        raise SqlRejected("the statement is empty once comments are removed")

    # Semicolons inside string literals are fine; between statements are not.
    if ";" in _STRING.sub("''", cleaned):
        raise SqlRejected("one statement at a time; found more than one")

    head = cleaned.lstrip("( \t\n").upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise SqlRejected("only SELECT and WITH statements are allowed")

    # Keywords inside string literals are just text.
    found = _FORBIDDEN_RE.search(_STRING.sub("''", cleaned))
    if found:
        raise SqlRejected(
            f"{found.group(1).upper()} is not allowed here; this connection is read-only"
        )
    return cleaned


def bounded(sql: str, limit: int) -> str:
    """Wrap the statement so it cannot return more than the cap.

    One extra row is fetched so truncation can be reported honestly rather than
    silently handing back a partial answer that looks complete.
    """
    return f"SELECT * FROM ({sql}) AS _q LIMIT {int(limit) + 1}"


def run_raw(conn: duckdb.DuckDBPyConnection, sql: str, limit: int, timeout_s: float) -> RawResult:
    """Validate, bound, and run with a timeout. Raises SqlRejected or SqlTimeout."""
    statement = bounded(validate(sql), limit)

    timer = threading.Timer(timeout_s, conn.interrupt)
    timer.start()
    started = time.perf_counter()
    try:
        cursor = conn.execute(statement)
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description]
    except duckdb.InterruptException as exc:
        raise SqlTimeout(timeout_s) from exc
    except duckdb.Error as exc:
        # A refused catalog function or a syntax error: report, never leak rows.
        raise SqlRejected(str(exc).strip().splitlines()[0]) from exc
    finally:
        timer.cancel()

    truncated = len(rows) > limit
    kept = rows[:limit]
    return RawResult(
        columns=columns,
        rows=[dict(zip(columns, row, strict=True)) for row in kept],
        truncated=truncated,
        timing_ms=int((time.perf_counter() - started) * 1000),
    )
