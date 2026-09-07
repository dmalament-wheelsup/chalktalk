# chalktalk — implementation plan

Authored 2026-09-07 against **nflreadpy 0.1.5, polars 1.44, duckdb 1.5.5** and the
nflverse-data releases as of that date. Every upstream fact in this plan that says
*verified* was checked by loading the data in a script, not recalled from memory.

This plan implements [`CLAUDE.md`](../../CLAUDE.md). It is written so that any
capable coding model can pick up any phase in a fresh session with no context
beyond `CLAUDE.md`, this index, and the phase file. Read
[`docs/decision-history.md`](../decision-history.md) only if you are tempted to
change the architecture — it records why the obvious alternatives were rejected.

---

## The shape of the system (read this first)

The product is not an answer to any particular question. It is a mechanism by
which a user, mid-conversation, turns a fuzzy football word into a precise,
saved, reusable definition — and a query engine that refuses to run until every
fuzzy word has been through that mechanism. Four layers, bottom to top:

1. **Feature layer** (phase 4). Derived tables that expose many *attributes* per
   entity: per game, per team-game, per team-season, per player-game, per
   player-season, and per play. Snaps, shares, baselines, roster status, injury
   listings, contracts, draft, production stats, game context, pbp aggregates,
   "what happened next" linkage. Every attribute has a description and a
   coverage window. This is the universe of *what is computable*.

2. **Five general signals** (phase 5). The only ways a definition can be
   expressed: `rule` (attribute op value, and/or/not), `percentile` (rank of an
   attribute within a cohort), `rank` (top-N within a cohort), `delta` (an
   attribute against a baseline attribute), `composite` (and/or/not of other
   definitions). No signal knows what a "star" or an "injury" is.

3. **Definitions** (phases 5, 7). Named, versioned, saved specs built from the
   five signals. The shipped vocabulary — `early_exit`, `star_by_snaps`,
   `blowout`, `rookie`, … — is expressed in exactly the same way a user's
   definitions are. If a shipped concept ever needs code of its own, the
   feature layer is missing an attribute; fix that instead.

4. **Gate, compiler, envelope** (phase 6). A structured query plan referencing
   attributes and terms; a gate that rejects unresolved terms and coverage gaps
   structurally; a compiler to SQL; a result envelope with definitions used,
   seasons covered, matched-row sample, and the SQL.

The injury-exit question in `CLAUDE.md` is one example. The **generality
suite** in phase 7 is ten unrelated questions — fourth-down aggression, rookie
QBs in primetime, favourites losing blowouts, RB usage after a heavy season —
that must all run with definitions only. That suite, not the injury example,
is the acceptance test for the architecture.

---

## How to use this plan in a session

1. Read `CLAUDE.md`, then this file top to bottom, then the phase file you are
   working on. Nothing else is required.
2. Look at the **Status ledger**. Work the first phase that is not `done`. Never
   start a phase whose prerequisites are not `done`.
3. Every phase file has the same sections: *Goal · Prerequisites · Deliverables ·
   Steps · Interfaces · Acceptance · Pitfalls · Done when*. Interfaces are exact:
   function signatures, table columns, JSON shapes. Match them — later phases
   depend on the names.
4. Run the phase's **Acceptance** commands before marking it done. They are the
   definition of done.
5. When a phase is done: update its ledger row (status, date, commit hash), and
   commit with the message prefix `phase-N:`.
6. **If the plan is wrong, fix the plan.** A column that doesn't exist, a count
   that differs, a loader that changed — correct the phase file in the same
   commit as the code, and add a line under **Amendments** at the bottom of this
   file. The next session trusts this document; keep it true.
7. Do not reopen anything under **Resolved decisions**. If you believe one is
   wrong, write the case in `docs/decision-history.md` first, then change the
   plan, then the code — in that order.
8. Commit small and often: one commit per completed step, tests green at each.
   `uv run pytest` (unit tier) must pass before every commit; the `data` tier
   is required where a phase's acceptance says so.

## Phases

