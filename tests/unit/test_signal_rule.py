"""The `rule` signal: validation, SQL, and English.

Every test that produces SQL executes it against the mini database, so a
predicate that compiles but does not run cannot pass.
"""

from __future__ import annotations

import pytest

from chalktalk.definitions.signals import SIGNALS, CompileCtx
from chalktalk.definitions.spec import Definition, InvalidDefinition

RULE = SIGNALS["rule"]


def _defn(params: dict, entity: str = "player_game", name: str = "test_rule") -> Definition:
    return Definition(name=name, entity=entity, signal="rule", params=params)


def _compile(defn: Definition, ctx_factory) -> tuple[str, list]:
    ctx = CompileCtx(**vars(ctx_factory(defn.entity)))
    compiled = RULE.compile(defn, ctx)
    return compiled.predicate_sql, compiled.params


def _run(mini_conn, defn, ctx_factory, alias: str = "pg") -> list[tuple]:
    """Execute the predicate against player_game and return the matching rows."""
    sql, params = _compile(defn, ctx_factory)
    predicate = sql.replace("{self}", alias)
    return mini_conn.execute(
        f"SELECT player_name, week FROM player_game {alias} WHERE {predicate} "
        f"AND {alias}.season = 2023 ORDER BY player_name, week",
        params,
    ).fetchall()


# ── validation ────────────────────────────────────────────────────────────────


def test_an_unknown_attribute_is_refused_with_suggestions(mini_ctx) -> None:
    defn = _defn({"rules": [{"attr": "snaps_unti", "op": ">=", "value": 1}]})
    with pytest.raises(InvalidDefinition) as excinfo:
        RULE.validate(defn, mini_ctx("player_game"))
    assert "snaps_unit" in excinfo.value.did_you_mean
    assert excinfo.value.field == "rules.snaps_unti"


def test_a_type_mismatch_is_refused(mini_ctx) -> None:
    defn = _defn({"rules": [{"attr": "snaps_unit", "op": ">=", "value": "lots"}]})
    with pytest.raises(InvalidDefinition, match="but 'lots' is str"):
        RULE.validate(defn, mini_ctx("player_game"))


def test_a_boolean_is_not_a_number(mini_ctx) -> None:
    """bool is a subclass of int in Python; the check must not let that through."""
    defn = _defn({"rules": [{"attr": "snaps_unit", "op": ">=", "value": True}]})
    with pytest.raises(InvalidDefinition, match="the value is a boolean"):
        RULE.validate(defn, mini_ctx("player_game"))


def test_an_integer_is_acceptable_for_a_float_attribute(mini_ctx) -> None:
    defn = _defn({"rules": [{"attr": "snap_share_unit", "op": ">=", "value": 1}]})
    RULE.validate(defn, mini_ctx("player_game"))


def test_between_needs_exactly_two_values(mini_ctx) -> None:
    defn = _defn({"rules": [{"attr": "snaps_unit", "op": "between", "value": [1]}]})
    with pytest.raises(InvalidDefinition, match="exactly two"):
        RULE.validate(defn, mini_ctx("player_game"))


def test_in_needs_a_non_empty_list(mini_ctx) -> None:
    defn = _defn({"rules": [{"attr": "game_type", "op": "in", "value": []}]})
    with pytest.raises(InvalidDefinition, match="non-empty list"):
        RULE.validate(defn, mini_ctx("player_game"))


def test_a_null_check_takes_no_value(mini_ctx) -> None:
    with pytest.raises(InvalidDefinition, match="takes no value"):
        RULE.validate(
            _defn({"rules": [{"attr": "snaps_unit", "op": "is_null", "value": 1}]}),
            mini_ctx("player_game"),
        )


def test_like_needs_text(mini_ctx) -> None:
    """A numeric value passes the type check, so the like check is what catches it."""
    with pytest.raises(InvalidDefinition, match="like needs a text attribute"):
        RULE.validate(
            _defn({"rules": [{"attr": "snaps_unit", "op": "like", "value": 5}]}),
            mini_ctx("player_game"),
        )


def test_a_text_value_against_a_numeric_attribute_says_which_is_which(mini_ctx) -> None:
    with pytest.raises(InvalidDefinition, match="snaps_unit is int, but 'x' is str"):
        RULE.validate(
            _defn({"rules": [{"attr": "snaps_unit", "op": "like", "value": "x"}]}),
            mini_ctx("player_game"),
        )


def test_at_least_one_rule_is_required() -> None:
    with pytest.raises(InvalidDefinition, match="rules"):
        RULE.parse(_defn({"rules": []}))


def test_an_unknown_operator_is_refused() -> None:
    with pytest.raises(InvalidDefinition):
        RULE.parse(_defn({"rules": [{"attr": "snaps_unit", "op": "≥", "value": 1}]}))


# ── compilation, executed ─────────────────────────────────────────────────────


def test_values_are_bound_never_interpolated(mini_ctx) -> None:
    """A literal in the SQL text is an injection waiting to happen."""
    defn = _defn({"rules": [{"attr": "player_name", "op": "=", "value": "'; DROP TABLE x --"}]})
    sql, params = _compile(defn, mini_ctx)
    assert sql == '{self}."player_name" = ?'
    assert params == ["'; DROP TABLE x --"]


