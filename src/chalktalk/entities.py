"""The six entities: base tables, namespaces, and the lift rules between them.

A plan or definition addresses an attribute as ``name`` (the entity's own table)
or ``ns.name`` (a fixed join). Namespaces are the *only* way to reach a related
row, so the set of joinable things is closed and knowable — the compiler never
invents a join.

Lifting (D16) is how a definition written for a coarser entity is used in a
finer one: a ``team_season`` definition asked about a ``player_game`` row means
"that player's team's season". :data:`LIFTS` says which of those are legal and
how the coarser entity's namespaces translate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: Placeholder in :data:`LIFTS` for a namespace chosen by the definition's
#: ``basis`` rather than fixed by the lift. See :func:`lift_namespace`.
BASIS = "<basis>"

#: The definition's own table, as addressed inside a lift mapping.
SELF = "self"


class Entity(StrEnum):
    game = "game"
    team_game = "team_game"
    team_season = "team_season"
    player_game = "player_game"
    player_season = "player_season"
    play = "play"


@dataclass(frozen=True)
class Join:
    table: str
    alias: str
    on: str  # references the aliases, not the table names


@dataclass(frozen=True)
class EntitySpec:
    table: str
    alias: str
    key: tuple[str, ...]
    identifying: tuple[str, ...]  # columns always returned so a row is recognisable
    namespaces: dict[str, Join]


ENTITIES: dict[str, EntitySpec] = {
    "game": EntitySpec(
        "game_ctx", "g", ("game_id",), ("season", "week", "home_team", "away_team"), {}
    ),
    "team_game": EntitySpec(
        "team_game",
        "tg",
        ("game_id", "team"),
        ("season", "week", "team", "opponent"),
        {
            "game": Join("game_ctx", "g", "g.game_id = tg.game_id"),
            "opp": Join("team_game", "tgo", "tgo.game_id = tg.game_id AND tgo.team = tg.opponent"),
            "season": Join("team_season", "ts", "ts.season = tg.season AND ts.team = tg.team"),
        },
    ),
    "team_season": EntitySpec(
        "team_season",
        "ts",
        ("season", "team"),
        ("season", "team"),
        {"prior": Join("team_season", "tsp", "tsp.season = ts.season - 1 AND tsp.team = ts.team")},
    ),
    "player_game": EntitySpec(
        "player_game",
        "pg",
        ("player_key", "game_id"),
        ("player_name", "season", "week", "team", "opponent"),
        {
            "cur": Join(
                "player_season",
                "psc",
                "psc.player_key = pg.player_key AND psc.season = pg.season",
            ),
            "prior": Join(
                "player_season",
                "psp",
                "psp.player_key = pg.player_key AND psp.season = pg.season - 1",
            ),
            "game": Join("game_ctx", "g", "g.game_id = pg.game_id"),
            "team": Join("team_game", "tg", "tg.game_id = pg.game_id AND tg.team = pg.team"),
            "team_season": Join("team_season", "ts", "ts.season = pg.season AND ts.team = pg.team"),
            "next": Join(
                "player_game",
                "pgn",
                "pgn.player_key = pg.player_key AND pgn.game_id = pg.team_next_game_id",
            ),
            "prev": Join(
                "player_game",
                "pgp",
                "pgp.player_key = pg.player_key AND pgp.game_id = pg.team_prev_game_id",
            ),
        },
    ),
    "player_season": EntitySpec(
        "player_season",
        "ps",
        ("player_key", "season"),
        ("player_name", "season", "team_primary"),
        {
            "prior": Join(
                "player_season",
                "psp",
                "psp.player_key = ps.player_key AND psp.season = ps.season - 1",
            ),
            "next": Join(
                "player_season",
                "psn",
                "psn.player_key = ps.player_key AND psn.season = ps.season + 1",
            ),
            "team_season": Join(
                "team_season", "ts", "ts.season = ps.season AND ts.team = ps.team_primary"
            ),
        },
    ),
    "play": EntitySpec(
        "pbp",
        "p",
        ("game_id", "play_id"),
        ("season", "week", "posteam", "defteam", "qtr", "desc"),
        {
            "game": Join("game_ctx", "g", "g.game_id = p.game_id"),
            "off": Join("team_game", "tgo", "tgo.game_id = p.game_id AND tgo.team = p.posteam"),
            "def": Join("team_game", "tgd", "tgd.game_id = p.game_id AND tgd.team = p.defteam"),
        },
    ),
}

#: D16. Key: (definition entity, plan entity). Value: definition namespace →
#: plan namespace. Namespaces not listed are not liftable → ``entity_mismatch``.
LIFTS: dict[tuple[str, str], dict[str, str]] = {
    ("game", "team_game"): {SELF: "game"},
    ("game", "player_game"): {SELF: "game"},
    ("game", "play"): {SELF: "game"},
    ("team_game", "player_game"): {SELF: "team", "game": "game", "season": "team_season"},
    ("team_season", "team_game"): {SELF: "season"},
    ("team_season", "player_game"): {SELF: "team_season"},
    ("team_season", "player_season"): {SELF: "team_season"},
    # basis current_season → "cur", prior_season → "prior" (and then the
    # definition's own "prior" namespace is unavailable — there is no
    # two-seasons-back join).
    ("player_season", "player_game"): {SELF: BASIS, "prior": "prior"},
    ("player_season", "player_season"): {SELF: BASIS},
}

#: What a definition's ``basis`` resolves to, per plan entity.
_BASIS_NAMESPACE: dict[tuple[str, str], str | None] = {
    ("player_game", "current_season"): "cur",
    ("player_game", "prior_season"): "prior",
    # On player_season itself, current_season is the row you already have.
    ("player_season", "current_season"): None,
    ("player_season", "prior_season"): "prior",
}


class EntityMismatch(Exception):
    """A definition's entity cannot be used in this plan's entity."""


def namespaces(entity: str) -> dict[str, Join]:
    return ENTITIES[entity].namespaces


def can_lift(definition_entity: str, plan_entity: str) -> bool:
    return definition_entity == plan_entity or (definition_entity, plan_entity) in LIFTS


def lift_namespace(
    definition_entity: str,
    plan_entity: str,
    definition_namespace: str = SELF,
    basis: str = "current_season",
) -> str | None:
    """Translate a namespace written against ``definition_entity`` into ``plan_entity``.

    Returns None when the target is the plan entity's own table (no join
    needed). Raises :class:`EntityMismatch` when the lift or the namespace is
    not allowed — a ``player_game`` definition is usable only on ``player_game``,
    and a ``play`` definition only on ``play``.
    """
    if basis == "prior_season" and definition_namespace == "prior":
        # The definition's own row is already season - 1, so its "prior" would
        # be two seasons back. There is no such join, by design.
        raise EntityMismatch(
            "a prior_season definition has no 'prior' namespace — that is two seasons back"
        )

    mapping = LIFTS.get((definition_entity, plan_entity))

    if mapping is None:
        if definition_entity != plan_entity:
            raise EntityMismatch(
                f"a {definition_entity} definition cannot be used in a {plan_entity} plan"
            )
        # Same entity, no basis to honour: namespaces mean what they say.
        return None if definition_namespace == SELF else definition_namespace

    target = mapping.get(definition_namespace)
    if target is None:
        if definition_entity == plan_entity:
            # Same entity: only `self` is redirected by the basis; the rest are
            # the entity's own namespaces.
            return definition_namespace
        raise EntityMismatch(
            f"namespace {definition_namespace!r} of a {definition_entity} definition "
            f"does not lift into {plan_entity}"
        )
    if target != BASIS:
        return target

    key = (plan_entity, basis)
    if key not in _BASIS_NAMESPACE:
        raise EntityMismatch(f"unknown basis {basis!r} for a {definition_entity} definition")
    return _BASIS_NAMESPACE[key]
