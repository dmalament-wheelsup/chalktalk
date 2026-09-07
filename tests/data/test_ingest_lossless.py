"""Ingest is lossless: every column and row upstream sent survives into DuckDB.

CLAUDE.md's warning about vendoring someone else's ingest is the reason this
exists — a dropped column or a number coerced to text would be inherited
silently and only surface months later as a definition that quietly means
something else.

Data tier: needs a build under CHALKTALK_HOME.
"""

from __future__ import annotations

import duckdb
import polars as pl
import pytest

from chalktalk.config import Settings
from chalktalk.db import open_ro, read_current
from chalktalk.ingest import loaders
from chalktalk.ingest.normalize import normalize
from chalktalk.ingest.registry import BY_ID, DATASETS, DatasetDef

pytestmark = pytest.mark.data

# One recent season for every seasonal dataset, plus the seasons where upstream
# is known to have changed schema (see the phase 2 amendment in 00-index.md).
CASES = [(d.dataset_id, 2023) for d in DATASETS if d.by_season] + [
    ("participation", 2016),
    ("injuries", 2025),
    ("depth_charts", 2025),
]

# polars dtypes that must never land in a VARCHAR column.
SCALAR_DTYPES = (
    pl.Int8,
    pl.Int16,
    pl.Int32,
    pl.Int64,
    pl.UInt8,
    pl.UInt16,
    pl.UInt32,
    pl.UInt64,
    pl.Float32,
    pl.Float64,
    pl.Boolean,
    pl.Date,
    pl.Datetime,
    pl.Time,
)


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings.load()


@pytest.fixture(scope="module")
def conn(settings: Settings) -> duckdb.DuckDBPyConnection:
    artifact = read_current(settings)
    if artifact is None:
        pytest.skip(f"no database under {settings.home}; run `chalktalk build`")
    loaders.configure_cache(settings)
    connection = open_ro(artifact, settings)
    yield connection
    connection.close()


