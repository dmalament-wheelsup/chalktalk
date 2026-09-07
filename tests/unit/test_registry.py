"""The dataset registry: season windows, ordering, and the loader contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import nflreadpy
import pytest

from chalktalk.config import Settings
from chalktalk.ingest.registry import BY_ID, DATASETS, DatasetDef, seasons_for


@pytest.fixture
def settings() -> Settings:
    return Settings.load()


def test_every_loader_exists_on_nflreadpy() -> None:
    for d in DATASETS:
        assert hasattr(nflreadpy, d.loader_fn), f"{d.dataset_id}: no nflreadpy.{d.loader_fn}"


def test_dataset_ids_and_tables_are_unique() -> None:
    assert len({d.dataset_id for d in DATASETS}) == len(DATASETS)
    assert len({d.table for d in DATASETS}) == len(DATASETS)


def test_pbp_is_ingested_last() -> None:
    assert DATASETS[-1].dataset_id == "pbp"


def test_registry_covers_the_planned_tables() -> None:
    expected = {
        "pbp",
        "snap_counts",
        "participation",
        "injuries",
        "rosters_weekly",
        "depth_charts",
        "player_stats",
        "schedules",
        "players",
        "contracts",
        "draft_picks",
        "teams",
    }
    assert {d.table for d in DATASETS} == expected


def test_replace_datasets_have_no_seasons(settings: Settings) -> None:
    for d in DATASETS:
        if d.storage == "replace":
            assert seasons_for(d, settings, 2025) == []


def test_prior_season_datasets_reach_back_one_year(settings: Settings) -> None:
    """D20: baselines for the floor season need the season before it."""
    for dataset_id in ("snap_counts", "rosters_weekly", "player_stats"):
        seasons = seasons_for(BY_ID[dataset_id], settings, 2025)
        assert seasons[0] == settings.season_floor - settings.baseline_lookback
        assert seasons[-1] == 2025


def test_other_seasonal_datasets_start_at_the_floor(settings: Settings) -> None:
    for dataset_id in ("injuries", "depth_charts", "pbp"):
        assert seasons_for(BY_ID[dataset_id], settings, 2025)[0] == settings.season_floor


def test_participation_starts_where_upstream_does(settings: Settings) -> None:
    """D4: participation begins in 2016, above the season floor."""
    assert seasons_for(BY_ID["participation"], settings, 2025)[0] == 2016


def test_min_season_raises_a_floor_it_cannot_meet() -> None:
    """A floor below the dataset's upstream start is clamped, not requested."""
    s = Settings(home=Settings.load().home, season_floor=2005)
    assert seasons_for(BY_ID["snap_counts"], s, 2025)[0] == 2012
    assert seasons_for(BY_ID["injuries"], s, 2025)[0] == 2009


def test_a_higher_floor_wins_over_min_season() -> None:
    s = Settings(home=Settings.load().home, season_floor=2020)
    assert seasons_for(BY_ID["snap_counts"], s, 2025) == list(range(2019, 2026))


def test_seasons_stop_at_the_current_season(settings: Settings) -> None:
    assert seasons_for(BY_ID["pbp"], settings, 2024)[-1] == 2024


def test_loader_kwargs_do_not_break_hashing() -> None:
    """DatasetDef is frozen and carries a dict; it must still be usable in a set."""
    assert len({DATASETS[0], DATASETS[0]}) == 1


def test_schedules_is_a_single_file_filtered_by_season() -> None:
    schedules = BY_ID["schedules"]
    assert schedules.storage == "replace"
    assert schedules.season_filter is True
    assert schedules.loader_kwargs == {"seasons": True}


def test_only_schedules_uses_the_season_filter() -> None:
    assert [d.dataset_id for d in DATASETS if d.season_filter] == ["schedules"]


def test_player_stats_asks_for_weekly_rows() -> None:
    assert BY_ID["player_stats"].loader_kwargs["summary_level"] == "week"


def test_by_season_flag_matches_storage() -> None:
    for d in DATASETS:
        assert d.by_season == (d.storage == "by_season")


def test_defs_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        DatasetDef.__setattr__(DATASETS[0], "table", "nope")
