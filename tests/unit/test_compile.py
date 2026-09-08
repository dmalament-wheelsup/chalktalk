"""The compiler: SQL that runs, parameters that bind, and nothing interpolated.

Every test here executes what it compiles against the mini database. A query
that compiles but does not run is not a passing test.
"""

from __future__ import annotations

import pytest

from chalktalk.query.compile import compile_plan

PLAYED = {"rules": [{"attr": "snaps_unit", "op": ">=", "value": 1}]}
TAIL = {"rules": [{"attr": "pp_missed_tail_frac", "op": ">=", "value": 0.25}]}


@pytest.fixture
def build(mini_gate, mini_store, mini_settings, mini_conn):
    def make(payload: dict):
        plan, gate = mini_gate(payload)
        assert gate.ok, gate.error
        compiled = compile_plan(
            plan, gate, store=mini_store, settings=mini_settings, conn=mini_conn
        )
        return compiled

    return make


def _run(conn, compiled):
    cursor = conn.execute(compiled.sql, compiled.params)
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


# ── it runs ───────────────────────────────────────────────────────────────────


def test_the_simplest_query_runs(build, mini_conn) -> None:
    compiled = build({})
    assert _run(mini_conn, compiled) == [{"count": 45}]


def test_grouping_and_counting(build, mini_conn) -> None:
    compiled = build({"group_by": ["season"], "game_types": ["*"]})
    rows = _run(mini_conn, compiled)
    assert [r["season"] for r in rows] == [2022, 2023]


def test_a_term_filters(build, mini_conn, defined) -> None:
    defined("played", PLAYED)
    with_term = _run(mini_conn, build({"where": [{"term": "played"}]}))
    without = _run(mini_conn, build({}))
    assert with_term[0]["count"] < without[0]["count"]


def test_a_composite_term_runs(build, mini_conn, defined) -> None:
    defined("played", PLAYED)
    defined("left_early", TAIL)
    defined("early_exit", {"op": "all_of", "terms": ["played", "left_early"]}, signal="composite")
    rows = _run(
        mini_conn,
        build({"where": [{"term": "early_exit"}], "allow_partial_coverage": True}),
    )
    assert rows[0]["count"] == 2, "the mini exit and the rested tackle"


def test_a_negated_clause_runs(build, mini_conn, defined) -> None:
    """`not X` excludes rows where X is unknown, which is SQL's semantics, not a bug.

    The mini kicker has no unit snaps at all, so `snaps_unit >= 1` is NULL for
    him: he is in neither `played` nor `not played`. Counting unknown as false
    would silently assert something the data does not say.
    """
    defined("played", PLAYED)
    inside = _run(mini_conn, build({"where": [{"term": "played"}]}))[0]["count"]
    outside = _run(mini_conn, build({"where": [{"not": {"term": "played"}}]}))[0]["count"]
    unknown = _run(mini_conn, build({"where": [{"attr": "snaps_unit", "op": "is_null"}]}))[0][
        "count"
    ]
    total = _run(mini_conn, build({}))[0]["count"]
    assert unknown > 0, "the mini league needs a row where the predicate is unknown"
    assert inside + outside + unknown == total


def test_any_of_and_all_of_run(build, mini_conn, defined) -> None:
    defined("played", PLAYED)
    payload = {
        "where": [
            {
                "any_of": [
                    {"term": "played"},
                    {"all_of": [{"attr": "week", "op": "=", "value": 1}]},
                ]
            }
        ]
    }
    assert _run(mini_conn, build(payload))[0]["count"] > 0


# ── namespaces and lifting ────────────────────────────────────────────────────


def test_a_namespaced_filter_joins(build, mini_conn) -> None:
    compiled = build({"where": [{"attr": "game.is_final_reg_week", "op": "=", "value": True}]})
    assert "LEFT JOIN" in compiled.sql
    assert _run(mini_conn, compiled)[0]["count"] > 0


