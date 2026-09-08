"""Gate B — the three `star_by_*` definitions against remembered players.

These rows are deliberately sensitive: Watt and Burrow are stars by two of three
and not the third, for reasons a football reader recognises. That sensitivity is
the product — three defensible definitions of the same word that disagree about
real people, with the disagreement inspectable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.data

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CASES = yaml.safe_load((FIXTURES / "stars.yaml").read_text())["cases"]
TERMS = ("star_by_snaps", "star_by_contract", "star_by_draft")

FLAT = [(c, t) for c in CASES for t in TERMS]
IDS = [f"{c['player'].split()[-1].lower()}-{c['season']}-{t[8:]}" for c, t in FLAT]


def _holds(ask, player: str, season: int, term: str) -> bool:
    out = ask(
        {
            "entity": "player_game",
            "where": [
                {"term": term, "basis": "prior_season"},
                {"attr": "player_name", "op": "=", "value": player},
            ],
            "seasons": {"from": season, "to": season},
            "game_types": ["*"],
            "metrics": [{"fn": "count"}],
            "allow_partial_coverage": True,
        }
    )
    assert out.get("ok"), out
    return bool(out["rows"] and out["rows"][0]["count"])


@pytest.mark.parametrize(("case", "term"), FLAT, ids=IDS)
def test_the_star_definitions_match_the_fixture(ask, case: dict, term: str) -> None:
    expected = case[term]["assert"]
    why = case[term].get("why", "")
    got = _holds(ask, case["player"], case["season"], term)
    assert got == expected, (
        f"\n{case['player']} {case['season']} {term}: expected {expected}, got {got}"
        + (f"\n  fixture says: {why}" if why else "")
    )


def test_the_three_definitions_disagree(ask) -> None:
    """If they agreed there would be nothing to choose between, and no gate worth having."""
    disagreements = [
        case["player"] for case in CASES if len({case[t]["assert"] for t in TERMS}) > 1
    ]
    assert len(disagreements) >= 6, disagreements


def test_a_player_can_be_a_star_by_one_measure_and_not_another(ask) -> None:
    """J.J. Watt in 2017: paid like a star, drafted like one, and unavailable in 2016."""
    assert _holds(ask, "J.J. Watt", 2017, "star_by_contract") is True
    assert _holds(ask, "J.J. Watt", 2017, "star_by_draft") is True
    assert _holds(ask, "J.J. Watt", 2017, "star_by_snaps") is False


def test_availability_gates_the_snap_measure(ask, real_conn) -> None:
    """Watt played 3 of 16 games in 2016, below the 0.5 eligibility floor."""
    share = real_conn.execute(
        "SELECT games_played_share FROM player_season "
        "WHERE player_name = 'J.J. Watt' AND season = 2016"
    ).fetchone()[0]
    assert share < 0.5


def test_a_rookie_has_no_prior_season_at_all(ask) -> None:
    """Even a career fact like draft round: a prior_season definition reads nothing."""
    for term in TERMS:
        assert _holds(ask, "Anthony Richardson", 2023, term) is False


def test_snap_share_barely_separates_quarterbacks(ask, real_conn) -> None:
    """The measurement behind the fixture corrections (see docs/decision-history.md).

    Every full-time quarterback plays essentially every snap, so the cohort is
    compressed and the top decile is a handful of players. A 17-game starter is
    not in it. For running backs the same definition discriminates properly.
    """
    rows = dict(
        real_conn.execute(
            """
            WITH q AS (
                SELECT position_group, snap_share_mean,
                       cume_dist() OVER (PARTITION BY position_group
                                         ORDER BY snap_share_mean ASC) AS cd
                FROM player_season
                WHERE season = 2022 AND snap_share_mean IS NOT NULL
                  AND games_played_share >= 0.5
            )
            SELECT position_group, min(snap_share_mean) FILTER (cd >= 0.9)
            FROM q WHERE position_group IN ('QB', 'RB') GROUP BY 1
            """
        ).fetchall()
    )
    assert rows["QB"] > 0.98, "the QB cutoff sits within a whisker of a perfect share"
    assert rows["RB"] < 0.75, "the RB cutoff leaves real room below it"


def test_the_answer_names_which_definition_it_used(ask) -> None:
    """The whole point: the number means nothing without the definition beside it."""
    out = ask(
        {
            "entity": "player_game",
            "where": [{"term": "star_by_contract", "basis": "prior_season"}],
            "seasons": {"from": 2023, "to": 2023},
            "metrics": [{"fn": "count_distinct", "of": "player_key"}],
        }
    )
    used = {d["name"]: d for d in out["definitions_used"]}
    assert "star_by_contract" in used
    assert "90th percentile" in used["star_by_contract"]["explanation"]