def _stored_types(conn: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    return {row[0]: row[1] for row in conn.execute(f'DESCRIBE "{table}"').fetchall()}


def _reload(d: DatasetDef, season: int | None) -> pl.DataFrame:
    return normalize(d.dataset_id, loaders.load(d, season))


@pytest.mark.parametrize(("dataset_id", "season"), CASES)
def test_no_column_is_dropped(
    conn: duckdb.DuckDBPyConnection, dataset_id: str, season: int
) -> None:
    d = BY_ID[dataset_id]
    df = _reload(d, season)
    stored = set(_stored_types(conn, d.table))
    missing = sorted(set(df.columns) - stored)
    assert not missing, f"{d.table} is missing {missing} that {dataset_id} {season} returned"


@pytest.mark.parametrize(("dataset_id", "season"), CASES)
def test_row_count_matches_upstream(
    conn: duckdb.DuckDBPyConnection, dataset_id: str, season: int
) -> None:
    d = BY_ID[dataset_id]
    df = _reload(d, season)
    stored = conn.execute(
        f'SELECT count(*) FROM "{d.table}" WHERE season = ?', [season]
    ).fetchone()[0]
    assert stored == len(df), f"{d.table} {season}: stored {stored}, upstream {len(df)}"


def _recorded_conflicts(conn: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    """Columns where the build noticed upstream disagreeing with itself across seasons."""
    return {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT column_name FROM type_conflicts WHERE table_name = ?", [table]
        ).fetchall()
    }


@pytest.mark.parametrize(("dataset_id", "season"), CASES)
def test_scalar_columns_are_not_stored_as_text(
    conn: duckdb.DuckDBPyConnection, dataset_id: str, season: int
) -> None:
    """A number must not silently become text.

    The one legitimate exception is a column upstream itself sends both ways —
    rosters_weekly.jersey_number is text through 2018 and an integer after —
    where VARCHAR is the union type. Those are recorded in ``type_conflicts`` by
    the build; anything else is this project's bug.
    """
    d = BY_ID[dataset_id]
    df = _reload(d, season)
    types = _stored_types(conn, d.table)
    known = _recorded_conflicts(conn, d.table)
    coerced = [
        f"{c} ({dtype} -> VARCHAR)"
        for c, dtype in df.schema.items()
        if dtype in SCALAR_DTYPES and types.get(c) == "VARCHAR" and c not in known
    ]
    assert not coerced, f"{d.table} {season}: unrecorded coercion to text: {coerced}"


def test_every_type_conflict_is_a_real_upstream_disagreement(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """The escape hatch above must not become a way to hide a bug.

    Every recorded conflict names two different types, so it can only have come
    from upstream sending both.
    """
    rows = conn.execute(
        "SELECT table_name, column_name, stored_type, incoming_type FROM type_conflicts"
    ).fetchall()
    for table, column, stored, incoming in rows:
        assert stored != incoming, f"{table}.{column}: recorded a conflict with itself"


@pytest.mark.parametrize("dataset_id", [d.dataset_id for d in DATASETS if not d.by_season])
def test_replace_datasets_are_stored_whole(
    conn: duckdb.DuckDBPyConnection, settings: Settings, dataset_id: str
) -> None:
    d = BY_ID[dataset_id]
    df = _reload(d, None)
    if d.season_filter:
        df = df.filter(pl.col("season") >= settings.season_floor)
    stored = conn.execute(f'SELECT count(*) FROM "{d.table}"').fetchone()[0]
    assert stored == len(df)
    assert not sorted(set(df.columns) - set(_stored_types(conn, d.table)))


def test_contracts_keeps_its_nested_columns(conn: duckdb.DuckDBPyConnection) -> None:
    """Two List(Struct) columns; a polars-dtype mapping would flatten them to VARCHAR (D7)."""
    types = _stored_types(conn, "contracts")
    nested = [c for c, t in types.items() if t.startswith("STRUCT") or "[]" in t]
    assert nested, f"contracts stored no nested column; types were {sorted(set(types.values()))}"


def test_participation_joins_pbp_on_game_and_play(conn: duckdb.DuckDBPyConnection) -> None:
    """The derived season/week and the BIGINT play_id have to line up with pbp."""
    matched = conn.execute(
        """
        SELECT count(*)
        FROM participation p
        JOIN pbp b ON b.game_id = p.nflverse_game_id AND b.play_id = p.play_id
        WHERE p.season = 2023
        """
    ).fetchone()[0]
    total = conn.execute("SELECT count(*) FROM participation WHERE season = 2023").fetchone()[0]
    assert matched == total, f"{total - matched} of {total} participation rows did not join pbp"


def test_injury_practice_status_has_no_whitespace_junk(conn: duckdb.DuckDBPyConnection) -> None:
    junk = conn.execute(
        "SELECT count(*) FROM injuries "
        "WHERE practice_status IS NOT NULL AND trim(practice_status) = ''"
    ).fetchone()[0]
    assert junk == 0


def test_snap_counts_totals_are_integers(conn: duckdb.DuckDBPyConnection) -> None:
    types = _stored_types(conn, "snap_counts")
    assert types["offense_snaps"] == "INTEGER"
    assert types["defense_snaps"] == "INTEGER"
    assert types["st_snaps"] == "INTEGER"


@pytest.mark.parametrize("dataset_id", [d.dataset_id for d in DATASETS if d.by_season])
def test_every_by_season_row_carries_its_season(
    conn: duckdb.DuckDBPyConnection, dataset_id: str
) -> None:
    """Without a season the per-season DELETE matches nothing and a rebuild duplicates.

    depth_charts lost its season column upstream in 2025; the build stamps one.
    """
    table = BY_ID[dataset_id].table
    orphans = conn.execute(f'SELECT count(*) FROM "{table}" WHERE season IS NULL').fetchone()[0]
    assert orphans == 0, f"{table}: {orphans} rows with no season"


def test_snap_counts_has_no_prior_season_for_the_floor(conn: duckdb.DuckDBPyConnection) -> None:
    """D20 asks for season_floor - 1; nflverse publishes no snap counts before 2013.

    Recorded so phase 4 does not expect a prior-season snap baseline for the
    floor season. The coverage registry is the runtime authority.
    """
    first = conn.execute("SELECT min(season) FROM snap_counts").fetchone()[0]
    assert first == 2013
