"""`propose`: what a fuzzy word could mean, and when to say it cannot be answered.

Deterministic by design — the server has no model in it — so these assert exact
behaviour rather than plausibility.
"""

from __future__ import annotations

from chalktalk.definitions.propose import as_dict, propose
from chalktalk.definitions.spec import DefinitionIn


def _propose(term: str, store, **kwargs):
    return propose(term, store=store, **kwargs)


def test_it_is_deterministic(mini_store) -> None:
    first = as_dict(_propose("star player", mini_store))
    second = as_dict(_propose("star player", mini_store))
    assert first == second


def test_a_fuzzy_word_finds_its_family(mini_store) -> None:
    assert _propose("star player", mini_store).families == ["star"]
    assert _propose("bellcow", mini_store).families == ["workhorse"]
    assert _propose("nail biter", mini_store).families == ["close game"]


def test_an_unknown_word_yields_nothing_rather_than_guessing(mini_store) -> None:
    proposal = _propose("zamboni", mini_store)
    assert proposal.families == []
    assert proposal.suggestions == []


def test_something_the_data_cannot_answer_says_so(mini_store) -> None:
    """D2: nflverse has no per-season honours table, and a substitute would lie."""
    proposal = _propose("pro bowler", mini_store)
    assert proposal.not_computable
    assert "honours" in proposal.not_computable[0]
    assert proposal.suggestions == [], "do not quietly offer a different question"


def test_pressure_is_also_out_of_reach_in_v1(mini_store) -> None:
    proposal = _propose("pass rush pressure", mini_store)
    assert proposal.not_computable


def test_the_star_note_explains_why_there_is_no_single_answer(mini_store) -> None:
    notes = _propose("star player", mini_store).notes
    assert notes and "disagree about real players" in notes[0]


def test_suggestions_are_ready_to_save(mini_store) -> None:
    """Each candidate is a DefinitionIn the user can accept as it stands."""
    proposal = _propose("bellcow", mini_store)
    assert proposal.suggestions
    for suggestion in proposal.suggestions:
        assert isinstance(suggestion.definition, DefinitionIn)
        saved = mini_store.save(suggestion.definition)
        assert saved.name == suggestion.definition.name


def test_a_workload_word_suggests_carries(mini_store) -> None:
    names = [s.definition.name for s in _propose("bellcow", mini_store).suggestions]
    assert any("carries" in n for n in names)


def test_related_attributes_are_ranked_by_the_family_first(mini_store) -> None:
    attributes = _propose("star player", mini_store).related_attributes
    assert attributes[0].name == "snap_share_mean"


def test_an_injury_word_surfaces_the_exit_attributes(mini_store) -> None:
    names = [a.name for a in _propose("left the game with injury", mini_store).related_attributes]
    assert "pp_missed_tail_frac" in names
    assert "played_team_next_game" in names


def test_an_inverted_attribute_is_not_offered_as_a_top_percentile(mini_store) -> None:
    """Round 1 is the best round, so "top 10% of draft_round" is backwards."""
    names = [s.definition.name for s in _propose("star player", mini_store).suggestions]
    assert not any("draft_round" in n for n in names)


def test_an_existing_definition_is_offered_before_a_new_one(mini_store) -> None:
    mini_store.save(
        DefinitionIn(
            name="star_player",
            entity="player_season",
            signal="rule",
            params={"rules": [{"attr": "snap_share_mean", "op": ">=", "value": 0.8}]},
            description="mine",
        )
    )
    proposal = _propose("star_player", mini_store)
    assert [m.name for m in proposal.matches] == ["star_player"]


def test_a_near_miss_still_matches(mini_store) -> None:
    mini_store.save(
        DefinitionIn(
            name="star_player",
            entity="player_game",
            signal="rule",
            params={"rules": [{"attr": "snaps_unit", "op": ">=", "value": 1}]},
        )
    )
    assert [m.name for m in _propose("star_players", mini_store).matches] == ["star_player"]


def test_a_broken_definition_is_never_offered(mini_store) -> None:
    import json

    mini_store.directory.mkdir(parents=True, exist_ok=True)
    (mini_store.directory / "star_player.json").write_text(
        json.dumps(
            {
                "name": "star_player",
                "entity": "player_game",
                "signal": "rule",
                "params": {"rules": [{"attr": "gone_away", "op": ">=", "value": 1}]},
            }
        )
    )
    mini_store.load()
    assert _propose("star_player", mini_store).matches == []


def test_the_signal_schemas_are_included(mini_store) -> None:
    """The caller needs them to write a definition of its own."""
    signals = _propose("star player", mini_store).signals
    assert set(signals) == {"rule", "percentile", "rank", "delta", "composite"}
    assert "properties" in signals["percentile"]


def test_an_entity_filter_narrows_the_attributes(mini_store) -> None:
    proposal = _propose("star player", mini_store, entity="player_game")
    assert {a.entity for a in proposal.related_attributes} == {"player_game"}


def test_context_sharpens_the_families(mini_store) -> None:
    """The rest of the question is often where the football word actually is."""
    plain = _propose("tendency", mini_store).families
    with_context = _propose("tendency", mini_store, context="on fourth down").families
    assert plain == []
    assert "fourth down" in with_context


def test_the_mcp_shape_is_serialisable(mini_store) -> None:
    import json

    payload = as_dict(_propose("star player", mini_store))
    assert json.loads(json.dumps(payload))["term"] == "star player"
