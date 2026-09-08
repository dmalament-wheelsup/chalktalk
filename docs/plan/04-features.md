# Phase 4 — Feature layer

## Goal

Six entity tables exposing a broad, documented attribute surface; the entity
registry (base tables, namespaces, joins, lift rules); a catalog with a
description for every derived column; and **Gate A**: the raw attributes for
the hand-authored fixture cases look right before any definition exists.

This phase is where breadth lives. Every attribute added here is a concept a
user can define without anyone writing code. When in doubt, add the attribute.

## Prerequisites

Phases 2 and 3 done. `tests/fixtures/exits.yaml` from phase 9 — write it now
from [09-testing.md](09-testing.md) if it does not exist yet.

## Deliverables

```
src/chalktalk/features/__init__.py     build_all(conn, settings) in the order below
src/chalktalk/features/xwalk.py        player_id_xwalk
src/chalktalk/features/game_ctx.py     entity game
src/chalktalk/features/team_game.py    entity team_game
src/chalktalk/features/team_season.py  entity team_season
src/chalktalk/features/player_play.py  helper (2016+)
src/chalktalk/features/player_season.py entity player_season
src/chalktalk/features/player_game.py  entity player_game
src/chalktalk/features/catalog.py      ATTRIBUTES + resolve()
src/chalktalk/entities.py              ENTITIES, LIFTS
tests/unit/test_catalog_complete.py    every derived column is documented (mini DB)
tests/unit/test_entities.py            namespaces/lifts are consistent
tests/data/test_features.py            counts, crosswalk rate, spot checks
tests/data/test_gate_a.py              fixture attribute checks
```

## Stages

Added 2026-09-07. This is the largest phase in the plan and the only one whose
acceptance is a gate. It is worked in four stages, each ending with the unit
tier green and its own commit (`phase-4a:` … `phase-4d:`). The phase is **not**
renumbered — phases 5, 6 and 7 reference "phase 4", and `ENTITIES`, `LIFTS` and
the catalog are one contract. The ledger row for phase 4 stays `in progress`
until 4d is green.

Measured before splitting, so the staging rests on numbers rather than worry:
`player_play` unnests to ~9.9M rows (4.95M offence + 4.95M defence slots over
450k plays) — no volume problem, so it needs no stage of its own. The risk is
concentrated in `player_game` and Gate A; the bulk of the *work* is the ~250
hand-written catalog entries, which is broad but cannot fail interestingly.

- [x] **4a — foundations.** _(done 2026-09-07)_ `tests/fixtures/exits.yaml`, `entities.py`,
      `catalog.py` machinery, `xwalk.py`. Green: crosswalk ≥ 99%, namespace and
      lift consistency, catalog well-formedness and completeness.
- [x] **4b — the team side.** _(done 2026-09-07)_ `game_ctx`, `team_game`, `team_season`. Three of
      the six entities, no dependency on participation or the crosswalk. Settles
      the `spread_line` sign question. Green: per-season counts, era-neutral
      attributes, spot checks.
- [x] **4c — the player season.** _(done 2026-09-07)_ `player_play`, `player_season`.
      `player_season` reads only raw tables, so it does not need `player_game`.
- [x] **4d — the player game and the gate.** _(done 2026-09-08)_ `player_game`, then Gate A.

Two things that matter more than the split itself:

1. **The catalog completeness test lands in 4a, not at the end.** "Every column
   of every derived entity table has an `ATTRIBUTES` entry" is what forces each
   table to arrive documented. Written last it becomes a 250-entry retrofit;
   written first, no table can slip past it. It is vacuous at 4a and must not be
   allowed to stay that way.
2. **`exits.yaml` is written in 4a, before any player table exists.** It is a
   verbatim copy of the block in [09-testing.md](09-testing.md), whose expected
   answers come from football knowledge. Writing it after `player_game` exists
   would let it be back-fitted to whatever the code happens to produce — the
   exact failure `CLAUDE.md` warns about. Doing it first makes that structural
   rather than a matter of discipline.

Each feature module exposes `build(conn, settings) -> None` and runs
`CREATE OR REPLACE TABLE <name> AS <sql>`. SQL lives as readable strings, one
CTE per concept, parameterized only by settings constants.

