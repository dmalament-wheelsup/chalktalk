"""Build orchestration: download, normalize, store, publish.

The register-Arrow-then-``INSERT ... BY NAME`` mechanics and the schema-drift
reconciliation are adapted from ``nfl_mcp/ingest.py`` in ebhattad/nfl-mcp:

    MIT License
    Copyright (c) 2026 ebhattad

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

Two departures from theirs (D7): drift column types are derived from what DuckDB
reports for the incoming Arrow table rather than mapped from polars dtypes — a
mapping that sends every non-scalar to VARCHAR — and a season that fails to
download for any reason other than "not published yet" aborts the build instead
of being skipped with a printed warning.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import nflreadpy
import polars as pl

from chalktalk import __version__, coverage, features
from chalktalk.config import Settings
from chalktalk.db import open_rw, write_current
from chalktalk.ingest import loaders
from chalktalk.ingest.normalize import normalize
from chalktalk.ingest.registry import DATASETS, DatasetDef, seasons_for
from chalktalk.paths import artifact_path, data_dir, ensure_layout

log = logging.getLogger(__name__)

BUILDING_SUFFIX = ".building"
ARTIFACT_GLOB = "nfl-*.duckdb"
DEFAULT_KEEP = 3

_INGEST_LOG_DDL = """
CREATE TABLE ingest_log (
    dataset_id        VARCHAR NOT NULL,
    table_name        VARCHAR NOT NULL,
    season            INTEGER,
    row_count         BIGINT,
    loaded_at         TIMESTAMP NOT NULL,
    loader_fn         VARCHAR,
    nflreadpy_version VARCHAR
)
"""

_TYPE_CONFLICTS_DDL = """
CREATE TABLE type_conflicts (
    dataset_id    VARCHAR NOT NULL,
    table_name    VARCHAR NOT NULL,
    season        INTEGER,
    column_name   VARCHAR NOT NULL,
    stored_type   VARCHAR NOT NULL,
    incoming_type VARCHAR NOT NULL
)
"""

_BUILD_INFO_DDL = """
CREATE TABLE build_info (
    built_at          TIMESTAMP NOT NULL,
    chalktalk_version VARCHAR,
    nflreadpy_version VARCHAR,
    duckdb_version    VARCHAR,
    polars_version    VARCHAR,
    season_floor      INTEGER,
    first_season      INTEGER,
    last_season       INTEGER,
    git_sha           VARCHAR,
    duration_s        DOUBLE
)
"""


@dataclass
class BuildResult:
    artifact: Path
    tables: dict[str, int]  # table → rows
    warnings: list[str] = field(default_factory=list)
    duration_s: float = 0.0


def _git_sha() -> str | None:
    """The commit this build was made from, when the source is a git checkout."""
    repo = Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else None


def _selected(only: set[str] | None) -> list[DatasetDef]:
    if only is None:
        return list(DATASETS)
    known = {d.dataset_id for d in DATASETS}
    unknown = sorted(only - known)
    if unknown:
        raise ValueError(
            f"unknown dataset(s): {', '.join(unknown)}; known: {', '.join(sorted(known))}"
        )
    return [d for d in DATASETS if d.dataset_id in only]


def _restrict(seasons: list[int], window: range | None) -> list[int]:
    if window is None:
        return seasons
    return [s for s in seasons if s in window]


def plan_lines(
    settings: Settings,
    *,
    seasons: range | None = None,
    only: set[str] | None = None,
    current: int | None = None,
) -> list[str]:
    """What a build would load, without loading anything."""
    current = current if current is not None else loaders.current_season()
    lines = [f"current season {current}; season floor {settings.season_floor}"]
    for d in _selected(only):
        if d.by_season:
            wanted = _restrict(seasons_for(d, settings, current), seasons)
            span = f"{wanted[0]}-{wanted[-1]} ({len(wanted)} seasons)" if wanted else "none"
        else:
            span = "whole file" + (" (filtered to the season floor)" if d.season_filter else "")
        lines.append(f"  {d.dataset_id:<16} -> {d.table:<16} {d.storage:<10} {span}")
    return lines


def _table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()
    )


def _reconcile_schema(conn: duckdb.DuckDBPyConnection, table: str, view: str) -> list[str]:
    """Add columns the incoming frame has and the table does not.

    Types come from what DuckDB reports for the registered Arrow view, so a
    struct or list column keeps its real type instead of collapsing to VARCHAR.
    """
    existing = {row[0] for row in conn.execute(f'DESCRIBE "{table}"').fetchall()}
    added = []
    for row in conn.execute(f"DESCRIBE {view}").fetchall():
        name, dtype = row[0], row[1]
        if name in existing:
            continue
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {dtype}')
        added.append(f"{name} {dtype}")
    return added


def _record_type_conflicts(
    conn: duckdb.DuckDBPyConnection,
    d: DatasetDef,
    season: int | None,
    view: str,
    warnings: list[str],
) -> None:
    """Record columns whose incoming type disagrees with the stored one.

    Upstream is not self-consistent across seasons — ``rosters_weekly``
    jersey_number is text through 2018 and an integer from 2019 — and
    ``INSERT ... BY NAME`` casts to the stored type without complaint. Whichever
    season is ingested first would otherwise decide the type silently. The
    conflict is written into the artifact so the coverage registry and the
    feature layer can see it instead of inferring it.
    """
    stored = {row[0]: row[1] for row in conn.execute(f'DESCRIBE "{d.table}"').fetchall()}
    for row in conn.execute(f"DESCRIBE {view}").fetchall():
        name, incoming = row[0], row[1]
        if stored.get(name) in (None, incoming):
            continue
        already = conn.execute(
            "SELECT 1 FROM type_conflicts WHERE table_name = ? AND column_name = ? "
            "AND stored_type = ? AND incoming_type = ?",
            [d.table, name, stored[name], incoming],
        ).fetchone()
        conn.execute(
            "INSERT INTO type_conflicts VALUES (?, ?, ?, ?, ?, ?)",
            [d.dataset_id, d.table, season, name, stored[name], incoming],
        )
        if not already:
            message = f"{d.table}.{name}: stored {stored[name]}, upstream sent {incoming}"
            warnings.append(message)
            log.warning("%s", message)


def _store(
    conn: duckdb.DuckDBPyConnection,
    d: DatasetDef,
    df: pl.DataFrame,
    season: int | None,
    warnings: list[str],
) -> None:
    conn.register("_df", df.to_arrow())
    try:
        if not _table_exists(conn, d.table):
            conn.execute(f'CREATE TABLE "{d.table}" AS SELECT * FROM _df')
        else:
            added = _reconcile_schema(conn, d.table, "_df")
            if added:
                warnings.append(f"{d.table}: schema drift added {', '.join(added)}")
                log.warning("%s: schema drift added %s", d.table, ", ".join(added))
            _record_type_conflicts(conn, d, season, "_df", warnings)
            if d.by_season and season is not None:
                conn.execute(f'DELETE FROM "{d.table}" WHERE season = ?', [season])
            conn.execute(f'INSERT INTO "{d.table}" BY NAME SELECT * FROM _df')
    finally:
        conn.unregister("_df")


def _record(conn: duckdb.DuckDBPyConnection, d: DatasetDef, season: int | None, rows: int) -> None:
    conn.execute(
        "DELETE FROM ingest_log WHERE dataset_id = ? AND season IS NOT DISTINCT FROM ?",
        [d.dataset_id, season],
    )
    conn.execute(
        "INSERT INTO ingest_log VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            d.dataset_id,
            d.table,
            season,
            rows,
            datetime.now(UTC).replace(tzinfo=None),
            d.loader_fn,
            nflreadpy.__version__,
        ],
    )


def _ingest_one(
    conn: duckdb.DuckDBPyConnection,
    d: DatasetDef,
    season: int | None,
    settings: Settings,
    warnings: list[str],
) -> int:
    """Load, normalize and store one dataset-season. Returns rows stored."""
    label = f"{d.dataset_id}" + (f" {season}" if season is not None else "")
    try:
        df = loaders.load(d, season)
    except loaders.NotPublished as exc:
        warnings.append(f"{label}: not published upstream yet, skipped")
        log.warning("%s: %s", label, exc)
        return 0

    if df is None or df.is_empty():
        warnings.append(f"{label}: upstream returned no rows")
        log.warning("%s: upstream returned no rows", label)
        return 0

    df = normalize(d.dataset_id, df)

    if d.by_season and season is not None and "season" not in df.columns:
        # depth_charts lost its season and week columns upstream in 2025. Without
        # a season the by_season DELETE matches nothing and a rebuild duplicates
        # the rows, so every by_season row carries the season it was loaded for.
        warnings.append(f"{label}: upstream sent no season column; stamped {season}")
        log.warning("%s: upstream sent no season column; stamped %s", label, season)
        df = df.with_columns(pl.lit(season, dtype=pl.Int32).alias("season"))

    if d.season_filter:
        if "season" not in df.columns:
            raise ValueError(f"{d.dataset_id}: season_filter set but there is no season column")
        df = df.filter(pl.col("season") >= settings.season_floor)

    _store(conn, d, df, season, warnings)
    _record(conn, d, season, len(df))
    log.info("%s: %s rows, %s columns", label, f"{len(df):,}", len(df.columns))
    return len(df)


def _next_free_artifact(target: Path) -> Path:
    """``nfl-YYYYMMDD.duckdb``, then ``-2``, ``-3``… if the name is taken."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for n in range(2, 100):
        candidate = target.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"too many artifacts named like {target.name}")


