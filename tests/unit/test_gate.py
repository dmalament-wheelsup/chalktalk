"""The definition gate.

The mechanism the project is built around: a query containing a fuzzy word must
be refused, structurally, with a way forward — not answered from a default the
server picked on its own.
"""

from __future__ import annotations

import json

PLAYED = {"rules": [{"attr": "snaps_unit", "op": ">=", "value": 1}]}
TAIL = {"rules": [{"attr": "pp_missed_tail_frac", "op": ">=", "value": 0.25}]}


# ── 1. invalid_plan ───────────────────────────────────────────────────────────


def test_a_limit_over_the_cap_is_refused(mini_gate, mini_settings) -> None:
    _, gate = mini_gate({"limit": mini_settings.query_row_cap + 1})
    assert gate.error.error == "invalid_plan"
    assert "cap" in gate.error.message


def test_an_unknown_game_type_lists_the_real_ones(mini_gate) -> None:
    _, gate = mini_gate({"game_types": ["REGULAR"]})
    assert gate.error.error == "invalid_plan"
    assert "REG" in gate.error.message


def test_an_empty_game_type_list_is_refused(mini_gate) -> None:
    _, gate = mini_gate({"game_types": []})
    assert gate.error.error == "invalid_plan"


def test_too_many_sample_rows_is_refused(mini_gate) -> None:
    _, gate = mini_gate({"sample_rows": 500})
    assert gate.error.error == "invalid_plan"


# ── 2. unknown_attribute ──────────────────────────────────────────────────────


def test_an_unknown_attribute_suggests(mini_gate) -> None:
    _, gate = mini_gate({"where": [{"attr": "snaps_unti", "op": ">=", "value": 1}]})
    assert gate.error.error == "unknown_attribute"
    miss = gate.error.model_dump()["attributes"][0]
    assert miss["attr"] == "snaps_unti"
    assert "snaps_unit" in miss["did_you_mean"]


def test_every_unknown_attribute_is_reported_at_once(mini_gate) -> None:
    _, gate = mini_gate(
        {
            "where": [{"attr": "nonesuch_one", "op": ">=", "value": 1}],
            "group_by": ["nonesuch_two"],
        }
    )
    assert {m["attr"] for m in gate.error.model_dump()["attributes"]} == {
        "nonesuch_one",
        "nonesuch_two",
    }


def test_an_attribute_in_a_metric_is_checked(mini_gate) -> None:
    """A coverage or catalog check that only read `where` would miss this."""
    _, gate = mini_gate({"metrics": [{"fn": "avg", "of": "nonesuch"}]})
    assert gate.error.error == "unknown_attribute"


def test_an_order_key_may_name_a_metric(mini_gate, defined) -> None:
    defined("played", PLAYED)
    _, gate = mini_gate(
        {
            "where": [{"term": "played"}],
            "group_by": ["season"],
            "metrics": [{"fn": "count", "as": "n"}],
            "order_by": [{"key": "n", "dir": "desc"}],
            "allow_partial_coverage": True,
        }
    )
    assert gate.ok, gate.error


def test_an_order_key_naming_nothing_is_refused(mini_gate) -> None:
    _, gate = mini_gate({"order_by": [{"key": "nonesuch"}]})
    assert gate.error.error == "unknown_attribute"


# ── 3. unresolved_term — the point of the whole thing ─────────────────────────


def test_an_unresolved_term_is_refused_with_a_way_forward(mini_gate) -> None:
    _, gate = mini_gate({"where": [{"term": "star_player"}]})
    assert gate.error.error == "unresolved_term"
    payload = gate.error.model_dump()
    assert payload["next_step"] == "propose_definition"
    assert payload["terms"][0]["term"] == "star_player"
    assert payload["terms"][0]["suggestions"], "a refusal must offer candidates"


def test_every_unresolved_term_is_reported_at_once(mini_gate) -> None:
    """Otherwise a caller learns about them one round trip at a time."""
    _, gate = mini_gate({"where": [{"term": "star_player"}, {"term": "early_exit"}]})
    assert {t["term"] for t in gate.error.model_dump()["terms"]} == {
        "star_player",
        "early_exit",
    }


def test_a_term_in_a_metric_is_also_gated(mini_gate) -> None:
    _, gate = mini_gate({"metrics": [{"fn": "avg", "of": {"term": "won"}}]})
    assert gate.error.error == "unresolved_term"


def test_a_term_in_a_group_key_is_also_gated(mini_gate) -> None:
    _, gate = mini_gate({"group_by": [{"term": "is_home"}]})
    assert gate.error.error == "unresolved_term"


def test_a_term_that_cannot_be_computed_says_so(mini_gate) -> None:
    _, gate = mini_gate({"where": [{"term": "pro_bowler"}]})
    reported = gate.error.model_dump()["terms"][0]
    assert reported["not_computable"], "the honest answer is that the data cannot say"


