"""Wiring a store to a database.

The store needs a validation context per entity, and the context needs the
store — so something has to tie the knot. This is that something.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from chalktalk.config import Settings
from chalktalk.coverage import Coverage
from chalktalk.definitions.signals import ValidationCtx
from chalktalk.definitions.store import DefinitionStore
from chalktalk.paths import definitions_dir


def open_store(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    *,
    directory: Path | None = None,
    load: bool = True,
) -> DefinitionStore:
    """A store bound to this database, with its validation context wired up."""
    coverage = Coverage(conn, settings)
    store: DefinitionStore

    def ctx_factory(entity: str) -> ValidationCtx:
        return ValidationCtx(
            entity=entity, conn=conn, coverage=coverage, store=store, settings=settings
        )

    store = DefinitionStore(directory or definitions_dir(settings), ctx_factory)
    if load:
        store.load()
    return store
