"""Normalization: renames, blank-string nulling, common casts, per-dataset hooks."""

from __future__ import annotations

import polars as pl
import pytest

from chalktalk.ingest import normalize as norm


def test_safe_column_replaces_unsafe_characters() -> None:
    assert norm.safe_column("a.b c-d") == "a_b_c_d"


def test_safe_columns_renames_every_column() -> None:
    df = pl.DataFrame({"a.b": [1], "c d": [2], "e-f": [3], "plain": [4]})
    assert norm.safe_columns(df).columns == ["a_b", "c_d", "e_f", "plain"]


def test_safe_columns_refuses_a_collision() -> None:
    df = pl.DataFrame({"a.b": [1], "a b": [2]})
    with pytest.raises(ValueError, match="collides"):
        norm.safe_columns(df)


def test_safe_columns_keeps_every_column() -> None:
    df = pl.DataFrame({f"c.{i}": [i] for i in range(20)})
    assert len(norm.safe_columns(df).columns) == 20


def test_blank_strings_become_null() -> None:
    df = pl.DataFrame({"practice_status": ["Full Participation in Practice", "   ", "", None]})
    got = norm.null_blank_strings(df)["practice_status"].to_list()
    assert got == ["Full Participation in Practice", None, None, None]


def test_surrounding_whitespace_is_stripped() -> None:
    df = pl.DataFrame({"s": ["  Out  "]})
    assert norm.null_blank_strings(df)["s"].to_list() == ["Out"]


def test_existing_nulls_are_left_alone() -> None:
    """injuries.report_status NULL means 'practice information only' — that is data."""
    df = pl.DataFrame({"report_status": [None, "Questionable"]}, schema={"report_status": pl.Utf8})
    assert norm.null_blank_strings(df)["report_status"].to_list() == [None, "Questionable"]


def test_non_string_columns_are_untouched() -> None:
    df = pl.DataFrame({"n": [1, 2], "f": [1.5, 2.5]})
    assert norm.null_blank_strings(df).equals(df)


def test_common_casts() -> None:
    df = pl.DataFrame(
        {"season": [2023.0], "week": ["8"], "play_id": [1234.0], "other": [1.0]},
        schema={"season": pl.Float64, "week": pl.Utf8, "play_id": pl.Float64, "other": pl.Float64},
    )
    got = norm.cast_common(df)
    assert got.schema["season"] == pl.Int32
    assert got.schema["week"] == pl.Int32
    assert got.schema["play_id"] == pl.Int64
    assert got.schema["other"] == pl.Float64
    assert got.row(0) == (2023, 8, 1234, 1.0)


def test_common_casts_skip_missing_columns() -> None:
    df = pl.DataFrame({"team": ["SF"]})
    assert norm.cast_common(df).equals(df)


def test_participation_gains_season_and_week() -> None:
    """participation carries neither; both live in the game id (= pbp.game_id)."""
    df = pl.DataFrame({"nflverse_game_id": ["2023_05_LAC_LV", "2016_18_PIT_MIA"]})
    got = norm.normalize("participation", df)
    assert got["season"].to_list() == [2023, 2016]
    assert got["week"].to_list() == [5, 18]
    assert got.schema["season"] == pl.Int32
    assert got.schema["week"] == pl.Int32


def test_participation_hook_is_a_noop_without_the_game_id() -> None:
    df = pl.DataFrame({"play_id": [1.0]})
    assert "season" not in norm.normalize("participation", df).columns


def test_snap_counts_totals_become_integers() -> None:
    df = pl.DataFrame({"offense_snaps": [61.0], "defense_snaps": [0.0], "st_snaps": [4.0]})
    got = norm.normalize("snap_counts", df)
    assert [got.schema[c] for c in ("offense_snaps", "defense_snaps", "st_snaps")] == [pl.Int32] * 3
    assert got.row(0) == (61, 0, 4)


def test_snap_counts_keeps_null_snap_totals() -> None:
    df = pl.DataFrame({"offense_snaps": [None, 12.0]}, schema={"offense_snaps": pl.Float64})
    assert norm.normalize("snap_counts", df)["offense_snaps"].to_list() == [None, 12]


def test_normalize_runs_renames_before_hooks() -> None:
    """The hook names columns as they will be stored, not as upstream sent them."""
    df = pl.DataFrame({"nflverse.game_id": ["2023_05_LAC_LV"]})
    got = norm.normalize("participation", df)
    assert "nflverse_game_id" in got.columns
    assert got["season"].to_list() == [2023]


def test_normalize_without_a_hook_still_normalizes() -> None:
    df = pl.DataFrame({"report status": ["  "], "season": [2023.0]})
    got = norm.normalize("injuries", df)
    assert got.columns == ["report_status", "season"]
    assert got["report_status"].to_list() == [None]
    assert got.schema["season"] == pl.Int32


def test_normalize_never_drops_a_column() -> None:
    df = pl.DataFrame({"a": [1], "b c": ["  "], "season": [2023.0]})
    assert len(norm.normalize("pbp", df).columns) == 3
