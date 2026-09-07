"""Coverage registry — first and last season for every column, from the data.

Phase 3 fills this in. The build already calls :func:`build` so the hook point
is exercised from the first build rather than bolted on later.
"""

from __future__ import annotations

import logging

import duckdb

from chalktalk.config import Settings

log = logging.getLogger(__name__)


def build(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Generate the coverage tables from the ingested data. No-op until phase 3."""
    log.debug("coverage.build: not implemented (phase 3)")