def test_a_single_rule_runs(mini_conn, mini_ctx) -> None:
    defn = _defn({"rules": [{"attr": "snaps_unit", "op": "<", "value": 5}]})
    rows = _run(mini_conn, defn, mini_ctx)
    assert ("Exit Runningback", 2) in rows
    assert all(name != "Starter Quarterback" for name, _ in rows)


def test_all_of_several_rules(mini_conn, mini_ctx) -> None:
    defn = _defn(
        {
            "match": "all",
            "rules": [
                {"attr": "snaps_unit", "op": "<", "value": 5},
                {"attr": "position_group", "op": "=", "value": "RB"},
            ],
        }
    )
    assert _run(mini_conn, defn, mini_ctx) == [("Exit Runningback", 2)]


def test_any_of_several_rules(mini_conn, mini_ctx) -> None:
    defn = _defn(
        {
            "match": "any",
            "rules": [
                {"attr": "position_group", "op": "=", "value": "RB"},
                {"attr": "position_group", "op": "=", "value": "OL"},
            ],
        }
    )
    groups = {name for name, _ in _run(mini_conn, defn, mini_ctx)}
    assert groups == {"Exit Runningback", "Rested Tackle"}


@pytest.mark.parametrize(
    ("op", "value"),
    [("between", [1, 5]), ("in", [2, 3]), ("not_in", [60]), ("is_not_null", None)],
)
def test_every_operator_produces_runnable_sql(mini_conn, mini_ctx, op: str, value) -> None:
    clause = {"attr": "snaps_unit", "op": op}
    if value is not None:
        clause["value"] = value
    defn = _defn({"rules": [clause]})
    RULE.validate(defn, mini_ctx("player_game"))
    _run(mini_conn, defn, mini_ctx)


def test_a_namespaced_attribute_records_its_namespace(mini_ctx) -> None:
    defn = _defn({"rules": [{"attr": "game.is_final_reg_week", "op": "=", "value": True}]})
    RULE.validate(defn, mini_ctx("player_game"))
    ctx = CompileCtx(**vars(mini_ctx("player_game")))
    compiled = RULE.compile(defn, ctx)
    assert compiled.namespaces_used == {"game"}
    assert compiled.predicate_sql == '{game}."is_final_reg_week" = ?'


def test_a_namespaced_attribute_runs_through_its_join(mini_conn, mini_ctx) -> None:
    defn = _defn({"rules": [{"attr": "game.is_final_reg_week", "op": "=", "value": True}]})
    sql, params = _compile(defn, mini_ctx)
    rows = mini_conn.execute(
        "SELECT DISTINCT pg.week FROM player_game pg JOIN game_ctx g ON g.game_id = pg.game_id "
        f"WHERE {sql.replace('{game}', 'g')} AND pg.season = 2023",
        params,
    ).fetchall()
    assert rows == [(3,)], "the mini league's last regular-season week is 3"


def test_an_unknown_namespace_is_refused(mini_ctx) -> None:
    defn = _defn({"rules": [{"attr": "nonesuch.week", "op": "=", "value": 1}]})
    with pytest.raises(InvalidDefinition):
        RULE.validate(defn, mini_ctx("player_game"))


# ── English ───────────────────────────────────────────────────────────────────


def test_explanation_reads_as_a_sentence(mini_ctx) -> None:
    defn = _defn(
        {
            "rules": [
                {"attr": "snaps_unit", "op": ">=", "value": 1},
                {"attr": "game_type", "op": "=", "value": "REG"},
            ]
        }
    )
    assert RULE.explain(defn, mini_ctx("player_game")) == ("snaps_unit ≥ 1 and game_type is 'REG'")


def test_explanation_uses_or_for_any(mini_ctx) -> None:
    defn = _defn(
        {
            "match": "any",
            "rules": [
                {"attr": "snaps_unit", "op": ">=", "value": 1},
                {"attr": "st_snaps", "op": ">=", "value": 1},
            ],
        }
    )
    assert " or " in RULE.explain(defn, mini_ctx("player_game"))


def test_explanation_warns_about_sql_null_semantics(mini_ctx) -> None:
    """`!=` not matching NULL surprises people; the explanation says so."""
    defn = _defn({"rules": [{"attr": "roster_status", "op": "!=", "value": "RES"}]})
    assert "do not match" in RULE.explain(defn, mini_ctx("player_game"))


def test_explanation_of_null_checks(mini_ctx) -> None:
    ctx = mini_ctx("player_game")
    assert "is missing" in RULE.explain(
        _defn({"rules": [{"attr": "snaps_unit", "op": "is_null"}]}), ctx
    )
    assert "is present" in RULE.explain(
        _defn({"rules": [{"attr": "snaps_unit", "op": "is_not_null"}]}), ctx
    )


def test_requires_lists_every_attribute(mini_ctx) -> None:
    defn = _defn(
        {
            "rules": [
                {"attr": "snaps_unit", "op": ">=", "value": 1},
                {"attr": "game.week", "op": "=", "value": 1},
            ]
        }
    )
    refs = {r.ref for r in RULE.requires(defn, mini_ctx("player_game"))}
    assert refs == {"snaps_unit", "game.week"}