| # | File | Goal | Depends on | Sessions |
|---|------|------|------------|----------|
| 1 | [01-scaffold.md](01-scaffold.md) | Package, tooling, config, paths, CLI skeleton | — | 0.5 |
| 2 | [02-ingest.md](02-ingest.md) | Raw nflverse tables into one DuckDB file, atomically | 1 | 1–2 |
| 3 | [03-coverage.md](03-coverage.md) | Coverage registry generated from the data | 2 | 0.5 |
| 4 | [04-features.md](04-features.md) | Feature layer: six entity tables and the attribute catalog. **Gate A**: fixture attributes look right | 2, 3 | 2–3 |
| 5 | [05-definitions.md](05-definitions.md) | Definition spec, store, the five signals, vocabulary catalog, propose | 4 | 2 |
| 6 | [06-query.md](06-query.md) | Query plan, gate, compiler, executor, envelope | 5 | 1–2 |
| 7 | [07-vocabulary.md](07-vocabulary.md) | Shipped definitions. **Gate B**: injury-exit fixtures and the generality suite pass end to end | 6 | 1 |
| 8 | [08-server.md](08-server.md) | MCP server, tool surface, raw SQL escape hatch, audit log | 7 | 1 |
| 9 | [09-testing.md](09-testing.md) | Test tiers, fixtures, CI — applies to every phase; read at phase 1 | 1 | ongoing |
| 10 | [10-release.md](10-release.md) | Packaging, rebuild procedure, docs sync, v1.1 backlog | 8 | 0.5 |

Gates A and B exist because `CLAUDE.md` is explicit: if the system cannot find
the injury exits already known by name, nothing built on top of it is worth
having. Gate A checks the raw attributes at the feature layer; Gate B checks
the same cases through definitions, compiler and SQL — and then checks that
the same machinery answers ten unrelated questions.

## Status ledger

| Phase | Status | Date | Commit | Notes |
|-------|--------|------|--------|-------|
| 1 | done | 2026-09-07 | e753384 | |
| 2 | done | 2026-09-07 | 64d27b1 | 6 amendments; 719 MB artifact in ~60s |
| 3 | done | 2026-09-07 | _pending_ | 4 amendments; 808 columns registered |
| 4 | not started | | | Gate A |
| 5 | not started | | | |
| 6 | not started | | | |
| 7 | not started | | | Gate B |
| 8 | not started | | | |
| 9 | in progress | 2026-09-07 | | tiers + unit CI in place; fixtures and mini DB land with phases 4/7 |
| 10 | not started | | | |

Statuses: `not started` · `in progress` · `blocked (reason)` · `done`.

---

## Global conventions

**Language and tooling.** Python ≥ 3.12. `uv` for environments and running
(`uv run …`). `src/` layout. `ruff` for lint and format. `pytest`. Type hints
everywhere; `pydantic` v2 for every JSON-shaped object.

**Names.** Package and CLI are both `chalktalk`. Environment variable
`CHALKTALK_HOME` (default `~/.chalktalk`):

```
$CHALKTALK_HOME/
  data/
    nfl-YYYYMMDD.duckdb     one immutable artifact per build
    CURRENT                 text file: the filename the server should open
  definitions/
    <name>.json             one definition per file — the user's work, never inside the .duckdb
    .history/<name>/v<N>.json
  logs/audit.jsonl          every query() and raw_sql() call
  cache/                    nflreadpy download cache
```

**Package layout** (created across phases; names are contracts):

```
src/chalktalk/
  cli.py  config.py  paths.py  db.py  log.py                    phase 1
  ingest/    registry.py loaders.py normalize.py build.py       phase 2
  coverage.py                                                   phase 3
  features/  xwalk.py game_ctx.py team_game.py team_season.py   phase 4
             player_play.py player_game.py player_season.py
             catalog.py            attribute docs for every derived column
  entities.py                      entity → table, namespaces, joins  phase 4
  definitions/ spec.py store.py explain.py propose.py vocabulary.py   phase 5
             signals/ base.py rule.py percentile.py rank.py delta.py composite.py
             shipped/*.json                                     phase 7
  query/     plan.py gate.py compile.py execute.py envelope.py  phase 6
  server.py  sqlguard.py  audit.py                              phase 8
```

**Tables.** Raw (named after the nflverse dataset): `pbp`, `snap_counts`,
`participation`, `injuries`, `rosters_weekly`, `depth_charts`, `player_stats`,
`schedules`, `players`, `contracts`, `draft_picks`, `teams`. Derived:
`player_id_xwalk`, `game_ctx`, `team_game`, `team_season`, `player_play`,
`player_game`, `player_season`. Registry: `coverage_columns`,
`coverage_seasons`, `season_status`, `ingest_log`, `build_info`.

