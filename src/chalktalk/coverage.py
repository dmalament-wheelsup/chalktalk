"""Coverage registry — what is actually computable, and for which seasons.

Every number here is generated from the data at build time. Nothing in this
module, and nothing that reads it, may hard-code a season, a week count or a
playoff field size (D24): the 17-game season, the 14-team playoff and the 2022
season's cancelled game are all facts the data already knows.

This is what lets the gate refuse a 2005 query against a snap-share definition
instead of returning a misleading zero.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb

from chalktalk.config import Settings

log = logging.getLogger(__name__)

#: Bookkeeping tables. They have a ``season`` column but describe the build,
#: not the football.
REGISTRY_TABLES = frozenset(
    {
        "coverage_columns",
        "coverage_seasons",
        "season_status",
        "ingest_log",
        "build_info",
        "type_conflicts",
    }
)

_COLUMNS_DDL = """
CREATE OR REPLACE TABLE coverage_columns (
    table_name        VARCHAR NOT NULL,
    column_name       VARCHAR NOT NULL,
    duck_type         VARCHAR NOT NULL,
    seasonal          BOOLEAN NOT NULL,
    first_season      INTEGER,
    last_season       INTEGER,
    seasons_with_data INTEGER,
    has_gaps          BOOLEAN,
    non_null_rows     BIGINT,
    total_rows        BIGINT,
    computed_at       TIMESTAMP NOT NULL
)
"""

_SEASONS_DDL = """
CREATE OR REPLACE TABLE coverage_seasons (
    table_name    VARCHAR NOT NULL,
    season        INTEGER NOT NULL,
    row_count     BIGINT,
    first_week    INTEGER,
    last_week     INTEGER,
    weeks_present INTEGER
)
"""

_STATUS_DDL = """
CREATE OR REPLACE TABLE season_status (
    season               INTEGER NOT NULL,
    reg_weeks            INTEGER,
    games_per_team       INTEGER,
    playoff_teams        INTEGER,
    reg_games_scheduled  INTEGER,
    reg_games_final      INTEGER,
    post_games_final     INTEGER,
    complete             BOOLEAN,
    in_progress          BOOLEAN,
    queryable            BOOLEAN
)
"""

# Season structure and progress, entirely from `schedules`. `games_per_team` and
# `reg_weeks` describe how the season is *shaped* (scheduled), so they are right
# for a season in progress; the `*_final` counts describe how far it has got.
_STATUS_SQL = """
WITH reg AS (
    SELECT season,
           max(week)                    AS reg_weeks,
           count(*)                     AS reg_games_scheduled,
           count(home_score)            AS reg_games_final
    FROM schedules WHERE game_type = 'REG' GROUP BY season
), post AS (
    SELECT season, count(home_score) AS post_games_final
    FROM schedules WHERE game_type <> 'REG' GROUP BY season
), post_teams AS (
    -- One row per team per postseason game, so DISTINCT counts the field size.
    SELECT season, count(DISTINCT team) AS playoff_teams
    FROM (
        SELECT season, home_team AS team FROM schedules WHERE game_type <> 'REG'
        UNION ALL
        SELECT season, away_team FROM schedules WHERE game_type <> 'REG'
    ) GROUP BY season
), per_team AS (
    SELECT season, max(n) AS games_per_team FROM (
        SELECT season, team, count(*) AS n FROM (
            SELECT season, home_team AS team FROM schedules WHERE game_type = 'REG'
            UNION ALL
            SELECT season, away_team FROM schedules WHERE game_type = 'REG'
        ) GROUP BY season, team
    ) GROUP BY season
), whole AS (
    SELECT season, count(*) AS scheduled, count(home_score) AS final
    FROM schedules GROUP BY season
)
SELECT w.season,
       reg.reg_weeks,
       per_team.games_per_team,
       post_teams.playoff_teams,
       reg.reg_games_scheduled,
       reg.reg_games_final,
       post.post_games_final,
       (w.final = w.scheduled AND w.scheduled > 0)              AS complete,
       (w.final > 0 AND w.final < w.scheduled)                  AS in_progress,
       (w.season >= ? AND w.final > 0)                          AS queryable
