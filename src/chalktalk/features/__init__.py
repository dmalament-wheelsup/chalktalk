"""Feature layer — the six entity tables and the attribute catalog.

Phase 4 fills this in. The build already calls :func:`build_all` so the hook
point is exercised from the first build rather than bolted on later.
"""

from __future__ import annotations

import logging

import duckdb

from chalktalk.config import Settings

log = logging.getLogger(__name__)


def build_all(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Build every derived entity table. No-op until phase 4."""
    log.debug("features.build_all: not implemented (phase 4)")
