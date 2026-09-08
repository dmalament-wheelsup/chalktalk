"""The attribute catalog: well-formed entries, working resolution, and completeness.

The completeness check is the point of this file. It is close to vacuous while
the entity tables are still being built (stage 4a), so it also proves that it
*would* fail — otherwise it could stay vacuous without anyone noticing.
"""

from __future__ import annotations

import duckdb
import pytest

from chalktalk.entities import ENTITIES
from chalktalk.features import catalog
from chalktalk.features.catalog import (
    FAMILIES,
    PLAY_DOCS,
    Attribute,
    UnknownAttribute,
    attributes_for,
    resolve,
)

# From the plan: PLAY_DOCS must describe at least these.
REQUIRED_PLAY_DOCS = {
    "down", "ydstogo", "yardline_100", "qtr", "game_seconds_remaining",
    "half_seconds_remaining", "score_differential", "play_type", "pass", "rush",
    "epa", "wpa", "wp", "success", "air_yards", "yards_after_catch", "yards_gained",
    "touchdown", "interception", "fumble_lost", "sack", "penalty",
    "fourth_down_converted", "fourth_down_failed", "third_down_converted",
    "field_goal_result", "two_point_attempt", "posteam", "defteam",
    "passer_player_id", "rusher_player_id", "receiver_player_id", "shotgun",
    "no_huddle", "qb_dropback", "qb_scramble", "pass_length", "pass_location",
    "run_location", "run_gap", "desc",
}  # fmt: skip


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE pbp (game_id VARCHAR, play_id BIGINT, epa DOUBLE, "
        '"desc" VARCHAR, posteam VARCHAR, touchdown INTEGER)'
    )
    yield connection
    connection.close()


# ── entries are well-formed ───────────────────────────────────────────────────


def test_every_entry_is_keyed_by_its_own_entity_and_name() -> None:
    for (entity, name), attribute in catalog.ATTRIBUTES.items():
        assert attribute.entity == entity
        assert attribute.name == name


def test_every_entry_names_a_real_entity() -> None:
    for entity, _ in catalog.ATTRIBUTES:
        assert entity in ENTITIES


def test_every_entry_has_a_known_family_and_a_description() -> None:
    for key, attribute in catalog.ATTRIBUTES.items():
        assert attribute.family in FAMILIES, f"{key}: unknown family {attribute.family!r}"
        assert attribute.description.strip(), f"{key}: no description"
        assert attribute.type in ("int", "float", "bool", "str", "date")


def test_play_docs_cover_what_the_plan_requires() -> None:
    assert REQUIRED_PLAY_DOCS <= set(PLAY_DOCS), sorted(REQUIRED_PLAY_DOCS - set(PLAY_DOCS))


def test_play_docs_all_say_something() -> None:
    for column, description in PLAY_DOCS.items():
        assert description.strip(), f"{column} has an empty description"


# ── the play entity is generated from the table ───────────────────────────────


def test_play_attributes_come_from_the_table(conn: duckdb.DuckDBPyConnection) -> None:
    names = {a.name for a in attributes_for("play", conn)}
    assert names == {"game_id", "play_id", "epa", "desc", "posteam", "touchdown"}


def test_play_attributes_carry_docs_where_we_have_them(conn: duckdb.DuckDBPyConnection) -> None:
    by_name = {a.name: a for a in attributes_for("play", conn)}
    assert by_name["epa"].description == PLAY_DOCS["epa"]
    assert by_name["game_id"].description == ""


def test_play_attribute_types_follow_the_column_types(conn: duckdb.DuckDBPyConnection) -> None:
    by_name = {a.name: a for a in attributes_for("play", conn)}
    assert by_name["epa"].type == "float"
    assert by_name["play_id"].type == "int"
    assert by_name["posteam"].type == "str"


# ── resolution ────────────────────────────────────────────────────────────────


def test_resolve_a_bare_name(conn: duckdb.DuckDBPyConnection) -> None:
    found = resolve("play", "epa", conn)
    assert found.namespace is None
    assert found.attribute.name == "epa"


def test_resolve_suggests_a_near_miss(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(UnknownAttribute) as excinfo:
        resolve("play", "eps", conn)
    assert "epa" in excinfo.value.did_you_mean


def test_resolve_reports_the_ref_it_was_given(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(UnknownAttribute) as excinfo:
        resolve("play", "nonesuch_at_all", conn)
    assert excinfo.value.ref == "nonesuch_at_all"


def test_resolve_rejects_an_unknown_namespace(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(UnknownAttribute):
        resolve("play", "nope.epa", conn)


def test_resolve_rejects_an_unknown_entity(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(UnknownAttribute):
        resolve("teams", "epa", conn)


def test_resolve_follows_a_namespace(conn: duckdb.DuckDBPyConnection) -> None:
    """`play` reaches game_ctx through `game.`; the attribute must be found there."""
    catalog.ATTRIBUTES[("game", "roof")] = Attribute(
        "game", "roof", "str", "weather", "dome, outdoors, closed or open."
    )
    try:
        found = resolve("play", "game.roof", conn)
        assert found.namespace == "game"
        assert found.attribute.entity == "game"
    finally:
        del catalog.ATTRIBUTES[("game", "roof")]


# ── completeness ──────────────────────────────────────────────────────────────


def test_no_entity_table_has_an_undocumented_column(conn: duckdb.DuckDBPyConnection) -> None:
    assert catalog.undocumented(conn) == {}


def test_no_entry_names_a_column_that_does_not_exist(conn: duckdb.DuckDBPyConnection) -> None:
    assert catalog.orphaned(conn) == {}


def test_the_completeness_check_actually_fails_on_an_undocumented_column(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Guard against the check quietly staying vacuous as tables land."""
    conn.execute("CREATE TABLE game_ctx (game_id VARCHAR, undocumented_thing INTEGER)")
    missing = catalog.undocumented(conn)
    assert missing == {"game": ["game_id", "undocumented_thing"]}


def test_the_orphan_check_actually_fails_on_a_stale_entry(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    conn.execute("CREATE TABLE game_ctx (game_id VARCHAR)")
    catalog.ATTRIBUTES[("game", "game_id")] = Attribute(
        "game", "game_id", "str", "identity", "nflverse game id."
    )
    catalog.ATTRIBUTES[("game", "typoed")] = Attribute(
        "game", "typoed", "str", "identity", "not a real column."
    )
    try:
        assert catalog.orphaned(conn) == {"game": ["typoed"]}
        assert catalog.undocumented(conn) == {}
    finally:
        del catalog.ATTRIBUTES[("game", "game_id")]
        del catalog.ATTRIBUTES[("game", "typoed")]


def test_unbuilt_tables_are_skipped_not_reported(conn: duckdb.DuckDBPyConnection) -> None:
    """Stage 4a has no entity tables at all; that is not the same as undocumented."""
    assert catalog.undocumented(conn) == {}
    assert catalog.orphaned(conn) == {}