**Build order:** `season_status` (from `coverage.build_season_status`, since
`game_ctx` reads it) → `xwalk → game_ctx → team_game → team_season →
player_play → player_season → player_game`, then `coverage.build`. `player_season` is computed
from raw tables only, so `player_game` can join it for prior-season attributes.

## Conventions

- Every derived table with a `season` column includes `season INTEGER`.
- Booleans are `BOOLEAN`, never 0/1.
- Attributes that are meaningless outside a coverage window are `NULL` outside
  it, never 0. `pp_*` are NULL before 2016.
- Two rows for the same (player, game) in `snap_counts` (rare PFR duplicates):
  keep the row with the larger `snaps_total`.
- **Every team column read from a non-`pbp` source must go through
  `features.team_abbr.sql()`** (added 2026-09-07). `pbp` and `player_stats`
  use current franchise codes; `schedules`, `snap_counts`, `injuries`,
  `participation`, `teams` and `rosters_weekly` do not. Mixing them matches
  nothing rather than erroring — it zeroed every Oakland, San Diego and
  St. Louis offensive stat before it was caught.

## `player_id_xwalk`

`pfr_id, gsis_id, display_name, position, position_group, source`.
From `players` where `pfr_id IS NOT NULL`, unioned with distinct
`(pfr_id, gsis_id)` pairs from `rosters_weekly` not already present
(`source='rosters_weekly'`). Position-group fallback for snap-count positions
when `players` has no row. Amended 2026-09-07: `snap_counts.position` has **48**
distinct values, not the 19 listed elsewhere in this plan, including ~600 rows
of compound strings (`C/G`, `DE/L`, `G/OT`). Resolve on the first token before
`/`, then map all 28 base tokens: `QB→QB · RB,FB,HB→RB · WR→WR · TE→TE ·
T,OT,G,OG,C,OL→OL · DE,DT,NT,DL→DL · LB,OLB,ILB,MLB→LB · CB,FS,SS,S,DB→DB ·
K,P,LS→SPEC`. Log the unresolved count.
Test: ≥ 99% of `snap_counts` rows (season ≥ floor) resolve to a `gsis_id`.
Measured: **99.93%** (226 of 324,611 unresolved); `rosters_weekly` contributes
only 2 ids beyond `players`.

## `game_ctx` — entity `game`

From `schedules` (season ≥ floor) plus `pbp` aggregates.

| attribute | type | description |
|---|---|---|
| `game_id` | str | nflverse id `YYYY_WW_AWAY_HOME` |
| `season, week` | int | |
| `game_type` | str | REG WC DIV CON SB |
| `is_postseason` | bool | `game_type <> 'REG'` |
| `reg_weeks` | int | that season's number of regular-season weeks (17 through 2020, 18 from 2021) — from `season_status` |
| `is_final_reg_week` | bool | `game_type = 'REG' AND week = reg_weeks` — the era-neutral "rest week" (D24) |
| `weeks_remaining_reg` | int | `reg_weeks - week` for REG games; NULL in the postseason |
| `gameday` | date | |
| `weekday` | str | Sunday … |
| `gametime` | str | `'HH:MM'` Eastern kickoff |
| `kickoff_hour` | int | integer hour of `gametime` |
| `home_team, away_team` | str | |
| `home_score, away_score` | int | NULL until final |
| `result` | int | `home_score - away_score` |
| `margin_abs` | int | `abs(result)` |
| `total` | int | combined points |
| `overtime` | bool | |
| `is_final` | bool | scores present |
| `spread_line` | float | upstream home-team line. **Verified 2026-09-07: positive means the HOME team is favoured** — the negation of the conventional spread |
| `total_line` | float | |
| `div_game` | bool | |
| `roof, surface` | str | |
| `temp, wind` | int | NULL for domes |
| `home_rest, away_rest` | int | days since previous game |
| `home_qb_id, home_qb_name, away_qb_id, away_qb_name` | str | starting QBs per nflverse |
| `home_coach, away_coach, referee, stadium` | str | |
| `plays_total` | int | pbp rows with `play_type IN ('pass','run','punt','field_goal','kickoff','extra_point','qb_kneel','qb_spike')` |
| `pass_plays, rush_plays` | int | `play_type = 'pass'` / `'run'` |

