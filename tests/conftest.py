"""Shared fixtures.

Two things live here: isolation of ``CHALKTALK_HOME`` so no unit test can read
or write the developer's real home, and the mini database — a tiny invented
league run through the *production* feature builders, so that unit tests query
the same SQL a real build produces.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from chalktalk.config import Settings
from chalktalk.coverage import Coverage
from chalktalk.paths import ensure_layout

sys.path.insert(0, str(Path(__file__).parent))

_ENV_VARS = (
    "CHALKTALK_HOME",
    "CHALKTALK_SEASON_FLOOR",
    "CHALKTALK_RAW_SQL_ROW_CAP",
    "CHALKTALK_RAW_SQL_TIMEOUT",
    "CHALKTALK_DUCKDB_MEMORY_LIMIT",
    "CHALKTALK_DUCKDB_THREADS",
)


@pytest.fixture(autouse=True)
def clean_env(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolate every unit test from the developer's shell and real home.

    CHALKTALK_HOME is pointed at a throwaway directory rather than merely
    unset, so a unit test that reaches for the real ``~/.chalktalk`` — or, worse,
    runs a real build into it — finds an empty one instead. Tests in the ``data``
    tier are exempt: the whole point of that tier is the real build.
    """
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    if request.node.get_closest_marker("data") is None:
        monkeypatch.setenv("CHALKTALK_HOME", str(tmp_path_factory.mktemp("home")))


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway CHALKTALK_HOME with the directory layout created."""
    home = tmp_path / "home"
    monkeypatch.setenv("CHALKTALK_HOME", str(home))
    ensure_layout(Settings.load())
    yield home


@pytest.fixture
def tmp_settings(tmp_home: Path) -> Settings:
    return Settings.load()


def requires_database(settings: Settings) -> None:
    """Skip a ``data``-tier test when no build exists under CHALKTALK_HOME."""
    from chalktalk.db import read_current

    if read_current(settings) is None:
        pytest.skip(f"no database under {settings.home}; run `chalktalk build` (or `-m data` off)")


# ── the mini database ─────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def mini_settings() -> Settings:
    """Settings for the mini league: two seasons, so 2022 is the baseline year."""
    return replace(Settings(home=Path("/nonexistent")), season_floor=2022)


@pytest.fixture(scope="session")
def _mini_db(mini_settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    from mini import build_mini

    conn = build_mini(mini_settings)
    yield conn
    conn.close()


@pytest.fixture
def mini_conn(_mini_db: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """A cursor on the mini database, so tests cannot disturb each other."""
    return _mini_db.cursor()


@pytest.fixture
def mini_coverage(mini_conn: duckdb.DuckDBPyConnection, mini_settings: Settings) -> Coverage:
    return Coverage(mini_conn, mini_settings)


@pytest.fixture
def mini_store(mini_conn, mini_settings, tmp_path):
    """A store bound to the mini database, writing to a throwaway directory."""
    from chalktalk.definitions.context import open_store

    return open_store(mini_conn, mini_settings, directory=tmp_path / "definitions")


@pytest.fixture
def mini_ctx(mini_conn, mini_settings, mini_store):
    """A validation context factory for the mini database."""
    from chalktalk.coverage import Coverage
    from chalktalk.definitions.signals import ValidationCtx

    coverage = Coverage(mini_conn, mini_settings)

    def make(entity: str) -> ValidationCtx:
        return ValidationCtx(
            entity=entity,
            conn=mini_conn,
            coverage=coverage,
            store=mini_store,
            settings=mini_settings,
        )

    return make
