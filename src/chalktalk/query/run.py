"""The whole pipeline, end to end.

`query` gates, compiles, executes and wraps; `explain_query` stops before
executing. Phase 8's MCP tools are thin wrappers over these two.
"""

from __future__ import annotations

from typing import Any

import duckdb

from chalktalk.config import Settings
from chalktalk.coverage import Coverage
from chalktalk.definitions.explain import explain as explain_definition
from chalktalk.query.compile import compile_plan
from chalktalk.query.envelope import (
    DefinitionUsed,
    Envelope,
    ErrorEnvelope,
    Sample,
    SeasonReport,
    render_english,
)
from chalktalk.query.execute import QueryTimeout, run
from chalktalk.query.gate import check
from chalktalk.query.plan import QueryPlan


def _definitions_used(compiled, store) -> list[DefinitionUsed]:
    out = []
    for definition in compiled.definitions_used:
        explanation = explain_definition(definition, store)
        span = explanation.coverage
        out.append(
            DefinitionUsed(
                name=definition.name,
                version=definition.version,
                entity=definition.entity,
                basis=definition.basis,
                signal=definition.signal,
                explanation=explanation.text,
                coverage={"first": span.first, "last": span.last} if span else None,
            )
        )
    return out


def _season_report(plan, gate, coverage: Coverage) -> SeasonReport:
    status = coverage.status(gate.seasons.last)
    return SeasonReport(
        requested=[plan.seasons.first, plan.seasons.last] if plan.seasons else None,
        covered=[gate.seasons.first, gate.seasons.last],
        excluded=gate.excluded,
        partial=bool(gate.excluded),
        includes_incomplete_season=bool(status and status.in_progress),
    )


def explain_query(
    plan: QueryPlan, *, store, coverage: Coverage, settings: Settings, conn
) -> dict[str, Any]:
    """Everything a query would return except the numbers."""
    gate = check(plan, store=store, coverage=coverage, settings=settings, conn=conn)
    if not gate.ok:
        return gate.error.as_dict()

    compiled = compile_plan(plan, gate, store=store, settings=settings, conn=conn)
    envelope = Envelope(
        question=plan.question,
        entity=plan.entity,
        english=render_english(plan, gate.seasons, plan.game_types),
        definitions_used=_definitions_used(compiled, store),
        seasons=_season_report(plan, gate, coverage),
        game_types=plan.game_types,
        sql=compiled.sql,
        sql_params=compiled.params,
        sample_sql=compiled.sample_sql,
        warnings=compiled.warnings,
    )
    return envelope.as_dict()


def query(
    plan: QueryPlan,
    *,
    store,
    coverage: Coverage,
    settings: Settings,
    conn: duckdb.DuckDBPyConnection,
    build: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gate, compile, execute, and wrap the answer in something checkable."""
    gate = check(plan, store=store, coverage=coverage, settings=settings, conn=conn)
    if not gate.ok:
        return gate.error.as_dict()

    compiled = compile_plan(plan, gate, store=store, settings=settings, conn=conn)
    try:
        result = run(conn, compiled, settings)
    except QueryTimeout as exc:
        return ErrorEnvelope(
            error="sql_timeout",
            message=str(exc),
            sql=compiled.sql,
            timeout_s=settings.query_timeout_s,
        ).as_dict()

    envelope = Envelope(
        question=plan.question,
        entity=plan.entity,
        english=render_english(plan, gate.seasons, plan.game_types),
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        definitions_used=_definitions_used(compiled, store),
        seasons=_season_report(plan, gate, coverage),
        game_types=plan.game_types,
        sample=Sample(
            total_matched=result.total_matched,
            columns=result.sample_columns,
            rows=result.sample_rows,
        ),
        sql=compiled.sql,
        sql_params=compiled.params,
        sample_sql=compiled.sample_sql,
        timing_ms=result.timing_ms,
        build=build,
        warnings=compiled.warnings,
    )
    return envelope.as_dict()
