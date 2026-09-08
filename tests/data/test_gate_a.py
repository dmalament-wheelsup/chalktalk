"""Gate A — the hand-authored injury-exit cases, checked against raw attributes.

CLAUDE.md is explicit: if the system cannot find the exits already known by
name, nothing built on top of it is worth having. This checks the *attributes*
in `player_game`, before any definition exists; Gate B (phase 7) checks the same
cases through definitions, compiler and SQL.

The expected values in `tests/fixtures/exits.yaml` are football knowledge,
written by a person and copied into the repository before `player_game` existed.
Where the data and the fixture disagreed, the phase 9 protocol was followed: the
join was checked first, and only then was the fixture corrected, with a note
saying what the data showed.

Data tier: needs a build under CHALKTALK_HOME.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
import yaml

from chalktalk.config import Settings
from chalktalk.db import open_ro, read_current

pytestmark = pytest.mark.data

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

#: The shipped `left_early` threshold (D14): a quarter of the unit's plays
#: happened after the player's last one.
TAIL_THRESHOLD = 0.25

#: Gate A's floor for "this was a regular player, not a cameo".
BASELINE_FLOOR = 0.5


def _cases() -> list[dict]:
    return yaml.safe_load((FIXTURES / "exits.yaml").read_text())["cases"]


CASES = _cases()
EXITS = [c for c in CASES if c["expect_exit"]]
NON_EXITS = [c for c in CASES if not c["expect_exit"]]


def _ids(cases: list[dict]) -> list[str]:
    return [c["id"] for c in cases]


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


def _row(conn: duckdb.DuckDBPyConnection, case: dict) -> dict:
    """The player_game row for a fixture case, as a dict, or a readable failure."""
    cursor = conn.execute(
        """
        SELECT * FROM player_game
        WHERE player_name = ? AND season = ? AND week = ? AND team = ?
        """,
        [case["player"], case["season"], case["week"], case["team"]],
    )
    rows = cursor.fetchall()
    names = [d[0] for d in cursor.description]
    assert rows, (
        f"{case['id']}: no player_game row for {case['player']} "
        f"{case['season']} week {case['week']} {case['team']}. "
        f"Check the crosswalk and the snap_counts join before touching the fixture."
    )
    assert len(rows) == 1, f"{case['id']}: {len(rows)} rows, expected 1"
    return dict(zip(names, rows[0], strict=True))


def _explain(case: dict, row: dict) -> str:
    """Every failure prints the row, as the phase 9 protocol requires."""
    keys = [
        "player_name", "season", "week", "team", "position", "unit",
        "snaps_unit", "snap_share_unit", "pp_available", "pp_first_idx",
        "pp_last_idx", "pp_team_unit_plays", "pp_missed_tail_frac",
        "pp_last_qtr", "recent_snap_share", "recent_games",
        "prior_season_snap_share", "baseline_share", "baseline_source",
        "is_starting_qb", "inj_listed", "inj_report_status", "roster_status",
        "team_next_game_id", "played_team_next_game", "reserve_within_3_games",
        "corroboration_available",
    ]  # fmt: skip
    body = "\n".join(f"    {k:24} {row.get(k)!r}" for k in keys)
    return f"\n{case['id']}  ({case['fact']})\n{body}\n"


# ── the snap counts themselves ────────────────────────────────────────────────


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_snaps_match_the_hand_authored_number(conn: duckdb.DuckDBPyConnection, case: dict) -> None:
    """These were read from nflverse by a person. The pipeline must reproduce them."""
    row = _row(conn, case)
    assert row["snaps_unit"] == case["snaps_unit"], _explain(case, row)


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_snap_share_is_close_to_the_hand_authored_share(
    conn: duckdb.DuckDBPyConnection, case: dict
) -> None:
    row = _row(conn, case)
    assert abs(row["snap_share_unit"] - case["share"]) < 0.02, _explain(case, row)


# ── leaving and not returning (D14) ───────────────────────────────────────────

PARTICIPATION_EXITS = [c for c in EXITS if c["season"] >= 2016]


@pytest.mark.parametrize("case", PARTICIPATION_EXITS, ids=_ids(PARTICIPATION_EXITS))
def test_an_injury_exit_leaves_a_tail_of_missed_plays(
    conn: duckdb.DuckDBPyConnection, case: dict
) -> None:
    """The direct expression of "left and did not return"."""
    row = _row(conn, case)
    assert row["pp_available"], _explain(case, row)
    assert row["pp_missed_tail_frac"] >= TAIL_THRESHOLD, _explain(case, row)


def test_a_late_exit_is_missed_by_the_shipped_threshold(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """D14's whole argument: Cousins tore his Achilles in Q4 after 61 snaps at 85%.

    A 25% tail threshold does not find him and a 3% one does. That is why the
    threshold is a definition a user can change, not a constant in the code.
    """
    case = next(c for c in CASES if c["id"] == "cousins_2023_w8")
    row = _row(conn, case)
    tail = row["pp_missed_tail_frac"]
    assert 0.02 <= tail <= 0.20, _explain(case, row)
    assert tail < TAIL_THRESHOLD, "the shipped threshold should miss him"
    assert tail >= 0.03, "a 3% threshold should catch him"
    assert row["pp_last_qtr"] == 4, _explain(case, row)


# ── the player was a regular, not a cameo ─────────────────────────────────────

REGULARS = [c for c in EXITS if not c.get("baseline_share_below_half")]


@pytest.mark.parametrize("case", REGULARS, ids=_ids(REGULARS))
def test_an_injury_exit_happens_to_someone_who_normally_plays(
    conn: duckdb.DuckDBPyConnection, case: dict
) -> None:
    row = _row(conn, case)
    assert row["baseline_share"] is not None, _explain(case, row)
    assert row["baseline_share"] >= BASELINE_FLOOR, _explain(case, row)


def test_a_running_backs_baseline_can_sit_under_a_position_blind_floor(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Nick Chubb 2023 week 2 comes out at 0.49, just under Gate A's 0.5.

    Not a join bug and not a wrong number: his recent window is the single week-1
    game at 49%, which beats his 0.564 mean across 2022. Running backs rotate, so
    a position-blind snap-share floor is the wrong shape for the shipped
    definition - recorded here for phase 7 rather than papered over by lowering
    the threshold, which the phase 9 protocol forbids.
    """
    case = next(c for c in CASES if c["id"] == "chubb_2023_w2")
    row = _row(conn, case)
    assert row["baseline_source"] == "recent", _explain(case, row)
    assert row["recent_games"] == 1, _explain(case, row)
    assert 0.45 <= row["baseline_share"] < BASELINE_FLOOR, _explain(case, row)
    assert row["prior_season_snap_share"] > BASELINE_FLOOR, _explain(case, row)


