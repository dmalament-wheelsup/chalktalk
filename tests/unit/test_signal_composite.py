"""The `composite` signal: definitions built from other definitions.

This is the mechanism that keeps football out of the code (D22), so the tests
care most about the ways it could quietly go wrong: a loop, a term that lifts
into the wrong entity, and a term that is itself broken.
"""

from __future__ import annotations

import json

import pytest

from chalktalk.definitions.signals import SIGNALS, CompileCtx
from chalktalk.definitions.spec import Definition, DefinitionIn, InvalidDefinition

COMPOSITE = SIGNALS["composite"]

PLAYED = {"rules": [{"attr": "snaps_unit", "op": ">=", "value": 1}]}
REGULAR = {"rules": [{"attr": "game_type", "op": "=", "value": "REG"}]}
LEFT_EARLY = {"rules": [{"attr": "pp_missed_tail_frac", "op": ">=", "value": 0.25}]}


@pytest.fixture
def parts(mini_store):
    """The small definitions a composite is assembled from."""
    for name, params in (("played", PLAYED), ("regular", REGULAR), ("left_early", LEFT_EARLY)):
        mini_store.save(DefinitionIn(name=name, entity="player_game", signal="rule", params=params))
    return mini_store


def _composite(
    op: str, terms: list[str], entity: str = "player_game", name: str = "combo"
) -> Definition:
    return Definition(
        name=name, entity=entity, signal="composite", params={"op": op, "terms": terms}
    )


def _matched(conn, defn, ctx_factory) -> list[tuple]:
    compiled = COMPOSITE.compile(defn, CompileCtx(**vars(ctx_factory(defn.entity))))
    predicate = compiled.predicate_sql.replace("{self}", "pg")
    return conn.execute(
        f"SELECT player_name, week FROM player_game pg WHERE {predicate} AND pg.season = 2023 "
        "ORDER BY player_name, week",
        compiled.params,
    ).fetchall()


# ── combining ─────────────────────────────────────────────────────────────────


def test_all_of_narrows(mini_conn, parts, mini_ctx) -> None:
    defn = _composite("all_of", ["played", "regular", "left_early"])
    COMPOSITE.validate(defn, mini_ctx("player_game"))
    rows = _matched(mini_conn, defn, mini_ctx)
    assert ("Exit Runningback", 2) in rows
    assert ("Rested Tackle", 3) in rows
    assert not [r for r in rows if r[0] == "Starter Quarterback"]


def test_any_of_widens(mini_conn, parts, mini_ctx) -> None:
    narrow = _matched(mini_conn, _composite("all_of", ["played", "left_early"]), mini_ctx)
    wide = _matched(
        mini_conn, _composite("any_of", ["played", "left_early"], name="other_combo"), mini_ctx
    )
    assert len(wide) > len(narrow)


def test_not_inverts(mini_conn, parts, mini_ctx) -> None:
    inside = set(_matched(mini_conn, _composite("all_of", ["left_early"]), mini_ctx))
    outside = set(
        _matched(mini_conn, _composite("not", ["left_early"], name="other_combo"), mini_ctx)
    )
    assert not (inside & outside)
    assert inside and outside


def test_not_takes_exactly_one_term() -> None:
    with pytest.raises(InvalidDefinition):
        COMPOSITE.parse(_composite("not", ["played", "regular"]))


def test_a_repeated_term_is_refused() -> None:
    with pytest.raises(InvalidDefinition):
        COMPOSITE.parse(_composite("all_of", ["played", "played"]))


def test_composites_nest(mini_conn, parts, mini_ctx) -> None:
    """`early_exit` is a composite of composites, so this has to work."""
    parts.save(
        DefinitionIn(
            name="evidence",
            entity="player_game",
            signal="composite",
            params={"op": "any_of", "terms": ["left_early"]},
        )
    )
    defn = _composite("all_of", ["played", "evidence"], name="exit_like")
    COMPOSITE.validate(defn, mini_ctx("player_game"))
    assert ("Exit Runningback", 2) in _matched(mini_conn, defn, mini_ctx)


def test_terms_used_is_reported_in_dependency_order(parts, mini_ctx) -> None:
    parts.save(
        DefinitionIn(
            name="evidence",
            entity="player_game",
            signal="composite",
            params={"op": "any_of", "terms": ["left_early"]},
        )
    )
    defn = _composite("all_of", ["played", "evidence"], name="exit_like")
    compiled = COMPOSITE.compile(defn, CompileCtx(**vars(mini_ctx("player_game"))))
    assert compiled.terms_used == ["played", "left_early", "evidence"]


# ── the ways it goes wrong ────────────────────────────────────────────────────


def test_an_unknown_term_suggests(parts, mini_ctx) -> None:
    defn = _composite("all_of", ["playd"])
    with pytest.raises(InvalidDefinition) as excinfo:
        COMPOSITE.validate(defn, mini_ctx("player_game"))
    assert "played" in excinfo.value.did_you_mean


