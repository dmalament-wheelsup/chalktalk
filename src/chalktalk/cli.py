"""The ``chalktalk`` command line.

Every subcommand except ``doctor`` is a stub until its phase lands; stubs exit 2
so a script that calls one before it exists fails loudly.
"""

from __future__ import annotations

import platform
import sys
from importlib import metadata
from pathlib import Path

import click

from chalktalk import __version__
from chalktalk.config import Settings
from chalktalk.db import read_current
from chalktalk.log import setup_logging
from chalktalk.paths import (
    cache_dir,
    current_pointer,
    data_dir,
    definitions_dir,
    history_dir,
    logs_dir,
)

_STUB_PHASE = {
    "serve": 8,
    "logs": 8,
}


def _not_implemented(command: str) -> None:
    print(f"not implemented (phase {_STUB_PHASE[command]})", file=sys.stderr)
    raise SystemExit(2)


def _n(value: int | None) -> str:
    return "-" if value is None else str(value)


def _dep_version(dist: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "not installed"


@click.group()
@click.version_option(__version__, prog_name="chalktalk")
@click.option("--log-level", default="INFO", show_default=True, help="Logging level (stderr).")
def main(log_level: str) -> None:
    """Composite NFL questions, one agreed definition at a time."""
    setup_logging(log_level.upper())


@main.command()
@click.option(
    "--seasons",
    "seasons_text",
    metavar="YYYY[-YYYY]",
    help="Restrict to these seasons. Default: the season floor through the current season.",
)
@click.option("--only", "only_text", metavar="a,b", help="Restrict to these dataset ids.")
@click.option("--skip-features", is_flag=True, help="Skip the feature layer (phase 4).")
@click.option("--no-publish", is_flag=True, help="Build the artifact but leave CURRENT alone.")
@click.option("--keep", default=3, show_default=True, help="Artifacts to retain after publishing.")
@click.option("--plan", "show_plan", is_flag=True, help="Print what would be loaded and exit.")
def build(
    seasons_text: str | None,
    only_text: str | None,
    skip_features: bool,
    no_publish: bool,
    keep: int,
    show_plan: bool,
) -> None:
    """Ingest nflverse and write a new database artifact."""
    from chalktalk.ingest import build as build_mod

    s = Settings.load()
    try:
        seasons = build_mod.parse_season_range(seasons_text) if seasons_text else None
        only = {p.strip() for p in only_text.split(",") if p.strip()} if only_text else None

        if show_plan:
            for line in build_mod.plan_lines(s, seasons=seasons, only=only):
                print(line)
            return

        result = build_mod.build(
            s,
            seasons=seasons,
            only=only,
            skip_features=skip_features,
            publish=not no_publish,
            keep=keep,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    print()
    for table, rows in sorted(result.tables.items()):
        print(f"  {table:<16} {rows:>10,} rows")
    print()
    for warning in result.warnings:
        print(f"  warning: {warning}")
    published = "published as CURRENT" if not no_publish else "not published (--no-publish)"
    print(f"\n{result.artifact} — {published} in {result.duration_s:.1f}s")


@main.command()
def serve() -> None:
    """Run the MCP server over stdio."""
    _not_implemented("serve")


@main.command()
@click.argument("table", required=False)
@click.option("--column", "column", metavar="COL", help="Show one column of TABLE.")
@click.option("--seasons", "show_seasons", is_flag=True, help="Show season_status instead.")
@click.option(
    "--rebuild",
    is_flag=True,
    help="Recompute the registry in the published artifact. Not while a server is running.",
)
def coverage(table: str | None, column: str | None, show_seasons: bool, rebuild: bool) -> None:
    """Show the coverage registry: what is computable, and for which seasons."""
    from chalktalk import coverage as coverage_mod
    from chalktalk.db import NoDatabase, open_ro, open_rw, read_current

    s = Settings.load()
    artifact = read_current(s)
    if artifact is None:
        raise click.ClickException("no database; run `chalktalk build`") from NoDatabase()

    if rebuild:
        conn = open_rw(artifact, s)
        try:
            coverage_mod.build(conn, s)
            conn.execute("CHECKPOINT")
        finally:
            conn.close()
        print(f"coverage rebuilt in {artifact.name}")
        return

    conn = open_ro(artifact, s)
    try:
        if show_seasons:
            rows = conn.execute(
                "SELECT season, reg_weeks, games_per_team, playoff_teams, reg_games_scheduled, "
                "reg_games_final, post_games_final, complete, in_progress, queryable "
                "FROM season_status ORDER BY season"
            ).fetchall()
            if not rows:
                raise click.ClickException("season_status is empty; rebuild with schedules")
            print(
                f"{'season':>6}  {'weeks':>5} {'g/team':>6} {'playoff':>7}  "
                f"{'reg':>9}  {'post':>5}  status"
            )
            for (
                season,
                reg_weeks,
                per_team,
                playoff,
                sched,
                final,
                post,
                complete,
                in_progress,
                queryable,
            ) in rows:
                state = "complete" if complete else "in progress" if in_progress else "scheduled"
                if not queryable:
                    state += " (not queryable)"
                print(
                    f"{season:>6}  {_n(reg_weeks):>5} {_n(per_team):>6} {_n(playoff):>7}  "
                    f"{final or 0:>4}/{sched or 0:<4}  {_n(post):>5}  {state}"
                )
            return

        query = (
            "SELECT table_name, column_name, duck_type, seasonal, first_season, last_season, "
            "seasons_with_data, has_gaps, non_null_rows, total_rows FROM coverage_columns"
        )
        params: list[object] = []
        where = []
        if table:
            where.append("table_name = ?")
            params.append(table)
        if column:
            where.append("column_name = ?")
            params.append(column)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY table_name, column_name"

        rows = conn.execute(query, params).fetchall()
        if not rows:
            raise click.ClickException(
                f"nothing in the registry for {table or 'any table'}"
                + (f" column {column}" if column else "")
            )

        width = max(len(f"{r[0]}.{r[1]}") for r in rows)
        for (
            table_name,
            column_name,
            duck_type,
            seasonal,
            first,
            last,
            with_data,
            has_gaps,
            non_null,
            total,
        ) in rows:
            ref = f"{table_name}.{column_name}"
            if not seasonal:
                span = "unbounded"
            elif first is None:
                span = "never populated"
            else:
                span = f"{first}..{last}" + (f" ({with_data} seasons, gaps)" if has_gaps else "")
            filled = f"{non_null:,}/{total:,}" if total else "empty"
            print(f"{ref:<{width}}  {duck_type:<12} {span:<28} {filled}")
    finally:
        conn.close()


def _open_store(directory: str | None = None):
    """A store bound to the published database."""
    from chalktalk.db import open_ro, read_current
    from chalktalk.definitions.context import open_store

    s = Settings.load()
    artifact = read_current(s)
    if artifact is None:
        raise click.ClickException("no database; run `chalktalk build`")
    conn = open_ro(artifact, s)
    return open_store(conn, s, directory=Path(directory) if directory else None), conn


@main.group()
def defs() -> None:
    """Inspect and manage the definitions store."""


@defs.command("list")
@click.option("--broken", "only_broken", is_flag=True, help="Show only quarantined definitions.")
def defs_list(only_broken: bool) -> None:
    """List saved definitions."""
    store, conn = _open_store()
    try:
        rows = [s for s in store.list() if not only_broken or s.broken]
        if not rows:
            print("no definitions" + (" are broken" if only_broken else " saved yet"))
            return
        width = max(len(s.name) for s in rows)
        for summary in rows:
            flag = "BROKEN " if summary.broken else ""
            print(
                f"{summary.name:<{width}}  {summary.entity:<14} {summary.signal:<11} "
                f"{flag}{summary.broken or summary.description}"
            )
    finally:
        conn.close()


@defs.command("show")
@click.argument("name")
def defs_show(name: str) -> None:
    """Explain a definition, and the definitions it is built from."""
    from chalktalk.definitions.explain import explain

    store, conn = _open_store()
    try:
        definition = store.get(name)
        if definition is None:
            raise click.ClickException(f"no definition named {name!r}")
        print(explain(definition, store).render())
        print()
        print(definition.model_dump_json(indent=2))
    finally:
        conn.close()


@defs.command("validate")
def defs_validate() -> None:
    """Recheck every definition against the current schema."""
    store, conn = _open_store()
    try:
        report = store.load()
        print(f"{report.loaded} valid, {len(report.broken)} broken")
        for name, why in sorted(report.broken.items()):
            print(f"  {name}: {why}")
        if report.broken:
            raise SystemExit(1)
    finally:
        conn.close()


@defs.command("add")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--overwrite", is_flag=True, help="Replace an existing definition.")
def defs_add(file: Path, overwrite: bool) -> None:
    """Save a definition from a JSON file."""
    import json

    from chalktalk.definitions.spec import DefinitionIn, InvalidDefinition

    store, conn = _open_store()
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
        known = set(DefinitionIn.model_fields)
        incoming = DefinitionIn(**{k: v for k, v in payload.items() if k in known})
        saved = store.save(incoming, overwrite=overwrite)
        print(f"saved {saved.name} v{saved.version}")
    except InvalidDefinition as exc:
        raise click.ClickException(exc.message) from exc
    finally:
        conn.close()


@defs.command("rm")
@click.argument("name")
def defs_rm(name: str) -> None:
    """Delete a definition, keeping its history."""
    from chalktalk.definitions.spec import InvalidDefinition

    store, conn = _open_store()
    try:
        store.delete(name)
        print(f"removed {name} (previous version kept in .history)")
    except InvalidDefinition as exc:
        raise click.ClickException(exc.message) from exc
    finally:
        conn.close()


@defs.command("export")
@click.argument("directory", type=click.Path(file_okay=False, path_type=Path))
def defs_export(directory: Path) -> None:
    """Write every definition to a directory."""
    store, conn = _open_store()
    try:
        print(f"exported {store.export(directory)} definitions to {directory}")
    finally:
        conn.close()


@defs.command("import")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--overwrite", is_flag=True, help="Replace definitions that already exist.")
def defs_import(path: Path, overwrite: bool) -> None:
    """Read definitions from a file or directory."""
    store, conn = _open_store()
    try:
        report = store.import_(path, overwrite=overwrite)
        print(
            f"added {len(report.added)}, skipped {len(report.skipped)}, failed {len(report.failed)}"
        )
        for name, why in sorted(report.failed.items()):
            print(f"  {name}: {why}")
    finally:
        conn.close()


@defs.command("propose")
@click.argument("term")
@click.option("--context", help="The rest of the question, for better suggestions.")
@click.option("--entity", help="Restrict to one entity.")
def defs_propose(term: str, context: str | None, entity: str | None) -> None:
    """Show what a fuzzy word could mean, grounded in what is computable."""
    from chalktalk.definitions.propose import propose

    store, conn = _open_store()
    try:
        proposal = propose(term, context, entity, store=store)
        if proposal.matches:
            print("you already have:")
            for match in proposal.matches:
                print(f"  {match.name}  {match.description}")
        for note in proposal.notes:
            print(f"\nnote: {note}")
        for why in proposal.not_computable:
            print(f"\nnot computable: {why}")
        if proposal.suggestions:
            print("\nsuggestions:")
            for suggestion in proposal.suggestions:
                span = suggestion.coverage
                covered = f"  [{span.first}-{span.last}]" if span else ""
                print(f"  {suggestion.definition.name}: {suggestion.explanation}{covered}")
        if proposal.related_attributes:
            print("\nrelated attributes:")
            for attribute in proposal.related_attributes[:10]:
                print(
                    f"  {attribute.entity}.{attribute.name} ({attribute.type})"
                    f" — {attribute.description[:70]}"
                )
    finally:
        conn.close()


@main.command()
def logs() -> None:
    """Show the audit log."""
    _not_implemented("logs")


@main.command()
def doctor() -> None:
    """Print resolved paths, database status and dependency versions."""
    s = Settings.load()
    current = read_current(s)

    print("chalktalk", __version__)
    print()
    print("paths")
    print(f"  home           {s.home}")
    print(f"  data           {data_dir(s)}")
    print(f"  definitions    {definitions_dir(s)}")
    print(f"  history        {history_dir(s)}")
    print(f"  logs           {logs_dir(s)}")
    print(f"  cache          {cache_dir(s)}")
    print(f"  CURRENT        {current_pointer(s)}")
    print()
    print("database")
    if current is None:
        print("  no database — CURRENT is missing or names a file that isn't there")
        print("  run: chalktalk build")
    else:
        print(f"  {current} ({current.stat().st_size / 1e9:.2f} GB)")
    print()
    print("versions")
    print(f"  python         {platform.python_version()}")
    for dist in ("duckdb", "nflreadpy", "polars", "pyarrow", "mcp"):
        print(f"  {dist:<14} {_dep_version(dist)}")
