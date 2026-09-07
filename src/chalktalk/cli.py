"""The ``chalktalk`` command line.

Every subcommand except ``doctor`` is a stub until its phase lands; stubs exit 2
so a script that calls one before it exists fails loudly.
"""

from __future__ import annotations

import platform
import sys
from importlib import metadata

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
    "coverage": 3,
    "defs": 5,
    "logs": 8,
}


def _not_implemented(command: str) -> None:
    print(f"not implemented (phase {_STUB_PHASE[command]})", file=sys.stderr)
    raise SystemExit(2)


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
def coverage() -> None:
    """Show the coverage registry."""
    _not_implemented("coverage")


@main.command()
def defs() -> None:
    """Manage the definitions store."""
    _not_implemented("defs")


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
