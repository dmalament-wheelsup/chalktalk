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
