"""CURRENT pointer round-trips and the read-only connection sandbox."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from chalktalk import db, paths
from chalktalk.config import Settings


def _make_artifact(s: Settings, built: date = date(2026, 9, 7)) -> Path:
    path = paths.artifact_path(s, built)
    conn = db.open_rw(path, s)
    conn.execute("CREATE TABLE t AS SELECT 1 AS n")
    conn.close()
    return path


def test_read_current_is_none_without_pointer(tmp_settings: Settings) -> None:
    assert db.read_current(tmp_settings) is None


def test_write_then_read_current(tmp_settings: Settings) -> None:
    artifact = _make_artifact(tmp_settings)
    db.write_current(tmp_settings, artifact)
    assert db.read_current(tmp_settings) == artifact


def test_current_records_a_bare_filename(tmp_settings: Settings) -> None:
    artifact = _make_artifact(tmp_settings)
    db.write_current(tmp_settings, artifact)
    assert paths.current_pointer(tmp_settings).read_text().strip() == "nfl-20260907.duckdb"


def test_current_survives_a_relocated_home(
    tmp_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _make_artifact(tmp_settings)
    db.write_current(tmp_settings, artifact)
    moved = tmp_path / "moved-home"
    tmp_settings.home.rename(moved)
    monkeypatch.setenv("CHALKTALK_HOME", str(moved))
    relocated = Settings.load()
    assert db.read_current(relocated) == paths.data_dir(relocated) / artifact.name


def test_write_current_leaves_no_temp_file(tmp_settings: Settings) -> None:
    db.write_current(tmp_settings, _make_artifact(tmp_settings))
    pointer = paths.current_pointer(tmp_settings)
    assert not pointer.with_name(pointer.name + ".tmp").exists()


def test_write_current_overwrites(tmp_settings: Settings) -> None:
    first = _make_artifact(tmp_settings, date(2026, 9, 1))
    second = _make_artifact(tmp_settings, date(2026, 9, 8))
    db.write_current(tmp_settings, first)
    db.write_current(tmp_settings, second)
    assert db.read_current(tmp_settings) == second


def test_read_current_is_none_when_target_is_missing(tmp_settings: Settings) -> None:
    artifact = _make_artifact(tmp_settings)
    db.write_current(tmp_settings, artifact)
    artifact.unlink()
    assert db.read_current(tmp_settings) is None


def test_read_current_is_none_when_pointer_is_empty(tmp_settings: Settings) -> None:
    paths.current_pointer(tmp_settings).write_text("\n")
    assert db.read_current(tmp_settings) is None


def test_current_accepts_an_absolute_path(tmp_settings: Settings, tmp_path: Path) -> None:
    outside = tmp_path / "outside.duckdb"
    db.open_rw(outside, tmp_settings).close()
    db.write_current(tmp_settings, outside)
    assert paths.current_pointer(tmp_settings).read_text().strip() == str(outside.resolve())
    assert db.read_current(tmp_settings) == outside


def test_open_ro_reads(tmp_settings: Settings) -> None:
    artifact = _make_artifact(tmp_settings)
    conn = db.open_ro(artifact, tmp_settings)
    assert conn.execute("SELECT n FROM t").fetchone() == (1,)
    conn.close()


def test_open_ro_rejects_writes(tmp_settings: Settings) -> None:
    conn = db.open_ro(_make_artifact(tmp_settings), tmp_settings)
    with pytest.raises(duckdb.Error):
        conn.execute("CREATE TABLE u AS SELECT 2 AS n")
    conn.close()


def test_open_ro_blocks_external_reads(tmp_settings: Settings) -> None:
    conn = db.open_ro(_make_artifact(tmp_settings), tmp_settings)
    with pytest.raises(duckdb.Error):
        conn.execute("SELECT * FROM read_csv('/etc/hosts')")
    conn.close()


def test_open_ro_locks_the_sandbox_open(tmp_settings: Settings) -> None:
    conn = db.open_ro(_make_artifact(tmp_settings), tmp_settings)
    with pytest.raises(duckdb.Error):
        conn.execute("SET enable_external_access=true")
    conn.close()


def test_resource_limits_are_applied(
    tmp_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _make_artifact(tmp_settings)
    monkeypatch.setenv("CHALKTALK_HOME", str(tmp_settings.home))
    monkeypatch.setenv("CHALKTALK_DUCKDB_THREADS", "2")
    limited = Settings.load()
    conn = db.open_ro(artifact, limited)
    assert conn.execute("SELECT current_setting('threads')").fetchone() == (2,)
    conn.close()


def test_bad_memory_limit_is_rejected(
    tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _make_artifact(tmp_settings)
    monkeypatch.setenv("CHALKTALK_HOME", str(tmp_settings.home))
    monkeypatch.setenv("CHALKTALK_DUCKDB_MEMORY_LIMIT", "6GB'; SET threads=1; --")
    with pytest.raises(ValueError):
        db.open_ro(artifact, Settings.load())
