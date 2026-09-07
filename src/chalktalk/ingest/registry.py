"""Which nflverse datasets are ingested, into which table, over which seasons.

The ``DatasetDef`` pattern is adapted from ``nfl_mcp/registry.py`` in
ebhattad/nfl-mcp:

    MIT License
    Copyright (c) 2026 ebhattad

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

The dataset list itself is this project's: no fantasy tables, no Next Gen Stats,
no per-dataset "default" flag, and ``min_season`` is an upstream guard rather
than a coverage claim — coverage is generated from the data in phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from chalktalk.config import Settings

Storage = Literal["by_season", "replace"]


@dataclass(frozen=True)
class DatasetDef:
    dataset_id: str
    loader_fn: str  # attribute name on nflreadpy
    table: str
    storage: Storage
    min_season: int | None = None  # upstream first season (guard only)
    needs_prior: bool = False  # D20: pull one extra season for prior-season attributes
    loader_kwargs: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    season_filter: bool = False  # a `replace` dataset that carries a season column

    @property
    def by_season(self) -> bool:
        return self.storage == "by_season"


# Order matters: pbp is the largest and goes last.
DATASETS: list[DatasetDef] = [
    DatasetDef("teams", "load_teams", "teams", "replace"),
    DatasetDef("players", "load_players", "players", "replace"),
    DatasetDef("contracts", "load_contracts", "contracts", "replace"),
    DatasetDef(
        "draft_picks", "load_draft_picks", "draft_picks", "replace", loader_kwargs={"seasons": True}
    ),
    DatasetDef(
        "schedules",
        "load_schedules",
        "schedules",
        "replace",
        loader_kwargs={"seasons": True},
        season_filter=True,
    ),
    DatasetDef(
        "snap_counts",
        "load_snap_counts",
        "snap_counts",
        "by_season",
        min_season=2012,
        needs_prior=True,
    ),
    DatasetDef(
        "rosters_weekly",
        "load_rosters_weekly",
        "rosters_weekly",
        "by_season",
        min_season=2002,
        needs_prior=True,
    ),
    DatasetDef(
        "player_stats",
        "load_player_stats",
        "player_stats",
        "by_season",
        min_season=1999,
        needs_prior=True,
        loader_kwargs={"summary_level": "week"},
    ),
    DatasetDef("injuries", "load_injuries", "injuries", "by_season", min_season=2009),
    DatasetDef("depth_charts", "load_depth_charts", "depth_charts", "by_season", min_season=2001),
    DatasetDef(
        "participation", "load_participation", "participation", "by_season", min_season=2016
    ),
    DatasetDef("pbp", "load_pbp", "pbp", "by_season", min_season=1999),
]

BY_ID: dict[str, DatasetDef] = {d.dataset_id: d for d in DATASETS}


def seasons_for(d: DatasetDef, settings: Settings, current: int) -> list[int]:
    """The seasons to ingest for ``d``. Empty for ``replace`` datasets.

    The floor is ``settings.season_floor``, pushed back by
    ``settings.baseline_lookback`` for datasets that feed prior-season
    attributes (D20), then raised to the dataset's upstream first season.
    """
    if not d.by_season:
        return []
    start = settings.season_floor - (settings.baseline_lookback if d.needs_prior else 0)
    if d.min_season is not None:
        start = max(start, d.min_season)
    return list(range(start, current + 1))