# ── corroboration ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", EXITS, ids=_ids(EXITS))
def test_an_injury_exit_is_corroborated_by_what_happened_next(
    conn: duckdb.DuckDBPyConnection, case: dict
) -> None:
    """Any of: listed on the next game's report, on reserve soon, or absent next game."""
    row = _row(conn, case)
    listed_next = False
    if row["team_next_game_id"] is not None:
        found = conn.execute(
            "SELECT inj_listed FROM player_game WHERE player_key = ? AND game_id = ?",
            [row["player_key"], row["team_next_game_id"]],
        ).fetchone()
        listed_next = bool(found and found[0])
    corroborated = (
        listed_next or bool(row["reserve_within_3_games"]) or row["played_team_next_game"] is False
    )
    assert corroborated, _explain(case, row)


def test_the_report_only_corroboration_path(conn: duckdb.DuckDBPyConnection) -> None:
    """Mike Evans hurt a knee in the last regular-season week and played the next game.

    He is the case that stops corroboration from being "missed the next game":
    the only evidence is the injury report for the wild-card game.
    """
    case = next(c for c in CASES if c["id"] == "evans_2020_w17")
    row = _row(conn, case)
    assert row["played_team_next_game"] is True, _explain(case, row)
    listed = conn.execute(
        "SELECT inj_listed, inj_report_status, inj_practice_status FROM player_game "
        "WHERE player_key = ? AND game_id = ?",
        [row["player_key"], row["team_next_game_id"]],
    ).fetchone()
    assert listed is not None and listed[0], _explain(case, row)


