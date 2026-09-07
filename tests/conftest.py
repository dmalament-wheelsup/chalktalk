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
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test inherits chalktalk configuration from the developer's shell."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


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