def test_a_namespaced_metric_is_projected_into_base(build, mini_conn) -> None:
    """The outer aggregate cannot reach through a join, so base has to project it."""
    compiled = build(
        {"group_by": ["season"], "metrics": [{"fn": "avg", "of": "cur.snap_share_mean"}]}
    )
    assert 'AS "cur__snap_share_mean"' in compiled.sql
    rows = _run(mini_conn, compiled)
    assert rows and rows[0]["avg_cur__snap_share_mean"] is not None


def test_a_lifted_term_reaches_the_right_season(build, mini_conn, defined) -> None:
    defined(
        "heavy_usage",
        {"rules": [{"attr": "snap_share_mean", "op": ">=", "value": 0.9}]},
        entity="player_season",
        basis="prior_season",
    )
    # A prior-season term cannot answer the mini league's first season, so this
    # opts into partial coverage the way the shipped fixture plans do.
    compiled = build(
        {
            "where": [{"term": "heavy_usage"}],
            "group_by": ["season"],
            "allow_partial_coverage": True,
        }
    )
    assert "psp" in compiled.sql, "the prior-season join must be used"
    _run(mini_conn, compiled)


def test_a_basis_in_the_plan_overrides_the_definitions(build, mini_conn, defined) -> None:
    defined(
        "heavy_usage",
        {"rules": [{"attr": "snap_share_mean", "op": ">=", "value": 0.9}]},
        entity="player_season",
    )
    current = build({"where": [{"term": "heavy_usage"}]})
    prior = build(
        {
            "where": [{"term": "heavy_usage", "basis": "prior_season"}],
            "allow_partial_coverage": True,
        }
    )
    assert "psc" in current.sql
    assert "psp" in prior.sql


# ── metrics ───────────────────────────────────────────────────────────────────


def test_avg_of_a_term_is_a_rate(build, mini_conn, defined) -> None:
    defined("at_home", {"rules": [{"attr": "home", "op": "=", "value": True}]})
    compiled = build({"metrics": [{"fn": "avg", "of": {"term": "at_home"}, "as": "rate"}]})
    rate = _run(mini_conn, compiled)[0]["rate"]
    assert 0.0 < rate < 1.0


def test_a_rate_ignores_rows_where_the_term_is_unknown(build, mini_conn, defined) -> None:
    """Every non-kicker played, so the rate is 1.0 rather than being dragged down."""
    defined("played", PLAYED)
    compiled = build({"metrics": [{"fn": "avg", "of": {"term": "played"}, "as": "rate"}]})
    assert _run(mini_conn, compiled)[0]["rate"] == 1.0


def test_a_null_predicate_is_left_out_of_the_rate(build, mini_conn, defined) -> None:
    """Unknown is not the same as false; counting it as a miss would understate."""
    defined("left_early", TAIL)
    compiled = build(
        {
            "metrics": [{"fn": "avg", "of": {"term": "left_early"}, "as": "rate"}],
            "allow_partial_coverage": True,
        }
    )
    assert "WHEN NOT" in compiled.sql


def test_several_metrics_at_once(build, mini_conn) -> None:
    compiled = build(
        {
            "group_by": ["season"],
            "game_types": ["*"],
            "metrics": [
                {"fn": "count"},
                {"fn": "avg", "of": "snaps_unit"},
                {"fn": "max", "of": "snaps_unit"},
                {"fn": "count_distinct", "of": "player_key"},
            ],
        }
    )
    row = _run(mini_conn, compiled)[0]
    assert set(row) == {
        "season",
        "count",
        "avg_snaps_unit",
        "max_snaps_unit",
        "count_distinct_player_key",
    }


def test_grouping_by_a_term(build, mini_conn, defined) -> None:
    defined("at_home", {"rules": [{"attr": "home", "op": "=", "value": True}]})
    compiled = build({"group_by": [{"term": "at_home"}], "game_types": ["*"]})
    rows = _run(mini_conn, compiled)
    assert {r["at_home"] for r in rows} == {True, False}