# ── the cases a naive heuristic gets wrong ────────────────────────────────────

RESTED = [
    c for c in NON_EXITS if c["id"].endswith(("_w17", "_w18")) and "expect_played_next" not in c
]


def test_a_rested_starter_plays_the_next_game(conn: duckdb.DuckDBPyConnection) -> None:
    """D15: the fact that separates resting from injury is playing the next week."""
    for case_id in ("allen_2019_w17", "williams_2023_w18", "fournette_2022_w18"):
        case = next(c for c in CASES if c["id"] == case_id)
        row = _row(conn, case)
        assert row["played_team_next_game"] is True, _explain(case, row)


def test_a_rested_starter_who_lost_his_job_is_a_false_positive(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Robert Hainsey is the named counter-example the shipped definition must face.

    He started week 18, was rested with the division clinched, and did not play
    the wild-card game - not through injury, but because Ryan Jensen returned
    from a season-long injury and took the centre job back. Every corroboration
    signal the shipped composite has says "exit", and he was not one. This is
    CLAUDE.md's rested-starter risk with a name, and it is why matched rows are
    always returned.
    """
    case = next(c for c in CASES if c["id"] == "hainsey_2022_w18")
    row = _row(conn, case)
    assert row["played_team_next_game"] is False, _explain(case, row)
    assert row["pp_first_idx"] == 1, "he did start the game" + _explain(case, row)
    assert not row["inj_listed"], _explain(case, row)
    assert not row["reserve_within_3_games"], _explain(case, row)
    assert row["baseline_share"] >= BASELINE_FLOOR, _explain(case, row)

    jensen = conn.execute(
        "SELECT count(*), max(snap_share_unit) FROM player_game "
        "WHERE player_name = 'Ryan Jensen' AND season = 2022"
    ).fetchone()
    assert jensen == (1, 1.0), "Jensen played only the postseason game, at every snap"


CAMEOS = ["bridgewater_2019_w17", "devito_2023_w18", "brissett_2022_w18"]


@pytest.mark.parametrize("case_id", CAMEOS)
def test_a_backup_cameo_did_not_start(conn: duckdb.DuckDBPyConnection, case_id: str) -> None:
    """Low snaps because he was a backup, not because he left.

    Bridgewater is the reason corroboration requires starting: he sat the
    wild-card game because Brees was healthy, and absence alone would flag him.
    """
    case = next(c for c in CASES if c["id"] == case_id)
    row = _row(conn, case)
    assert row["is_starting_qb"] is False, _explain(case, row)
    assert row["pp_first_idx"] is None or row["pp_first_idx"] != 1, _explain(case, row)


def test_an_uncorroborable_case_says_so(conn: duckdb.DuckDBPyConnection) -> None:
    """Tommy DeVito's season ended that week, so nothing can corroborate it (D15)."""
    case = next(c for c in CASES if c["id"] == "devito_2023_w18")
    row = _row(conn, case)
    assert row["team_next_game_id"] is None, _explain(case, row)
    assert row["corroboration_available"] is False, _explain(case, row)
    assert row["played_team_next_game"] is None, _explain(case, row)


# ── the target question ───────────────────────────────────────────────────────


def test_the_fifteen_snap_line_splits_the_exits(conn: duckdb.DuckDBPyConnection) -> None:
    """The question in CLAUDE.md asks for exits under 15 snaps.

    The suite deliberately contains exits on both sides of that line, so that
    `early_exit` and `snaps_unit < 15` cannot be confused for each other.
    """
    under, over = [], []
    for case in EXITS:
        row = _row(conn, case)
        (under if row["snaps_unit"] < 15 else over).append(case["id"])
    assert len(under) >= 8, under
    assert len(over) >= 4, over