FROM whole w
LEFT JOIN reg      USING (season)
LEFT JOIN post       USING (season)
LEFT JOIN post_teams USING (season)
LEFT JOIN per_team   USING (season)
ORDER BY w.season
"""


@dataclass(frozen=True)
class ColumnCoverage:
    first_season: int | None
    last_season: int | None
    seasonal: bool
    has_gaps: bool


@dataclass(frozen=True)
class SeasonRange:
    first: int
    last: int

    def __contains__(self, season: int) -> bool:
        return self.first <= season <= self.last


@dataclass(frozen=True)
class SeasonStatus:
    season: int
    reg_weeks: int | None
    games_per_team: int | None
    playoff_teams: int | None
    reg_games_scheduled: int | None
    reg_games_final: int | None
    post_games_final: int | None
    complete: bool
    in_progress: bool
    queryable: bool


def _data_tables(conn: duckdb.DuckDBPyConnection) -> list[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' ORDER BY table_name"
    ).fetchall()
    return [t for (t,) in rows if t not in REGISTRY_TABLES]


def _table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = ?",
            [table],
        ).fetchone()
    )


def _columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[tuple[str, str]]:
    return [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
    ]


def _reduce_seasonal(
    seasons: Sequence[int], counts: Sequence[int]
) -> tuple[int | None, int | None, int, bool, int]:
    """Per-column reduction over one `GROUP BY season` scan.

    A column present in the schema but never populated in the early seasons —
    the Next Gen Stats fields in ``pbp`` — correctly gets a later
    ``first_season``. That is the point of computing this from the data.
    """
    populated = [s for s, n in zip(seasons, counts, strict=True) if n > 0]
    non_null = sum(counts)
    if not populated:
        return None, None, 0, False, non_null
    first, last = populated[0], populated[-1]
    with_data = len(populated)
    return first, last, with_data, with_data < last - first + 1, non_null


def _scan_seasonal(
    conn: duckdb.DuckDBPyConnection, table: str, columns: list[tuple[str, str]], floor: int
) -> tuple[list[tuple], list[tuple]]:
    """One pass over the table; returns (coverage_columns rows, coverage_seasons rows)."""
    names = [c for c, _ in columns]
    has_week = "week" in names
    aggregates = ", ".join(f'count("{c}")' for c in names)
    week_aggregates = (
        ', min("week"), max("week"), count(DISTINCT "week")' if has_week else ", NULL, NULL, NULL"
    )
    rows = conn.execute(
        f"SELECT season, count(*){', ' if aggregates else ''}{aggregates}{week_aggregates} "
        f'FROM "{table}" WHERE season >= ? GROUP BY season ORDER BY season',
        [floor],
    ).fetchall()

    seasons = [r[0] for r in rows]
    totals = [r[1] for r in rows]
    now = datetime.now(UTC).replace(tzinfo=None)

    column_rows = []
    for i, (name, duck_type) in enumerate(columns):
        counts = [r[2 + i] for r in rows]
        first, last, with_data, has_gaps, non_null = _reduce_seasonal(seasons, counts)
        column_rows.append(
            (
                table,
                name,
                duck_type,
                True,
                first,
                last,
                with_data,
                has_gaps,
                non_null,
                sum(totals),
                now,
            )
        )

    season_rows = [(table, r[0], r[1], r[-3], r[-2], r[-1]) for r in rows]
    return column_rows, season_rows


def _scan_flat(
    conn: duckdb.DuckDBPyConnection, table: str, columns: list[tuple[str, str]]
) -> list[tuple]:
    """A table with no season column: counts only, coverage unbounded."""
    names = [c for c, _ in columns]
    aggregates = ", ".join(f'count("{c}")' for c in names)
    row = conn.execute(
        f'SELECT count(*){", " if aggregates else ""}{aggregates} FROM "{table}"'
    ).fetchone()
    total = row[0]
    now = datetime.now(UTC).replace(tzinfo=None)
    return [
        (table, name, duck_type, False, None, None, None, None, row[1 + i], total, now)
        for i, (name, duck_type) in enumerate(columns)
    ]


def build_season_status(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Season structure and progress, from `schedules` alone.

    Split out of :func:`build` because the feature layer *reads* it — `game_ctx`
    needs each season's regular-season length to know which week is the final
    one (D24) — while column coverage *describes* the feature layer and must run
    after it. This half depends on no derived table, so it runs first.
    """
    conn.execute(_STATUS_DDL)
    if not _table_exists(conn, "schedules"):
        # A partial build (`--only pbp`). Guess nothing; nothing is queryable.
        log.warning("coverage: no schedules table, so season_status is empty")
        return
    conn.execute(f"INSERT INTO season_status {_STATUS_SQL}", [settings.season_floor])


