"""Shared fixtures.

The mini in-memory database (phase 4) lands here too; for now this only isolates
``CHALKTALK_HOME`` so no test can read or write the developer's real home.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from chalktalk.config import Settings
from chalktalk.paths import ensure_layout

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
