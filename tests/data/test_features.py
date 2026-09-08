"""The feature layer against the real build.

Grows with each stage of phase 4: 4a crosswalk, 4b game/team tables,
4c player_season, 4d player_game. Data tier — needs a build under
CHALKTALK_HOME.
"""

from __future__ import annotations

import duckdb
import pytest

from chalktalk.config import Settings
from chalktalk.db import open_ro, read_current
from chalktalk.entities import ENTITIES
from chalktalk.features import catalog

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


# ── the catalog covers what was actually built ────────────────────────────────


def test_every_built_entity_column_is_documented(conn: duckdb.DuckDBPyConnection) -> None:
    """Failing here is the feature layer being incomplete, not a style problem."""
    assert catalog.undocumented(conn) == {}


def test_no_catalog_entry_names_a_missing_column(conn: duckdb.DuckDBPyConnection) -> None:
    assert catalog.orphaned(conn) == {}


@pytest.mark.parametrize("entity", ["game", "team_game", "team_season"])
def test_the_catalog_type_matches_the_built_column(
    conn: duckdb.DuckDBPyConnection, entity: str
) -> None:
    """A catalog that says `int` where the column is text misleads every definition."""
    built = {r[0]: r[1] for r in conn.execute(f'DESCRIBE "{ENTITIES[entity].table}"').fetchall()}
    wrong = [
        (a.name, a.type, built[a.name])
        for a in catalog.attributes_for(entity, conn)
        if catalog._DUCK_TO_ATTR.get(built[a.name].split("(")[0]) != a.type
    ]
    assert not wrong, wrong


def test_booleans_are_booleans_not_flags(conn: duckdb.DuckDBPyConnection) -> None:
    """A convention from the plan: 0/1 integers invite a definition to compare them wrongly."""
    for entity in ("game", "team_game", "team_season"):
        built = {
            r[0]: r[1] for r in conn.execute(f'DESCRIBE "{ENTITIES[entity].table}"').fetchall()
        }
        for attribute in catalog.attributes_for(entity, conn):
            if attribute.type == "bool":
                assert built[attribute.name] == "BOOLEAN", f"{entity}.{attribute.name}"


# ── player_id_xwalk (D6) ──────────────────────────────────────────────────────


def test_the_crosswalk_was_built(conn: duckdb.DuckDBPyConnection) -> None:
    assert conn.execute("SELECT count(*) FROM player_id_xwalk").fetchone()[0] > 20_000


def test_pfr_ids_are_unique(conn: duckdb.DuckDBPyConnection) -> None:
    """It is a lookup; a duplicated pfr_id would silently fan out every snap-count join."""
    total, distinct = conn.execute(
        "SELECT count(*), count(DISTINCT pfr_id) FROM player_id_xwalk"
    ).fetchone()
    assert total == distinct


def test_no_row_is_missing_an_id(conn: duckdb.DuckDBPyConnection) -> None:
    assert (
        conn.execute(
            "SELECT count(*) FROM player_id_xwalk WHERE pfr_id IS NULL OR gsis_id IS NULL"
        ).fetchone()[0]
        == 0
    )


def test_snap_counts_resolve_at_least_99_percent(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    total, resolved = conn.execute(
        """
        SELECT count(*), count(x.gsis_id)
        FROM snap_counts s
        LEFT JOIN player_id_xwalk x ON x.pfr_id = s.pfr_player_id
        WHERE s.season >= ?
        """,
        [settings.season_floor],
    ).fetchone()
    rate = resolved / total
    assert rate >= 0.99, f"only {rate:.2%} of snap_counts rows crosswalk to a gsis_id"


@pytest.mark.parametrize("season", [2013, 2016, 2019, 2023, 2025])
def test_the_crosswalk_holds_up_in_every_era(conn: duckdb.DuckDBPyConnection, season: int) -> None:
    """D6 verified 2013/2019/2023/2025 individually; a good average could hide a bad season."""
    total, resolved = conn.execute(
        """
        SELECT count(DISTINCT s.pfr_player_id), count(DISTINCT x.gsis_id)
        FROM snap_counts s
        LEFT JOIN player_id_xwalk x ON x.pfr_id = s.pfr_player_id
        WHERE s.season = ?
        """,
        [season],
    ).fetchone()
    assert resolved / total >= 0.99, f"{season}: {resolved}/{total} distinct ids resolved"


def test_position_groups_use_the_agreed_vocabulary(conn: duckdb.DuckDBPyConnection) -> None:
    groups = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT position_group FROM player_id_xwalk WHERE position_group IS NOT NULL"
        ).fetchall()
    }
    assert groups <= {"QB", "RB", "WR", "TE", "OL", "DL", "LB", "DB", "SPEC"}, groups


