"""The `percentile` signal, and the tie behaviour that motivates it.

The plan calls this out as a pitfall and it deserves the emphasis: with
`percent_rank`, a definition can match nobody at all while looking correct.
"""

from __future__ import annotations

import duckdb
import pytest

from chalktalk.definitions.signals import SIGNALS, CompileCtx, ValidationCtx
from chalktalk.definitions.spec import Definition, InvalidDefinition

PCT = SIGNALS["percentile"]


def _defn(params: dict, name: str = "top_share") -> Definition:
    return Definition(name=name, entity="player_season", signal="percentile", params=params)


@pytest.fixture
def tie_conn() -> duckdb.DuckDBPyConnection:
    """A ten-row cohort with four rows tied at the top and four tied at the bottom.

    Values: 1.0 x4, 0.5, 0.4, 0.3, 0.1 x3. Deliberately not distinct, because
    distinct values hide exactly the bug this guards against.
    """
    conn = duckdb.connect()
    conn.execute(
        "CREATE TABLE player_season (player_key VARCHAR, season INTEGER, "
        "snap_share_mean DOUBLE, games_played_share DOUBLE, position_group VARCHAR)"
    )
    values = [1.0, 1.0, 1.0, 1.0, 0.5, 0.4, 0.3, 0.1, 0.1, 0.1]
    conn.executemany(
        "INSERT INTO player_season VALUES (?, 2023, ?, 1.0, 'QB')",
        [(f"p{i}", v) for i, v in enumerate(values)],
    )
    yield conn
    conn.close()


@pytest.fixture
def tie_ctx(tie_conn, mini_settings, mini_store):
    def make(entity: str = "player_season") -> ValidationCtx:
        from chalktalk.coverage import Coverage

        return ValidationCtx(
            entity=entity,
            conn=tie_conn,
            coverage=Coverage(duckdb.connect(), mini_settings)
            if False
            else mini_store._ctx_factory(entity).coverage,
            store=mini_store,
            settings=mini_settings,
        )

    return make


def _matched(conn, defn, ctx_factory) -> list[str]:
    compiled = PCT.compile(defn, CompileCtx(**vars(ctx_factory(defn.entity))))
    cte = compiled.ctes[0]
    sql = (
        f"WITH {cte.name} AS ({cte.sql}) SELECT player_key FROM player_season ps "
        f"WHERE {compiled.predicate_sql.replace('{self}', 'ps')} ORDER BY player_key"
    )
    return [r[0] for r in conn.execute(sql, cte.params + compiled.params).fetchall()]


# ── ties ──────────────────────────────────────────────────────────────────────


def test_top_90_returns_the_whole_tied_group(tie_conn, tie_ctx) -> None:
    """Four rows share the top value; all four are in the top 10%.

    percent_rank would put the tied group at 0.6 and return nobody.
    """
    defn = _defn({"attr": "snap_share_mean", "cohort": ["season"], "pctile": 90})
    assert _matched(tie_conn, defn, tie_ctx) == ["p0", "p1", "p2", "p3"]


def test_top_70_returns_four_not_three(tie_conn, tie_ctx) -> None:
    """The tied group cannot be split, so a 70th-percentile cut still takes all four."""
    defn = _defn({"attr": "snap_share_mean", "cohort": ["season"], "pctile": 70})
    assert _matched(tie_conn, defn, tie_ctx) == ["p0", "p1", "p2", "p3"]


def test_top_50_reaches_further_down(tie_conn, tie_ctx) -> None:
    defn = _defn({"attr": "snap_share_mean", "cohort": ["season"], "pctile": 50})
    assert _matched(tie_conn, defn, tie_ctx) == ["p0", "p1", "p2", "p3", "p4", "p5"]


def test_bottom_treats_ties_the_same_way(tie_conn, tie_ctx) -> None:
    """Three rows share the lowest value; a 25th-percentile cut takes all three."""
    defn = _defn(
        {"attr": "snap_share_mean", "cohort": ["season"], "pctile": 25, "direction": "bottom"}
    )
    assert _matched(tie_conn, defn, tie_ctx) == ["p7", "p8", "p9"]


def test_bottom_is_inclusive_at_the_boundary(tie_conn, tie_ctx) -> None:
    """A row sitting exactly on the cut is included, as it is at the top end."""
    defn = _defn(
        {"attr": "snap_share_mean", "cohort": ["season"], "pctile": 30, "direction": "bottom"}
    )
    assert _matched(tie_conn, defn, tie_ctx) == ["p6", "p7", "p8", "p9"]


def test_bottom_means_at_or_below_that_percentile(tie_conn, tie_ctx) -> None:
    """`bottom 70` is not "the lowest three"; it is everyone at or below the 70th.

    Here that is all ten, and `top 30` is also all ten - the mirror image. A tied
    group is indivisible, so it is included whenever any part of it falls inside
    the slice, at either end.
    """
    bottom = _defn(
        {"attr": "snap_share_mean", "cohort": ["season"], "pctile": 70, "direction": "bottom"},
        name="low_share",
    )
    top = _defn({"attr": "snap_share_mean", "cohort": ["season"], "pctile": 30})
    assert len(_matched(tie_conn, bottom, tie_ctx)) == 10
    assert len(_matched(tie_conn, top, tie_ctx)) == 10


