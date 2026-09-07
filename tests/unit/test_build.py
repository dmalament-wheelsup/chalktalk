"""Build mechanics with no network: storage, idempotency, naming, retention.

The loader is stubbed, so these run in the unit tier. What they cover is the
part that goes wrong quietly — a re-ingest that duplicates rows, a type that
changes shape between seasons, an artifact that overwrites yesterday's.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import pytest

from chalktalk.config import Settings
from chalktalk.db import read_current
from chalktalk.ingest import build as build_mod
from chalktalk.ingest import loaders
from chalktalk.ingest.registry import DatasetDef
from chalktalk.paths import artifact_path, data_dir


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(build_mod._INGEST_LOG_DDL)
    connection.execute(build_mod._TYPE_CONFLICTS_DDL)
    yield connection
    connection.close()


SEASONAL = DatasetDef("demo", "load_demo", "demo", "by_season")


def _store(conn: duckdb.DuckDBPyConnection, df: pl.DataFrame, season: int) -> list[str]:
    warnings: list[str] = []
    build_mod._store(conn, SEASONAL, df, season, warnings)
    return warnings


def _rows(conn: duckdb.DuckDBPyConnection, table: str = "demo") -> int:
    return conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


def test_first_store_creates_the_table(conn: duckdb.DuckDBPyConnection) -> None:
    _store(conn, pl.DataFrame({"season": [2023] * 3, "n": [1, 2, 3]}), 2023)
    assert _rows(conn) == 3


def test_reingesting_a_season_replaces_it(conn: duckdb.DuckDBPyConnection) -> None:
    df = pl.DataFrame({"season": [2023] * 3, "n": [1, 2, 3]})
    _store(conn, df, 2023)
    _store(conn, df, 2023)
    assert _rows(conn) == 3


def test_reingesting_one_season_leaves_the_others(conn: duckdb.DuckDBPyConnection) -> None:
    _store(conn, pl.DataFrame({"season": [2022] * 2, "n": [1, 2]}), 2022)
    _store(conn, pl.DataFrame({"season": [2023] * 3, "n": [1, 2, 3]}), 2023)
    _store(conn, pl.DataFrame({"season": [2023] * 5, "n": [1, 2, 3, 4, 5]}), 2023)
    assert _rows(conn) == 7
    assert conn.execute("SELECT count(*) FROM demo WHERE season = 2022").fetchone()[0] == 2


def test_schema_drift_adds_columns_and_warns(conn: duckdb.DuckDBPyConnection) -> None:
    _store(conn, pl.DataFrame({"season": [2023], "n": [1]}), 2023)
    warnings = _store(conn, pl.DataFrame({"season": [2024], "n": [1], "extra": ["x"]}), 2024)
    assert any("schema drift added extra" in w for w in warnings)
    assert conn.execute("SELECT extra FROM demo WHERE season = 2023").fetchone() == (None,)


def test_drift_keeps_nested_types(conn: duckdb.DuckDBPyConnection) -> None:
    """D7: a polars-dtype mapping would send a list column to VARCHAR."""
    _store(conn, pl.DataFrame({"season": [2023], "n": [1]}), 2023)
    _store(conn, pl.DataFrame({"season": [2024], "n": [1], "years": [[1, 2]]}), 2024)
    stored = {r[0]: r[1] for r in conn.execute("DESCRIBE demo").fetchall()}
    assert stored["years"] != "VARCHAR"


def test_a_type_conflict_is_recorded(conn: duckdb.DuckDBPyConnection) -> None:
    _store(conn, pl.DataFrame({"season": [2023], "jersey": ["12"]}), 2023)
    warnings = _store(conn, pl.DataFrame({"season": [2024], "jersey": [12]}), 2024)
    recorded = conn.execute(
        "SELECT column_name, stored_type, incoming_type FROM type_conflicts"
    ).fetchall()
    assert recorded == [("jersey", "VARCHAR", "BIGINT")]
    assert any("jersey" in w for w in warnings)


def test_a_repeated_conflict_warns_once(conn: duckdb.DuckDBPyConnection) -> None:
    _store(conn, pl.DataFrame({"season": [2023], "jersey": ["12"]}), 2023)
    first = _store(conn, pl.DataFrame({"season": [2024], "jersey": [12]}), 2024)
    second = _store(conn, pl.DataFrame({"season": [2025], "jersey": [12]}), 2025)
    assert first and not second
    assert conn.execute("SELECT count(*) FROM type_conflicts").fetchone()[0] == 2


def test_matching_types_record_nothing(conn: duckdb.DuckDBPyConnection) -> None:
    _store(conn, pl.DataFrame({"season": [2023], "n": [1]}), 2023)
    _store(conn, pl.DataFrame({"season": [2024], "n": [2]}), 2024)
    assert conn.execute("SELECT count(*) FROM type_conflicts").fetchone()[0] == 0


def test_a_season_column_is_stamped_when_upstream_omits_it(
    conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """depth_charts 2025 ships no season column; without one, re-ingest duplicates."""
    frame = pl.DataFrame({"player": ["a", "b"]})
    monkeypatch.setattr(loaders, "load", lambda d, season: frame)
    settings = Settings.load()
    warnings: list[str] = []

    build_mod._ingest_one(conn, SEASONAL, 2025, settings, warnings)
    build_mod._ingest_one(conn, SEASONAL, 2025, settings, warnings)

    assert _rows(conn) == 2, "a season-less frame was ingested twice"
    assert conn.execute("SELECT count(*) FROM demo WHERE season = 2025").fetchone()[0] == 2
    assert any("stamped 2025" in w for w in warnings)


def test_an_unpublished_season_warns_and_continues(
    conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(d: DatasetDef, season: int | None) -> pl.DataFrame:
        raise loaders.NotPublished("no release yet")

    monkeypatch.setattr(loaders, "load", refuse)
    warnings: list[str] = []
    rows = build_mod._ingest_one(conn, SEASONAL, 2026, Settings.load(), warnings)
    assert rows == 0
    assert any("not published" in w for w in warnings)


def test_a_real_load_failure_aborts(
    conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(d: DatasetDef, season: int | None) -> pl.DataFrame:
        raise RuntimeError("upstream is on fire")

    monkeypatch.setattr(loaders, "load", explode)
    with pytest.raises(RuntimeError, match="on fire"):
        build_mod._ingest_one(conn, SEASONAL, 2023, Settings.load(), [])


def test_ingest_log_records_one_row_per_season(
    conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loaders, "load", lambda d, season: pl.DataFrame({"season": [season]}))
    settings = Settings.load()
    for season in (2023, 2024, 2023):
        build_mod._ingest_one(conn, SEASONAL, season, settings, [])
    logged = conn.execute("SELECT season, row_count FROM ingest_log ORDER BY season").fetchall()
    assert logged == [(2023, 1), (2024, 1)]


def test_parse_season_range() -> None:
    assert build_mod.parse_season_range("2023") == range(2023, 2024)
    assert build_mod.parse_season_range("2013-2025") == range(2013, 2026)


@pytest.mark.parametrize("text", ["", "23", "2013-2012", "2013–2025", "last year"])
def test_bad_season_ranges_are_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        build_mod.parse_season_range(text)


def test_unknown_dataset_is_named(tmp_settings: Settings) -> None:
    with pytest.raises(ValueError, match="nonesuch"):
        build_mod.plan_lines(tmp_settings, only={"nonesuch"}, current=2025)


def test_plan_lists_only_what_was_asked_for(tmp_settings: Settings) -> None:
    lines = build_mod.plan_lines(tmp_settings, only={"pbp"}, current=2025)
    assert len(lines) == 2
    assert "pbp" in lines[1]


def test_plan_honours_a_season_window(tmp_settings: Settings) -> None:
    lines = build_mod.plan_lines(
        tmp_settings, only={"pbp"}, seasons=range(2020, 2023), current=2025
    )
    assert "2020-2022 (3 seasons)" in lines[1]


def test_artifact_names_do_not_collide(tmp_settings: Settings) -> None:
    first = artifact_path(tmp_settings, date(2026, 9, 7))
    first.touch()
    second = build_mod._next_free_artifact(first)
    assert second.name == "nfl-20260907-2.duckdb"
    second.touch()
    assert build_mod._next_free_artifact(first).name == "nfl-20260907-3.duckdb"


def test_pruning_keeps_the_newest(tmp_settings: Settings) -> None:
    made = []
    for day in range(1, 6):
        path = artifact_path(tmp_settings, date(2026, 9, day))
        path.touch()
        import os

        os.utime(path, (day * 1000, day * 1000))
        made.append(path)
    removed = build_mod._prune(tmp_settings, keep=3, protect=made[-1])
    assert sorted(removed) == ["nfl-20260901.duckdb", "nfl-20260902.duckdb"]
    assert len(list(data_dir(tmp_settings).glob("nfl-*.duckdb"))) == 3


def test_pruning_never_deletes_the_published_artifact(tmp_settings: Settings) -> None:
    import os

    paths = []
    for day in range(1, 4):
        path = artifact_path(tmp_settings, date(2026, 9, day))
        path.touch()
        os.utime(path, (day * 1000, day * 1000))
        paths.append(path)
    oldest = paths[0]
    build_mod._prune(tmp_settings, keep=1, protect=oldest)
    assert oldest.exists()


def test_a_failed_build_leaves_the_partial_file_and_does_not_publish(
    tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(d: DatasetDef, season: int | None) -> pl.DataFrame:
        raise RuntimeError("upstream is on fire")

    monkeypatch.setattr(loaders, "load", explode)
    monkeypatch.setattr(loaders, "configure_cache", lambda s: None)
    monkeypatch.setattr(loaders, "current_season", lambda: 2025)

    with pytest.raises(RuntimeError):
        build_mod.build(tmp_settings, only={"teams"}, built=date(2026, 9, 7))

    building = Path(str(artifact_path(tmp_settings, date(2026, 9, 7))) + ".building")
    assert building.exists(), "the partial database should be left for inspection"
    assert read_current(tmp_settings) is None


def test_a_successful_build_publishes(
    tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loaders, "load", lambda d, season: pl.DataFrame({"team": ["AAA"]}))
    monkeypatch.setattr(loaders, "configure_cache", lambda s: None)
    monkeypatch.setattr(loaders, "current_season", lambda: 2025)

    result = build_mod.build(tmp_settings, only={"teams"}, built=date(2026, 9, 7))

    assert result.artifact.name == "nfl-20260907.duckdb"
    assert result.tables == {"teams": 1}
    assert read_current(tmp_settings) == result.artifact
    assert not Path(str(result.artifact) + ".building").exists()


def test_build_info_is_written(tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        loaders, "load", lambda d, season: pl.DataFrame({"season": [2023], "n": [1]})
    )
    monkeypatch.setattr(loaders, "configure_cache", lambda s: None)
    monkeypatch.setattr(loaders, "current_season", lambda: 2023)

    result = build_mod.build(
        tmp_settings, only={"pbp"}, seasons=range(2023, 2024), built=date(2026, 9, 7)
    )
    conn = duckdb.connect(str(result.artifact), read_only=True)
    row = conn.execute(
        "SELECT chalktalk_version, season_floor, first_season, last_season FROM build_info"
    ).fetchone()
    conn.close()
    assert row == ("0.1.0", 2013, 2023, 2023)