## `team_game` — entity `team_game` (two rows per game)

| attribute | type | description |
|---|---|---|
| `game_id, season, week, game_type, is_postseason` | | |
| `team, opponent` | str | |
| `home` | bool | |
| `points_for, points_against, margin` | int | team-relative |
| `won, lost, tied` | bool | NULL until final |
| `rest_days, opp_rest_days` | int | |
| `games_played_before` | int | this team's REG games already played this season (0 in week 1); era-neutral progress |
| `season_progress` | float | `games_played_before / games_per_team` |
| `spread` | float | team-relative: **negative = team favoured**; derive from `spread_line` once its sign is verified |
| `favorite` | bool | `spread < 0`; NULL when no line |
| `covered` | bool | `margin + spread > 0` |
| `div_game` | bool | |
| `starting_qb_id, starting_qb_name` | str | from schedules |
| `off_plays` | int | pbp `posteam = team AND play_type IN ('pass','run')` |
| `pass_attempts, rush_attempts, pass_rate` | | |
| `off_epa, off_epa_per_play, off_success_rate, off_yards` | float | `sum(epa)`, `avg(epa)`, `avg(success)`, `sum(yards_gained)` |
| `turnovers` | int | `sum(interception) + sum(fumble_lost)` |
| `sacks_taken` | int | `sum(sack)` on offense |
| `fourth_downs, fourth_down_go, fourth_down_go_rate` | | `down = 4` excluding kneels; go = pass/run |
| `def_plays, def_epa_per_play, def_success_rate, sacks, takeaways` | | `defteam = team` |
| `max_lead, max_deficit` | int | from `score_differential` made team-relative |
| `plays_leading, plays_trailing, plays_tied` | int | |

pbp columns used: `posteam, defteam, play_type, down, epa, success, yards_gained,
interception, fumble_lost, sack, score_differential, qtr, game_seconds_remaining`.
All are standard nflfastR names; assert they exist at build time.

## `team_season` — entity `team_season`

`season, team, games, games_per_team, wins, losses, ties, win_pct, points_for,
points_against, point_diff, points_for_per_game, points_against_per_game,
made_playoffs` (exists postseason `team_game`), `playoff_wins,
off_epa_per_play, def_epa_per_play, pass_rate` (REG only), `division,
conference` (from `teams`; verify column names, likely `team_division`,
`team_conf`). Per-game rates exist because 16- and 17-game totals are not
comparable (D24).

## `player_play` — helper, 2016+

`game_id, play_id, gsis_id, side, team, unit_play_idx, team_unit_plays`.
Built by splitting `participation.offense_players` / `defense_players` on `;`
and unnesting. `team` = `possession_team` for offense, the other team (from
`game_ctx`) for defense. `unit_play_idx = row_number() OVER (PARTITION BY
game_id, team, side ORDER BY play_id)` over *distinct plays where that unit
was on the field*; `team_unit_plays` = the count. Only plays with non-empty
lists.

## `player_season` — entity `player_season`

Regular season only unless stated.

| attribute | type | description |
|---|---|---|
| `player_key, gsis_id, pfr_id, player_name, season` | | |
| `position_group, position` | str | group from `players`; position = most frequent snap-count position |
| `team_primary, teams_count` | | team with most games |
| `games` | int | snap-count rows |
| `games_with_snaps` | int | `snaps_unit >= 1` |
| `snaps_unit_total, snap_share_mean, snap_share_max` | | over games with snaps |
| `team_games, games_missed` | int | `team_primary`'s REG games; `team_games - games_with_snaps` |
| `games_played_share` | float | `games_with_snaps / team_games` — era-neutral availability; the default percentile eligibility is `>= 0.5` (D13, D24) |
| `years_exp` | int | max over the season |
| `is_rookie` | bool | `players.rookie_season = season` |
| `draft_year, draft_round, draft_pick, undrafted` | | from `players`; fallback `draft_picks` by `gsis_id` |
| `contract_year_signed, contract_years, apy, apy_cap_pct, guaranteed` | | contract active in `season`: `year_signed <= season < year_signed + years`; latest `year_signed` wins |
| `attempts, completions, passing_yards, passing_tds, passing_interceptions, sacks_suffered, carries, rushing_yards, rushing_tds, targets, receptions, receiving_yards, receiving_tds, fantasy_points, fantasy_points_ppr` | | summed from `player_stats` (REG). **Use the names phase 2 recorded**; omit any that don't exist and amend |
| `<stat>_per_game` | float | every production total above divided by `games_with_snaps`; e.g. `carries_per_game`. A "300-carry season" is 17.6/game in a 17-game season and 18.75 in a 16-game one — say it per game (D24) |
| `qb_starts` | int | games where `team_game.starting_qb_id = gsis_id` |
| `first_unit_play_games` | int | 2016+: games with `pp_first_idx = 1` |

