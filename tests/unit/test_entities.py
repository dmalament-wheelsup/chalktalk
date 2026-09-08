"""Entity registry: namespaces resolve, joins are well-formed, lifts are consistent.

These are structural invariants the compiler will depend on. A namespace whose
`on` clause names an alias that is not in scope produces SQL that fails at query
time, which is the worst place to find out.
"""

from __future__ import annotations

import pytest

from chalktalk.entities import (
    BASIS,
    ENTITIES,
    LIFTS,
    SELF,
    Entity,
    EntityMismatch,
    can_lift,
    lift_namespace,
)

TABLES = {spec.table for spec in ENTITIES.values()}


def test_every_entity_in_the_enum_has_a_spec() -> None:
    assert set(ENTITIES) == {e.value for e in Entity}


def test_aliases_are_unique_within_an_entity() -> None:
    for name, spec in ENTITIES.items():
        aliases = [spec.alias] + [j.alias for j in spec.namespaces.values()]
        assert len(aliases) == len(set(aliases)), f"{name} reuses an alias: {aliases}"


def test_join_conditions_reference_the_entity_and_the_namespace_alias() -> None:
    for name, spec in ENTITIES.items():
        for ns, join in spec.namespaces.items():
            assert f"{join.alias}." in join.on, f"{name}.{ns} never references its own alias"
            other = {spec.alias} | {j.alias for j in spec.namespaces.values()} - {join.alias}
            assert any(f"{a}." in join.on for a in other), f"{name}.{ns} joins to nothing in scope"


def test_namespaces_point_at_known_tables() -> None:
    for name, spec in ENTITIES.items():
        for ns, join in spec.namespaces.items():
            assert join.table in TABLES, f"{name}.{ns} points at unknown table {join.table}"


def test_keys_and_identifying_columns_are_named() -> None:
    for name, spec in ENTITIES.items():
        assert spec.key, f"{name} has no key"
        assert spec.identifying, f"{name} has no identifying columns"


def test_player_game_reaches_both_seasons_and_both_neighbours() -> None:
    """The early-exit machinery needs prior-season baselines and the next game."""
    assert set(ENTITIES["player_game"].namespaces) >= {"cur", "prior", "next", "prev", "game"}


def test_next_and_prev_go_through_the_teams_schedule() -> None:
    """Bye weeks mean the next game is not week + 1 (a pitfall in the plan)."""
    for ns, column in (("next", "team_next_game_id"), ("prev", "team_prev_game_id")):
        assert column in ENTITIES["player_game"].namespaces[ns].on


# ── lifts ─────────────────────────────────────────────────────────────────────


def test_lift_pairs_name_real_entities() -> None:
    for definition_entity, plan_entity in LIFTS:
        assert definition_entity in ENTITIES
        assert plan_entity in ENTITIES


def test_lift_targets_exist_on_the_plan_entity() -> None:
    for (definition_entity, plan_entity), mapping in LIFTS.items():
        available = set(ENTITIES[plan_entity].namespaces)
        for source, target in mapping.items():
            if target == BASIS:
                continue
            assert target in available, (
                f"lift {definition_entity}->{plan_entity} maps {source} to {target}, "
                f"which {plan_entity} does not have"
            )


def test_lift_sources_exist_on_the_definition_entity() -> None:
    for (definition_entity, _), mapping in LIFTS.items():
        available = set(ENTITIES[definition_entity].namespaces) | {SELF}
        for source in mapping:
            assert source in available, f"{definition_entity} has no namespace {source}"


def test_lifting_only_goes_coarse_to_fine() -> None:
    """A player_game definition is usable only on player_game; play only on play."""
    for fine in ("player_game", "play"):
        assert not [pair for pair in LIFTS if pair[0] == fine], f"{fine} should not lift anywhere"


def test_an_entity_always_lifts_into_itself() -> None:
    for entity in ENTITIES:
        assert can_lift(entity, entity)
        assert lift_namespace(entity, entity) is None


def test_an_entitys_own_namespace_survives_an_identity_lift() -> None:
    assert lift_namespace("player_game", "player_game", "next") == "next"


def test_a_game_definition_lifts_into_finer_entities() -> None:
    for plan_entity in ("team_game", "player_game", "play"):
        assert lift_namespace("game", plan_entity) == "game"


def test_a_team_game_definition_lifts_into_player_game() -> None:
    assert lift_namespace("team_game", "player_game") == "team"
    assert lift_namespace("team_game", "player_game", "game") == "game"
    assert lift_namespace("team_game", "player_game", "season") == "team_season"


def test_a_player_season_definition_follows_its_basis() -> None:
    """D16: 'star last year' and 'star this year' are the same definition, different basis."""
    assert lift_namespace("player_season", "player_game", basis="current_season") == "cur"
    assert lift_namespace("player_season", "player_game", basis="prior_season") == "prior"


def test_a_player_season_definition_on_player_season() -> None:
    assert lift_namespace("player_season", "player_season", basis="current_season") is None
    assert lift_namespace("player_season", "player_season", basis="prior_season") == "prior"


def test_a_prior_season_definition_has_no_prior_of_its_own() -> None:
    """Its own row is already season - 1; `prior` would be two back, which does not exist."""
    for plan_entity in ("player_game", "player_season"):
        with pytest.raises(EntityMismatch, match="two seasons back"):
            lift_namespace("player_season", plan_entity, "prior", basis="prior_season")


def test_a_current_season_definition_keeps_its_prior() -> None:
    assert (
        lift_namespace("player_season", "player_game", "prior", basis="current_season") == "prior"
    )
    assert (
        lift_namespace("player_season", "player_season", "prior", basis="current_season") == "prior"
    )


def test_an_illegal_lift_is_refused() -> None:
    with pytest.raises(EntityMismatch, match="cannot be used"):
        lift_namespace("player_game", "team_game")
    assert not can_lift("player_game", "team_game")


def test_an_unliftable_namespace_is_refused() -> None:
    """team_game's `opp` has no meaning once lifted into player_game."""
    with pytest.raises(EntityMismatch, match="does not lift"):
        lift_namespace("team_game", "player_game", "opp")


def test_an_unknown_basis_is_refused() -> None:
    with pytest.raises(EntityMismatch, match="unknown basis"):
        lift_namespace("player_season", "player_game", basis="two_seasons_ago")
