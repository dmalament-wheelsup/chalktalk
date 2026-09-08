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
from chalktalk.features import (
    game_ctx,
    player_play,
    player_season,
    team_game,
    team_season,
    xwalk,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Builder:
    table: str
    build: Callable[[duckdb.DuckDBPyConnection, Settings], None]
    requires: tuple[str, ...]


#: In dependency order. Phase 4d appends player_game.
BUILDERS: list[Builder] = [
    Builder("player_id_xwalk", xwalk.build, ("players", "rosters_weekly", "snap_counts")),
    Builder("game_ctx", game_ctx.build, ("schedules", "pbp", "season_status")),
    Builder("team_game", team_game.build, ("game_ctx", "pbp", "season_status")),
    Builder("team_season", team_season.build, ("team_game", "teams", "season_status")),
    Builder("player_play", player_play.build, ("participation", "pbp", "game_ctx")),
    Builder(
        "player_season",
        player_season.build,
        (
            "snap_counts",
            "player_id_xwalk",
            "team_season",
            "rosters_weekly",
            "players",
            "player_stats",
            "contracts",
            "team_game",
            "player_play",
        ),
    ),
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