## `player_game` — entity `player_game` (one row per snap-count row, season ≥ floor)

| attribute | type | description |
|---|---|---|
| `player_key, gsis_id, pfr_id, player_name` | | |
| `season, week, game_type, is_postseason, game_id, team, opponent, home` | | |
| `position, position_group, unit` | str | |
| `offense_snaps, offense_pct, defense_snaps, defense_pct, st_snaps, st_pct` | | raw |
| `snaps_unit, snap_share_unit, snaps_total` | | D12 |
| `roster_status` | str | `rosters_weekly.status` that week (ACT, RES, …) |
| `years_exp` | int | |
| `is_rookie` | bool | |
| `is_starting_qb` | bool | `gsis_id` equals this team's QB in `schedules`. nflverse's field is the listed starter, which can differ from who took the first snap (2019 W17 BUF lists Barkley; Allen played the first series) — `pp_first_idx = 1` is the first-snap notion |
| `inj_listed` | bool | an `injuries` row for this season/week with `report_status IS NOT NULL OR practice_status IN (DNP, Limited)` |
| `inj_report_status, inj_practice_status, inj_primary_injury` | str | this game's report |
| production stats | | same list as `player_season`, per game, from `player_stats` by `(game_id, gsis_id)` |
| `pp_available` | bool | participation rows exist for this game and unit |
| `pp_plays, pp_team_unit_plays, pp_first_idx, pp_last_idx` | int | |
| `pp_first_frac` | float | `(pp_first_idx - 1) / pp_team_unit_plays` |
| `pp_last_frac` | float | `pp_last_idx / pp_team_unit_plays` |
| `pp_missed_tail_frac` | float | `1 - pp_last_frac` — share of the unit's plays after the player's last one |
| `pp_missed_head_frac` | float | `pp_first_frac` |
| `pp_last_qtr, pp_last_gsr` | int | quarter and `game_seconds_remaining` at last play |
| `recent_snap_share, recent_games` | | mean `snap_share_unit` over the player's previous ≤ 4 games **this season** with `snaps_unit >= 1` (window ordered by week, partition by `player_key, season`) |
| `prior_season_snap_share, prior_season_games` | | from `player_season` at `season - 1` |
| `baseline_share` | float | `coalesce(recent_snap_share, prior_season_snap_share)` |
| `baseline_source` | str | `recent` / `prior_season` / NULL |
| `team_prev_game_id, team_next_game_id` | str | this team's adjacent games incl. postseason, ordered by `(season, week)` |
| `played_team_prev_game, played_team_next_game` | bool | NULL when no such game; true if this `player_key` has `snaps_unit >= 1` in it |
| `reserve_within_3_games` | bool | `rosters_weekly.status = 'RES'` for this `gsis_id` in any of the team's next 3 game weeks |
| `corroboration_available` | bool | `team_next_game_id IS NOT NULL` |

## `entities.py` (exact)

