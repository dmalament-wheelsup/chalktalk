"""The coverage registry against the real build.

These assertions are the ones the gate will lean on: if `participation` claims
to start in 2013, a snap-share definition will silently answer a question it
cannot answer. Data tier: needs a build under CHALKTALK_HOME.
"""

from __future__ import annotations

import duckdb
import pytest

from chalktalk.config import Settings
from chalktalk.coverage import Coverage
from chalktalk.db import open_ro, read_current

pytestmark = pytest.mark.data


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings.load()


@pytest.fixture(scope="module")
def conn(settings: Settings) -> duckdb.DuckDBPyConnection:
    artifact = read_current(settings)
    if artifact is None:
        pytest.skip(f"no database under {settings.home}; run `chalktalk build`")
    connection = open_ro(artifact, settings)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def registry(conn: duckdb.DuckDBPyConnection, settings: Settings) -> Coverage:
    return Coverage(conn, settings)


def test_the_registry_was_built(conn: duckdb.DuckDBPyConnection) -> None:
    for table in ("coverage_columns", "coverage_seasons", "season_status"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0


def test_snap_counts_start_in_2013(registry: Coverage) -> None:
    """Not 2012. The loader accepts 2012 and the release is empty (phase 2 amendment).

    Consequence: no prior-season snap baseline exists for the 2013 season.
    """
    found = registry.column("snap_counts", "offense_snaps")
    assert (found.first_season, found.last_season) == (2013, 2025)
    assert not found.has_gaps


def test_participation_starts_in_2016(registry: Coverage) -> None:
    """D4. This is the fact that stops a 2013 snap-share query returning zero."""
    found = registry.column("participation", "offense_players")
    assert found.first_season >= 2016
    assert found.last_season == 2025


def test_pbp_reaches_the_season_floor(registry: Coverage, settings: Settings) -> None:
    found = registry.column("pbp", "epa")
    assert found.first_season == settings.season_floor
    assert not found.has_gaps


def test_no_seasonal_column_predates_the_baseline_window(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    floor = settings.season_floor - settings.baseline_lookback
    early = conn.execute(
        "SELECT table_name, column_name, first_season FROM coverage_columns "
        "WHERE seasonal AND first_season < ?",
        [floor],
    ).fetchall()
    assert not early, f"columns claiming data before {floor}: {early}"


def test_reference_tables_are_unbounded(registry: Coverage) -> None:
    """players and contracts have no season; they must not narrow an intersection."""
    for table, column in (("players", "draft_round"), ("contracts", "apy_cap_pct")):
        found = registry.column(table, column)
        assert found is not None, f"{table}.{column} missing from the registry"
        assert found.seasonal is False
        assert found.first_season is None


def test_every_queryable_season_is_covered(registry: Coverage, settings: Settings) -> None:
    seasons = registry.queryable_seasons()
    assert seasons[0] == settings.season_floor
    assert seasons == list(range(seasons[0], seasons[-1] + 1)), "a queryable season is missing"


def test_a_scheduled_but_unplayed_season_is_not_queryable(registry: Coverage) -> None:
    """schedules carries next season before a game is played; it answers nothing."""
    latest = max(registry._status)
    unplayed = [s for s, st in registry._status.items() if st.reg_games_final == 0]
    for season in unplayed:
        assert registry.status(season).queryable is False
        assert season not in registry.queryable_seasons()
    assert latest not in registry.queryable_seasons() or registry.status(latest).reg_games_final > 0


# D24: read out of the data, never written into the code. These are the eras the
# plan verified; the assertion is that the registry reports them, not that the
# code knows them.
ERAS = (
    [(season, 17, 16, 12) for season in range(2013, 2020)]
    + [(2020, 17, 16, 14)]
    + [(season, 18, 17, 14) for season in range(2021, 2026)]
)


@pytest.mark.parametrize(("season", "reg_weeks", "games_per_team", "playoff_teams"), ERAS)
def test_season_structure_matches_the_era(
    registry: Coverage, season: int, reg_weeks: int, games_per_team: int, playoff_teams: int
) -> None:
    status = registry.status(season)
    assert status is not None, f"{season} missing from season_status"
    assert (status.reg_weeks, status.games_per_team, status.playoff_teams) == (
        reg_weeks,
        games_per_team,
        playoff_teams,
    )


def test_the_cancelled_2022_game_is_visible(registry: Coverage) -> None:
    """271 regular-season games, not 272. Proof the counts are not hard-coded."""
    assert registry.status(2022).reg_games_scheduled == 271


def test_finished_seasons_are_complete(registry: Coverage) -> None:
    for season in registry.queryable_seasons()[:-1]:
        status = registry.status(season)
        assert status.complete, f"{season} is queryable but not complete"
        assert not status.in_progress


def test_latest_complete_is_a_real_season(registry: Coverage) -> None:
    latest = registry.latest_complete()
    assert latest in registry.queryable_seasons()
    assert registry.status(latest).post_games_final > 0


def test_intersecting_snaps_with_participation_starts_at_2016(registry: Coverage) -> None:
    """The composite early_exit definition needs both; the tighter one wins."""
    span = registry.intersect(
        [("snap_counts", "offense_snaps"), ("participation", "offense_players")]
    )
    assert span.first == 2016


def test_intersecting_with_a_reference_table_does_not_narrow(registry: Coverage) -> None:
    snaps = registry.intersect([("snap_counts", "offense_snaps")])
    with_draft = registry.intersect([("snap_counts", "offense_snaps"), ("players", "draft_round")])
    assert snaps == with_draft


def test_an_all_null_column_makes_the_intersection_none(
    conn: duckdb.DuckDBPyConnection, registry: Coverage
) -> None:
    """Present in the schema, never populated — the gate must refuse, not return zero."""
    empty = conn.execute(
        "SELECT table_name, column_name FROM coverage_columns "
        "WHERE seasonal AND first_season IS NULL LIMIT 1"
    ).fetchone()
    assert empty, "expected at least one always-NULL column in pbp"
    assert registry.intersect([tuple(empty)]) is None


def test_coverage_seasons_row_counts_match_the_tables(conn: duckdb.DuckDBPyConnection) -> None:
    for table, season, expected in conn.execute(
        "SELECT table_name, season, row_count FROM coverage_seasons "
        "WHERE table_name IN ('snap_counts', 'participation', 'pbp')"
    ).fetchall():
        actual = conn.execute(
            f'SELECT count(*) FROM "{table}" WHERE season = ?', [season]
        ).fetchone()[0]
        assert actual == expected, f"{table} {season}: registry {expected}, table {actual}"


def test_pbp_weeks_span_the_whole_season(conn: duckdb.DuckDBPyConnection) -> None:
    """Postseason weeks are in there too; the last week moved with the era (D24)."""
    rows = dict(
        conn.execute(
            "SELECT season, last_week FROM coverage_seasons WHERE table_name = 'pbp'"
        ).fetchall()
    )
    assert rows[2013] == 21
    assert rows[2023] == 22
