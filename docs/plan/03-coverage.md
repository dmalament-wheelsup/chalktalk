# Phase 3 — Coverage registry

## Goal

Generated tables stating, for every column of every seasonal table (raw and
derived), the first and last season with data; per-season row counts; and a
completeness flag per season. Consumed by the gate (phase 6), by definitions
(coverage of a definition), by `describe_schema`, and by the CLI.

## Prerequisites

Phase 2 done. (Phase 4 re-runs this after derived tables exist; the code is
the same.)

## Deliverables

```
src/chalktalk/coverage.py
tests/unit/test_coverage_math.py        synthetic tables
tests/data/test_coverage_registry.py
tests/unit/test_cli.py                  coverage subcommand
CLI: chalktalk coverage [TABLE] [--column COL]
```

## Tables (exact)

```sql
coverage_columns(
  table_name VARCHAR, column_name VARCHAR, duck_type VARCHAR, seasonal BOOLEAN,
  first_season INTEGER, last_season INTEGER, seasons_with_data INTEGER, has_gaps BOOLEAN,
  non_null_rows BIGINT, total_rows BIGINT, computed_at TIMESTAMP)

coverage_seasons(table_name VARCHAR, season INTEGER, row_count BIGINT,
  first_week INTEGER, last_week INTEGER, weeks_present INTEGER)

season_status(season INTEGER, reg_weeks INTEGER, games_per_team INTEGER, playoff_teams INTEGER,
  reg_games_scheduled INTEGER, reg_games_final INTEGER, post_games_final INTEGER,
  complete BOOLEAN, in_progress BOOLEAN, queryable BOOLEAN)
```

## Algorithm

For every table that has a `season` column (discover with
`information_schema.columns`), excluding the registry tables themselves
(`coverage_*`, `season_status`, `ingest_log`, `build_info`, `type_conflicts` —
they carry a `season` but describe the build), one scan:
`SELECT season, count(*), count(c1), count(c2), … GROUP BY season` — then
reduce in Python per column: `first_season` = min season with `count(c) > 0`,
`last_season` = max, `seasons_with_data` = number of such seasons, `has_gaps` =
`seasons_with_data < last - first + 1`. Restrict to `season >= floor -
baseline_lookback` (2012 rows are real data for baselines).

Tables without `season`: `seasonal = false`, first/last NULL, counts only.

`season_status` from `schedules` (D24 — never hard-code these): `reg_weeks =
max(week) WHERE game_type = 'REG'`; `games_per_team = max games any team plays
in REG`; `playoff_teams = count(distinct team) in postseason games`;
`reg_games_scheduled = count(game_type='REG')`,
`reg_games_final = count(… AND home_score IS NOT NULL)`, `post_games_final`
likewise for postseason; `complete = final = scheduled AND scheduled > 0`;
`in_progress = final > 0 AND NOT complete`; `queryable = season >= floor AND
final > 0` (amended 2026-09-07 — `schedules` already carries next season, 272
games scheduled and none played; `season >= floor` alone would mark it
queryable). `complete`/`in_progress`/`queryable` count **every** game type, not
just REG; the `reg_*`/`post_*` columns are stored detail.

## Interfaces

```python
def build(conn, settings) -> None                     # CREATE OR REPLACE the three tables

@dataclass(frozen=True)
class ColumnCoverage: first_season: int | None; last_season: int | None; seasonal: bool; has_gaps: bool
@dataclass(frozen=True)
class SeasonRange: first: int; last: int

class Coverage:                                       # loaded once per connection; cheap
    def __init__(self, conn, settings)
    def column(self, table: str, column: str) -> ColumnCoverage | None
    def intersect(self, refs: Iterable[tuple[str, str]]) -> SeasonRange | None   # None if any ref unknown/non-seasonal-with-no-data
    def queryable_seasons(self) -> list[int]          # from season_status
    def latest_complete(self) -> int | None
    def status(self, season: int) -> SeasonStatus | None
```

`intersect` over refs with `seasonal=false` (e.g. `players.draft_round`) treats
them as unbounded. A ref with `first_season IS NULL` (present, never populated)
makes the result `None` — the gate reports it as the limiting ref.

## Acceptance

```bash
uv run chalktalk build --skip-features        # or reuse phase 2 artifact: chalktalk coverage --rebuild
uv run chalktalk coverage snap_counts                          # offense_snaps 2013..2025 (amended: no 2012 file)
uv run chalktalk coverage participation --column offense_players   # 2016..2025
uv run chalktalk coverage --seasons                            # season_status; 2025 complete, 2026 scheduled/not queryable
uv run pytest -m data tests/data/test_coverage_registry.py
```

Data test asserts: `snap_counts.offense_snaps` first **2013** (amended — the
2012 release is empty, so there is no prior-season snap baseline for 2013);
`participation.offense_players` first ≥ 2016; `pbp.epa` first 2013; no seasonal
table has `first_season < floor - 1`; every season from the floor to the latest
*played* season is queryable and contiguous, and a scheduled-but-unplayed season
is present with `queryable = false`; `reg_weeks` is 17 and `games_per_team` 16
for 2013–2020, 18 and 17 from 2021; `playoff_teams` is 12 through 2019 and 14
from 2020; 2022 has 271 regular-season games, not 272 (the cancelled
Bills–Bengals game — proof the numbers are read, not written).

## Pitfalls

- A column present in the schema but all-NULL in early seasons (NGS fields in
  `pbp`) correctly gets a later `first_season`. That is the point.
- Build order is raw → features → coverage. Phase 4 must call `coverage.build`
  last; do not compute coverage before derived tables exist.
- `Coverage.queryable_seasons()` starts at `floor` even though 2012 rows exist.
- A build with `--only` and no `schedules` leaves `season_status` empty; coverage
  must not crash on it (amended 2026-09-07).
- `playoff_teams` counts distinct *teams*, so it needs the home/away union;
  `post_games_final` counts *games* and must not use that union, or every
  postseason game is counted twice.

## Done when

- [ ] Acceptance passes.
- [ ] `phase-3:` commits; ledger updated.
