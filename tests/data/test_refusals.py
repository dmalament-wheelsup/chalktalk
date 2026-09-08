"""What the gate refuses, on real data.

The refusals are the product as much as the answers are. A system that returns a
confident number for "how many star players left with injury" without ever
asking what a star is has answered a question nobody posed.
"""

from __future__ import annotations

import pytest

from chalktalk.definitions.spec import DefinitionIn

pytestmark = pytest.mark.data


def test_the_headline_question_is_refused_until_star_is_defined(ask) -> None:
    """D2: three star definitions ship and `star_player` does not."""
    out = ask(
        {
            "entity": "player_game",
            "where": [
                {"term": "star_player", "basis": "prior_season"},
                {"term": "early_exit"},
                {"attr": "snaps_unit", "op": "<", "value": 15},
            ],
            "group_by": ["season"],
        }
    )
    assert out["ok"] is False
    assert out["error"] == "unresolved_term"
    assert out["next_step"] == "propose_definition"
    offered = {s["name"] for s in out["terms"][0]["suggestions"]}
    assert offered == {"star_by_snaps", "star_by_contract", "star_by_draft"}


def test_the_refusal_explains_each_candidate(ask) -> None:
    """A list of names is not a choice; the user needs to know what they measure."""
    out = ask({"entity": "player_game", "where": [{"term": "star_player"}]})
    for suggestion in out["terms"][0]["suggestions"]:
        assert suggestion["explanation"], suggestion["name"]
        assert (
            "percentile" in suggestion["explanation"] or "draft_round" in suggestion["explanation"]
        )


def test_asking_for_seasons_the_data_cannot_reach(ask) -> None:
    """Snap counts begin in 2013, so 2005 is not a coverage gap — it is nothing."""
    out = ask(
        {
            "entity": "player_game",
            "where": [{"term": "early_exit"}],
            "seasons": {"from": 2005, "to": 2025},
            "metrics": [{"fn": "count"}],
        }
    )
    assert out["ok"] is False
    assert out["error"] == "coverage_gap"
    assert out["requested"] == [2005, 2025]
    assert out["covered"][0] == 2013
    assert out["limiting"], "the refusal must name what limits it"


def test_the_same_query_runs_when_partial_coverage_is_allowed(ask) -> None:
    out = ask(
        {
            "entity": "player_game",
            "where": [{"term": "early_exit"}],
            "seasons": {"from": 2005, "to": 2025},
            "metrics": [{"fn": "count"}],
            "allow_partial_coverage": True,
        }
    )
    assert out["ok"]
    assert out["seasons"]["covered"] == [2013, 2025]
    assert out["seasons"]["excluded"][0] == 2005
    assert out["seasons"]["partial"] is True
    assert out["warnings"]


def test_a_definition_from_the_wrong_entity_is_refused(ask) -> None:
    """`qb` is about a player; a team-game has no position."""
    out = ask({"entity": "team_game", "where": [{"term": "qb"}]})
    assert out["error"] == "entity_mismatch"
    assert out["definition_entity"] == "player_season"
    assert "player_game" in out["allowed_entities"]
    assert "team_game" not in out["allowed_entities"]


def test_a_misspelt_attribute_suggests_the_real_one(ask) -> None:
    out = ask(
        {"entity": "player_game", "where": [{"attr": "snap_share", "op": ">=", "value": 0.5}]}
    )
    assert out["error"] == "unknown_attribute"
    assert "snap_share_unit" in out["attributes"][0]["did_you_mean"]


def test_something_the_data_cannot_answer_says_so_rather_than_guessing(ask) -> None:
    """Pro Bowl selection is not in nflverse at all (D2)."""
    out = ask({"entity": "player_game", "where": [{"term": "pro_bowler"}]})
    assert out["error"] == "unresolved_term"
    assert out["terms"][0]["not_computable"]
    assert out["terms"][0]["suggestions"] == []


def test_a_prior_season_definition_has_no_prior_of_its_own(ask, real_store) -> None:
    """Its own row is already last season, so `prior` would be two seasons back."""
    real_store.save(
        DefinitionIn(
            name="usage_fell",
            entity="player_season",
            signal="delta",
            params={
                "attr": "snap_share_mean",
                "baseline": "prior.snap_share_mean",
                "value": 0.7,
            },
            description="Usage fell against the season before.",
        )
    )
    fine = ask(
        {
            "entity": "player_game",
            "where": [{"term": "usage_fell"}],
            "seasons": {"from": 2023, "to": 2023},
        }
    )
    assert fine["ok"], fine

    out = ask(
        {
            "entity": "player_game",
            "where": [{"term": "usage_fell", "basis": "prior_season"}],
            "seasons": {"from": 2023, "to": 2023},
        }
    )
    assert out["ok"] is False
    assert "two seasons back" in out["message"]


def test_a_broken_definition_fails_its_own_query_not_everyone_elses(ask, real_store) -> None:
    """D10: quarantine is per definition; the rest of the store keeps working."""
    (real_store.directory / "stale.json").write_text(
        '{"name": "stale", "entity": "player_game", "signal": "rule", '
        '"params": {"rules": [{"attr": "gone_away", "op": ">=", "value": 1}]}}'
    )
    real_store.load()
    try:
        broken = ask({"entity": "player_game", "where": [{"term": "stale"}]})
        assert broken["error"] == "definition_broken"
        assert "stale" in broken["definitions"]

        fine = ask({"entity": "player_game", "where": [{"term": "played"}]})
        assert fine["ok"]
    finally:
        (real_store.directory / "stale.json").unlink()
        real_store.load()


def test_a_limit_beyond_the_cap_is_refused(ask, real_settings) -> None:
    out = ask({"entity": "player_game", "limit": real_settings.query_row_cap + 1})
    assert out["error"] == "invalid_plan"


@pytest.mark.parametrize("term", ["star_player", "starter", "injury_exit", "bellcow", "elite"])
def test_the_on_ramp_words_are_deliberately_not_shipped(ask, term: str) -> None:
    """These are the vocabulary's front door: the gate fires and the user decides."""
    out = ask({"entity": "player_game", "where": [{"term": term}]})
    assert out["error"] == "unresolved_term"
