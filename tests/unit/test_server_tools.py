"""The MCP tools, driven in process.

The sequence that matters most is the one a user actually walks: ask the
headline question, be refused, see the candidates, choose one, ask again. That
flow *is* the product, so it is tested end to end here — a regression in it
would not show up anywhere else.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from chalktalk.server import create_server

TOOLS = {
    "describe_schema", "coverage", "list_definitions", "get_definition",
    "propose_definition", "save_definition", "delete_definition",
    "explain_query", "query", "raw_sql", "build_status",
}  # fmt: skip


@pytest.fixture
def server(mini_conn, mini_settings, tmp_path, monkeypatch):
    """A server wired to the mini database, through the real lifecycle."""
    from chalktalk.coverage import Coverage
    from chalktalk.definitions.context import open_store

    settings = replace(mini_settings, home=tmp_path)
    store = open_store(mini_conn, settings, directory=tmp_path / "definitions")

    monkeypatch.setattr("chalktalk.server.read_current", lambda s: tmp_path / "mini.duckdb")
    monkeypatch.setattr("chalktalk.server.open_ro", lambda path, s: mini_conn)
    monkeypatch.setattr("chalktalk.server.open_store", lambda conn, s, **kw: store)
    monkeypatch.setattr("chalktalk.server.Coverage", lambda conn, s: Coverage(mini_conn, s))
    return create_server(settings)


def call(app, tool: str, **arguments: Any) -> dict[str, Any]:
    """One tool call over an in-memory client session."""

    async def go() -> dict[str, Any]:
        async with connect(app._mcp_server) as client:
            result = await client.call_tool(tool, arguments)
        assert result.content, f"{tool} returned nothing"
        return json.loads(result.content[0].text)

    return asyncio.run(go())


def tool_descriptions(app) -> dict[str, str]:
    async def go() -> dict[str, str]:
        async with connect(app._mcp_server) as client:
            return {t.name: t.description or "" for t in (await client.list_tools()).tools}

    return asyncio.run(go())


# ── the surface ───────────────────────────────────────────────────────────────


def test_every_tool_is_registered(server) -> None:
    assert set(tool_descriptions(server)) == TOOLS


def test_the_descriptions_tell_the_model_what_not_to_do(server) -> None:
    """A description is product surface: an eager model reads it and acts on it."""
    described = tool_descriptions(server)
    assert "do **not** replace it with an attribute rule" in described["query"]
    assert "Do not pick one silently" in described["propose_definition"]
    assert "prefer `query`" in described["raw_sql"]
    assert "must be a term" in described["describe_schema"]


def test_the_handshake_reports_chalktalks_own_version(server) -> None:
    """Not the MCP SDK's.

    FastMCP takes no `version` and the low-level server falls back to the mcp
    package's, so a client would show chalktalk as whatever SDK it was built
    against — and disagree with `build_status` in the same session.
    """
    from chalktalk import __version__

    assert server._mcp_server.version == __version__
    assert call(server, "build_status")["chalktalk_version"] == __version__


def test_build_status_reports_what_it_is_running_on(server) -> None:
    out = call(server, "build_status")
    assert out["ok"]
    assert out["seasons"]["first"] == 2022
    assert out["definitions"]["broken"] == []
    assert out["home"]
    assert set(out["signals"]) == {"rule", "percentile", "rank", "delta", "composite"}


def test_describe_schema_names_the_entities_and_their_coverage(server) -> None:
    out = call(server, "describe_schema", entity="player_game")
    entity = out["entities"]["player_game"]
    assert entity["table"] == "player_game"
    assert "prior" in entity["namespaces"]
    by_name = {a["name"]: a for a in entity["attributes"]}
    assert by_name["snaps_unit"]["type"] == "int"
    assert by_name["pp_missed_tail_frac"]["coverage"]["first"] == 2023


def test_describe_schema_keeps_the_play_entity_readable(server) -> None:
    """~370 raw pbp columns would drown the useful ones unless asked for."""
    curated = call(server, "describe_schema", entity="play")
    everything = call(server, "describe_schema", entity="play", include_play_columns=True)
    assert len(curated["entities"]["play"]["attributes"]) < len(
        everything["entities"]["play"]["attributes"]
    )


def test_coverage_answers_for_attributes_and_definitions(server) -> None:
    assert call(server, "coverage", ref="player_game.snaps_unit")["first"] == 2022
    assert call(server, "coverage", ref="early_exit")["kind"] == "definition"
    assert call(server, "coverage", ref="nonesuch")["error"] == "unknown_attribute"


# ── the flow the product exists for ───────────────────────────────────────────

HEADLINE = {
    "question": "How many times have star players played fewer than 15 snaps before "
    "leaving with injury?",
    "entity": "player_game",
    "where": [
        {"term": "star_player", "basis": "prior_season"},
        {"term": "early_exit"},
        {"attr": "snaps_unit", "op": "<", "value": 15},
    ],
    "group_by": ["season"],
    "allow_partial_coverage": True,
}


def test_the_headline_question_is_refused(server) -> None:
    out = call(server, "query", plan=HEADLINE)
    assert out["ok"] is False
    assert out["error"] == "unresolved_term"
    assert out["next_step"] == "propose_definition"
    assert [t["term"] for t in out["terms"]] == ["star_player"]


def test_the_refusal_leads_to_candidates(server) -> None:
    proposal = call(server, "propose_definition", term="star player")
    offered = {s["definition"]["name"] for s in proposal["suggestions"]}
    assert offered == {"star_by_snaps", "star_by_contract", "star_by_draft"}
    assert proposal["notes"], "the user needs to know why there is no single answer"


def test_choosing_one_makes_the_question_answerable(server) -> None:
    """The whole arc, in the order a user walks it."""
    assert call(server, "query", plan=HEADLINE)["error"] == "unresolved_term"

    saved = call(
        server,
        "save_definition",
        name="star_player",
        copy_of="star_by_snaps",
        description="A star, by prior-season snap share.",
    )
    assert saved["ok"]
    assert "90th percentile" in saved["explanation"]

    answered = call(server, "query", plan=HEADLINE)
    assert answered["ok"], answered
    assert "star_player" in {d["name"] for d in answered["definitions_used"]}
    assert answered["sample"] is not None


def test_the_answer_carries_what_makes_it_checkable(server) -> None:
    call(server, "save_definition", name="star_player", copy_of="star_by_snaps")
    out = call(server, "query", plan=HEADLINE)
    assert out["english"]
    assert out["sql"].count("?") == len(out["sql_params"])
    assert out["seasons"]["covered"]
    assert out["build"]["artifact"]


def test_explain_query_is_a_dry_run(server) -> None:
    call(server, "save_definition", name="star_player", copy_of="star_by_snaps")
    out = call(server, "explain_query", plan=HEADLINE)
    assert out["ok"]
    assert out["sql"]
    assert out["rows"] == []
    assert "timing_ms" not in out


def test_a_malformed_plan_says_where_it_is_wrong(server) -> None:
    out = call(server, "query", plan={"entity": "player_game", "where": [{"nonsense": 1}]})
    assert out["error"] == "invalid_plan"
    assert out["problems"]


# ── definitions ───────────────────────────────────────────────────────────────


def test_list_definitions_returns_the_shipped_vocabulary(server) -> None:
    out = call(server, "list_definitions")
    names = {d["name"] for d in out["definitions"]}
    assert {"early_exit", "star_by_snaps", "played", "went_for_it"} <= names
    assert "star_player" not in names, "the on-ramp word is deliberately absent"
    assert out["broken"] == 0
    assert all(d["explanation"] for d in out["definitions"] if not d["broken_reason"])


def test_get_definition_explains_the_whole_tree(server) -> None:
    out = call(server, "get_definition", name="early_exit")
    assert "left_early" in out["explanation_tree"]
    assert "snap_drop" in out["explanation_tree"]
    assert out["coverage"]["first"] == 2022


def test_get_definition_on_something_absent(server) -> None:
    assert call(server, "get_definition", name="nonesuch")["ok"] is False


def test_saving_something_invalid_says_why(server) -> None:
    out = call(
        server,
        "save_definition",
        name="bad_one",
        entity="player_game",
        signal="rule",
        params={"rules": [{"attr": "snaps_unt", "op": ">=", "value": 1}]},
    )
    assert out["error"] == "invalid_definition"
    assert "snaps_unit" in out["did_you_mean"]


def test_a_typo_too_far_from_anything_gets_no_false_suggestion(server) -> None:
    """Offering an unrelated column would be worse than offering nothing."""
    out = call(
        server,
        "save_definition",
        name="bad_two",
        entity="player_game",
        signal="rule",
        params={"rules": [{"attr": "zamboni_count", "op": ">=", "value": 1}]},
    )
    assert out["error"] == "invalid_definition"
    assert out.get("did_you_mean", []) == []


def test_deleting_a_definition_reports_what_it_broke(server) -> None:
    out = call(server, "delete_definition", name="left_early")
    assert out["ok"]
    assert "exit_evidence" in out["broken_now"], "things built on it are now broken"


# ── the escape hatch ──────────────────────────────────────────────────────────


def test_raw_sql_reads(server) -> None:
    out = call(server, "raw_sql", sql="select count(*) as n from player_game")
    assert out["ok"]
    assert out["rows"] == [{"n": 60}]


def test_raw_sql_refuses_to_write(server) -> None:
    out = call(server, "raw_sql", sql="copy (select 1) to '/tmp/x.csv'")
    assert out["error"] == "sql_rejected"


def test_a_refused_statement_returns_no_rows_at_all(server) -> None:
    """A refusal must not come back looking like a partial answer.

    File reads are refused by the read-only connection rather than by the text
    guard, which test_sqlguard exercises against a real sandboxed connection —
    the mini database here is a plain in-memory one and would happily read a file.
    """
    out = call(server, "raw_sql", sql="attach '/tmp/x.db' as x")
    assert out["ok"] is False
    assert out["error"] == "sql_rejected"
    assert "rows" not in out and "columns" not in out


def test_raw_sql_is_capped(server) -> None:
    out = call(server, "raw_sql", sql="select * from range(1000)", limit=5)
    assert len(out["rows"]) == 5
    assert out["truncated"] is True


def test_raw_sql_cannot_exceed_the_configured_cap(server, mini_settings) -> None:
    out = call(server, "raw_sql", sql="select * from range(100000)", limit=100000)
    assert len(out["rows"]) <= mini_settings.raw_sql_row_cap


# ── the audit log ─────────────────────────────────────────────────────────────


def test_calls_are_logged(server, tmp_path) -> None:
    from chalktalk import audit
    from chalktalk.config import Settings

    call(server, "raw_sql", sql="select 1 as n")
    call(server, "query", plan={"entity": "player_game", "group_by": ["season"]})

    settings = replace(Settings.load(), home=tmp_path)
    entries = audit.read(settings)
    tools = [e["tool"] for e in entries]
    assert "raw_sql" in tools
    assert "query" in tools


def test_recurring_raw_queries_group_together(server, tmp_path) -> None:
    """The point of the log: a shape written by hand repeatedly is a missing feature."""
    from chalktalk import audit
    from chalktalk.config import Settings

    for season in (2022, 2023):
        call(server, "raw_sql", sql=f"select count(*) from player_game where season = {season}")

    summary = audit.summarize(replace(Settings.load(), home=tmp_path))
    shapes = {shape: count for shape, count, _ in summary.raw_shapes}
    assert any(count == 2 for count in shapes.values()), shapes


# ── no database ───────────────────────────────────────────────────────────────


def test_without_a_database_every_data_tool_says_so(mini_settings, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("chalktalk.server.read_current", lambda s: None)
    app = create_server(replace(mini_settings, home=tmp_path))
    for tool, arguments in (
        ("query", {"plan": {"entity": "player_game"}}),
        ("list_definitions", {}),
        ("raw_sql", {"sql": "select 1"}),
    ):
        out = call(app, tool, **arguments)
        assert out["error"] == "no_database"
        assert "chalktalk build" in out["message"]
