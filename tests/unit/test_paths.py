"""Path resolution: env overrides, defaults, and the artifact naming contract."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from chalktalk import paths
from chalktalk.config import DEFAULT_HOME, Settings


def test_home_defaults_to_user_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHALKTALK_HOME", raising=False)
    assert Settings.load().home == DEFAULT_HOME.expanduser()


def test_home_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHALKTALK_HOME", str(tmp_path / "elsewhere"))
    assert Settings.load().home == tmp_path / "elsewhere"


def test_home_env_override_expands_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHALKTALK_HOME", "~/somewhere")
    assert Settings.load().home == Path("~/somewhere").expanduser()


def test_blank_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHALKTALK_HOME", "   ")
    assert Settings.load().home == DEFAULT_HOME.expanduser()


def test_scalar_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHALKTALK_SEASON_FLOOR", "2016")
    monkeypatch.setenv("CHALKTALK_RAW_SQL_ROW_CAP", "25")
    monkeypatch.setenv("CHALKTALK_RAW_SQL_TIMEOUT", "2.5")
    monkeypatch.setenv("CHALKTALK_DUCKDB_MEMORY_LIMIT", "6GB")
    monkeypatch.setenv("CHALKTALK_DUCKDB_THREADS", "3")
    s = Settings.load()
    assert (s.season_floor, s.raw_sql_row_cap, s.raw_sql_timeout_s) == (2016, 25, 2.5)
    assert (s.duckdb_memory_limit, s.duckdb_threads) == ("6GB", 3)


def test_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings.load()
    assert s.season_floor == 2013
    assert s.baseline_lookback == 1
    assert s.pctile_default_min_share == 0.5
    assert s.duckdb_memory_limit is None
    assert s.duckdb_threads is None


def test_every_path_lives_under_home(tmp_settings: Settings) -> None:
    home = tmp_settings.home
    for path in (
        paths.data_dir(tmp_settings),
        paths.definitions_dir(tmp_settings),
        paths.history_dir(tmp_settings),
        paths.logs_dir(tmp_settings),
        paths.cache_dir(tmp_settings),
        paths.current_pointer(tmp_settings),
        paths.audit_log(tmp_settings),
        paths.artifact_path(tmp_settings, date(2026, 9, 7)),
    ):
        assert home in path.parents


def test_layout_names(tmp_settings: Settings) -> None:
    s = tmp_settings
    assert paths.data_dir(s) == s.home / "data"
    assert paths.definitions_dir(s) == s.home / "definitions"
    assert paths.history_dir(s) == s.home / "definitions" / ".history"
    assert paths.logs_dir(s) == s.home / "logs"
    assert paths.cache_dir(s) == s.home / "cache"
    assert paths.current_pointer(s) == s.home / "data" / "CURRENT"
    assert paths.audit_log(s) == s.home / "logs" / "audit.jsonl"


def test_artifact_path_is_dated(tmp_settings: Settings) -> None:
    got = paths.artifact_path(tmp_settings, date(2026, 1, 3))
    assert got.name == "nfl-20260103.duckdb"
    assert got.parent == paths.data_dir(tmp_settings)


def test_ensure_layout_creates_dirs_and_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHALKTALK_HOME", str(tmp_path / "fresh"))
    s = Settings.load()
    paths.ensure_layout(s)
    for d in (
        paths.data_dir(s),
        paths.definitions_dir(s),
        paths.history_dir(s),
        paths.logs_dir(s),
        paths.cache_dir(s),
    ):
        assert d.is_dir()
    assert not paths.current_pointer(s).exists()
    assert [p for p in s.home.rglob("*") if p.is_file()] == []


def test_ensure_layout_is_idempotent(tmp_settings: Settings) -> None:
    paths.ensure_layout(tmp_settings)
    paths.ensure_layout(tmp_settings)
    assert paths.definitions_dir(tmp_settings).is_dir()