**Entities**: `game`, `team_game`, `team_season`, `player_game`,
`player_season`, `play`. Each has a base table and fixed namespaces (phase 4).

**Signals**: `rule`, `percentile`, `rank`, `delta`, `composite`. Nothing else.

**MCP tools**: `describe_schema`, `coverage`, `list_definitions`,
`get_definition`, `propose_definition`, `save_definition`, `delete_definition`,
`explain_query`, `query`, `raw_sql`, `build_status`.

**Error codes** (every tool error is `{"error": <code>, …}`):
`unresolved_term`, `unknown_attribute`, `definition_broken`, `entity_mismatch`,
`coverage_gap`, `invalid_plan`, `invalid_definition`, `sql_rejected`,
`sql_timeout`, `no_database`.

**Data conventions.**
- `season` and `week` are `INTEGER` in every table. Cast at ingest.
- `game_type` ∈ `REG WC DIV CON SB` (from `schedules`). `is_postseason =
  game_type <> 'REG'` — never a week-number test. Week numbering changed with
  the 17-game season (D24): through 2020 the regular season is weeks 1–17 and
  the playoffs 18–21; from 2021 it is 1–18 and 19–22.
- `gsis_id` (`00-0012345`) is the canonical player id. Snap counts are keyed by
  PFR id and are crosswalked (D6). `player_key = coalesce(gsis_id, 'pfr:' || pfr_id)`.
- `position_group` ∈ `QB RB WR TE OL DL LB DB SPEC`. `unit` is `offense` for
  QB/RB/WR/TE/OL, `defense` for DL/LB/DB, `special` for SPEC.
- "Snaps" unqualified means `snaps_unit` (offense snaps for offense-unit
  players, defense snaps for defense-unit players, special teams excluded).
  `snaps_total` includes special teams. The attribute name is the decision.
- Whitespace-only strings are `NULL`.

**Rules that never bend.**
- Definitions store specs, never SQL.
- Coverage numbers are never hand-written anywhere the code reads. They come
  from `coverage_columns` at build time.
- The `query` tool cannot execute a plan with an unresolved term. Enforced in
  code (phase 6 gate), not by tool-description wording.
- No concept gets its own signal. If a concept can't be expressed with the five
  signals, add an attribute to the feature layer.
- `raw_sql` runs read-only, external access off, row cap, timeout, logged.
- The `.duckdb` file is disposable. Nothing the user authored is inside it.

---

## Resolved decisions (do not reopen)

**D1 — nflverse license and attribution.** `nflverse-data/LICENSE.md` is
Creative Commons **Attribution 4.0 International** — plain BY, not ShareAlike
(verified by reading the file). Attribution is the only obligation; the README
carries it. The underlying NFL data belongs to its owners; the project claims
no rights to it and distributes none of it. A courtesy note to nflverse is
optional and not an implementation item.

**D2 — "Star player" ships as three named definitions and one deliberate
gap.** `star_by_snaps` (prior-season `snap_share_mean` ≥ 90th percentile within
`(season, position_group)`), `star_by_contract` (prior-season `apy_cap_pct` ≥
90th percentile within `(season, position_group)`), `star_by_draft`
(`draft_round = 1`). All three are ordinary `percentile`/`rule` definitions. The
term `star_player` is **not** shipped, so the gate fires on first use and
`propose_definition` offers the three; the user's pick is saved as
`star_player` via `save_definition(copy_of=…)`. Pro Bowl / All-Pro is not
computable: nflverse has no per-season honours table (`draft_picks.probowls`
is a career total — verified).

**D3 — Community definitions repository.** Not built in v1. The store is one
JSON file per definition with `provenance`, plus `chalktalk defs import`, so a
community repo is just a git repository of those files. Nothing else is needed.

**D4 — Participation coverage is 2016–2025.** Verified: `offense_players` is
populated on 100% of plays in 2023, 2024, 2025 (92% in 2016). `CLAUDE.md` said
2016–2024; corrected. The coverage registry is the runtime authority.

