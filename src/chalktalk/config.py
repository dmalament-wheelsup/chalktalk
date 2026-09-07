"""Settings resolution.

One frozen dataclass, built once by ``Settings.load()``. Environment variable
names are given per field; anything without one is not env-configurable in v1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOME = Path("~/.chalktalk")


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return Path(raw).expanduser()


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return float(raw)


def _env_str(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


@dataclass(frozen=True)
class Settings:
    home: Path  # CHALKTALK_HOME, default ~/.chalktalk
    season_floor: int = 2013  # CHALKTALK_SEASON_FLOOR
    baseline_lookback: int = 1  # seasons of history for prior-season attributes
    pctile_default_min_share: float = 0.5  # D13/D24: share of team games for percentile eligibility
    raw_sql_row_cap: int = 500  # CHALKTALK_RAW_SQL_ROW_CAP
    raw_sql_timeout_s: float = 10.0  # CHALKTALK_RAW_SQL_TIMEOUT
    query_row_cap: int = 1000
    query_default_limit: int = 200
    query_timeout_s: float = 30.0
    sample_rows: int = 10
    duckdb_memory_limit: str | None = None  # CHALKTALK_DUCKDB_MEMORY_LIMIT, e.g. "6GB"
    duckdb_threads: int | None = None  # CHALKTALK_DUCKDB_THREADS

    @classmethod
    def load(cls) -> Settings:
        """Build settings from the environment, falling back to the defaults above."""
        kwargs: dict[str, object] = {
            "home": _env_path("CHALKTALK_HOME") or DEFAULT_HOME.expanduser()
        }
        overrides = {
            "season_floor": _env_int("CHALKTALK_SEASON_FLOOR"),
            "raw_sql_row_cap": _env_int("CHALKTALK_RAW_SQL_ROW_CAP"),
            "raw_sql_timeout_s": _env_float("CHALKTALK_RAW_SQL_TIMEOUT"),
            "duckdb_memory_limit": _env_str("CHALKTALK_DUCKDB_MEMORY_LIMIT"),
            "duckdb_threads": _env_int("CHALKTALK_DUCKDB_THREADS"),
        }
        kwargs.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**kwargs)  # type: ignore[arg-type]
