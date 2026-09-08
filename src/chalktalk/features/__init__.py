"""Feature layer — the six entity tables and the attribute catalog.

Build order matters: `player_season` is computed from raw tables only, so
`player_game` can join it for prior-season attributes without a cycle.
Coverage is regenerated after this, never before (phase 3).

Each builder declares the raw tables it reads. A partial build
(`chalktalk build --only pbp`) simply has no `players` table, and a feature
that cannot be built is skipped with a warning rather than aborting the
ingest that did succeed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import duckdb

from chalktalk.config import Settings
from chalktalk.features import xwalk

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Builder:
    table: str
    build: Callable[[duckdb.DuckDBPyConnection, Settings], None]
    requires: tuple[str, ...]


#: In dependency order. Later stages of phase 4 append: game_ctx, team_game,
#: team_season (4b), player_play, player_season (4c), player_game (4d).
BUILDERS: list[Builder] = [
    Builder("player_id_xwalk", xwalk.build, ("players", "rosters_weekly", "snap_counts")),
]


def _missing(conn: duckdb.DuckDBPyConnection, tables: tuple[str, ...]) -> list[str]:
    present = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    return [t for t in tables if t not in present]


def build_all(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Build every derived table whose inputs are present, in order."""
    for builder in BUILDERS:
        missing = _missing(conn, builder.requires)
        if missing:
            log.warning("features: skipping %s, no %s", builder.table, ", ".join(missing))
            continue
        started = time.monotonic()
        builder.build(conn, settings)
        log.info("features: %s in %.1fs", builder.table, time.monotonic() - started)