```python
class Entity(StrEnum): game, team_game, team_season, player_game, player_season, play

@dataclass(frozen=True)
class Join: table: str; alias: str; on: str            # on uses the aliases
@dataclass(frozen=True)
class EntitySpec: table: str; alias: str; key: tuple[str, ...]; identifying: tuple[str, ...]; namespaces: dict[str, Join]

ENTITIES = {
 "game":          EntitySpec("game_ctx","g",("game_id",),("season","week","home_team","away_team"),{}),
 "team_game":     EntitySpec("team_game","tg",("game_id","team"),("season","week","team","opponent"),{
                    "game":   Join("game_ctx","g","g.game_id = tg.game_id"),
                    "opp":    Join("team_game","tgo","tgo.game_id = tg.game_id AND tgo.team = tg.opponent"),
                    "season": Join("team_season","ts","ts.season = tg.season AND ts.team = tg.team")}),
 "team_season":   EntitySpec("team_season","ts",("season","team"),("season","team"),{
                    "prior":  Join("team_season","tsp","tsp.season = ts.season - 1 AND tsp.team = ts.team")}),
 "player_game":   EntitySpec("player_game","pg",("player_key","game_id"),("player_name","season","week","team","opponent"),{
                    "cur":    Join("player_season","psc","psc.player_key = pg.player_key AND psc.season = pg.season"),
                    "prior":  Join("player_season","psp","psp.player_key = pg.player_key AND psp.season = pg.season - 1"),
                    "game":   Join("game_ctx","g","g.game_id = pg.game_id"),
                    "team":   Join("team_game","tg","tg.game_id = pg.game_id AND tg.team = pg.team"),
                    "team_season": Join("team_season","ts","ts.season = pg.season AND ts.team = pg.team"),
                    "next":   Join("player_game","pgn","pgn.player_key = pg.player_key AND pgn.game_id = pg.team_next_game_id"),
                    "prev":   Join("player_game","pgp","pgp.player_key = pg.player_key AND pgp.game_id = pg.team_prev_game_id")}),
 "player_season": EntitySpec("player_season","ps",("player_key","season"),("player_name","season","team_primary"),{
                    "prior":  Join("player_season","psp","psp.player_key = ps.player_key AND psp.season = ps.season - 1"),
                    "next":   Join("player_season","psn","psn.player_key = ps.player_key AND psn.season = ps.season + 1"),
                    "team_season": Join("team_season","ts","ts.season = ps.season AND ts.team = ps.team_primary")}),
 "play":          EntitySpec("pbp","p",("game_id","play_id"),("season","week","posteam","defteam","qtr","desc"),{
                    "game":   Join("game_ctx","g","g.game_id = p.game_id"),
                    "off":    Join("team_game","tgo","tgo.game_id = p.game_id AND tgo.team = p.posteam"),
                    "def":    Join("team_game","tgd","tgd.game_id = p.game_id AND tgd.team = p.defteam")}),
}

# D16. Key: (definition entity, plan entity). Value: definition namespace → plan namespace.
# "self" is the definition's own table. Namespaces not listed are not liftable → entity_mismatch.
LIFTS = {
 ("game","team_game"):            {"self": "game"},
 ("game","player_game"):          {"self": "game"},
 ("game","play"):                 {"self": "game"},
 ("team_game","player_game"):     {"self": "team", "game": "game", "season": "team_season"},
 ("team_season","team_game"):     {"self": "season"},
 ("team_season","player_game"):   {"self": "team_season"},
 ("team_season","player_season"): {"self": "team_season"},
 ("player_season","player_game"): {"self": "<basis>", "prior": "prior"},   # basis current_season→"cur", prior_season→"prior" (then "prior" ns is unavailable)
 ("player_season","player_season"): {"self": "<basis>"},                   # basis prior_season → "prior"
}
```

A plan or definition addresses attributes as `name` (self) or `ns.name`.

## `catalog.py` (exact)

```python
@dataclass(frozen=True)
class Attribute: entity: str; name: str; type: Literal["int","float","bool","str","date"]; family: str; description: str
ATTRIBUTES: dict[tuple[str, str], Attribute]         # hand-written for all derived tables
PLAY_DOCS: dict[str, str]                             # curated descriptions for ~40 pbp columns
def attributes_for(entity: str, conn) -> list[Attribute]   # play: generated from PRAGMA table_info(pbp) + PLAY_DOCS
def resolve(entity: str, ref: str, conn) -> ResolvedAttr   # "ns.name" → (namespace, Attribute); raises UnknownAttribute(ref, did_you_mean)
```

Families (used by `propose_definition` search): `identity, context, result,
betting, weather, snaps, roster, injury, participation, baseline, linkage,
production, contract, draft, epa, situation`.