def _prune(settings: Settings, keep: int, protect: Path) -> list[str]:
    """Keep the newest ``keep`` artifacts. Never delete the one CURRENT names."""
    artifacts = sorted(
        data_dir(settings).glob(ARTIFACT_GLOB),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for old in artifacts[keep:]:
        if old.resolve() == protect.resolve():
            continue
        old.unlink()
        removed.append(old.name)
    return removed


def _write_build_info(
    conn: duckdb.DuckDBPyConnection, settings: Settings, duration_s: float
) -> None:
    first_season, last_season = conn.execute(
        "SELECT min(season), max(season) FROM ingest_log"
    ).fetchone()
    conn.execute(_BUILD_INFO_DDL)
    conn.execute(
        "INSERT INTO build_info VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            datetime.now(UTC).replace(tzinfo=None),
            __version__,
            nflreadpy.__version__,
            duckdb.__version__,
            pl.__version__,
            settings.season_floor,
            first_season,
            last_season,
            _git_sha(),
            duration_s,
        ],
    )


def build(
    settings: Settings,
    *,
    seasons: range | None = None,
    only: set[str] | None = None,
    skip_features: bool = False,
    publish: bool = True,
    keep: int = DEFAULT_KEEP,
    built: date | None = None,
) -> BuildResult:
    """Ingest every selected dataset into a fresh artifact and publish it.

    The database is written to ``<name>.duckdb.building`` and only renamed once
    every step has succeeded, so a failed build never becomes CURRENT and leaves
    the partial file behind for inspection.
    """
    started = time.monotonic()
    warnings: list[str] = []
    tables: dict[str, int] = {}

    ensure_layout(settings)
    loaders.configure_cache(settings)
    current = loaders.current_season()
    datasets = _selected(only)

    final = artifact_path(settings, built or date.today())
    building = final.with_name(final.name + BUILDING_SUFFIX)
    if building.exists():
        log.warning("removing a stale %s", building.name)
        building.unlink()

    conn = open_rw(building, settings)
    try:
        conn.execute(_INGEST_LOG_DDL)
        conn.execute(_TYPE_CONFLICTS_DDL)
        for d in datasets:
            if d.by_season:
                for season in _restrict(seasons_for(d, settings, current), seasons):
                    tables[d.table] = tables.get(d.table, 0) + _ingest_one(
                        conn, d, season, settings, warnings
                    )
            else:
                tables[d.table] = tables.get(d.table, 0) + _ingest_one(
                    conn, d, None, settings, warnings
                )

        # season_status describes the shape of each season and is an *input* to
        # the feature layer (game_ctx needs each season's regular-season length,
        # D24). Column coverage describes the feature layer and so runs after it.
        coverage.build_season_status(conn, settings)
        if not skip_features:
            features.build_all(conn, settings)
        coverage.build(conn, settings)

        _write_build_info(conn, settings, time.monotonic() - started)
        conn.execute("CHECKPOINT")
    except BaseException:
        conn.close()
        log.error("build failed; the partial database is at %s", building)
        raise
    conn.close()

    artifact = _next_free_artifact(final)
    building.rename(artifact)
    if publish:
        write_current(settings, artifact)
        removed = _prune(settings, keep, protect=artifact)
        if removed:
            log.info("pruned older artifacts: %s", ", ".join(removed))

    return BuildResult(
        artifact=artifact,
        tables=tables,
        warnings=warnings,
        duration_s=time.monotonic() - started,
    )


_SEASON_RANGE_RE = re.compile(r"^(\d{4})(?:-(\d{4}))?$")


def parse_season_range(text: str) -> range:
    """``2023`` or ``2013-2025`` to an inclusive range."""
    m = _SEASON_RANGE_RE.match(text.strip())
    if not m:
        raise ValueError(f"expected a season or season range like 2013-2025, got {text!r}")
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    if hi < lo:
        raise ValueError(f"season range is backwards: {text!r}")
    return range(lo, hi + 1)