def test_known_players_crosswalk(conn: duckdb.DuckDBPyConnection) -> None:
    """Spot-check the fixture cases' players actually resolve, since Gate A needs them."""
    for name in ("Aaron Rodgers", "Nick Chubb", "Kirk Cousins", "Trent Williams"):
        found = conn.execute(
            "SELECT count(*) FROM player_id_xwalk WHERE display_name = ?", [name]
        ).fetchone()[0]
        assert found >= 1, f"{name} is not in the crosswalk"


def test_the_crosswalk_reaches_snap_counts_for_a_known_game(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Rodgers 2023 W1: 4 offensive snaps. The join has to actually land."""
    row = conn.execute(
        """
        SELECT x.display_name, s.offense_snaps
        FROM snap_counts s
        JOIN player_id_xwalk x ON x.pfr_id = s.pfr_player_id
        WHERE s.season = 2023 AND s.week = 1 AND s.team = 'NYJ' AND x.display_name = 'Aaron Rodgers'
        """
    ).fetchone()
    assert row is not None, "Rodgers 2023 W1 did not join through the crosswalk"
    assert row[1] == 4


# ── game_ctx ──────────────────────────────────────────────────────────────────


def test_one_row_per_scheduled_game(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    games, distinct = conn.execute(
        "SELECT count(*), count(DISTINCT game_id) FROM game_ctx"
    ).fetchone()
    scheduled = conn.execute(
        "SELECT count(*) FROM schedules WHERE season >= ?", [settings.season_floor]
    ).fetchone()[0]
    assert games == distinct == scheduled


def test_the_regular_season_length_is_read_not_written(conn: duckdb.DuckDBPyConnection) -> None:
    """D24. 17 weeks through 2020, 18 from 2021, and the table must say so itself."""
    by_season = dict(
        conn.execute("SELECT DISTINCT season, reg_weeks FROM game_ctx ORDER BY season").fetchall()
    )
    assert by_season[2019] == 17
    assert by_season[2020] == 17
    assert by_season[2021] == 18
    assert by_season[2025] == 18


@pytest.mark.parametrize(("season", "week"), [(2019, 17), (2020, 17), (2021, 18), (2023, 18)])
def test_the_final_regular_week_moves_with_the_era(
    conn: duckdb.DuckDBPyConnection, season: int, week: int
) -> None:
    weeks = conn.execute(
        "SELECT DISTINCT week FROM game_ctx WHERE season = ? AND is_final_reg_week", [season]
    ).fetchall()
    assert weeks == [(week,)]


def test_no_postseason_game_is_a_final_regular_week(conn: duckdb.DuckDBPyConnection) -> None:
    assert (
        conn.execute(
            "SELECT count(*) FROM game_ctx WHERE is_final_reg_week AND is_postseason"
        ).fetchone()[0]
        == 0
    )


def test_weeks_remaining_is_null_in_the_postseason(conn: duckdb.DuckDBPyConnection) -> None:
    assert (
        conn.execute(
            "SELECT count(*) FROM game_ctx WHERE is_postseason AND weeks_remaining_reg IS NOT NULL"
        ).fetchone()[0]
        == 0
    )


def test_the_result_is_home_relative(conn: duckdb.DuckDBPyConnection) -> None:
    assert (
        conn.execute(
            "SELECT count(*) FROM game_ctx WHERE is_final AND result <> home_score - away_score"
        ).fetchone()[0]
        == 0
    )


def test_scheduled_but_unplayed_games_are_present_and_marked(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """`schedules` carries next season. Those rows belong here, flagged, not filtered out."""
    unplayed = conn.execute("SELECT count(*) FROM game_ctx WHERE NOT is_final").fetchone()[0]
    assert unplayed > 0
    assert (
        conn.execute(
            "SELECT count(*) FROM game_ctx WHERE NOT is_final AND home_score IS NOT NULL"
        ).fetchone()[0]
        == 0
    )


def test_kickoff_hour_is_parsed(conn: duckdb.DuckDBPyConnection) -> None:
    lo, hi = conn.execute(
        "SELECT min(kickoff_hour), max(kickoff_hour) FROM game_ctx WHERE kickoff_hour IS NOT NULL"
    ).fetchone()
    assert 0 <= lo <= hi <= 23


def test_play_counts_line_up_with_pbp(conn: duckdb.DuckDBPyConnection) -> None:
    row = conn.execute(
        """
        SELECT g.plays_total, g.pass_plays, g.rush_plays,
               count(*) FILTER (p.play_type = 'pass'), count(*) FILTER (p.play_type = 'run')
        FROM game_ctx g JOIN pbp p ON p.game_id = g.game_id
        WHERE g.game_id = '2023_22_SF_KC' GROUP BY 1, 2, 3
        """
    ).fetchone()
    assert row[1] == row[3]
    assert row[2] == row[4]
    assert row[0] > row[1] + row[2]


# ── team_game ─────────────────────────────────────────────────────────────────


def test_two_rows_per_game(conn: duckdb.DuckDBPyConnection) -> None:
    games, rows = conn.execute(
        "SELECT (SELECT count(*) FROM game_ctx), count(*) FROM team_game"
    ).fetchone()
    assert rows == games * 2


def test_margins_are_mirrored(conn: duckdb.DuckDBPyConnection) -> None:
    mismatched = conn.execute(
        """
        SELECT count(*) FROM team_game a JOIN team_game b
          ON b.game_id = a.game_id AND b.team = a.opponent
        WHERE a.is_final AND a.margin <> -b.margin
        """
    ).fetchone()[0]
    assert mismatched == 0


def test_exactly_one_winner_per_completed_game(conn: duckdb.DuckDBPyConnection) -> None:
    bad = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT game_id, count(*) FILTER (won) AS w, count(*) FILTER (tied) AS t
            FROM team_game WHERE is_final GROUP BY game_id
        ) WHERE NOT ((w = 1 AND t = 0) OR (w = 0 AND t = 2))
        """
    ).fetchone()[0]
    assert bad == 0


