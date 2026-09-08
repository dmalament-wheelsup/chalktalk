"""The attribute catalog: what every derived column means.

This is the surface `propose_definition` searches and the compiler resolves
against. An attribute with no entry here does not exist as far as the rest of
the system is concerned, which is why the completeness test is a build error and
not a lint.

`play` is the exception: its attributes are the ~370 raw `pbp` columns, so they
are generated from the table itself and only the ones worth a human description
appear in :data:`PLAY_DOCS`.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Literal

import duckdb

from chalktalk.entities import ENTITIES, namespaces

AttrType = Literal["int", "float", "bool", "str", "date"]

#: Used by `propose_definition` to search the surface by theme.
FAMILIES = frozenset(
    {
        "identity",
        "context",
        "result",
        "betting",
        "weather",
        "snaps",
        "roster",
        "injury",
        "participation",
        "baseline",
        "linkage",
        "production",
        "contract",
        "draft",
        "epa",
        "situation",
    }
)

_DUCK_TO_ATTR: dict[str, AttrType] = {
    "BOOLEAN": "bool",
    "TINYINT": "int",
    "SMALLINT": "int",
    "INTEGER": "int",
    "BIGINT": "int",
    "HUGEINT": "int",
    "UTINYINT": "int",
    "USMALLINT": "int",
    "UINTEGER": "int",
    "UBIGINT": "int",
    "FLOAT": "float",
    "DOUBLE": "float",
    "DECIMAL": "float",
    "VARCHAR": "str",
    "DATE": "date",
    "TIMESTAMP": "date",
}


@dataclass(frozen=True)
class Attribute:
    entity: str
    name: str
    type: AttrType
    family: str
    description: str


@dataclass(frozen=True)
class ResolvedAttr:
    namespace: str | None  # None = the entity's own table
    attribute: Attribute


class UnknownAttribute(Exception):
    def __init__(self, ref: str, did_you_mean: list[str]) -> None:
        self.ref = ref
        self.did_you_mean = did_you_mean
        hint = f"; did you mean {', '.join(did_you_mean)}?" if did_you_mean else ""
        super().__init__(f"unknown attribute {ref!r}{hint}")


def _attrs(*rows: tuple[str, AttrType, str, str], entity: str) -> dict[tuple[str, str], Attribute]:
    return {
        (entity, name): Attribute(entity, name, type_, family, description)
        for name, type_, family, description in rows
    }


ATTRIBUTES: dict[tuple[str, str], Attribute] = {}

# Entity tables are documented here as each lands (4b: game/team_game/
# team_season; 4c: player_season; 4d: player_game). `player_id_xwalk` and
# `player_play` are internal helpers, not part of the attribute surface.


#: Curated descriptions for the `pbp` columns worth naming. Everything else in
#: `pbp` is still addressable on the `play` entity; it just carries no prose.
PLAY_DOCS: dict[str, str] = {
    "down": "Down, 1-4. NULL on kickoffs, extra points and timeouts.",
    "ydstogo": "Yards needed for a first down.",
    "yardline_100": "Distance to the opponent's end zone, 1-99. 20 or less is the red zone.",
    "qtr": "Quarter, 1-4; 5 is overtime.",
    "game_seconds_remaining": "Seconds left in the game, 3600 at kickoff.",
    "half_seconds_remaining": "Seconds left in the half.",
    "score_differential": "Possession team's score minus the opponent's, before the play.",
    "play_type": "pass, run, punt, field_goal, kickoff, extra_point, qb_kneel, qb_spike, no_play.",
    "pass": "1 if the play was a pass attempt, sack or scramble.",
    "rush": "1 if the play was a designed run.",
    "epa": "Expected points added by this play for the possession team.",
    "wpa": "Win probability added by this play for the possession team.",
    "wp": "Possession team's win probability before the play, 0-1.",
    "success": "1 if EPA was positive.",
    "air_yards": "Yards the ball travelled past the line of scrimmage before the catch point.",
    "yards_after_catch": "Yards gained after the reception.",
    "yards_gained": "Net yards gained on the play.",
    "touchdown": "1 if the play ended in a touchdown by either team.",
    "interception": "1 if the pass was intercepted.",
    "fumble_lost": "1 if a fumble was lost to the defence.",
    "sack": "1 if the quarterback was sacked.",
    "penalty": "1 if a penalty was called on the play.",
    "fourth_down_converted": "1 if a fourth-down attempt gained the first down or scored.",
    "fourth_down_failed": "1 if a fourth-down attempt fell short.",
    "third_down_converted": "1 if a third-down attempt gained the first down or scored.",
    "field_goal_result": "made, missed or blocked.",
    "two_point_attempt": "1 if the play was a two-point conversion attempt.",
    "posteam": "Team with the ball.",
    "defteam": "Team on defence.",
    "passer_player_id": "gsis_id of the passer.",
    "rusher_player_id": "gsis_id of the ball carrier on a designed run.",
    "receiver_player_id": "gsis_id of the targeted receiver.",
    "shotgun": "1 if the offence lined up in shotgun.",
    "no_huddle": "1 if the offence ran the play without huddling.",
    "qb_dropback": "1 if the quarterback dropped back to pass, including sacks and scrambles.",
    "qb_scramble": "1 if a called pass became a quarterback run.",
    "pass_length": "short or deep.",
    "pass_location": "left, middle or right.",
    "run_location": "left, middle or right.",
    "run_gap": "end, tackle or guard.",
    "desc": "The play-by-play text description.",
}


def _play_attributes(conn: duckdb.DuckDBPyConnection) -> list[Attribute]:
    """The `play` entity is the raw `pbp` table; describe what we can, expose it all."""
    rows = conn.execute(f'DESCRIBE "{ENTITIES["play"].table}"').fetchall()
    return [
        Attribute(
            "play",
            name,
            _DUCK_TO_ATTR.get(duck_type.split("(")[0], "str"),
            "situation",
            PLAY_DOCS.get(name, ""),
        )
        for name, duck_type, *_ in rows
    ]


def attributes_for(entity: str, conn: duckdb.DuckDBPyConnection) -> list[Attribute]:
    """Every attribute addressable on ``entity``'s own table."""
    if entity not in ENTITIES:
        raise UnknownAttribute(entity, sorted(ENTITIES))
    if entity == "play":
        return _play_attributes(conn)
    return sorted(
        (a for (e, _), a in ATTRIBUTES.items() if e == entity), key=lambda a: (a.family, a.name)
    )


