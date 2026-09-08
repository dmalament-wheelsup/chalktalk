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


# ── 6b. season lag: a term read a season back cannot answer the first season ──


HEAVY = {"rules": [{"attr": "snap_share_mean", "op": ">=", "value": 0.9}]}


def _heavy(defined, basis="prior_season"):
    defined("heavy_usage", HEAVY, entity="player_season", basis=basis)


def test_a_prior_season_term_cannot_answer_the_first_season(mini_gate, defined) -> None:
    """The bug this guards: 2013 reported as covered when no 2012 row exists.

    The mini league starts in 2022, so a term measured on the previous season
    can only speak about 2023. Reporting 2022 as covered would make a season
    with no possible match read as a season with no matches.
    """
    _heavy(defined)
    _, gate = mini_gate({"where": [{"term": "heavy_usage"}], "allow_partial_coverage": True})
    assert gate.ok, gate.error
    assert gate.excluded == [2022]
    assert (gate.seasons.first, gate.seasons.last) == (2023, 2023)


def test_a_prior_season_gap_is_refused_by_default(mini_gate, defined) -> None:
    _heavy(defined)
    _, gate = mini_gate({"where": [{"term": "heavy_usage"}]})
    assert gate.error.error == "coverage_gap"
    assert gate.error.model_dump()["covered"] == [2023, 2023]


def test_the_lag_is_explained_not_reported_as_missing_data(mini_gate, defined) -> None:
    """ "No data" would send the reader hunting for a hole that is not there."""
    _heavy(defined)
    _, gate = mini_gate({"where": [{"term": "heavy_usage"}], "allow_partial_coverage": True})
    excluded = [w for w in gate.warnings if "excluded" in w]
    assert excluded, gate.warnings
    assert "no data for the attributes used" not in excluded[0]
    assert "season back" in excluded[0]


def test_a_lagged_ref_reports_the_seasons_it_can_answer_for(mini_gate, defined) -> None:
    """`limiting` must be in query seasons, not data seasons."""
    _heavy(defined)
    _, gate = mini_gate({"where": [{"term": "heavy_usage"}]})
    rows = gate.error.model_dump()["limiting"]
    lagged = [r for r in rows if r.get("lag")]
    assert lagged, rows
    assert lagged[0]["first"] == lagged[0]["data_first"] + 1


def test_the_plans_own_basis_drives_the_lag(mini_gate, defined) -> None:
    """A current-season definition asked for on a prior basis lags all the same."""
    _heavy(defined, basis=None)
    _, current = mini_gate({"where": [{"term": "heavy_usage"}]})
    assert current.ok, current.error
    assert current.excluded == []

    _, prior = mini_gate(
        {
            "where": [{"term": "heavy_usage", "basis": "prior_season"}],
            "allow_partial_coverage": True,
        }
    )
    assert prior.ok, prior.error
    assert prior.excluded == [2022]


def test_an_unlagged_term_still_answers_the_first_season(mini_gate, defined) -> None:
    """The shift must apply to the lagged refs only, not to the whole plan."""
    defined("played", PLAYED)
    _, gate = mini_gate({"where": [{"term": "played"}]})
    assert gate.ok, gate.error
    assert gate.excluded == []
    assert gate.seasons.first == 2022


def test_a_prior_namespace_attribute_lags_too(mini_gate) -> None:
    """Not only terms: `prior.` in the plan reads a season back as well."""
    _, gate = mini_gate({"where": [{"attr": "prior.snap_share_mean", "op": ">=", "value": 0.5}]})
    assert gate.error.error == "coverage_gap"
    assert gate.error.model_dump()["covered"] == [2023, 2023]


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