def test_the_spread_is_team_relative_and_negative_means_favoured(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Verified against a known game: BAL hosted HOU in 2023 W1 as 9.5-point favourites."""
    rows = dict(
        conn.execute(
            "SELECT team, spread FROM team_game WHERE game_id = '2023_01_HOU_BAL'"
        ).fetchall()
    )
    assert rows["BAL"] == -9.5
    assert rows["HOU"] == 9.5


def test_favourites_win_more_often_than_they_lose(conn: duckdb.DuckDBPyConnection) -> None:
    """If the sign were flipped this would come out near 0.35 instead of near 0.65."""
    rate = conn.execute(
        "SELECT avg(CASE WHEN won THEN 1.0 ELSE 0.0 END) FROM team_game "
        "WHERE is_final AND favorite AND game_type = 'REG'"
    ).fetchone()[0]
    assert 0.6 < rate < 0.75, rate


def test_covering_the_spread_is_close_to_a_coin_flip(conn: duckdb.DuckDBPyConnection) -> None:
    """A closing line that is beaten 65% of the time would mean the sign is wrong."""
    rate = conn.execute(
        "SELECT avg(CASE WHEN covered THEN 1.0 ELSE 0.0 END) FROM team_game "
        "WHERE is_final AND covered IS NOT NULL"
    ).fetchone()[0]
    assert 0.45 < rate < 0.55, rate


def test_games_played_before_counts_games_not_weeks(conn: duckdb.DuckDBPyConnection) -> None:
    """A bye means week - 1 overstates games played; the fixture is SF's 2023 bye."""
    rows = dict(
        conn.execute(
            "SELECT week, games_played_before FROM team_game "
            "WHERE season = 2023 AND team = 'SF' AND game_type = 'REG' AND week IN (8, 10)"
        ).fetchall()
    )
    assert rows[8] == 7
    assert rows[10] == 8


def test_season_progress_spans_zero_to_one(conn: duckdb.DuckDBPyConnection) -> None:
    lo, hi = conn.execute(
        "SELECT min(season_progress), max(season_progress) FROM team_game WHERE game_type = 'REG'"
    ).fetchone()
    assert lo == 0.0
    assert hi < 1.0


def test_every_team_plays_the_seasons_full_slate(conn: duckdb.DuckDBPyConnection) -> None:
    """16 games through 2020, 17 from 2021 — and 2022 has one team short, by cancellation."""
    counts = dict(
        conn.execute(
            "SELECT season, max(games_played_before) + 1 FROM team_game "
            "WHERE game_type = 'REG' AND is_final GROUP BY season"
        ).fetchall()
    )
    assert counts[2019] == 16
    assert counts[2021] == 17
    assert counts[2023] == 17


def test_offensive_plays_split_into_passes_and_runs(conn: duckdb.DuckDBPyConnection) -> None:
    assert (
        conn.execute(
            "SELECT count(*) FROM team_game WHERE pass_attempts + rush_attempts <> off_plays"
        ).fetchone()[0]
        == 0
    )


def test_fourth_down_go_never_exceeds_fourth_downs(conn: duckdb.DuckDBPyConnection) -> None:
    assert (
        conn.execute(
            "SELECT count(*) FROM team_game WHERE fourth_down_go > fourth_downs"
        ).fetchone()[0]
        == 0
    )


def test_fourth_down_aggression_has_risen(conn: duckdb.DuckDBPyConnection) -> None:
    """One of phase 7's generality questions; the attribute has to actually carry the trend."""
    early, late = conn.execute(
        """
        SELECT avg(fourth_down_go_rate) FILTER (season <= 2015),
               avg(fourth_down_go_rate) FILTER (season >= 2022)
        FROM team_game WHERE game_type = 'REG' AND is_final
        """
    ).fetchone()
    assert late > early, f"{early:.3f} -> {late:.3f}"


def test_leads_and_deficits_are_consistent(conn: duckdb.DuckDBPyConnection) -> None:
    assert (
        conn.execute("SELECT count(*) FROM team_game WHERE max_lead < max_deficit").fetchone()[0]
        == 0
    )


def test_the_defence_faces_what_the_opponent_runs(conn: duckdb.DuckDBPyConnection) -> None:
    mismatched = conn.execute(
        """
        SELECT count(*) FROM team_game a JOIN team_game b
          ON b.game_id = a.game_id AND b.team = a.opponent
        WHERE a.def_plays <> b.off_plays
        """
    ).fetchone()[0]
    assert mismatched == 0


# ── team_season ───────────────────────────────────────────────────────────────


def test_one_row_per_team_per_played_season(conn: duckdb.DuckDBPyConnection) -> None:
    seasons, rows = conn.execute(
        "SELECT count(DISTINCT season), count(*) FROM team_season"
    ).fetchone()
    assert rows == seasons * 32


def test_wins_and_losses_add_up(conn: duckdb.DuckDBPyConnection) -> None:
    assert (
        conn.execute(
            "SELECT count(*) FROM team_season WHERE wins + losses + ties <> games"
        ).fetchone()[0]
        == 0
    )


def test_league_wins_equal_league_losses(conn: duckdb.DuckDBPyConnection) -> None:
    """Every win is someone's loss; a join that fanned out would break this."""
    bad = conn.execute(
        "SELECT count(*) FROM (SELECT season FROM team_season GROUP BY season "
        "HAVING sum(wins) <> sum(losses))"
    ).fetchone()[0]
    assert bad == 0


def test_a_known_season_is_right(conn: duckdb.DuckDBPyConnection) -> None:
    """2023: Baltimore finished 13-4 and lost the conference championship."""
    row = conn.execute(
        "SELECT games, wins, losses, made_playoffs, playoff_wins, division, conference "
        "FROM team_season WHERE season = 2023 AND team = 'BAL'"
    ).fetchone()
    assert row == (17, 13, 4, True, 1, "AFC North", "AFC")


def test_the_super_bowl_winner_has_the_most_playoff_wins(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """2023: Kansas City were the 3 seed, so they had no bye and won four games."""
    assert (
        conn.execute(
            "SELECT playoff_wins FROM team_season WHERE season = 2023 AND team = 'KC'"
        ).fetchone()[0]
        == 4
    )


def test_the_playoff_field_matches_the_era(conn: duckdb.DuckDBPyConnection) -> None:
    """12 teams through 2019, 14 from 2020 — derived, never written down (D24)."""
    counts = dict(
        conn.execute(
            "SELECT season, count(*) FILTER (made_playoffs) FROM team_season GROUP BY season"
        ).fetchall()
    )
    assert counts[2019] == 12
    assert counts[2020] == 14
    assert counts[2023] == 14


def test_per_game_rates_are_era_comparable(conn: duckdb.DuckDBPyConnection) -> None:
    """The reason per-game columns exist: totals from 16- and 17-game seasons are not comparable."""
    assert (
        conn.execute(
            "SELECT count(*) FROM team_season "
            "WHERE abs(points_for_per_game - points_for::DOUBLE / games) > 1e-9"
        ).fetchone()[0]
        == 0
    )


def test_win_pct_counts_a_tie_as_half(conn: duckdb.DuckDBPyConnection) -> None:
    tied = conn.execute(
        "SELECT wins, ties, games, win_pct FROM team_season WHERE ties > 0 LIMIT 1"
    ).fetchone()
    assert tied is not None, "expected at least one tied season in the era"
    wins, ties, games, pct = tied
    assert abs(pct - (wins + 0.5 * ties) / games) < 1e-9


def test_divisions_and_conferences_are_populated(conn: duckdb.DuckDBPyConnection) -> None:
    conferences = {
        r[0] for r in conn.execute("SELECT DISTINCT conference FROM team_season").fetchall()
    }
    assert conferences == {"AFC", "NFC"}
    assert conn.execute("SELECT count(DISTINCT division) FROM team_season").fetchone()[0] == 8


# ── team abbreviations ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("game_ctx", "home_team"),
        ("game_ctx", "away_team"),
        ("team_game", "team"),
        ("team_game", "opponent"),
        ("team_season", "team"),
    ],
)
def test_derived_tables_use_one_spelling_per_franchise(
    conn: duckdb.DuckDBPyConnection, table: str, column: str
) -> None:
    """nflverse mixes historical and current codes; the feature layer must not."""
    from chalktalk.features.team_abbr import CANONICAL

    found = {
        r[0]
        for r in conn.execute(f'SELECT DISTINCT "{column}" FROM "{table}"').fetchall()
        if r[0] is not None
    }
    assert found <= CANONICAL, (
        f"{table}.{column} has non-canonical codes: {sorted(found - CANONICAL)}"
    )