def test_an_alias_resolves(mini_gate, mini_store, defined) -> None:
    defined("played", PLAYED, aliases=["suited_up"])
    _, gate = mini_gate({"where": [{"term": "suited_up"}], "allow_partial_coverage": True})
    assert gate.ok, gate.error


# ── 4. definition_broken ──────────────────────────────────────────────────────


def test_a_quarantined_definition_is_refused(mini_gate, mini_store) -> None:
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
    _, gate = mini_gate({"where": [{"term": "stale"}]})
    assert gate.error.error == "definition_broken"
    assert "stale" in gate.error.model_dump()["definitions"]


# ── 5. entity_mismatch ────────────────────────────────────────────────────────


def test_a_definition_from_the_wrong_entity_is_refused(mini_gate, defined) -> None:
    """A player_game definition has no meaning on team_game (D16)."""
    defined("played", PLAYED)
    _, gate = mini_gate({"entity": "team_game", "where": [{"term": "played"}]})
    assert gate.error.error == "entity_mismatch"
    payload = gate.error.model_dump()
    assert payload["definition_entity"] == "player_game"
    assert payload["plan_entity"] == "team_game"
    assert "player_game" in payload["allowed_entities"]


def test_a_coarser_definition_lifts_without_complaint(mini_gate, defined) -> None:
    defined(
        "final_week",
        {"rules": [{"attr": "is_final_reg_week", "op": "=", "value": True}]},
        entity="game",
    )
    _, gate = mini_gate({"where": [{"term": "final_week"}]})
    assert gate.ok, gate.error


def test_a_basis_on_a_non_season_definition_is_refused(mini_gate, defined) -> None:
    defined("played", PLAYED)
    _, gate = mini_gate({"where": [{"term": "played", "basis": "prior_season"}]})
    assert gate.error.error == "invalid_plan"
    assert "basis applies only" in gate.error.message


# ── 6. coverage_gap ───────────────────────────────────────────────────────────


def test_a_coverage_gap_is_refused_by_default(mini_gate, defined) -> None:
    """Participation is 2023-only in the mini league, as it is 2016+ in the real one."""
    defined("left_early", TAIL)
    _, gate = mini_gate({"where": [{"term": "left_early"}], "seasons": {"from": 2022, "to": 2023}})
    assert gate.error.error == "coverage_gap"
    payload = gate.error.model_dump()
    assert payload["requested"] == [2022, 2023]
    assert payload["covered"] == [2023, 2023]
    assert payload["limiting"]


def test_the_gap_names_what_limits_it(mini_gate, defined) -> None:
    defined("left_early", TAIL)
    _, gate = mini_gate({"where": [{"term": "left_early"}], "seasons": {"from": 2022, "to": 2023}})
    refs = [row["ref"] for row in gate.error.model_dump()["limiting"]]
    assert any("pp_missed_tail_frac" in ref for ref in refs)


def test_partial_coverage_runs_on_what_is_there(mini_gate, defined) -> None:
    defined("left_early", TAIL)
    _, gate = mini_gate(
        {
            "where": [{"term": "left_early"}],
            "seasons": {"from": 2022, "to": 2023},
            "allow_partial_coverage": True,
        }
    )
    assert gate.ok
    assert gate.excluded == [2022]
    assert (gate.seasons.first, gate.seasons.last) == (2023, 2023)
    assert any("excluded" in w for w in gate.warnings)


def test_coverage_covers_attributes_in_metrics(mini_gate) -> None:
    """Not just `where` — the pitfall the plan names."""
    _, gate = mini_gate(
        {
            "metrics": [{"fn": "avg", "of": "pp_missed_tail_frac"}],
            "seasons": {"from": 2022, "to": 2023},
        }
    )
    assert gate.error.error == "coverage_gap"


def test_a_plan_inside_coverage_passes(mini_gate, defined) -> None:
    defined("played", PLAYED)
    _, gate = mini_gate({"where": [{"term": "played"}], "seasons": {"from": 2022, "to": 2023}})
    assert gate.ok, gate.error
    assert gate.excluded == []


# ── 7. warnings, never errors ─────────────────────────────────────────────────


def test_game_types_on_a_season_entity_warn_rather_than_fail(mini_gate) -> None:
    """player_season has no game_type column, so the filter is meaningless, not wrong."""
    _, gate = mini_gate({"entity": "player_season", "game_types": ["WC"]})
    assert gate.ok, gate.error
    assert any("no game_type" in w for w in gate.warnings)


def test_the_definitions_used_are_reported_transitively(mini_gate, defined) -> None:
    defined("played", PLAYED)
    defined("left_early", TAIL)
    defined(
        "early_exit",
        {"op": "all_of", "terms": ["played", "left_early"]},
        signal="composite",
    )
    _, gate = mini_gate({"where": [{"term": "early_exit"}], "allow_partial_coverage": True})
    assert [d.name for d in gate.definitions_used] == ["played", "left_early", "early_exit"]


def test_an_empty_plan_is_allowed(mini_gate) -> None:
    _, gate = mini_gate({})
    assert gate.ok, gate.error