`PLAY_DOCS` must cover at least: `down, ydstogo, yardline_100, qtr,
game_seconds_remaining, half_seconds_remaining, score_differential, play_type,
pass, rush, epa, wpa, wp, success, air_yards, yards_after_catch, yards_gained,
touchdown, interception, fumble_lost, sack, penalty, fourth_down_converted,
fourth_down_failed, third_down_converted, field_goal_result, two_point_attempt,
posteam, defteam, passer_player_id, rusher_player_id, receiver_player_id,
shotgun, no_huddle, qb_dropback, qb_scramble, pass_length, pass_location,
run_location, run_gap, desc`.

`test_catalog_complete.py`: every column of every derived table has an
`ATTRIBUTES` entry. This test failing is the build being incomplete.

## Gate A (`tests/data/test_gate_a.py`)

For each case in `tests/fixtures/exits.yaml` find the `player_game` row by
`(player_name, season, week, team)` and assert:

- `snaps_unit == case.snaps_unit` exactly (these were read from the data).
- For `expect_exit: true` cases with `season >= 2016`: `pp_available` and
  `pp_missed_tail_frac >= 0.25`. Exception `cousins_2023_w8`: `0.02 <=
  pp_missed_tail_frac <= 0.20`.
- For `expect_exit: true`: `baseline_share >= 0.5` — **except `chubb_2023_w2`**
  at 0.49 (amended 2026-09-08), whose recent window is one game at 49% against a
  0.564 prior season. A position-blind floor is wrong for running backs; the
  threshold was not lowered. And at least one of:
  `next.inj_listed` (join the next row), `reserve_within_3_games`,
  `played_team_next_game = false`.
- For the rested cases: `played_team_next_game = true` — **except
  `hainsey_2022_w18`** (amended 2026-09-08). He did not play the wild-card game:
  Ryan Jensen returned from a season-long injury and took the job back. He is a
  named false positive for the shipped composite, not a rested starter the
  corroboration catches.
- For the backup cameos: not started — `is_starting_qb = false` and
  (`pp_first_idx IS NULL OR pp_first_idx <> 1`). (Bridgewater 2019 W17 has
  `corroboration_available = true` and `baseline_share ≈ 0.52`, so neither of
  those would exclude him; starting is what does.)
- For `evans_2020_w17`: `played_team_next_game = true` **and** `next.inj_listed
  = true` — the report-only corroboration path.

On failure print the full row. The data wins over the fixture's football fact
only after the join has been checked; see the first-run protocol in phase 9.

## Acceptance

```bash
uv run chalktalk build
uv run pytest tests/unit/test_catalog_complete.py tests/unit/test_entities.py
uv run pytest -m data tests/data/test_features.py tests/data/test_gate_a.py
uv run chalktalk coverage player_game --column pp_last_frac     # 2016..2025
```

Spot checks (`raw_sql` or duckdb CLI): Rodgers 2023 W1 → `snaps_unit 4`,
`pp_missed_tail_frac > 0.9`, `played_team_next_game false`,
`reserve_within_3_games true`. Trent Williams 2023 W18 → `played_team_next_game
true`. A.J. Brown 2023 W18 → `team_next_game_id` is the 2023 week-19 game and
`played_team_next_game false`.

Performance: `build_all` under 3 minutes on a laptop. If `player_game` is slow,
the culprit is usually a correlated subquery for next-game or recent-window
logic; use window functions over `team_game` / `player_game` instead.

## Pitfalls

- Postseason week numbering agrees across `schedules`, `rosters_weekly`,
  `injuries`, `snap_counts` within each era (verified: playoffs are weeks
  18–21 through 2020 and 19–22 from 2021). Never test `week >= 19`; use
  `game_type` (D24).
- Bye weeks: `team_next_game_id` comes from ordering, not `week + 1`.
- A player on both units in one game: `unit` by `position_group` decides.
- Traded players: `team_next_game_id` is the *team's* next game; a traded
  player shows `played_team_next_game = false`. Accept and document in the
  attribute description.
- `recent_snap_share` must not cross seasons: partition by `(player_key,
  season)`, order by `week` (postseason weeks sort after the final REG week in
  both eras).
- `spread_line` sign: check one famous game with a known favourite and write
  the finding into the catalog description and `00-index.md` amendments.

## Done when

- [ ] Every derived table builds; catalog test green; Gate A green.
- [ ] `phase-4:` commits; ledger updated; amendments recorded.
