"""The result envelope.

The model narrates; the envelope makes the narration checkable. For this class
of question an unverifiable number is worse than no number, so what matters here
is that every answer carries the definitions it used, the seasons it actually
covered, the matched rows and the SQL.
"""

from __future__ import annotations

import json

from chalktalk.query.envelope import Envelope, ErrorEnvelope

PLAYED = {"rules": [{"attr": "snaps_unit", "op": ">=", "value": 1}]}
TAIL = {"rules": [{"attr": "pp_missed_tail_frac", "op": ">=", "value": 0.25}]}


def test_a_successful_answer_validates(mini_query, defined) -> None:
    defined("played", PLAYED)
    out = mini_query({"where": [{"term": "played"}], "group_by": ["season"]})
    assert Envelope.model_validate(out).ok is True


def test_it_is_json_serialisable(mini_query) -> None:
    out = mini_query({"group_by": ["season"]})
    assert json.loads(json.dumps(out, default=str))["entity"] == "player_game"


def test_the_english_line_states_what_was_asked(mini_query, defined) -> None:
    defined("played", PLAYED)
    out = mini_query(
        {
            "where": [{"term": "played"}, {"attr": "snaps_unit", "op": "<", "value": 15}],
            "group_by": ["season"],
        }
    )
    assert out["english"] == (
        "Count of player-games (REG, 2022–2023) where played and snaps_unit < 15, by season."
    )


def test_the_english_line_says_when_a_term_looks_back(mini_query, defined) -> None:
    defined(
        "heavy_usage",
        {"rules": [{"attr": "snap_share_mean", "op": ">=", "value": 0.9}]},
        entity="player_season",
        basis="prior_season",
    )
    out = mini_query({"where": [{"term": "heavy_usage", "basis": "prior_season"}]})
    assert "heavy_usage [prior season]" in out["english"]


def test_the_english_line_describes_a_rate(mini_query, defined) -> None:
    defined("at_home", {"rules": [{"attr": "home", "op": "=", "value": True}]})
    out = mini_query({"metrics": [{"fn": "avg", "of": {"term": "at_home"}, "as": "rate"}]})
    assert out["english"].startswith("Share of rows where at_home")


def test_every_definition_used_is_named_and_explained(mini_query, defined) -> None:
    """Including the ones reached through a composite, which the user never typed."""
    defined("played", PLAYED)
    defined("left_early", TAIL)
    defined("early_exit", {"op": "all_of", "terms": ["played", "left_early"]}, signal="composite")
    out = mini_query({"where": [{"term": "early_exit"}], "allow_partial_coverage": True})
    used = {d["name"]: d for d in out["definitions_used"]}
    assert set(used) == {"played", "left_early", "early_exit"}
    assert used["played"]["explanation"] == "snaps_unit ≥ 1"
    assert used["early_exit"]["explanation"] == "played and left_early"
    assert used["left_early"]["coverage"] == {"first": 2023, "last": 2023}


def test_the_seasons_actually_covered_are_reported(mini_query, defined) -> None:
    defined("left_early", TAIL)
    out = mini_query(
        {
            "where": [{"term": "left_early"}],
            "seasons": {"from": 2022, "to": 2023},
            "allow_partial_coverage": True,
        }
    )
    assert out["seasons"] == {
        "requested": [2022, 2023],
        "covered": [2023, 2023],
        "excluded": [2022],
        "partial": True,
        "includes_incomplete_season": False,
    }


def test_a_matched_row_sample_comes_back(mini_query, defined) -> None:
    """CLAUDE.md: return matched rows, always — a rested starter looks like an exit."""
    defined("left_early", TAIL)
    out = mini_query({"where": [{"term": "left_early"}], "allow_partial_coverage": True})
    sample = out["sample"]
    assert sample["total_matched"] == 2
    assert len(sample["rows"]) == 2
    assert "player_name" in sample["columns"]
    assert "pp_missed_tail_frac" in sample["columns"]


