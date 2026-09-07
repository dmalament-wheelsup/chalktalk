# Phase 2 — Ingest

## Goal

`chalktalk build` downloads every needed nflverse dataset through nflreadpy,
writes them into a **new** DuckDB file with normalized types, records what it
loaded, and atomically publishes the file as `CURRENT`. Idempotent per season.
Phases 3 and 4 hook into the same pipeline (`features.build_all`,
`coverage.build`) — leave the hook calls in place as no-ops now.

## Prerequisites

Phase 1 done.

## Deliverables

```
src/chalktalk/ingest/registry.py     DatasetDef + DATASETS (vendored pattern, see below)
src/chalktalk/ingest/loaders.py      nflreadpy wrappers, cache config, current season
src/chalktalk/ingest/normalize.py    column renames, casts, NULL cleanup, per-dataset fixes
src/chalktalk/ingest/build.py        orchestration, ingest_log, build_info, atomic publish
src/chalktalk/cli.py                 `build` wired
tests/unit/test_normalize.py
tests/unit/test_registry.py
tests/data/test_ingest_lossless.py   (data tier)
docs/plan/00-index.md                Amendment: actual player_stats column names observed
```

## Vendoring

Copy from `ebhattad/nfl-mcp` (MIT): the `DatasetDef` pattern in
`nfl_mcp/registry.py`, and from `nfl_mcp/ingest.py` the season loop, the
register-Arrow-then-`INSERT BY NAME` mechanics, and `_reconcile_schema`. Put
their copyright and MIT notice at the top of `registry.py` and `build.py`
(read the exact line from their `LICENSE` file). Do **not** copy:
`enhanced_description`, the derived fantasy tables, the `plays` indexes, or
their `NFL_MCP_*` env names. Replace `_duckdb_type_for_polars` with Arrow-derived
types (D7).

## Dataset registry (exact)

`floor` = `settings.season_floor` (2013). `current` =
`nflreadpy.get_current_season()`. `prior` = `floor - settings.baseline_lookback`.

| dataset_id | loader_fn | table | seasons | storage | notes |
|---|---|---|---|---|---|
| `teams` | `load_teams` | `teams` | — | replace | |
| `players` | `load_players` | `players` | — | replace | crosswalk source (D6) |
| `contracts` | `load_contracts` | `contracts` | — | replace | two `List(Struct)` columns; keep |
| `draft_picks` | `load_draft_picks` (`True`) | `draft_picks` | — | replace | |
| `schedules` | `load_schedules` (`True`) | `schedules` | filter `season >= floor` | replace | one file for all seasons |
| `snap_counts` | `load_snap_counts` | `snap_counts` | `prior..current` | by_season | `needs_prior`; snaps `Float64` → `INTEGER` |
| `rosters_weekly` | `load_rosters_weekly` | `rosters_weekly` | `prior..current` | by_season | `needs_prior` |
| `player_stats` | `load_player_stats(summary_level="week")` | `player_stats` | `prior..current` | by_season | `needs_prior`; record columns (D5) |
| `injuries` | `load_injuries` | `injuries` | `floor..current` | by_season | whitespace → NULL |
| `depth_charts` | `load_depth_charts` | `depth_charts` | `floor..current` | by_season | schema varies by season; drift adds columns |
| `participation` | `load_participation` | `participation` | `max(2016, floor)..current` | by_season | add `season`, `week`; `play_id` → `BIGINT` |
| `pbp` | `load_pbp` | `pbp` | `floor..current` | by_season | 372 cols; `play_id` → `BIGINT`; ingest last |

```python
@dataclass(frozen=True)
class DatasetDef:
    dataset_id: str
    loader_fn: str                              # attribute name on nflreadpy
    table: str
    storage: Literal["by_season", "replace"]
    min_season: int | None = None               # upstream first season (guard only)
    needs_prior: bool = False                   # D20
    loader_kwargs: dict[str, Any] = field(default_factory=dict)
    season_filter: bool = False                 # replace datasets that carry a season column (schedules)

DATASETS: list[DatasetDef]                       # in the table's order (pbp last)

def seasons_for(d: DatasetDef, settings: Settings, current: int) -> list[int]
```

## Normalization (`normalize.py`)

Applied to every DataFrame in this order; no column is ever dropped:

1. `safe_columns(df)`: rename `.`, space, `-` → `_`. Assert no collisions.
2. `null_blank_strings(df)`: for every string column, strip; `''` → null.
   This is what fixes `injuries.practice_status` junk.
3. `cast_common(df)`: `season` → `Int32`, `week` → `Int32`, `play_id` → `Int64`
   when present.
4. Per-dataset hooks (`HOOKS: dict[str, Callable]`):
   - `participation`: add `season = split(nflverse_game_id, '_')[0]` and
     `week = split(...)[1]` as `Int32`.
   - `snap_counts`: `offense_snaps`, `defense_snaps`, `st_snaps` → `Int32`.

## Build (`build.py`)