def test_top_100_takes_everyone(tie_conn, tie_ctx) -> None:
    defn = _defn({"attr": "snap_share_mean", "cohort": ["season"], "pctile": 0})
    assert len(_matched(tie_conn, defn, tie_ctx)) == 10


# ── validation ────────────────────────────────────────────────────────────────


def test_a_text_attribute_cannot_be_ranked(mini_ctx) -> None:
    defn = _defn({"attr": "position_group", "cohort": ["season"], "pctile": 90})
    with pytest.raises(InvalidDefinition, match="only be ordered by a number"):
        PCT.validate(defn, mini_ctx("player_season"))


def test_a_namespaced_attribute_is_refused(mini_ctx) -> None:
    """The cohort is computed over the entity's own table, so a join makes no sense."""
    defn = _defn({"attr": "prior.snap_share_mean", "cohort": ["season"], "pctile": 90})
    with pytest.raises(InvalidDefinition, match="namespaced"):
        PCT.validate(defn, mini_ctx("player_season"))


def test_an_empty_cohort_is_refused(mini_ctx) -> None:
    defn = _defn({"attr": "snap_share_mean", "cohort": [], "pctile": 90})
    with pytest.raises(InvalidDefinition, match="cohort is empty"):
        PCT.validate(defn, mini_ctx("player_season"))


def test_an_unknown_cohort_column_suggests(mini_ctx) -> None:
    defn = _defn({"attr": "snap_share_mean", "cohort": ["seasn"], "pctile": 90})
    with pytest.raises(InvalidDefinition) as excinfo:
        PCT.validate(defn, mini_ctx("player_season"))
    assert "season" in excinfo.value.did_you_mean


@pytest.mark.parametrize("pctile", [-1, 101])
def test_a_percentile_outside_the_range_is_refused(pctile: int) -> None:
    with pytest.raises(InvalidDefinition):
        PCT.parse(_defn({"attr": "snap_share_mean", "cohort": ["season"], "pctile": pctile}))


# ── eligibility (D13/D24) ─────────────────────────────────────────────────────


def test_player_season_gets_the_availability_default(mini_ctx) -> None:
    """Half the team's games — the same fraction in a 16- and a 17-game season."""
    defn = _defn({"attr": "snap_share_mean", "cohort": ["season"], "pctile": 90})
    ctx = mini_ctx("player_season")
    assert "games_played_share ≥ 0.5" in PCT.explain(defn, ctx)
    compiled = PCT.compile(defn, CompileCtx(**vars(ctx)))
    assert "games_played_share" in compiled.ctes[0].sql
    assert compiled.ctes[0].params == [0.5]


def test_another_entity_gets_no_default(mini_ctx) -> None:
    defn = Definition(
        name="big_win",
        entity="team_season",
        signal="percentile",
        params={"attr": "point_diff", "cohort": ["season"], "pctile": 90},
    )
    ctx = mini_ctx("team_season")
    compiled = PCT.compile(defn, CompileCtx(**vars(ctx)))
    assert compiled.ctes[0].params == []
    assert "among rows where" not in PCT.explain(defn, ctx)


def test_eligibility_can_be_replaced(mini_ctx) -> None:
    defn = _defn(
        {
            "attr": "snap_share_mean",
            "cohort": ["season"],
            "pctile": 90,
            "eligible": {"rules": [{"attr": "games_with_snaps", "op": ">=", "value": 8}]},
        }
    )
    ctx = mini_ctx("player_season")
    assert "games_with_snaps ≥ 8" in PCT.explain(defn, ctx)
    assert PCT.compile(defn, CompileCtx(**vars(ctx))).ctes[0].params == [8]


def test_the_default_eligibility_actually_excludes(mini_conn, mini_ctx) -> None:
    """The mini kicker has no unit snaps, so he is not in any snap-share cohort."""
    defn = _defn({"attr": "snap_share_mean", "cohort": ["season"], "pctile": 0})
    assert "Kicker Guy" not in _matched(mini_conn, defn, mini_ctx)


# ── the rest of the contract ──────────────────────────────────────────────────


def test_it_runs_on_the_mini_league(mini_conn, mini_ctx) -> None:
    defn = _defn({"attr": "snap_share_mean", "cohort": ["season"], "pctile": 90})
    assert _matched(mini_conn, defn, mini_ctx)


def test_requires_covers_the_cohort_and_the_eligibility(mini_ctx) -> None:
    defn = _defn({"attr": "snap_share_mean", "cohort": ["season", "position_group"], "pctile": 90})
    refs = {r.name for r in PCT.requires(defn, mini_ctx("player_season"))}
    assert refs == {"snap_share_mean", "season", "position_group", "games_played_share"}


def test_the_explanation_reads_as_english(mini_ctx) -> None:
    defn = _defn({"attr": "snap_share_mean", "cohort": ["season", "position_group"], "pctile": 90})
    text = PCT.explain(defn, mini_ctx("player_season"))
    assert text.startswith(
        "snap_share_mean at or above the 90th percentile within season and position_group"
    )


def test_the_bottom_direction_reads_differently(mini_ctx) -> None:
    defn = _defn(
        {"attr": "snap_share_mean", "cohort": ["season"], "pctile": 10, "direction": "bottom"}
    )
    assert "at or below the 10th percentile" in PCT.explain(defn, mini_ctx("player_season"))
