"""Coverage arithmetic on synthetic tables.

Synthetic because the point is the reduction — a column that starts late, a
column with a hole in it, a season scheduled but not played — and real data
happens to contain only some of those. The era-neutrality tests build two
invented eras so that nothing can pass by matching a real NFL number.
"""

from __future__ import annotations

import duckdb
import pytest

from chalktalk import coverage as cov
from chalktalk.config import Settings


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    yield connection
    connection.close()


@pytest.fixture
def settings() -> Settings:
    return Settings.load()


def _schedules(conn: duckdb.DuckDBPyConnection, rows: list[tuple]) -> None:
    """(season, week, game_type, home_team, away_team, home_score)."""
    conn.execute(
        "CREATE OR REPLACE TABLE schedules (season INTEGER, week INTEGER, game_type VARCHAR, "
        "home_team VARCHAR, away_team VARCHAR, home_score INTEGER)"
    )
    conn.executemany("INSERT INTO schedules VALUES (?, ?, ?, ?, ?, ?)", rows)


def _round_robin(season: int, weeks: int, teams: list[str], played: bool = True) -> list[tuple]:
    """A toy regular season: one game per week between the first two teams."""
    return [
        (
            season,
            w,
            "REG",
            teams[w % len(teams)],
            teams[(w + 1) % len(teams)],
            20 if played else None,
        )
        for w in range(1, weeks + 1)
    ]


def _coverage_row(conn: duckdb.DuckDBPyConnection, table: str, column: str) -> tuple:
    return conn.execute(
        "SELECT first_season, last_season, seasons_with_data, has_gaps, non_null_rows, total_rows "
        "FROM coverage_columns WHERE table_name = ? AND column_name = ?",
        [table, column],
    ).fetchone()


# ── the per-column reduction ──────────────────────────────────────────────────


def test_reduce_finds_the_populated_span() -> None:
    first, last, with_data, gaps, non_null = cov._reduce_seasonal([2013, 2014, 2015], [0, 5, 7])
    assert (first, last, with_data, gaps, non_null) == (2014, 2015, 2, False, 12)


def test_reduce_flags_a_hole() -> None:
    first, last, with_data, gaps, _ = cov._reduce_seasonal([2013, 2014, 2015], [3, 0, 9])
    assert (first, last, with_data, gaps) == (2013, 2015, 2, True)


def test_reduce_on_a_column_that_was_never_populated() -> None:
    assert cov._reduce_seasonal([2013, 2014], [0, 0]) == (None, None, 0, False, 0)


def test_reduce_on_no_seasons_at_all() -> None:
    assert cov._reduce_seasonal([], []) == (None, None, 0, False, 0)


# ── build over synthetic tables ───────────────────────────────────────────────


@pytest.fixture
def built(conn: duckdb.DuckDBPyConnection, settings: Settings) -> duckdb.DuckDBPyConnection:
    conn.execute(
        "CREATE TABLE facts (season INTEGER, week INTEGER, always INTEGER, "
        "starts_late INTEGER, gappy INTEGER, never INTEGER)"
    )
    conn.executemany(
        "INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?)",
        [
            (2012, 1, 1, None, 1, None),  # below the floor but inside the baseline window
            (2013, 1, 1, None, 1, None),
            (2014, 1, 1, None, None, None),
            (2015, 1, 1, 1, 1, None),
            (2015, 2, 1, 1, 1, None),
        ],
    )
    conn.execute("CREATE TABLE reference (name VARCHAR, value INTEGER)")
    conn.executemany("INSERT INTO reference VALUES (?, ?)", [("a", 1), ("b", None)])
    _schedules(
        conn,
        _round_robin(2013, 3, ["AAA", "BBB"])
        + _round_robin(2014, 3, ["AAA", "BBB"])
        + _round_robin(2015, 3, ["AAA", "BBB"]),
    )
    cov.build(conn, settings)
    return conn


def test_a_column_populated_throughout(built: duckdb.DuckDBPyConnection) -> None:
    assert _coverage_row(built, "facts", "always")[:4] == (2012, 2015, 4, False)


def test_a_column_that_starts_late(built: duckdb.DuckDBPyConnection) -> None:
    """Present in the schema, empty in the early seasons — the NGS case."""
    assert _coverage_row(built, "facts", "starts_late")[:4] == (2015, 2015, 1, False)


def test_a_column_with_a_hole(built: duckdb.DuckDBPyConnection) -> None:
    assert _coverage_row(built, "facts", "gappy")[:4] == (2012, 2015, 3, True)


def test_a_column_never_populated(built: duckdb.DuckDBPyConnection) -> None:
    assert _coverage_row(built, "facts", "never")[:4] == (None, None, 0, False)


def test_null_counts_are_recorded(built: duckdb.DuckDBPyConnection) -> None:
    assert _coverage_row(built, "facts", "gappy")[4:] == (4, 5)


def test_a_table_without_seasons_is_unbounded(built: duckdb.DuckDBPyConnection) -> None:
    seasonal, first, last = built.execute(
        "SELECT seasonal, first_season, last_season FROM coverage_columns "
        "WHERE table_name = 'reference' AND column_name = 'value'"
    ).fetchone()
    assert (seasonal, first, last) == (False, None, None)
    assert _coverage_row(built, "reference", "value")[4:] == (1, 2)