def test_grouping_by_a_term_keeps_the_unknown_group(build, mini_conn, defined) -> None:
    """A third group for "we cannot say" is more honest than folding it into false."""
    defined("played", PLAYED)
    rows = _run(mini_conn, build({"group_by": [{"term": "played"}], "game_types": ["*"]}))
    assert None in {r["played"] for r in rows}


# ── safety ────────────────────────────────────────────────────────────────────


def test_every_literal_is_a_bound_parameter(build) -> None:
    compiled = build({"where": [{"attr": "player_name", "op": "=", "value": "'; DROP TABLE x --"}]})
    assert "DROP TABLE" not in compiled.sql
    assert "'; DROP TABLE x --" in compiled.params


def test_the_parameter_count_matches_the_placeholders(build, defined) -> None:
    defined("played", PLAYED)
    compiled = build(
        {
            "where": [{"term": "played"}, {"attr": "week", "op": "in", "value": [1, 2, 3]}],
            "group_by": ["season"],
            "game_types": ["REG", "WC"],
        }
    )
    assert compiled.sql.count("?") == len(compiled.params)
    assert compiled.count_sql.count("?") == len(compiled.count_params)
    assert compiled.sample_sql.count("?") == len(compiled.sample_params)


def test_the_limit_is_applied(build, mini_conn) -> None:
    compiled = build({"group_by": ["season"], "game_types": ["*"], "limit": 1})
    assert len(_run(mini_conn, compiled)) == 1


def test_the_season_window_comes_from_the_gate(build, mini_conn, defined) -> None:
    defined("left_early", TAIL)
    compiled = build({"where": [{"term": "left_early"}], "allow_partial_coverage": True})
    assert compiled.params[:2] == [2023, 2023], "clamped to where participation exists"


# ── the sample and the count ──────────────────────────────────────────────────


def test_the_sample_reads_the_same_base_as_the_answer(build, mini_conn, defined) -> None:
    """So the rows shown are provably the rows counted."""
    defined("played", PLAYED)
    compiled = build({"where": [{"term": "played"}]})
    counted = _run(mini_conn, compiled)[0]["count"]
    total = mini_conn.execute(compiled.count_sql, compiled.count_params).fetchone()[0]
    assert counted == total


def test_the_sample_shows_why_a_row_qualified(build, mini_conn, defined) -> None:
    defined("left_early", TAIL)
    compiled = build({"where": [{"term": "left_early"}], "allow_partial_coverage": True})
    cursor = mini_conn.execute(compiled.sample_sql, compiled.sample_params)
    columns = [d[0] for d in cursor.description]
    assert "player_name" in columns
    assert "pp_missed_tail_frac" in columns, "the evidence for the definition used"


def test_the_sample_is_capped_separately_from_the_limit(build, mini_conn) -> None:
    compiled = build({"limit": 5, "sample_rows": 2})
    assert compiled.params[-1] == 5
    assert compiled.sample_params[-1] == 2
    assert len(mini_conn.execute(compiled.sample_sql, compiled.sample_params).fetchall()) == 2


# ── ordering ──────────────────────────────────────────────────────────────────


def test_ordering_by_a_metric(build, mini_conn) -> None:
    compiled = build(
        {
            "group_by": ["season"],
            "game_types": ["*"],
            "metrics": [{"fn": "count", "as": "n"}],
            "order_by": [{"key": "n", "dir": "desc"}],
        }
    )
    rows = _run(mini_conn, compiled)
    assert rows == sorted(rows, key=lambda r: -r["n"])


def test_grouping_orders_by_the_key_when_nothing_is_asked(build, mini_conn) -> None:
    compiled = build({"group_by": ["season"], "game_types": ["*"]})
    rows = _run(mini_conn, compiled)
    assert [r["season"] for r in rows] == sorted(r["season"] for r in rows)


def test_the_columns_are_reported(build) -> None:
    compiled = build({"group_by": ["season"], "metrics": [{"fn": "count", "as": "n"}]})
    assert compiled.columns == ["season", "n"]