def build(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Regenerate the three coverage tables from whatever is in the database.

    Called last in a build, after the derived tables exist: coverage describes
    the feature layer as well as the raw tables.
    """
    floor = settings.season_floor - settings.baseline_lookback
    conn.execute(_COLUMNS_DDL)
    conn.execute(_SEASONS_DDL)

    for table in _data_tables(conn):
        columns = _columns(conn, table)
        if not columns:
            continue
        if any(name == "season" for name, _ in columns):
            column_rows, season_rows = _scan_seasonal(conn, table, columns, floor)
            conn.executemany(
                "INSERT INTO coverage_columns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", column_rows
            )
            if season_rows:
                conn.executemany(
                    "INSERT INTO coverage_seasons VALUES (?, ?, ?, ?, ?, ?)", season_rows
                )
        else:
            conn.executemany(
                "INSERT INTO coverage_columns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _scan_flat(conn, table, columns),
            )

    build_season_status(conn, settings)

    covered = conn.execute("SELECT count(*) FROM coverage_columns").fetchone()[0]
    log.info("coverage: %s columns across %s tables", covered, len(_data_tables(conn)))


class Coverage:
    """A read-only view of the registry, loaded once per connection."""

    def __init__(self, conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
        self._settings = settings
        self._columns: dict[tuple[str, str], ColumnCoverage] = {
            (table, column): ColumnCoverage(first, last, seasonal, bool(has_gaps))
            for table, column, seasonal, first, last, has_gaps in conn.execute(
                "SELECT table_name, column_name, seasonal, first_season, last_season, has_gaps "
                "FROM coverage_columns"
            ).fetchall()
        }
        self._status: dict[int, SeasonStatus] = {
            row[0]: SeasonStatus(*row)
            for row in conn.execute(
                "SELECT season, reg_weeks, games_per_team, playoff_teams, reg_games_scheduled, "
                "reg_games_final, post_games_final, complete, in_progress, queryable "
                "FROM season_status ORDER BY season"
            ).fetchall()
        }

    def column(self, table: str, column: str) -> ColumnCoverage | None:
        return self._columns.get((table, column))

    def intersect(self, refs: Iterable[tuple[str, str]]) -> SeasonRange | None:
        """The seasons every ref can answer for, or None if some ref never can.

        Non-seasonal refs (``players.draft_round``) are unbounded and do not
        narrow the range. A ref that exists but was never populated makes the
        answer None — the gate reports it as the limiting ref.
        """
        queryable = self.queryable_seasons()
        if not queryable:
            return None
        first, last = queryable[0], queryable[-1]
        for table, column in refs:
            found = self.column(table, column)
            if found is None:
                return None
            if not found.seasonal:
                continue
            if found.first_season is None or found.last_season is None:
                return None
            first = max(first, found.first_season)
            last = min(last, found.last_season)
        return SeasonRange(first, last) if first <= last else None

    def queryable_seasons(self) -> list[int]:
        return [s for s, status in sorted(self._status.items()) if status.queryable]

    def latest_complete(self) -> int | None:
        complete = [s for s, status in self._status.items() if status.complete and status.queryable]
        return max(complete) if complete else None

    def status(self, season: int) -> SeasonStatus | None:
        return self._status.get(season)
