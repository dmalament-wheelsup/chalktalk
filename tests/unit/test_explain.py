"""English rendering of definitions.

Every answer names the definitions behind it, and a name is not something anyone
can disagree with. These check that the tree reads as prose and that a broken
definition still explains itself rather than blowing up.
"""

from __future__ import annotations

from chalktalk.definitions.explain import explain
from chalktalk.definitions.spec import DefinitionIn

PLAYED = {"rules": [{"attr": "snaps_unit", "op": ">=", "value": 1}]}


def _save(store, name, params, entity="player_game", signal="rule", **kwargs):
    return store.save(
        DefinitionIn(name=name, entity=entity, signal=signal, params=params, **kwargs)
    )


def test_a_rule_reads_as_a_sentence(mini_store) -> None:
    saved = _save(mini_store, "played", PLAYED)
    assert explain(saved, mini_store).text == "snaps_unit ≥ 1"


def test_the_coverage_window_is_attached(mini_store) -> None:
    saved = _save(mini_store, "played", PLAYED)
    rendered = explain(saved, mini_store).render()
    assert "[2022-2023]" in rendered


def test_a_participation_definition_is_bounded_by_participation(mini_store) -> None:
    """The mini league has participation for 2023 only, as the real one starts in 2016."""
    saved = _save(
        mini_store,
        "left_early",
        {"rules": [{"attr": "pp_missed_tail_frac", "op": ">=", "value": 0.25}]},
    )
    explanation = explain(saved, mini_store)
    assert explanation.coverage.first == 2023


def test_a_composite_explains_its_parts(mini_store) -> None:
    _save(mini_store, "played", PLAYED)
    _save(mini_store, "regular", {"rules": [{"attr": "game_type", "op": "=", "value": "REG"}]})
    saved = _save(
        mini_store,
        "played_regular",
        {"op": "all_of", "terms": ["played", "regular"]},
        signal="composite",
    )
    explanation = explain(saved, mini_store)
    assert explanation.text == "played and regular"
    assert [p.name for p in explanation.parts] == ["played", "regular"]
    rendered = explanation.render()
    assert "snaps_unit ≥ 1" in rendered
    assert "game_type is 'REG'" in rendered


def test_nested_composites_nest_in_the_output(mini_store) -> None:
    _save(mini_store, "played", PLAYED)
    _save(mini_store, "evidence", {"op": "any_of", "terms": ["played"]}, signal="composite")
    saved = _save(mini_store, "outer", {"op": "all_of", "terms": ["evidence"]}, signal="composite")
    rendered = explain(saved, mini_store).render()
    assert "\n  evidence:" in rendered
    assert "\n    played:" in rendered


def test_a_prior_season_definition_says_when_it_is_measured(mini_store) -> None:
    saved = _save(
        mini_store,
        "was_heavy",
        {"rules": [{"attr": "snap_share_mean", "op": ">=", "value": 0.9}]},
        entity="player_season",
        basis="prior_season",
    )
    assert "measured on the previous season" in explain(saved, mini_store).text


def test_a_percentile_explains_its_cohort_and_eligibility(mini_store) -> None:
    saved = _save(
        mini_store,
        "top_usage",
        {"attr": "snap_share_mean", "cohort": ["season", "position_group"], "pctile": 90},
        entity="player_season",
        signal="percentile",
    )
    text = explain(saved, mini_store).text
    assert "90th percentile within season and position_group" in text
    assert "games_played_share ≥ 0.5" in text


def test_evidence_names_the_columns_worth_showing(mini_store) -> None:
    saved = _save(mini_store, "played", PLAYED)
    assert [str(r) for r in explain(saved, mini_store).evidence] == ["snaps_unit"]


def test_a_broken_definition_still_explains_itself(mini_store) -> None:
    """It has to appear in a listing with a reason, not vanish or raise."""
    import json

    mini_store.directory.mkdir(parents=True, exist_ok=True)
    (mini_store.directory / "stale.json").write_text(
        json.dumps(
            {
                "name": "stale",
                "entity": "player_game",
                "signal": "rule",
                "params": {"rules": [{"attr": "gone_away", "op": ">=", "value": 1}]},
            }
        )
    )
    mini_store.load()
    stale = mini_store._read_raw("stale")
    explanation = explain(stale, mini_store)
    assert explanation.warnings
    assert "gone_away" in explanation.render()