def test_registry_tables_are_not_covered(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    conn.execute("CREATE TABLE ingest_log (season INTEGER, row_count BIGINT)")
    conn.execute("INSERT INTO ingest_log VALUES (2023, 1)")
    _schedules(conn, _round_robin(2013, 2, ["AAA", "BBB"]))
    cov.build(conn, settings)
    covered = conn.execute(
        "SELECT count(*) FROM coverage_columns WHERE table_name = 'ingest_log'"
    ).fetchone()[0]
    assert covered == 0


def test_rows_below_the_baseline_window_are_excluded(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    conn.execute("CREATE TABLE facts (season INTEGER, n INTEGER)")
    conn.executemany(
        "INSERT INTO facts VALUES (?, ?)", [(1999, 1), (2011, 1), (2012, 1), (2013, 1)]
    )
    _schedules(conn, _round_robin(2013, 2, ["AAA", "BBB"]))
    cov.build(conn, settings)
    assert _coverage_row(conn, "facts", "n")[:2] == (2012, 2013)
    assert _coverage_row(conn, "facts", "n")[5] == 2


def test_per_season_rows_and_weeks(built: duckdb.DuckDBPyConnection) -> None:
    rows = built.execute(
        "SELECT season, row_count, first_week, last_week, weeks_present FROM coverage_seasons "
        "WHERE table_name = 'facts' ORDER BY season"
    ).fetchall()
    assert rows[0] == (2012, 1, 1, 1, 1)
    assert rows[-1] == (2015, 2, 1, 2, 2)


def test_a_table_without_weeks_reports_none(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    conn.execute("CREATE TABLE facts (season INTEGER, n INTEGER)")
    conn.execute("INSERT INTO facts VALUES (2013, 1)")
    _schedules(conn, _round_robin(2013, 2, ["AAA", "BBB"]))
    cov.build(conn, settings)
    assert conn.execute(
        "SELECT first_week, last_week, weeks_present FROM coverage_seasons WHERE table_name='facts'"
    ).fetchone() == (None, None, None)


def test_build_is_idempotent(built: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    before = built.execute("SELECT count(*) FROM coverage_columns").fetchone()[0]
    cov.build(built, settings)
    assert built.execute("SELECT count(*) FROM coverage_columns").fetchone()[0] == before


# ── season_status: era-neutral by construction (D24) ──────────────────────────


@pytest.fixture
def eras(conn: duckdb.DuckDBPyConnection, settings: Settings) -> duckdb.DuckDBPyConnection:
    """Two invented eras and one scheduled-but-unplayed season.

    The week counts (5 and 7) and field sizes (2 and 4) are deliberately not NFL
    numbers: anything that passes here read them out of the data.
    """
    rows: list[tuple] = []
    rows += _round_robin(2013, 5, ["AAA", "BBB"])
    rows += [(2013, 6, "WC", "AAA", "BBB", 20)]
    rows += _round_robin(2014, 7, ["AAA", "BBB"])
    rows += [
        (2014, 8, "WC", "AAA", "BBB", 20),
        (2014, 8, "WC", "CCC", "DDD", 17),
        (2014, 9, "SB", "AAA", "CCC", 24),
    ]
    rows += _round_robin(2015, 7, ["AAA", "BBB"], played=False)
    _schedules(conn, rows)
    cov.build(conn, settings)
    return conn


def _status(conn: duckdb.DuckDBPyConnection, season: int) -> cov.SeasonStatus | None:
    return cov.Coverage(conn, Settings.load()).status(season)


def test_regular_season_length_comes_from_the_data(eras: duckdb.DuckDBPyConnection) -> None:
    assert _status(eras, 2013).reg_weeks == 5
    assert _status(eras, 2014).reg_weeks == 7


def test_games_per_team_comes_from_the_data(eras: duckdb.DuckDBPyConnection) -> None:
    assert _status(eras, 2013).games_per_team == 5
    assert _status(eras, 2014).games_per_team == 7


def test_playoff_field_size_counts_teams_not_games(eras: duckdb.DuckDBPyConnection) -> None:
    assert _status(eras, 2013).playoff_teams == 2
    assert _status(eras, 2014).playoff_teams == 4


def test_postseason_games_are_not_double_counted(eras: duckdb.DuckDBPyConnection) -> None:
    assert _status(eras, 2013).post_games_final == 1
    assert _status(eras, 2014).post_games_final == 3


def test_a_finished_season_is_complete(eras: duckdb.DuckDBPyConnection) -> None:
    status = _status(eras, 2014)
    assert (status.complete, status.in_progress, status.queryable) == (True, False, True)


def test_a_scheduled_but_unplayed_season_is_not_queryable(
    eras: duckdb.DuckDBPyConnection,
) -> None:
    """schedules carries next season before a snap is taken; it answers nothing."""
    status = _status(eras, 2015)
    assert status.reg_games_scheduled == 7
    assert status.reg_games_final == 0
    assert (status.complete, status.in_progress, status.queryable) == (False, False, False)


def test_a_season_in_progress(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    rows = _round_robin(2013, 4, ["AAA", "BBB"]) + [(2013, 5, "REG", "AAA", "BBB", None)]
    _schedules(conn, rows)
    cov.build(conn, settings)
    status = _status(conn, 2013)
    assert (status.complete, status.in_progress, status.queryable) == (False, True, True)


def test_seasons_below_the_floor_are_not_queryable(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """2012 rows exist for baselines; 2012 is never a queryable season (D20)."""
    _schedules(conn, _round_robin(2012, 3, ["AAA", "BBB"]) + _round_robin(2013, 3, ["AAA", "BBB"]))
    cov.build(conn, settings)
    assert _status(conn, 2012).queryable is False
    assert _status(conn, 2013).queryable is True


# ── the Coverage accessor ─────────────────────────────────────────────────────


@pytest.fixture
def registry(built: duckdb.DuckDBPyConnection) -> cov.Coverage:
    return cov.Coverage(built, Settings.load())


def test_column_lookup(registry: cov.Coverage) -> None:
    found = registry.column("facts", "starts_late")
    assert found == cov.ColumnCoverage(2015, 2015, True, False)


def test_unknown_column_lookup(registry: cov.Coverage) -> None:
    assert registry.column("facts", "nonesuch") is None
    assert registry.column("nonesuch", "always") is None


def test_intersect_narrows_to_the_tightest_ref(registry: cov.Coverage) -> None:
    assert registry.intersect([("facts", "always"), ("facts", "starts_late")]) == cov.SeasonRange(
        2015, 2015
    )


def test_intersect_is_bounded_by_the_queryable_window(registry: cov.Coverage) -> None:
    """`always` reaches back to 2012, but 2012 is not a queryable season."""
    assert registry.intersect([("facts", "always")]) == cov.SeasonRange(2013, 2015)


def test_intersect_treats_non_seasonal_refs_as_unbounded(registry: cov.Coverage) -> None:
    assert registry.intersect([("reference", "value")]) == cov.SeasonRange(2013, 2015)


def test_intersect_of_disjoint_refs_is_none(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """Two attributes that never overlap answer nothing together."""
    conn.execute("CREATE TABLE facts (season INTEGER, early INTEGER, late INTEGER)")
    conn.executemany("INSERT INTO facts VALUES (?, ?, ?)", [(2013, 1, None), (2014, None, 1)])
    _schedules(conn, _round_robin(2013, 2, ["AAA", "BBB"]) + _round_robin(2014, 2, ["AAA", "BBB"]))
    cov.build(conn, settings)
    registry = cov.Coverage(conn, settings)
    assert registry.intersect([("facts", "early")]) == cov.SeasonRange(2013, 2013)
    assert registry.intersect([("facts", "early"), ("facts", "late")]) is None


def test_intersect_refuses_an_unknown_ref(registry: cov.Coverage) -> None:
    assert registry.intersect([("facts", "always"), ("facts", "nonesuch")]) is None


def test_intersect_refuses_a_never_populated_ref(registry: cov.Coverage) -> None:
    assert registry.intersect([("facts", "never")]) is None


def test_intersect_of_nothing_is_the_queryable_window(registry: cov.Coverage) -> None:
    assert registry.intersect([]) == cov.SeasonRange(2013, 2015)


def test_season_range_contains(registry: cov.Coverage) -> None:
    span = cov.SeasonRange(2013, 2015)
    assert 2014 in span
    assert 2012 not in span


def test_queryable_seasons_start_at_the_floor(eras: duckdb.DuckDBPyConnection) -> None:
    registry = cov.Coverage(eras, Settings.load())
    assert registry.queryable_seasons() == [2013, 2014]


def test_latest_complete_ignores_a_season_in_progress(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    rows = _round_robin(2013, 3, ["AAA", "BBB"]) + _round_robin(2014, 3, ["AAA", "BBB"])
    rows += [(2014, 4, "REG", "AAA", "BBB", None)]
    _schedules(conn, rows)
    cov.build(conn, settings)
    assert cov.Coverage(conn, settings).latest_complete() == 2013


def test_status_of_an_unknown_season(registry: cov.Coverage) -> None:
    assert registry.status(1999) is None


def test_a_partial_build_leaves_season_status_empty(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """`chalktalk build --only pbp` has no schedules; guess nothing rather than crash."""
    conn.execute("CREATE TABLE facts (season INTEGER, n INTEGER)")
    conn.execute("INSERT INTO facts VALUES (2023, 1)")
    cov.build(conn, settings)
    assert conn.execute("SELECT count(*) FROM season_status").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM coverage_columns").fetchone()[0] == 2


def test_nothing_is_queryable_without_season_status(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    conn.execute("CREATE TABLE facts (season INTEGER, n INTEGER)")
    conn.execute("INSERT INTO facts VALUES (2023, 1)")
    cov.build(conn, settings)
    registry = cov.Coverage(conn, settings)
    assert registry.queryable_seasons() == []
    assert registry.latest_complete() is None
    assert registry.intersect([("facts", "n")]) is None