def test_the_sql_and_its_parameters_are_returned(mini_query, defined) -> None:
    defined("played", PLAYED)
    out = mini_query({"where": [{"term": "played"}], "group_by": ["season"]})
    assert out["sql"].count("?") == len(out["sql_params"])
    assert "player_game" in out["sql"]


def test_timings_are_reported(mini_query) -> None:
    assert set(mini_query({})["timing_ms"]) == {"main", "sample", "count"}


def test_warnings_are_carried_through(mini_query, defined) -> None:
    defined("left_early", TAIL)
    out = mini_query(
        {
            "where": [{"term": "left_early"}],
            "seasons": {"from": 2022, "to": 2023},
            "allow_partial_coverage": True,
        }
    )
    assert any("excluded" in w for w in out["warnings"])


def test_the_question_is_echoed(mini_query) -> None:
    out = mini_query({"question": "how many player-games?"})
    assert out["question"] == "how many player-games?"


# ── refusals ──────────────────────────────────────────────────────────────────


def test_a_refusal_validates_as_an_error_envelope(mini_query) -> None:
    out = mini_query({"where": [{"term": "star_player"}]})
    assert ErrorEnvelope.model_validate(out).error == "unresolved_term"
    assert out["ok"] is False


def test_a_refusal_carries_no_rows(mini_query) -> None:
    out = mini_query({"where": [{"term": "star_player"}]})
    assert "rows" not in out


def test_a_timeout_becomes_an_error_envelope(
    mini_conn, mini_store, mini_settings, monkeypatch
) -> None:
    """The interruption itself is tested in test_execute; this is the wrapper.

    What matters here is that a cancelled query returns a refusal naming the SQL
    and the limit, rather than a partial answer or a stack trace.
    """
    from chalktalk.coverage import Coverage
    from chalktalk.query import run as run_module
    from chalktalk.query.execute import QueryTimeout
    from chalktalk.query.plan import QueryPlan

    def timeout(*args, **kwargs):
        raise QueryTimeout(mini_settings.query_timeout_s)

    monkeypatch.setattr(run_module, "run", timeout)
    out = run_module.query(
        QueryPlan.model_validate({"entity": "player_game"}),
        store=mini_store,
        coverage=Coverage(mini_conn, mini_settings),
        settings=mini_settings,
        conn=mini_conn,
    )
    assert out["error"] == "sql_timeout"
    assert out["sql"]
    assert out["timeout_s"] == mini_settings.query_timeout_s


# ── explain_query ─────────────────────────────────────────────────────────────


def test_explain_returns_everything_but_the_numbers(
    mini_conn, mini_store, mini_settings, defined
) -> None:
    from chalktalk.coverage import Coverage
    from chalktalk.query.plan import QueryPlan
    from chalktalk.query.run import explain_query

    defined("played", PLAYED)
    plan = QueryPlan.model_validate(
        {"entity": "player_game", "where": [{"term": "played"}], "group_by": ["season"]}
    )
    out = explain_query(
        plan,
        store=mini_store,
        coverage=Coverage(mini_conn, mini_settings),
        settings=mini_settings,
        conn=mini_conn,
    )
    assert out["english"]
    assert out["sql"]
    assert out["definitions_used"]
    assert out["rows"] == []
    assert "sample" not in out
    assert "timing_ms" not in out


def test_explain_refuses_the_same_way_query_does(mini_conn, mini_store, mini_settings) -> None:
    from chalktalk.coverage import Coverage
    from chalktalk.query.plan import QueryPlan
    from chalktalk.query.run import explain_query

    plan = QueryPlan.model_validate({"entity": "player_game", "where": [{"term": "star_player"}]})
    out = explain_query(
        plan,
        store=mini_store,
        coverage=Coverage(mini_conn, mini_settings),
        settings=mini_settings,
        conn=mini_conn,
    )
    assert out["error"] == "unresolved_term"
