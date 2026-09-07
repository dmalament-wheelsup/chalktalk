"""Thin wrappers over nflreadpy.

The only job here is to turn "a dataset and a season" into a polars frame, and
to tell a season that upstream has not published yet apart from a real failure.
"""

from __future__ import annotations

import logging

import nflreadpy
import polars as pl
from nflreadpy.config import CacheMode
from nflreadpy.config import update_config as _update_config

from chalktalk.config import Settings
from chalktalk.ingest.registry import DatasetDef
from chalktalk.paths import cache_dir

log = logging.getLogger(__name__)

CACHE_DURATION_S = 7 * 86400


class NotPublished(Exception):
    """Upstream has no release file for this dataset/season yet."""


def configure_cache(settings: Settings) -> None:
    """Point nflreadpy at an on-disk cache under CHALKTALK_HOME.

    The default is an in-memory cache, which makes a rebuild re-download
    everything. ``update_config`` lives on ``nflreadpy.config``, not the package
    root.
    """
    _update_config(
        cache_mode=CacheMode.FILESYSTEM,
        cache_dir=cache_dir(settings),
        cache_duration=CACHE_DURATION_S,
    )


def current_season() -> int:
    return int(nflreadpy.get_current_season())


def _is_not_published(exc: Exception) -> bool:
    """Distinguish "no release file yet" from a real error.

    nflreadpy reports it two ways: a loader-side season guard
    (``ValueError: Season must be between 2012 and 2025``) and a 404 from the
    release download (wrapped as ``ConnectionError``).
    """
    text = str(exc)
    if isinstance(exc, ValueError) and "Season must be between" in text:
        return True
    return isinstance(exc, ConnectionError) and "404" in text


def load(d: DatasetDef, season: int | None) -> pl.DataFrame:
    """Load one dataset (one season, or the whole file when ``season`` is None)."""
    loader = getattr(nflreadpy, d.loader_fn)
    try:
        if season is None:
            return loader(**d.loader_kwargs)
        return loader([season], **d.loader_kwargs)
    except Exception as exc:  # noqa: BLE001 — re-raised unless it is a missing release
        if _is_not_published(exc):
            raise NotPublished(
                f"{d.dataset_id} {season if season is not None else ''}: {exc}"
            ) from exc
        raise