**D5 — `player_stats` moved upstream; pin nflreadpy ≥ 0.1.5.** The
`player_stats` release tag stops at 2024, but `load_player_stats([2025])`
returns 19,422 × 150 (verified) because 0.1.5 reads the successor release.
Column names differ between the old and new upstream schemas — phase 2 records
which names the loader actually returns and phase 4 uses only those.

**D6 — Player id crosswalk.** `snap_counts.pfr_player_id → players.pfr_id →
gsis_id` resolves 99.9% / 99.8% / 100% / 99.7% of distinct ids in 2013 / 2019 /
2023 / 2025 (verified). `rosters_weekly.pfr_id` is 64% populated and is not the
crosswalk. Unresolved players are kept under a `pfr:` key.

**D7 — nfl-mcp's ingest is lossless enough to vendor, with one replacement.**
It registers polars → Arrow → DuckDB directly. Its schema-drift path maps any
non-scalar dtype to `VARCHAR`; ours derives drift types from Arrow. Its `_str()`
helper only feeds a text column we don't build. Column renaming (`.`, space,
`-` → `_`) is kept.

**D8 — `query` takes a structured plan, not natural language.** The server
has no model in it. The calling model parses the user's question into a
`QueryPlan`; fuzzy concepts can only appear as `{"term": …}` and raw thresholds
as attribute rules. The gate is a lookup. The plan carries the original
question text for the audit log and envelope only.

**D9 — Definitions store format.** A directory of JSON files, one per
definition, previous versions moved to `.history/`. Not SQLite: diffable,
hand-editable, `git init` gives history and backup for free.

**D10 — Broken definitions are quarantined, not fatal.** Every definition is
compiled against the current schema and coverage on load. Failures are listed
loudly at startup and in `list_definitions`; a query referencing one fails
with `definition_broken`. The server still starts.

**D11 — Coverage gaps refuse by default.** `coverage_gap` names the limiting
term/attribute and the covered range. `allow_partial_coverage: true` runs on
the covered subset; the envelope lists excluded seasons.

**D12 — Special teams.** Excluded from `snaps_unit`, included in
`snaps_total`. No hidden flag.