def _entity_of_namespace(entity: str, namespace: str) -> str:
    """Which entity a namespace's rows belong to, so its attributes can be looked up."""
    join = namespaces(entity)[namespace]
    for name, spec in ENTITIES.items():
        if spec.table == join.table:
            return name
    raise KeyError(f"namespace {namespace!r} of {entity} points at unknown table {join.table}")


def resolve(entity: str, ref: str, conn: duckdb.DuckDBPyConnection) -> ResolvedAttr:
    """``"name"`` or ``"ns.name"`` → the attribute it names.

    Raises :class:`UnknownAttribute` with suggestions rather than guessing.
    """
    if entity not in ENTITIES:
        raise UnknownAttribute(ref, sorted(ENTITIES))

    namespace: str | None = None
    name = ref
    if "." in ref:
        namespace, _, name = ref.partition(".")
        available = namespaces(entity)
        if namespace not in available:
            raise UnknownAttribute(
                ref, [f"{n}.{name}" for n in difflib.get_close_matches(namespace, available, n=3)]
            )
        target_entity = _entity_of_namespace(entity, namespace)
    else:
        target_entity = entity

    for attribute in attributes_for(target_entity, conn):
        if attribute.name == name:
            return ResolvedAttr(namespace, attribute)

    known = [a.name for a in attributes_for(target_entity, conn)]
    prefix = f"{namespace}." if namespace else ""
    raise UnknownAttribute(
        ref, [prefix + m for m in difflib.get_close_matches(name, known, n=3, cutoff=0.6)]
    )


def documented_entities() -> list[str]:
    """Entities whose attributes are hand-written here (everything but `play`)."""
    return [e for e in ENTITIES if e != "play"]


def _existing_columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[str] | None:
    found = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
        [table],
    ).fetchone()
    if not found:
        return None
    return [row[0] for row in conn.execute(f'DESCRIBE "{table}"').fetchall()]


def undocumented(conn: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """Columns of built entity tables with no ATTRIBUTES entry.

    A non-empty result is the feature layer being incomplete, not a style
    problem: an undocumented column is invisible to `propose_definition` and
    unresolvable by the compiler.
    """
    missing: dict[str, list[str]] = {}
    for entity in documented_entities():
        columns = _existing_columns(conn, ENTITIES[entity].table)
        if columns is None:
            continue  # not built yet
        absent = [c for c in columns if (entity, c) not in ATTRIBUTES]
        if absent:
            missing[entity] = absent
    return missing


def orphaned(conn: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """ATTRIBUTES entries naming a column the built table does not have (typos)."""
    orphans: dict[str, list[str]] = {}
    for entity in documented_entities():
        columns = _existing_columns(conn, ENTITIES[entity].table)
        if columns is None:
            continue
        extra = [name for (e, name) in ATTRIBUTES if e == entity and name not in columns]
        if extra:
            orphans[entity] = sorted(extra)
    return orphans
