"""Path resolution under ``CHALKTALK_HOME``.

Pure functions over :class:`~chalktalk.config.Settings`. Only
:func:`ensure_layout` touches the filesystem, and it only creates directories.
Never create files here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from chalktalk.config import Settings

ARTIFACT_PREFIX = "nfl-"
ARTIFACT_SUFFIX = ".duckdb"


def data_dir(s: Settings) -> Path:
    return s.home / "data"


def definitions_dir(s: Settings) -> Path:
    return s.home / "definitions"


def history_dir(s: Settings) -> Path:
    return definitions_dir(s) / ".history"


def logs_dir(s: Settings) -> Path:
    return s.home / "logs"


def cache_dir(s: Settings) -> Path:
    return s.home / "cache"


def current_pointer(s: Settings) -> Path:
    """The text file naming the artifact the server should open."""
    return data_dir(s) / "CURRENT"


def audit_log(s: Settings) -> Path:
    return logs_dir(s) / "audit.jsonl"


def artifact_path(s: Settings, built: date) -> Path:
    """``data/nfl-YYYYMMDD.duckdb`` for a build made on ``built``."""
    return data_dir(s) / f"{ARTIFACT_PREFIX}{built:%Y%m%d}{ARTIFACT_SUFFIX}"


def ensure_layout(s: Settings) -> None:
    """Create every directory the layout expects. Idempotent; creates no files."""
    for d in (data_dir(s), definitions_dir(s), history_dir(s), logs_dir(s), cache_dir(s)):
        d.mkdir(parents=True, exist_ok=True)