```python
@dataclass
class BuildResult:
    artifact: Path
    tables: dict[str, int]          # table → rows
    warnings: list[str]
    duration_s: float

def build(settings: Settings, *, seasons: range | None = None, only: set[str] | None = None,
          skip_features: bool = False, publish: bool = True) -> BuildResult
```

Steps, in order:

1. `ensure_layout`. Configure nflreadpy for on-disk caching:
   `nflreadpy.update_config(cache_mode=<filesystem member of CacheMode>,
   cache_dir=cache_dir(settings), cache_duration=7*86400)`. Look up the
   member name in `nflreadpy.config.CacheMode` and hard-code it.
2. Target: `data/nfl-YYYYMMDD.duckdb.building`. If a stale `.building` exists,
   delete it. `open_rw`.
3. `CREATE TABLE ingest_log(dataset_id VARCHAR, table_name VARCHAR, season INTEGER,
   row_count BIGINT, loaded_at TIMESTAMP, loader_fn VARCHAR, nflreadpy_version VARCHAR)`.
4. For each `DatasetDef` in order, for each season (or once for `replace`):
   load → normalize → `conn.register("_df", df.to_arrow())` →
   if table absent: `CREATE TABLE t AS SELECT * FROM _df` ·
   else: reconcile schema (for each column in `_df` missing from `t`, `ALTER
   TABLE t ADD COLUMN` with the type DuckDB reports for `_df`), then for
   `by_season`: `DELETE FROM t WHERE season = ?` · then `INSERT INTO t BY NAME
   SELECT * FROM _df` → `conn.unregister` → write `ingest_log` row.
   Failure policy: a season whose download fails with a not-found error is
   logged as a warning and skipped (the current season before its first
   release); any other exception aborts, leaves `.building` for inspection,
   and does not publish.
5. Hooks: `features.build_all(conn, settings)` unless `skip_features`;
   then `coverage.build(conn, settings)`. Both are no-ops until phases 3–4.
6. `build_info` (one row): `built_at, chalktalk_version, nflreadpy_version,
   duckdb_version, polars_version, season_floor, first_season, last_season,
   git_sha (or NULL), duration_s`.
7. `CHECKPOINT`; close. Rename to `nfl-YYYYMMDD.duckdb` (`-2`, `-3` suffix if
   today's name exists). If `publish`: `write_current`.
8. Retention: keep the newest 3 artifacts matching `nfl-*.duckdb`; never delete
   `CURRENT`'s target.

CLI:

```
chalktalk build [--seasons 2013-2025] [--only a,b] [--skip-features] [--no-publish] [--keep 3]
chalktalk build --plan          # print datasets and seasons; no downloads
```

## Interfaces

```python
# loaders.py
def current_season() -> int
def load(d: DatasetDef, season: int | None) -> pl.DataFrame     # applies loader_kwargs
class NotPublished(Exception)                                   # wraps upstream not-found
```

## Acceptance

```bash
uv run chalktalk build --plan
uv run chalktalk build --seasons 2023-2023 --only schedules,players,snap_counts,injuries --no-publish
uv run chalktalk build                       # full: expect 10–25 min on first run, minutes after (cache)
uv run chalktalk doctor                      # CURRENT → today's artifact
uv run python -c "import duckdb,os;c=duckdb.connect(open(os.path.expanduser('~/.chalktalk/data/CURRENT')).read().strip(),read_only=True);print(c.sql('select table_name,count(*) n,min(season),max(season) from ingest_log group by 1 order by 1'))"
uv run pytest -m data tests/data/test_ingest_lossless.py
```

Sanity numbers (2023, REG+POST, verified): `pbp` 49,665 · `snap_counts`
26,540 · `participation` 46,168 · `injuries` 5,599 · `rosters_weekly` 45,655.
`player_stats` 2025: 19,422.

`test_ingest_lossless.py`: for each seasonal dataset and one season, reload
via nflreadpy, normalize, and assert: identical column set, identical row
count, and no column whose polars dtype is numeric/boolean/date is stored as
`VARCHAR`.

**Amend `00-index.md`** with the exact `player_stats` column names the loader
returned for 2013 and 2025 (they must match each other; if they don't, record
both and phase 4 must handle it).

## Pitfalls

- `participation` has no `season`/`week`; derive them. Its `nflverse_game_id`
  equals `pbp.game_id`.
- `injuries.report_status` NULL means "listed with practice information only"
  — that is data, not junk. Only whitespace-only strings become NULL.
- `contracts` `List(Struct)` columns ingest fine from Arrow. They are created on
  the first (only) insert, so the drift path never sees them.
- `depth_charts` schema differs across seasons; drift handles it. No v1
  attribute depends on it (D23).
- `play_id` is `Float64` upstream in both `pbp` and `participation`.
- nflreadpy raises on a season with no release file yet. Treat as
  `NotPublished`, warn, continue.
- Memory: if the machine has < 8 GB, set `CHALKTALK_DUCKDB_MEMORY_LIMIT`.
- Never open a `.building` file read-only from another process.

## Done when

- [ ] Acceptance passes, including the data-tier lossless test.
- [ ] `00-index.md` amended with `player_stats` columns.
- [ ] `phase-2:` commits; ledger updated.
