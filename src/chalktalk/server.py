"""The MCP server: eleven tools over stdio.

The tool descriptions are product surface, not documentation. The calling model
reads them and decides what to do, so the one on `query` says explicitly not to
route around `unresolved_term` by substituting an attribute rule — because that
is exactly what an eager model will otherwise do, and it would defeat the point
of the whole system.

Nothing writes to stdout but the transport. All logging goes to stderr.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
from mcp.server.fastmcp import FastMCP

from chalktalk import __version__, audit, sqlguard
from chalktalk.config import Settings
from chalktalk.coverage import Coverage
from chalktalk.db import open_ro, read_current
from chalktalk.definitions.context import open_store
from chalktalk.definitions.explain import explain as explain_definition
from chalktalk.definitions.propose import as_dict as proposal_as_dict
from chalktalk.definitions.propose import propose as propose_term
from chalktalk.definitions.signals import SIGNALS
from chalktalk.definitions.spec import DefinitionIn, InvalidDefinition
from chalktalk.definitions.vocabulary_install import install
from chalktalk.entities import ENTITIES
from chalktalk.features.catalog import ATTRIBUTES, PLAY_DOCS, attributes_for
from chalktalk.query.plan import QueryPlan
from chalktalk.query.run import explain_query as run_explain
from chalktalk.query.run import query as run_query

log = logging.getLogger(__name__)

NO_DATABASE = {
    "ok": False,
    "error": "no_database",
    "message": "No database has been built yet. Run `chalktalk build` and try again.",
}


@dataclass
class State:
    """Everything a tool call needs, refreshed when the world changes."""

    settings: Settings
    conn: duckdb.DuckDBPyConnection | None = None
    artifact: Path | None = None
    store: Any = None
    coverage: Coverage | None = None

    @property
    def ready(self) -> bool:
        return self.conn is not None

    def refresh(self) -> None:
        """Reopen if CURRENT moved; reload definitions if the directory changed.

        Rollback is editing CURRENT, so noticing it between calls is what makes
        a rebuild take effect without restarting the server.
        """
        current = read_current(self.settings)
        if current != self.artifact:
            if self.conn is not None:
                self.conn.close()
                self.conn = None
            self.artifact = current
            if current is not None:
                self.conn = open_ro(current, self.settings)
                self.coverage = Coverage(self.conn, self.settings)
                self.store = open_store(self.conn, self.settings)
                report = install(self.store)
                if report.installed:
                    log.info("installed %s shipped definitions", len(report.installed))
                _log_broken(self.store)
        elif self.store is not None and self.store.maybe_reload():
            _log_broken(self.store)


def _log_broken(store) -> None:
    if store.broken:
        log.warning(
            "%s definition(s) quarantined and unusable until fixed:\n%s",
            len(store.broken),
            "\n".join(f"  {n}: {w}" for n, w in sorted(store.broken.items())),
        )


def _build_info(state: State) -> dict[str, Any]:
    """What produced this database. Absent from a hand-made one, which is fine."""
    if not state.ready:
        return {}
    try:
        row = state.conn.execute(
            "SELECT built_at, chalktalk_version, nflreadpy_version, duckdb_version, "
            "polars_version, first_season, last_season, git_sha FROM build_info"
        ).fetchone()
    except duckdb.Error:
        return {"artifact": state.artifact.name if state.artifact else None}
    if row is None:
        return {"artifact": state.artifact.name}
    keys = (
        "built_at", "chalktalk_version", "nflreadpy_version", "duckdb_version",
        "polars_version", "first_season", "last_season", "git_sha",
    )  # fmt: skip
    return {"artifact": state.artifact.name, **dict(zip(keys, row, strict=True))}


def _coverage_of(state: State, table: str, column: str) -> dict[str, Any] | None:
    found = state.coverage.column(table, column)
    if found is None:
        return None
    return {
        "first": found.first_season,
        "last": found.last_season,
        "seasonal": found.seasonal,
        "has_gaps": found.has_gaps,
    }


def create_server(settings: Settings | None = None) -> FastMCP:
    """Build the app. Kept separate from `serve` so tests can drive it in-process."""
    state = State(settings or Settings.load())
    state.refresh()
    if not state.ready:
        log.warning("no database yet; data tools will say so. Run `chalktalk build`.")

    mcp = FastMCP(
        "chalktalk",
        instructions=(
            "Composite NFL questions where the hard part is agreeing what the words mean. "
            "Any fuzzy concept in a question — star, starter, injury, blowout, workhorse — "
            "must be a saved definition before it can be queried. When `query` returns "
            "`unresolved_term`, call `propose_definition`, show the candidates to the user, "
            "and let them choose. Never substitute an attribute rule to get past the gate: "
            "the definition is the answer's meaning, and an unstated one makes the number "
            "worthless."
        ),
    )

    def guard() -> dict[str, Any] | None:
        state.refresh()
        return None if state.ready else NO_DATABASE

    # ── schema and coverage ───────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Describe what can be queried: the six entities (game, team_game, team_season, "
            "player_game, player_season, play), their namespaces (e.g. `prior.`, `game.`, "
            "`next.`), and every attribute with its coverage window. Attributes are the only "
            "raw fields a plan may reference. Anything that is a *concept* rather than a "
            'field — "star", "starter", "injury", "blowout" — must be a term: see '
            "`list_definitions` and `propose_definition`."
        )
    )
    def describe_schema(
        entity: str | None = None, include_play_columns: bool = False
    ) -> dict[str, Any]:
        if (refusal := guard()) is not None:
            return refusal
        wanted = [entity] if entity else list(ENTITIES)
        unknown = [e for e in wanted if e not in ENTITIES]
        if unknown:
            return {
                "ok": False,
                "error": "invalid_plan",
                "message": f"unknown entity {unknown[0]!r}",
                "entities": list(ENTITIES),
            }

        out: dict[str, Any] = {"entities": {}}
        for name in wanted:
            spec = ENTITIES[name]
            attributes = attributes_for(name, state.conn)
            if name == "play" and not include_play_columns:
                attributes = [a for a in attributes if a.name in PLAY_DOCS]
            out["entities"][name] = {
                "table": spec.table,
                "key": list(spec.key),
                "namespaces": {
                    ns: {"table": join.table, "meaning": f"joined on {join.on}"}
                    for ns, join in spec.namespaces.items()
                },
                "attributes": [
                    {
                        "name": a.name,
                        "type": a.type,
                        "family": a.family,
                        "description": a.description,
                        "coverage": _coverage_of(state, spec.table, a.name),
                    }
                    for a in attributes
                ],
            }
        return out

    @mcp.tool(
        description=(
            "The seasons a thing can answer for. Accepts `table.column`, `entity.attribute`, "
            "or the name of a definition. A query is refused when what it asks for reaches "
            "outside this window, so check here before promising a range."
        )
    )
    def coverage(ref: str) -> dict[str, Any]:
        if (refusal := guard()) is not None:
            return refusal
        definition = state.store.get(ref)
        if definition is not None:
            span = state.store.coverage_of(ref)
            return {
                "ref": ref,
                "kind": "definition",
                "first": span.first if span else None,
                "last": span.last if span else None,
            }
        head, _, column = ref.rpartition(".")
        if not head:
            return {
                "ok": False,
                "error": "unknown_attribute",
                "message": f"{ref!r} is not a table.column, entity.attribute or definition",
            }
        table = ENTITIES[head].table if head in ENTITIES else head
        found = _coverage_of(state, table, column)
        if found is None:
            return {"ok": False, "error": "unknown_attribute", "message": f"nothing named {ref!r}"}
        return {"ref": ref, "kind": "attribute", "table": table, **found}

    # ── definitions ───────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "The user's vocabulary. Every term used in a query must be here. Broken "
            "definitions (a column they depend on disappeared) are listed with the reason "
            "and cannot be used until fixed."
        )
    )
    def list_definitions(include_broken: bool = True, family: str | None = None) -> dict[str, Any]:
        if (refusal := guard()) is not None:
            return refusal
        rows = []
        for summary in state.store.list(include_broken=include_broken):
            definition = state.store.get(summary.name)
            explanation = ""
            span = None
            if definition is not None:
                explanation = explain_definition(definition, state.store).text
                span = state.store.coverage_of(summary.name)
            if family and definition is not None and definition.entity != family:
                continue
            rows.append(
                {
                    "name": summary.name,
                    "aliases": summary.aliases,
                    "entity": summary.entity,
                    "basis": summary.basis,
                    "signal": summary.signal,
                    "description": summary.description,
                    "explanation": explanation,
                    "coverage": {"first": span.first, "last": span.last} if span else None,
                    "source": summary.source,
                    "version": summary.version,
                    "broken_reason": summary.broken,
                }
            )
        return {"definitions": rows, "count": len(rows), "broken": len(state.store.broken)}

    @mcp.tool(
        description=(
            "Everything about one definition: its spec, a nested English explanation of the "
            "definitions it is built from, the attributes it uses as evidence, its coverage, "
            "and its previous versions."
        )
    )
    def get_definition(name: str) -> dict[str, Any]:
        if (refusal := guard()) is not None:
            return refusal
        definition = state.store.get(name)
        if definition is None:
            reason = state.store.broken.get(name)
            return {
                "ok": False,
                "error": "definition_broken" if reason else "invalid_definition",
                "message": reason or f"no definition named {name!r}",
            }
        explanation = explain_definition(definition, state.store)
        history = (
            sorted(p.stem for p in (state.store.directory / ".history" / name).glob("v*.json"))
            if (state.store.directory / ".history" / name).is_dir()
            else []
        )
        return {
            "definition": definition.model_dump(),
            "explanation": explanation.text,
            "explanation_tree": explanation.render(),
            "evidence": [str(r) for r in explanation.evidence],
            "coverage": (
                {"first": explanation.coverage.first, "last": explanation.coverage.last}
                if explanation.coverage
                else None
            ),
            "warnings": explanation.warnings,
            "history": history,
        }

    @mcp.tool(
        description=(
            "Call this when a query needs a concept that has no definition yet, or when "
            "`query` returns `unresolved_term`. Returns existing near-matches, ready-to-save "
            "candidate definitions grounded in what the data can compute (each with an "
            "English explanation and coverage), related attributes, and the five signal "
            "schemas for composing something new. Present the candidates to the user and let "
            "them choose or adjust. Do not pick one silently."
        )
    )
    def propose_definition(
        term: str, context: str | None = None, entity: str | None = None
    ) -> dict[str, Any]:
        if (refusal := guard()) is not None:
            return refusal
        return proposal_as_dict(propose_term(term, context, entity, store=state.store))

    @mcp.tool(
        description=(
            "Save a definition the user has chosen. `copy_of` copies an existing definition "
            "(e.g. `star_by_snaps`) under a new name. Saving is versioned; previous versions "
            "are kept. Tell the user what was saved in plain English — the returned "
            "explanation is written for that."
        )
    )
    def save_definition(
        name: str,
        entity: str | None = None,
        signal: str | None = None,
        params: dict[str, Any] | None = None,
        aliases: list[str] | None = None,
        description: str | None = None,
        basis: str | None = None,
        copy_of: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        if (refusal := guard()) is not None:
            return refusal
        try:
            incoming = DefinitionIn(
                name=name,
                entity=entity or (copy_of and state.store.get(copy_of).entity) or "player_game",
                signal=signal or (copy_of and state.store.get(copy_of).signal) or "rule",
                params=params or {},
                aliases=aliases or [],
                description=description or "",
                basis=basis,
            )
            saved = state.store.save(incoming, overwrite=overwrite, copied_from=copy_of)
        except InvalidDefinition as exc:
            return exc.as_error()
        except (AttributeError, ValueError) as exc:
            return {"ok": False, "error": "invalid_definition", "message": str(exc)}
        explanation = explain_definition(saved, state.store)
        span = state.store.coverage_of(saved.name)
        return {
            "ok": True,
            "saved": saved.model_dump(),
            "explanation": explanation.text,
            "explanation_tree": explanation.render(),
            "coverage": {"first": span.first, "last": span.last} if span else None,
        }

    @mcp.tool(
        description=(
            "Delete a definition. Its previous version is kept in history, so this is "
            "recoverable. Anything built on top of it becomes broken until it is replaced."
        )
    )
    def delete_definition(name: str) -> dict[str, Any]:
        if (refusal := guard()) is not None:
            return refusal
        try:
            state.store.delete(name)
        except InvalidDefinition as exc:
            return exc.as_error()
        return {"ok": True, "deleted": name, "broken_now": sorted(state.store.broken)}

    # ── querying ──────────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Dry run. Shows the English reading of the plan, which definitions it will use, "
            "the seasons it will cover, warnings, and the SQL. Use it to confirm your "
            "interpretation with the user before running an expensive or ambiguous query."
        )
    )
    def explain_query(plan: dict[str, Any]) -> dict[str, Any]:
        if (refusal := guard()) is not None:
            return refusal
        parsed = _parse_plan(plan)
        if isinstance(parsed, dict):
            return parsed
        out = run_explain(
            parsed,
            store=state.store,
            coverage=state.coverage,
            settings=state.settings,
            conn=state.conn,
        )
        _audit("explain_query", state, parsed, out)
        return out

    @mcp.tool(
        description=(
            "Run a structured query. `plan` is `{entity, where, seasons, game_types, "
            "group_by, metrics, order_by, limit, allow_partial_coverage, sample_rows, "
            'question}`; `where` clauses are `{"term": name}` for concepts, '
            '`{"attr": name, "op": …, "value": …}` for raw fields, and '
            '`{"not"|"any_of"|"all_of": …}` to combine. Every fuzzy word in the user\'s '
            "question must be a term. If a term has no definition this tool returns "
            "`unresolved_term` — do **not** replace it with an attribute rule to get past "
            "the error; call `propose_definition` and ask the user. The result includes "
            "`definitions_used`, the `seasons` actually covered, a `sample` of matched rows "
            "and the SQL; report the definitions and coverage alongside the number, because "
            "the number is not meaningful without them. If `warnings` mention evidence "
            "unavailable for early seasons, say so."
        )
    )
    def query(plan: dict[str, Any]) -> dict[str, Any]:
        if (refusal := guard()) is not None:
            return refusal
        parsed = _parse_plan(plan)
        if isinstance(parsed, dict):
            return parsed
        out = run_query(
            parsed,
            store=state.store,
            coverage=state.coverage,
            settings=state.settings,
            conn=state.conn,
            build=_build_info(state),
        )
        _audit("query", state, parsed, out)
        return out

    @mcp.tool(
        description=(
            "Escape hatch: read-only SQL over every raw and derived table (see "
            "`describe_schema` and `coverage`). A single SELECT or WITH statement, row cap, "
            "timeout, no file or network access. Results carry no definitions — prefer "
            "`query` for anything involving a concept, and tell the user when a number came "
            "from raw SQL. Every call is logged; recurring raw queries are how new "
            "attributes get prioritised."
        )
    )
    def raw_sql(sql: str, limit: int = 500) -> dict[str, Any]:
        if (refusal := guard()) is not None:
            return refusal
        capped = min(int(limit), state.settings.raw_sql_row_cap)
        try:
            result = sqlguard.run_raw(state.conn, sql, capped, state.settings.raw_sql_timeout_s)
        except sqlguard.SqlRejected as exc:
            audit.record(state.settings, {"tool": "raw_sql", "sql": sql, "error": "sql_rejected"})
            return {"ok": False, "error": "sql_rejected", "message": exc.reason}
        except sqlguard.SqlTimeout as exc:
            audit.record(state.settings, {"tool": "raw_sql", "sql": sql, "error": "sql_timeout"})
            return {"ok": False, "error": "sql_timeout", "message": str(exc)}
        audit.record(
            state.settings,
            {
                "tool": "raw_sql",
                "sql": sql,
                "row_count": len(result.rows),
                "timing_ms": result.timing_ms,
            },
        )
        return {
            "ok": True,
            "columns": result.columns,
            "rows": result.rows,
            "truncated": result.truncated,
            "timing_ms": result.timing_ms,
        }

    @mcp.tool(
        description=(
            "What this server is running on: which database artifact, when it was built, "
            "the seasons it holds, whether the latest is still in progress, how many "
            "definitions exist and how many are broken."
        )
    )
    def build_status() -> dict[str, Any]:
        state.refresh()
        if not state.ready:
            return {**NO_DATABASE, "home": str(state.settings.home)}
        queryable = state.coverage.queryable_seasons()
        latest = state.coverage.status(queryable[-1]) if queryable else None
        return {
            "ok": True,
            "chalktalk_version": __version__,
            "home": str(state.settings.home),
            "build": _build_info(state),
            "seasons": {
                "first": queryable[0] if queryable else None,
                "last": queryable[-1] if queryable else None,
                "latest_in_progress": bool(latest and latest.in_progress),
            },
            "definitions": {
                "count": len(state.store.list(include_broken=False)),
                "broken": sorted(state.store.broken),
            },
            "signals": sorted(SIGNALS),
        }

    return mcp


def _parse_plan(payload: dict[str, Any]) -> QueryPlan | dict[str, Any]:
    """FastMCP gives `dict` an open schema, so the shape is validated here."""
    from pydantic import ValidationError

    try:
        return QueryPlan.model_validate(payload)
    except ValidationError as exc:
        return {
            "ok": False,
            "error": "invalid_plan",
            "message": "the plan does not have the expected shape",
            "problems": [
                {"where": ".".join(str(p) for p in e["loc"]) or "plan", "problem": e["msg"]}
                for e in exc.errors()[:10]
            ],
        }


def _audit(tool: str, state: State, plan: QueryPlan, out: dict[str, Any]) -> None:
    audit.record(
        state.settings,
        {
            "tool": tool,
            "question": plan.question,
            "entity": plan.entity,
            "plan": plan.model_dump(exclude_none=True),
            "definitions_used": [
                {"name": d["name"], "version": d["version"]}
                for d in out.get("definitions_used", [])
            ],
            "row_count": out.get("row_count"),
            "timing_ms": out.get("timing_ms"),
            "error": out.get("error"),
        },
    )


def serve(settings: Settings | None = None) -> None:
    """Run over stdio. Nothing but the transport may write to stdout."""
    create_server(settings).run(transport="stdio")


ATTRIBUTE_COUNT = len(ATTRIBUTES)
