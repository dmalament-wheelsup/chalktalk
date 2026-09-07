"""Column normalization applied to every frame before it reaches DuckDB.

No column is ever dropped. The order below matters: renames first, so hooks and
casts can name columns as they will be stored.
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl

_SAFE_TRANSLATION = str.maketrans({".": "_", " ": "_", "-": "_"})


def safe_column(name: str) -> str:
    """DuckDB-safe column name: ``.``, space and ``-`` become ``_``."""
    return name.translate(_SAFE_TRANSLATION)


def safe_columns(df: pl.DataFrame) -> pl.DataFrame:
    mapping = {c: safe_column(c) for c in df.columns}
    if len(set(mapping.values())) != len(mapping):
        seen: dict[str, str] = {}
        collisions = []
        for original, safe in mapping.items():
            if safe in seen:
                collisions.append((seen[safe], original, safe))
            seen[safe] = original
        raise ValueError(f"column renaming collides: {collisions}")
    return df.rename(mapping)


def null_blank_strings(df: pl.DataFrame) -> pl.DataFrame:
    """Strip every string column; whitespace-only becomes NULL.

    This is what clears the junk out of ``injuries.practice_status``. A genuine
    NULL — ``injuries.report_status`` for a player listed with practice
    information only — is data and is left alone.
    """
    string_cols = [c for c, dt in df.schema.items() if dt == pl.Utf8]
    if not string_cols:
        return df
    return df.with_columns(
        pl.col(c).str.strip_chars().replace("", None).alias(c) for c in string_cols
    )


_COMMON_CASTS = {"season": pl.Int32, "week": pl.Int32, "play_id": pl.Int64}


def cast_common(df: pl.DataFrame) -> pl.DataFrame:
    """``season``/``week`` to Int32 and ``play_id`` to Int64 wherever they appear.

    Both ``pbp.play_id`` and ``participation.play_id`` arrive as Float64.
    """
    casts = [
        pl.col(name).cast(dtype).alias(name)
        for name, dtype in _COMMON_CASTS.items()
        if name in df.columns and df.schema[name] != dtype
    ]
    return df.with_columns(casts) if casts else df


def _hook_participation(df: pl.DataFrame) -> pl.DataFrame:
    """``participation`` carries neither season nor week; both are in the game id.

    ``nflverse_game_id`` is ``2023_05_LAC_LV`` and equals ``pbp.game_id``.
    """
    if "nflverse_game_id" not in df.columns:
        return df
    parts = pl.col("nflverse_game_id").str.split("_")
    return df.with_columns(
        parts.list.get(0).cast(pl.Int32).alias("season"),
        parts.list.get(1).cast(pl.Int32).alias("week"),
    )


def _hook_snap_counts(df: pl.DataFrame) -> pl.DataFrame:
    """Snap totals arrive as Float64; they are counts."""
    casts = [
        pl.col(c).cast(pl.Int32).alias(c)
        for c in ("offense_snaps", "defense_snaps", "st_snaps")
        if c in df.columns
    ]
    return df.with_columns(casts) if casts else df


HOOKS: dict[str, Callable[[pl.DataFrame], pl.DataFrame]] = {
    "participation": _hook_participation,
    "snap_counts": _hook_snap_counts,
}


def normalize(dataset_id: str, df: pl.DataFrame) -> pl.DataFrame:
    """Apply every normalization step, in order, for ``dataset_id``."""
    df = safe_columns(df)
    df = null_blank_strings(df)
    df = cast_common(df)
    hook = HOOKS.get(dataset_id)
    return hook(df) if hook else df