**D13 — Percentiles and ranks are computed at query time.** `percentile` and
`rank` compile to window functions over the cohort the definition names, with
an `eligible` rule (default for player-season cohorts: `games_played_share >=
0.5`, i.e. half the team's games — era-neutral; overridable per definition). Nothing is precomputed, so any numeric attribute
can be ranked within any cohort without a rebuild. DuckDB handles this at this
scale in milliseconds.

**D14 — Early exit is "left and did not return", not "low snaps".** Kirk
Cousins tore his Achilles in Q4 of 2023 W8 after 61 snaps (85%). Low-snap
heuristics miss him; participation data (last play on the field) does not.
The shipped `early_exit` is a composite: played ∧ regular ∧ (left-early-by-
participation ∨ snap-drop) ∧ corroborated. Corroboration is any of: listed on
the next game's injury report, on the reserve list within three games, or
*missed the next game having started this one* — the last qualifier exists
because Teddy Bridgewater 2019 W17 (backup, 11 snaps, sat the wild-card game
behind a healthy Brees) would otherwise count. Every piece is its own
definition a user can swap.

**D15 — Rested starters.** Final-week rest (week 17 through 2020, week 18
from 2021) looks like an exit in snap counts (Trent Williams 2023 W18: 12
snaps vs a 90% average — verified). The
distinguishing fact is the player plays the next game. Corroboration handles
it. When there is no next game the case is uncorroborable and excluded by the
shipped definition; a user who wants season-enders composes a variant. Matched
rows are always returned.

**D16 — Entity lifting.** A definition declares its entity. Coarser entities
lift into finer ones through fixed namespaces: `game` → `team_game`,
`player_game`, `play`; `team_game` → `player_game`; `team_season` →
`team_game`, `player_game`, `player_season`; `player_season` → `player_game`
(with a `basis` of `current_season` or `prior_season`). A `player_game`
definition is usable only there; a `play` definition only on `play`.

**D17 — Weekly rebuild is a documented command.** `chalktalk build` is
idempotent and atomic; a cron/launchd example is in phase 10. No scheduler.

**D18 — Transport is stdio only.** Self-run, one user, no auth, no HTTP.

**D19 — MCP elicitation is not used in v1.** Plain structured errors.

**D20 — Season floor 2013; baseline sources pull 2012.** `snap_counts`,
`rosters_weekly`, `player_stats` ingest from 2012 so prior-season attributes
exist for 2013. `season_status` starts at 2013; 2012 is never a queryable
season.

**D21 — `pbp` is stored whole and exposed as the `play` entity.** ~370 columns
× ~1.5M rows is under 2 GB. The raw-SQL hatch and the `play` entity are only as
valuable as the data behind them.

**D22 — No concept-specific code.** The signal set is closed at five. A
"concept" is a definition. The test is phase 7's generality suite: ten
unrelated questions with no code changes.

**D24 — Era changes are data, never constants.** The 17-game season (2021),
the 14-team playoff (2020), and any future change must not appear as a number
in code or in a definition. Verified from the data: through 2020 the regular
season is weeks 1–17 with playoffs 18–21 and 16 games per team; from 2021 it
is weeks 1–18, playoffs 19–22, 17 games — and `schedules`, `snap_counts`,
`injuries` and `rosters_weekly` all agree within each era. The feature layer
therefore exposes era-neutral attributes: `season_status.reg_weeks` and
`games_per_team`; `game_ctx.is_final_reg_week` and `weeks_remaining_reg`;
`team_game.games_played_before` and `season_progress`; `player_season.
games_played_share` and `*_per_game` rates for every production total;
`team_season.*_per_game`. Percentile cohorts are per season, so they are
era-neutral by construction; absolute totals are not, which is why per-game
rates exist. Anything that says "week 18" or "16 games" in a definition is a
bug in the vocabulary, not a fact about football.

**D23 — `started` is derived from data, not depth charts.** `is_starting_qb`
comes from `schedules.home_qb_id/away_qb_id` (all seasons). For other
positions, `pp_first_idx = 1` (on the field for the team's first unit play,
2016+). Depth charts are ingested but no v1 attribute depends on them — their
upstream schema changed across seasons and is a rabbit hole (phase 10 backlog).

---

## Corrections this plan makes to `CLAUDE.md`

- Participation coverage: 2016–2024 → **2016–2025** (D4).
- `query(question)` → the tool takes a structured plan (D8).
- The "Open questions" section is answered by D1–D3; `CLAUDE.md` points here.

---

## Verified upstream facts (2026-09-07)

| Release | Seasons | Notes |
|---------|---------|-------|
| `pbp` | 1999–2025 | 2023: 49,665 plays × 372 cols |
| `pbp_participation` | 2016–2025 | no `season` column; derive from `nflverse_game_id` |
| `snap_counts` | **2013**–2025 | keyed by `pfr_player_id`; ~26.5k rows/season. The loader accepts 2012 and returns 0 rows — the release starts at 2013 (amended 2026-09-07) |
| `injuries` | 2009–2026 | weeks 1–19+; 2026 file already exists |
| `weekly_rosters` | 2002–2026 | loader `load_rosters_weekly` |
| `depth_charts` | 2001–2026 | ingested, unused in v1 (D23) |
| `player_stats` | 1999–2024 as a tag | loader serves 2025 (D5) |
| `contracts` | single file | 52,687 rows; two `List(Struct)` columns |
| `draft_picks` | single file | 12,927 rows |
| `players` | single file | 24,828 rows |
| `schedules` | single file | 2023: 285 games × 46 cols |

Loader signatures (nflreadpy 0.1.5): seasonal loaders take
`seasons: int | list[int] | bool | None` (`True` = all). `load_contracts()`,
`load_players()` take nothing. `load_player_stats(seasons, summary_level='week')`.
`nflreadpy.get_current_season()` returned 2025 on 2026-09-07.
`nflreadpy.config.update_config(cache_mode=…, cache_dir=…, cache_duration=…)` exists
(on `nflreadpy.config`, **not** the package root);
default cache mode is in-memory.

Value sets: `rosters_weekly.status` ∈ {ACT, DEV, RES, INA, CUT, RET, EXE, …};
`RES` is the reserve list. `injuries.report_status` ∈ {Out, Doubtful,
Questionable, NULL}; `injuries.practice_status` ∈ {Did Not Participate In
Practice, Limited Participation in Practice, Full Participation in Practice}
plus whitespace junk that must be nulled. `snap_counts.position` is
PFR-granular (`C CB DE DT FB FS G K LB LS NT P QB RB SS T TE WR`).
`schedules.weekday` ∈ {Sunday, Monday, Thursday, Friday, Saturday};
`schedules.gametime` is `'HH:MM'` Eastern.

Join keys: `participation.nflverse_game_id = pbp.game_id` and `play_id` match
100% of 2023 rows (both `Float64` upstream — cast to `BIGINT`).

Week numbering by era (verified on `schedules`, `snap_counts`, `injuries`,
`rosters_weekly` for 2013, 2020, 2021, 2023):

| seasons | REG weeks | WC | DIV | CON | SB | games/team | WC games |
|---|---|---|---|---|---|---|---|
| 2013–2019 | 1–17 | 18 | 19 | 20 | 21 | 16 | 4 |
| 2020 | 1–17 | 18 | 19 | 20 | 21 | 16 | 6 |
| 2021– | 1–18 | 19 | 20 | 21 | 22 | 17 | 6 |

---

## Amendments

_(Sessions append here: date · phase · what was wrong · what changed.)_

- **2026-09-07 · phase 2 · `update_config` is not on the package root.** The plan
  said `nflreadpy.update_config(...)`; it lives at
  `nflreadpy.config.update_config`. `CacheMode.FILESYSTEM` is the filesystem
  member. Corrected here and in 02-ingest.md.

- **2026-09-07 · phase 2 · `snap_counts` starts at 2013, not 2012.** The loader's
  own guard says 2012–2025, but `load_snap_counts([2012])` returns 0 rows and
  `load_snap_counts(True)` has `min(season) = 2013`. **Consequence for D20:**
  there is no prior-season snap baseline for the 2013 season, so a
  `prior_season` snap definition (`star_by_snaps`) is uncomputable for 2013 and
  first has data in 2014. `rosters_weekly` and `player_stats` do have 2012 and
  are unaffected. Phase 3's coverage registry is the runtime authority and will
  report this from the data; phase 4 must not assume a 2013 snap baseline
  exists. A data-tier test pins `min(snap_counts.season) = 2013`.

- **2026-09-07 · phase 2 · `player_stats` column names are identical for 2013
  and 2025 (D5 confirmed).** 150 columns both seasons. The names are the
  *successor* schema — phase 4 must use these, not the pre-2025 ones:
  `player_id, player_name, player_display_name, position, position_group,
  headshot_url, season, week, season_type, game_id, team, opponent_team` then
  `completions, attempts, passing_yards, passing_tds, passing_interceptions,
  sacks_suffered, sack_yards_lost, sack_fumbles, sack_fumbles_lost,
  passing_air_yards, passing_yards_after_catch, passing_first_downs,
  passing_epa, passing_cpoe, passing_2pt_conversions, pacr, passing_10,
  passing_16, passing_20, passing_40, carries, rushing_yards, rushing_tds,
  rushing_fumbles, rushing_fumbles_lost, rushing_first_downs, rushing_epa,
  rushing_2pt_conversions, rushing_10, rushing_12, rushing_20, rushing_40,
  receptions, targets, receiving_yards, receiving_tds, receiving_fumbles,
  receiving_fumbles_lost, receiving_air_yards, receiving_yards_after_catch,
  receiving_first_downs, receiving_epa, receiving_2pt_conversions,
  receiving_10, receiving_16, receiving_20, receiving_40, racr, target_share,
  air_yards_share, wopr, special_teams_tds`, the `def_*` block
  (`def_tackles_solo … def_2pt_made`), the fumble/penalty block, the kicking
  block (`fg_*`, `pat_*`, `gwfg_*`), the punting block (`pt_*`), and
  `fantasy_points, fantasy_points_ppr`. Note `team`/`opponent_team`, **not**
  `recent_team`; `carries`, **not** `rushing_attempts`; `passing_interceptions`
  and `sacks_suffered` on the passing side.

- **2026-09-07 · phase 2 · `depth_charts` lost `season` and `week` upstream in
  2025.** 2013–2024 is 15 columns keyed by season/week; 2025 is a 554,215-row,
  12-column ESPN-shaped table (`dt, team, player_name, espn_id, gsis_id,
  pos_grp_id, pos_grp, pos_id, pos_name, pos_abb, pos_slot, pos_rank`) with no
  season at all. Rows with a NULL season are invisible to the per-season
  `DELETE`, so a rebuild would have duplicated them: the build now stamps the
  requested season onto any `by_season` frame that arrives without one, and a
  data-tier test asserts no `by_season` table has a NULL season. This reinforces
  D23 — nothing in v1 should depend on `depth_charts`.

- **2026-09-07 · phase 2 · upstream contradicts itself on four column types;
  they are now recorded in the artifact.** `INSERT ... BY NAME` casts silently,
  so the column type was decided by whichever season happened to be ingested
  first. A new `type_conflicts` table (`dataset_id, table_name, season,
  column_name, stored_type, incoming_type`) records every disagreement, and the
  build warns once per column. As of this build:
  `rosters_weekly.jersey_number` and `rosters_weekly.draft_number` (VARCHAR
  through 2015, INTEGER from 2016 — stored VARCHAR, the union type),
  `rosters_weekly.height` (DOUBLE, INTEGER in 2025), and `pbp.goal_to_go`
  (INTEGER, DOUBLE in three seasons from 2020). Phase 4 must cast
  `jersey_number`/`draft_number` rather than assume a number. The lossless test
  fails on any coercion to text that is *not* in `type_conflicts`.

- **2026-09-07 · phase 3 · `queryable = season >= floor` is not enough — `schedules`
  already carries next season.** The single `schedules` file contains 2026: 272
  regular-season games scheduled, none played, no postseason rows. Under the
  plan's rule 2026 would be queryable with no data behind it at all. Corrected
  to `queryable = season >= floor AND at least one game has a final score`,
  which is still derived entirely from the data (D24). The phase 3 acceptance
  line "`season_status` has every season from floor to current with
  `queryable = true`" is amended accordingly: every season from the floor to the
  *latest played* season is queryable, and a scheduled-but-unplayed season is
  present with `complete = false, in_progress = false, queryable = false`.

- **2026-09-07 · phase 3 · `complete` spans the whole season, not just REG.**
  The plan's `complete = final = scheduled AND scheduled > 0` does not say which
  games. It counts every game type: a season is complete when every scheduled
  game, postseason included, has a final score. `reg_games_scheduled`,
  `reg_games_final` and `post_games_final` remain as stored detail.

- **2026-09-07 · phase 3 · registry tables are excluded from coverage.**
  `ingest_log`, `type_conflicts` and `build_info` have a `season` column but
  describe the build, not the football. `coverage.REGISTRY_TABLES` is the list;
  keep it current when a registry table is added. Coverage of a partial build
  that has no `schedules` (`chalktalk build --only pbp`) leaves `season_status`
  empty rather than guessing, and `Coverage.queryable_seasons()` is then empty,
  so nothing is queryable.

- **2026-09-07 · phase 3 · the registry immediately earns its keep.** Findings
  from the first real run, none of which were known when the plan was written:
  `participation.ngs_air_yards` stops at **2022** (a definition using it silently
  answers nothing for 2023–2025); four `pbp` columns and `draft_picks.car_av`
  are present in the schema but never populated, so `Coverage.intersect` returns
  None for them and the gate must refuse rather than return zero; and several
  `pbp` tackle-assist columns have real gaps (7 non-null rows across 5
  scattered seasons). This is why coverage is generated, never hand-maintained.

- **2026-09-07 · phase 2 · the full build takes about a minute, not 10–25.**
  13 seasons of everything including `pbp` is ~59s warm and ~82s cold on a
  laptop; the artifact is 719 MB. 02-ingest.md's acceptance note is corrected.

---

## Glossary

- **attribute** — a column of an entity table, documented in the catalog.
- **term** — a name in a plan that must resolve to a definition.
- **definition** — a saved, versioned `{entity, signal, params}` with provenance.
- **signal** — one of the five general ways a definition is expressed.
- **entity** — the row type a plan is about.
- **namespace** — a fixed join from an entity to a related row (`prior.`, `game.`, `next.`).
- **lift** — using a coarser entity's definition in a finer entity's plan (D16).
- **cohort** — the partition a percentile/rank is computed within.
- **coverage** — first and last season for which an attribute/definition has data.
- **corroboration** — next-game evidence that an inferred exit was an injury.
- **envelope** — rows + definitions used + coverage + sample + SQL.
