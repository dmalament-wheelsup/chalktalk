"""The `delta` signal: an attribute against a baseline, NULL-safe."""

from __future__ import annotations

import pytest

from chalktalk.definitions.signals import SIGNALS, CompileCtx
from chalktalk.definitions.spec import Definition, InvalidDefinition

DELTA = SIGNALS["delta"]


def _defn(params: dict, entity: str = "player_game", name: str = "snap_drop") -> Definition:
    return Definition(name=name, entity=entity, signal="delta", params=params)


def _matched(conn, defn, ctx_factory) -> list[tuple]:
    compiled = DELTA.compile(defn, CompileCtx(**vars(ctx_factory(defn.entity))))
    predicate = compiled.predicate_sql.replace("{self}", "pg")
    return conn.execute(
        f"SELECT player_name, week FROM player_game pg WHERE {predicate} AND pg.season = 2023 "
        "ORDER BY player_name, week",
        compiled.params,
    ).fetchall()


def test_a_ratio_finds_the_usage_drop(mini_conn, mini_ctx) -> None:
    """Half the usual snap share. Both mini exits qualify; a full-time starter does not."""
    defn = _defn(
        {
            "attr": "snap_share_unit",
            "baseline": "baseline_share",
            "kind": "ratio",
            "op": "<=",
            "value": 0.5,
            "min_baseline": 0.3,
        }
    )
    rows = _matched(mini_conn, defn, mini_ctx)
    assert ("Exit Runningback", 2) in rows
    assert ("Rested Tackle", 3) in rows
    assert not [r for r in rows if r[0] == "Starter Quarterback"]


def test_min_baseline_excludes_the_meaningless_case(mini_conn, mini_ctx) -> None:
    """A backup at 3% who plays 1.5% has halved his workload, and it means nothing.

    Evaluated against literal rows rather than the mini league, because the case
    only exists where a low-usage player actually drops.
    """
    rows = (
        "(VALUES ('regular', 0.2, 0.9), ('marginal', 0.01, 0.03)) "
        "AS pg(tag, snap_share_unit, baseline_share)"
    )

    def matching(min_baseline: float | None) -> set[str]:
        params = {"attr": "snap_share_unit", "baseline": "baseline_share", "value": 0.5}
        if min_baseline is not None:
            params["min_baseline"] = min_baseline
        compiled = DELTA.compile(_defn(params), CompileCtx(**vars(mini_ctx("player_game"))))
        predicate = compiled.predicate_sql.replace("{self}", "pg")
        return {
            r[0]
            for r in mini_conn.execute(
                f"SELECT tag FROM {rows} WHERE {predicate}", compiled.params
            ).fetchall()
        }

    assert matching(None) == {"regular", "marginal"}
    assert matching(0.3) == {"regular"}


def test_a_difference_rather_than_a_ratio(mini_conn, mini_ctx) -> None:
    defn = _defn(
        {
            "attr": "snap_share_unit",
            "baseline": "baseline_share",
            "kind": "diff",
            "op": "<=",
            "value": -0.5,
        }
    )
    rows = _matched(mini_conn, defn, mini_ctx)
    assert ("Exit Runningback", 2) in rows


def test_nulls_never_match(mini_conn, mini_ctx) -> None:
    """A missing baseline means unknown, and unknown is not a drop."""
    defn = _defn(
        {"attr": "snap_share_unit", "baseline": "prior_season_snap_share", "value": 0.5},
        name="vs_last_year",
    )
    compiled = DELTA.compile(defn, CompileCtx(**vars(mini_ctx("player_game"))))
    assert "IS NOT NULL" in compiled.predicate_sql
    rows = mini_conn.execute(
        "SELECT count(*) FROM player_game pg WHERE "
        + compiled.predicate_sql.replace("{self}", "pg")
        + " AND pg.prior_season_snap_share IS NULL",
        compiled.params,
    ).fetchone()[0]
    assert rows == 0


def test_a_namespaced_baseline_compares_across_seasons(mini_ctx) -> None:
    """ "Usage change against last year" is a delta with a namespaced baseline."""
    defn = _defn(
        {
            "attr": "snap_share_mean",
            "baseline": "prior.snap_share_mean",
            "value": 0.7,
        },
        entity="player_season",
        name="usage_fell",
    )
    DELTA.validate(defn, mini_ctx("player_season"))
    compiled = DELTA.compile(defn, CompileCtx(**vars(mini_ctx("player_season"))))
    assert compiled.namespaces_used == {"self", "prior"}
    assert '{prior}."snap_share_mean"' in compiled.predicate_sql


def test_a_text_attribute_is_refused(mini_ctx) -> None:
    defn = _defn({"attr": "roster_status", "baseline": "baseline_share", "value": 0.5})
    with pytest.raises(InvalidDefinition, match="needs numbers on both sides"):
        DELTA.validate(defn, mini_ctx("player_game"))


def test_an_unknown_baseline_suggests(mini_ctx) -> None:
    defn = _defn({"attr": "snap_share_unit", "baseline": "baseline_shre", "value": 0.5})
    with pytest.raises(InvalidDefinition) as excinfo:
        DELTA.validate(defn, mini_ctx("player_game"))
    assert "baseline_share" in excinfo.value.did_you_mean


def test_values_are_bound(mini_ctx) -> None:
    defn = _defn(
        {"attr": "snap_share_unit", "baseline": "baseline_share", "value": 0.5, "min_baseline": 0.3}
    )
    compiled = DELTA.compile(defn, CompileCtx(**vars(mini_ctx("player_game"))))
    assert compiled.params == [0.3, 0.5]
    assert "0.5" not in compiled.predicate_sql


def test_the_explanation_reads_as_english(mini_ctx) -> None:
    defn = _defn(
        {"attr": "snap_share_unit", "baseline": "baseline_share", "value": 0.5, "min_baseline": 0.3}
    )
    assert DELTA.explain(defn, mini_ctx("player_game")) == (
        "snap_share_unit ≤ 0.5 × baseline_share (only where baseline_share > 0.3)"
    )


def test_the_explanation_of_a_difference(mini_ctx) -> None:
    defn = _defn(
        {"attr": "snap_share_unit", "baseline": "baseline_share", "kind": "diff", "value": -0.5}
    )
    assert "minus baseline_share ≤ -0.5" in DELTA.explain(defn, mini_ctx("player_game"))