def test_relocated_franchises_have_their_offensive_stats(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Oakland, San Diego and St. Louis had zero offensive plays before normalization.

    `schedules` called them OAK/SD/STL while `pbp` always called them LV/LAC/LA,
    so the join matched nothing and every stat silently came back zero.
    """
    empty = conn.execute(
        """
        SELECT season, team, count(*) FROM team_game
        WHERE is_final AND game_type = 'REG' AND off_plays = 0
        GROUP BY 1, 2
        """
    ).fetchall()
    assert not empty, f"team-games with no offensive plays: {empty}"


def test_the_relocated_franchises_are_continuous(conn: duckdb.DuckDBPyConnection) -> None:
    """One franchise, one row per season — not two half-populated identities."""
    for team, seasons in (("LV", 13), ("LAC", 13), ("LA", 13)):
        found = conn.execute(
            "SELECT count(DISTINCT season) FROM team_season WHERE team = ?", [team]
        ).fetchone()[0]
        assert found == seasons, f"{team}: {found} seasons"


def test_a_pre_relocation_season_is_intact(conn: duckdb.DuckDBPyConnection) -> None:
    """Oakland went 12-4 in 2016 as OAK; the row must be there under LV, with real stats."""
    row = conn.execute(
        "SELECT wins, losses, off_epa_per_play FROM team_season WHERE season = 2016 AND team = 'LV'"
    ).fetchone()
    assert row is not None
    wins, losses, off_epa = row
    assert (wins, losses) == (12, 4)
    assert off_epa is not None and off_epa != 0
