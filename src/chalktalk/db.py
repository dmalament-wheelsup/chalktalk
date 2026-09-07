"""DuckDB connections and the CURRENT pointer.

The build writes a fresh ``data/nfl-YYYYMMDD.duckdb`` and then flips
``data/CURRENT`` to name it. The server only ever opens the file through
:func:`open_ro`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import duckdb

from chalktalk.config import Settings
from chalktalk.paths import current_pointer, data_dir

_MEMORY_LIMIT_RE = re.compile(r"^\d+(\.\d+)?\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)$", re.IGNORECASE)


class NoDatabase(Exception):
    """Raised when an operation needs a built database and CURRENT names none."""


def read_current(s: Settings) -> Path | None:
    """The artifact CURRENT points at, or None if the pointer or its target is missing."""
    pointer = current_pointer(s)
    try:
        raw = pointer.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    if not raw:
        return None
    target = Path(raw)
    if not target.is_absolute():
        target = data_dir(s) / target
    return target if target.is_file() else None


def write_current(s: Settings, artifact: Path) -> None:
    """Point CURRENT at ``artifact``, atomically.

    Artifacts inside the data directory are recorded by bare filename so the
    home directory stays relocatable.
    """
    pointer = current_pointer(s)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    value = artifact.name if artifact.parent == data_dir(s) else str(artifact.resolve())
    tmp = pointer.with_name(pointer.name + ".tmp")
    tmp.write_text(value + "\n", encoding="utf-8")
    os.replace(tmp, pointer)


def _apply_resource_limits(conn: duckdb.DuckDBPyConnection, s: Settings) -> None:
    if s.duckdb_memory_limit is not None:
        limit = s.duckdb_memory_limit.strip()
        if not _MEMORY_LIMIT_RE.match(limit):
            raise ValueError(f"invalid duckdb_memory_limit: {s.duckdb_memory_limit!r}")
        conn.execute(f"SET memory_limit='{limit}'")
    if s.duckdb_threads is not None:
        if s.duckdb_threads < 1:
            raise ValueError(f"invalid duckdb_threads: {s.duckdb_threads!r}")
        conn.execute(f"SET threads={int(s.duckdb_threads)}")


def open_rw(path: Path, s: Settings) -> duckdb.DuckDBPyConnection:
    """Open a writable connection. Builds only — never the server."""
    conn = duckdb.connect(str(path))
    _apply_resource_limits(conn, s)
    return conn


def open_ro(path: Path, s: Settings) -> duckdb.DuckDBPyConnection:
    """Open a read-only, sandboxed connection.

    External access is disabled and the configuration is then locked, so no
    statement on this connection can re-enable it. ``lock_configuration`` must
    stay the last SET.
    """
    conn = duckdb.connect(str(path), read_only=True)
    _apply_resource_limits(conn, s)
    conn.execute("SET enable_external_access=false")
    conn.execute("SET lock_configuration=true")
    return conn
