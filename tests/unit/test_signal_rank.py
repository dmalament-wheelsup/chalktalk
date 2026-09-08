"""The `rank` signal: top-N within a cohort, with a deterministic tiebreak."""

from __future__ import annotations

import pytest

from chalktalk.definitions.signals import SIGNALS, CompileCtx
from chalktalk.definitions.spec import Definition, InvalidDefinition

RANK = SIGNALS["rank"]


def _defn(params: dict, entity: str = "player_season", name: str = "team_leader") -> Definition:
    return Definition(name=name, entity=entity, signal="rank", params=params)


def _matched(conn, defn, ctx_factory, table: str = "player_season", column: str = "player_name"):
    compiled = RANK.compile(defn, CompileCtx(**vars(ctx_factory(defn.entity))))
    cte = compiled.ctes[0]
    sql = (
        f"WITH {cte.name} AS ({cte.sql}) SELECT {column} FROM {table} t2 "
        f"WHERE {compiled.predicate_sql.replace('{self}', 't2')} AND t2.season = 2023 "
        f"ORDER BY {column}"
    )
    return [r[0] for r in conn.execute(sql, cte.params + compiled.params).fetchall()]


def test_top_one_finds_the_leader(mini_conn, mini_ctx) -> None:
    """The mini league's leading rusher is the running back, by construction."""
    defn = _defn(
        {
            "attr": "carries",
            "cohort": ["season", "team_primary"],
            "top_n": 1,
            "eligible": {"rules": [{"attr": "carries", "op": ">", "value": 0}]},
        }
    )
    assert _matched(mini_conn, defn, mini_ctx) == ["Exit Runningback"]


def test_top_n_returns_n(mini_conn, mini_ctx) -> None:
    defn = _defn(
        {
            "attr": "snaps_unit_total",
            "cohort": ["season"],
            "top_n": 3,
            "eligible": {"rules": [{"attr": "snaps_unit_total", "op": ">", "value": 0}]},
        }
    )
    assert len(_matched(mini_conn, defn, mini_ctx)) == 3


def test_bottom_finds_the_other_end(mini_conn, mini_ctx) -> None:
    defn = _defn(
        {
            "attr": "snap_share_mean",
            "cohort": ["season"],
            "top_n": 1,
            "direction": "bottom",
            "eligible": {"rules": [{"attr": "snap_share_mean", "op": ">", "value": 0}]},
        }
    )
    assert _matched(mini_conn, defn, mini_ctx) == ["Backup Quarterback"]


def test_ties_break_on_the_entity_key_so_the_answer_is_stable(mini_conn, mini_ctx) -> None:
    """Two mini players share a perfect snap share; the same one must win every run."""
    defn = _defn(
        {
            "attr": "snap_share_mean",
            "cohort": ["season"],
            "top_n": 1,
            "eligible": {"rules": [{"attr": "snap_share_mean", "op": ">", "value": 0}]},
        }
    )
    answers = {tuple(_matched(mini_conn, defn, mini_ctx)) for _ in range(5)}
    assert len(answers) == 1, f"unstable answer across runs: {answers}"


def test_top_n_must_be_at_least_one() -> None:
    with pytest.raises(InvalidDefinition):
        RANK.parse(_defn({"attr": "carries", "cohort": ["season"], "top_n": 0}))


def test_a_text_attribute_cannot_be_ranked(mini_ctx) -> None:
    defn = _defn({"attr": "team_primary", "cohort": ["season"], "top_n": 1})
    with pytest.raises(InvalidDefinition, match="only be ordered by a number"):
        RANK.validate(defn, mini_ctx("player_season"))


def test_the_explanation_names_the_cohort(mini_ctx) -> None:
    defn = _defn({"attr": "carries", "cohort": ["season", "team_primary"], "top_n": 1})
    assert RANK.explain(defn, mini_ctx("player_season")).startswith(
        "#1 by carries within season and team_primary"
    )


def test_the_explanation_of_several(mini_ctx) -> None:
    defn = _defn({"attr": "carries", "cohort": ["season"], "top_n": 3})
    assert "highest 3 by carries" in RANK.explain(defn, mini_ctx("player_season"))


def test_it_works_on_a_team_entity(mini_conn, mini_ctx) -> None:
    defn = _defn(
        {"attr": "point_diff", "cohort": ["season"], "top_n": 1},
        entity="team_season",
        name="best_team",
    )
    RANK.validate(defn, mini_ctx("team_season"))
    assert len(_matched(mini_conn, defn, mini_ctx, table="team_season", column="team")) == 1