def test_a_loop_is_refused(mini_store, mini_ctx) -> None:
    """Two definitions naming each other would recurse for ever at compile time."""
    mini_store.directory.mkdir(parents=True, exist_ok=True)
    for name, other in (("a_def", "b_def"), ("b_def", "a_def")):
        (mini_store.directory / f"{name}.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "entity": "player_game",
                    "signal": "composite",
                    "params": {"op": "all_of", "terms": [other]},
                }
            )
        )
    mini_store.load()
    assert "a_def" in mini_store.broken
    assert "loop" in mini_store.broken["a_def"]


def test_a_definition_naming_itself_is_refused(mini_store) -> None:
    mini_store.directory.mkdir(parents=True, exist_ok=True)
    (mini_store.directory / "self_ref.json").write_text(
        json.dumps(
            {
                "name": "self_ref",
                "entity": "player_game",
                "signal": "composite",
                "params": {"op": "all_of", "terms": ["self_ref"]},
            }
        )
    )
    mini_store.load()
    assert "loop" in mini_store.broken["self_ref"]


def test_a_broken_term_makes_the_composite_broken_too(mini_store) -> None:
    """But it says which term, rather than failing obscurely."""
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
    (mini_store.directory / "uses_stale.json").write_text(
        json.dumps(
            {
                "name": "uses_stale",
                "entity": "player_game",
                "signal": "composite",
                "params": {"op": "all_of", "terms": ["stale"]},
            }
        )
    )
    mini_store.load()
    assert "stale" in mini_store.broken
    assert "quarantined" in mini_store.broken["uses_stale"]


def test_a_term_from_an_entity_that_cannot_lift_is_refused(parts, mini_ctx) -> None:
    """A player_game definition has no meaning on team_game (D16)."""
    defn = _composite("all_of", ["played"], entity="team_game", name="team_thing")
    with pytest.raises(InvalidDefinition, match="cannot be used in a team_game one"):
        COMPOSITE.validate(defn, mini_ctx("team_game"))


def test_the_refusal_names_where_the_term_does_work(parts, mini_ctx) -> None:
    defn = _composite("all_of", ["played"], entity="team_game", name="team_thing")
    with pytest.raises(InvalidDefinition, match="it works in: player_game"):
        COMPOSITE.validate(defn, mini_ctx("team_game"))


# ── lifting (D16) ─────────────────────────────────────────────────────────────


def test_a_coarser_definition_lifts_into_a_finer_plan(mini_conn, mini_store, mini_ctx) -> None:
    """A game-level fact used in a player-level composite reaches through `game.`."""
    mini_store.save(
        DefinitionIn(
            name="final_week",
            entity="game",
            signal="rule",
            params={"rules": [{"attr": "is_final_reg_week", "op": "=", "value": True}]},
        )
    )
    mini_store.save(DefinitionIn(name="played", entity="player_game", signal="rule", params=PLAYED))
    defn = _composite("all_of", ["played", "final_week"], name="played_in_final_week")
    COMPOSITE.validate(defn, mini_ctx("player_game"))

    compiled = COMPOSITE.compile(defn, CompileCtx(**vars(mini_ctx("player_game"))))
    predicate = compiled.predicate_sql.replace("{self}", "pg").replace("{game}", "g")
    rows = mini_conn.execute(
        "SELECT DISTINCT pg.week FROM player_game pg JOIN game_ctx g ON g.game_id = pg.game_id "
        f"WHERE {predicate} AND pg.season = 2023",
        compiled.params,
    ).fetchall()
    assert rows == [(3,)]


def test_a_prior_season_term_reaches_last_year(mini_store, mini_ctx) -> None:
    """`basis: prior_season` is what turns "a star" into "a star last year"."""
    mini_store.save(
        DefinitionIn(
            name="heavy_usage",
            entity="player_season",
            basis="prior_season",
            signal="rule",
            params={"rules": [{"attr": "snap_share_mean", "op": ">=", "value": 0.9}]},
        )
    )
    defn = _composite("all_of", ["heavy_usage"], name="was_heavy")
    COMPOSITE.validate(defn, mini_ctx("player_game"))
    compiled = COMPOSITE.compile(defn, CompileCtx(**vars(mini_ctx("player_game"))))
    assert compiled.namespaces_used == {"prior"}


def test_the_same_definition_with_a_current_basis_reaches_this_year(mini_store, mini_ctx) -> None:
    mini_store.save(
        DefinitionIn(
            name="heavy_now",
            entity="player_season",
            signal="rule",
            params={"rules": [{"attr": "snap_share_mean", "op": ">=", "value": 0.9}]},
        )
    )
    defn = _composite("all_of", ["heavy_now"], name="is_heavy")
    compiled = COMPOSITE.compile(defn, CompileCtx(**vars(mini_ctx("player_game"))))
    assert compiled.namespaces_used == {"cur"}
