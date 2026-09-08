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
| 3 | done | 2026-09-07 | 2eb48b7 | 4 amendments; 808 columns registered |
| 4 | done | 2026-09-08 | d2f1725 | Gate A green; 241 attributes; staged 4a–4d |
| 5 | done | 2026-09-08 | b66cf4b | 5 signals, store, propose; mini DB landed |
| 6 | done | 2026-09-08 | 011a504 | gate + compiler + envelope; 10/10 plans compile |
| 7 | done | 2026-09-08 | b104b44 | Gate B: 19/21 exits, 10/10 plans on real data |
| 8 | done | 2026-09-08 | _pending_ | 11 tools, sql guard, audit log |
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

- **2026-09-08 · phase 8 · the guard is the second fence, not the first.** The
  plan's forbidden-keyword list does not cover `read_csv` and friends, and it
  should not: listing every catalog function that touches the filesystem is a
  losing game. The boundary is `open_ro` from phase 1. A test now states this
  layering explicitly — the text guard *permits* `select * from read_csv(...)`,
  and a real sandboxed connection refuses it — so nobody later mistakes the
  guard for the fence. The mini database is a plain in-memory connection and
  would happily read a file, which is exactly why that test needs a real one.

- **2026-09-08 · phase 8 · `build_info` is absent from a hand-made database.**
  It is written by the ingest build, not by `features.build_all`, so any server
  pointed at a database assembled another way (the mini one, a restored subset)
  crashed on `build_status` and on every `query`, which embeds it. Now tolerated.

- **2026-09-08 · phase 8 · the stdio server was driven end to end.** Not just
  in-process: a subprocess speaking JSON-RPC over stdin/stdout initialises,
  reports 45 definitions across 2013–2025, and refuses the headline question
  with the three `star_by_*` candidates. stdout carried nothing but protocol;
  logging went to stderr.

- **2026-09-08 · phase 7 · Gate B: 19 of 21 exit fixtures agree, and the two
  that do not are a genuine trade-off, not a bug.** Measured on the real build:

  | variant | correct | gets wrong |
  |---|---|---|
  | shipped `early_exit` | 19/21 | misses Chubb, matches Hainsey |
  | stricter corroboration | 19/21 | matches Hainsey correctly, **loses A.J. Brown** |
  | looser `regular` (0.4) | 20/21 | still matches Hainsey |

  `chubb_2023_w2` fails `regular` (`baseline_share >= 0.5`) at 0.49, because a
  single threshold across every position is the wrong shape for running backs.
  `hainsey_2022_w18` matches because he started, was rested, and missed the next
  game — and no attribute distinguishes "displaced by a returning teammate" from
  "injured". **Neither threshold was tuned**; both cases are asserted explicitly
  with the variant that changes each, because which trade-off to take is the
  user's call. This is why matched rows are always returned.

- **2026-09-08 · phase 7 · `star_by_snaps` barely discriminates among
  quarterbacks, and the fixture was corrected rather than the definition.**
  In 2022, 34 eligible QBs had a median snap share of 0.965, so the top decile
  is four players at 0.992+; a 17-game starter at 0.974 is not one. Offensive
  linemen are worse — the cutoff is a perfect 1.000. Running backs (median
  0.322, cutoff 0.648), receivers and defensive linemen behave as intended.
  `stars.yaml` originally asserted that full-time starting quarterbacks were
  `star_by_snaps`; that expectation was about *availability*, not a
  within-position percentile. D2 is a resolved decision and was **not** changed;
  the evidence and three options are written up in `docs/decision-history.md`
  for whoever revisits it. Two of the fixture's sharpest predictions held
  untouched: Watt 2017 (2016 was 3 of 16 games, below the eligibility floor) and
  Burrow 2023 by contract (2022 was still the rookie deal).

- **2026-09-08 · phase 7 · `any_of` branches are preferred, not required.**
  The plan's acceptance says `early_exit` should show coverage **2013–2025** with
  a warning about participation, but a composite that required every branch's
  attributes gave 2016–2025 and refused 2013 outright. Inside an `any_of` one
  branch suffices, so a branch whose attribute has no data weakens the answer
  rather than making it impossible. `CompositeSignal.requires` now stops at an
  `any_of` and reports those refs through `prefers` instead. Two consequences:
  the gate must compute coverage from the plan's **own** terms, not from the
  flattened transitive list (which reinstates every branch as hard), and a lift
  failure during compilation now becomes an `entity_mismatch` envelope rather
  than escaping as an exception.

- **2026-09-08 · phase 7 · the generality suite answers on real data.** All ten
  plans run: fourth-down aggression over 3,039 matched plays and rising as
  anyone following the sport would expect, 180 favourites blown out across 13
  seasons, 110 star-exit player-games. Plan 8 matches **3** rows in 13 seasons —
  correctly: the NFL schedules Thursday games with both teams on four days' rest,
  so "short week against a rested opponent" barely happens. The envelope's
  matched total is what makes that visible rather than mysterious.

- **2026-09-08 · phase 6 · the shipped vocabulary landed here, not in phase 7.**
  Phase 6's acceptance requires the ten generality plans to compile "with the
  shipped definitions loaded from `definitions/shipped/`", so they had to exist.
  31 definitions now ship, installed by `chalktalk defs install`. **All ten plans
  parse, gate, compile and execute**, and every filter in them is a term or an
  attribute rule — no code was written for any of them, which is the acceptance
  test for the architecture (D22). Phase 7 is therefore Gate B — running them
  against real data and checking the numbers — rather than authoring them.

- **2026-09-08 · phase 6 · the same lifting bug, a third time.** A bare lifted
  term in a plan's `where` kept its own `{self}` placeholder, exactly as terms
  inside a composite did in 5b. The rewriting now lives in
  `signals/base.lift_compiled` and both callers use it, because they have to
  agree: a `prior_season` `player_season` term in a `player_game` query must
  read from the `prior` join, not from the `player_game` row. Anywhere else that
  compiles a definition into a different entity must call it too.

- **2026-09-08 · phase 6 · the `play` entity has no `game_type`.** `pbp` spells
  it `season_type` and only distinguishes REG from POST, so the plan's list of
  game-typed entities is wrong for `play`. The filter reaches through the `game`
  join instead, where the real five values live. `GAME_TYPED` is now the
  entities whose own table carries the column; `GAME_TYPE_VIA_GAME` is `play`.

- **2026-09-08 · phase 6 · three-valued logic is surfaced, not smoothed over.**
  A predicate over a NULL attribute is *unknown*, not false. A row where it is
  unknown appears in neither `X` nor `not X`, is excluded from `avg(term)`
  rather than counted as a miss, and forms its own group when grouping by a
  term. All three are asserted by tests. Folding unknown into false would
  quietly assert something the data does not say, which is the failure this
  project exists to prevent.

- **2026-09-08 · phase 5 · two lifting bugs the plan's design invited.**
  (1) Signals emit `{namespace}` placeholders, and the plan resolves them
  through `CompileCtx.alias_of` — but a signal never calls `alias_of`, it just
  writes the placeholder. So a lifted term kept its own `{self}`: a
  `prior_season` `player_season` term used inside a `player_game` composite
  would have read `snap_share_mean` off the `player_game` row, where no such
  column exists. `composite` now rewrites a term's placeholders through
  `lift_namespace` in a single regex pass, so renaming `self`→`prior` cannot
  then rename `prior`→something else. (2) The store validated in alphabetical
  order, so a composite naming a term that sorts after it was accepted even when
  that term was broken; validation now runs to a fixed point.

- **2026-09-08 · phase 5 · `propose` must not offer a top-percentile of an
  inverted ordinal.** The automatic fallback suggested "draft_round at or above
  the 90th percentile", which is backwards — round 1 is the best round, which is
  exactly why D2 ships `star_by_draft` as a *rule* (`draft_round = 1`). The
  fallback is now restricted to attribute families where a larger number means
  more of the thing (`snaps`, `production`, `contract`, `epa`, `result`).
  Related: when a family is `not_computable`, no substitute is offered at all —
  the note explains what could stand in, but quietly answering a different
  question is the failure this project exists to prevent.

- **2026-09-08 · phase 5 · `build --skip-features` published a database with no
  entity tables.** It publishes by default, so a `--skip-features` run replaced
  a complete artifact with one that answers nothing; this broke the data tier
  mid-phase. It now warns loudly, and says so again when it is also publishing.

- **2026-09-08 · phase 5 · the mini database is real, and it is the phase 9
  conftest.** Two seasons, two teams, three regular-season weeks, a playoff
  game, eight players, pushed through the *production* builders so schema drift
  between fixtures and real SQL is impossible. Its `season_status` comes out at
  3 regular-season weeks and a 2-team playoff — invented numbers that the
  era-neutral code reads without complaint, which is the point of D24.
  Fixtures: `mini_conn`, `mini_settings`, `mini_store`, `mini_ctx`,
  `mini_coverage`.

- **2026-09-08 · phase 5 · percentile tie semantics, confirmed both ways.**
  `top P` and `bottom (100-P)` return the same rows when a tied group straddles
  the cut, because a tied group is indivisible and is included whenever any part
  of it falls inside the slice. In a ten-row cohort with four tied at the top,
  `top 30` and `bottom 70` both return all ten. This follows from the plan's
  `cume_dist` spec and is pinned by tests at both ends.

- **2026-09-08 · phase 4d · Gate A passes; two fixture cases were adjudicated
  against the data.** All 21 hand-authored cases resolve to a `player_game` row,
  and **`snaps_unit` matches the hand-written number in all 21** — those were
  read from nflverse by a person before the table existed. The plan's three named
  spot checks all hold: Rodgers 2023 W1 (4 snaps, tail 0.93, on reserve within
  three games, last play in Q1), Trent Williams 2023 W18 (played the next game),
  A.J. Brown 2023 W18 (next game is the week-19 wild card, did not play it).

  Two cases needed the phase 9 protocol, and in both the join was checked first:

  1. **`hainsey_2022_w18`: the fixture's football fact was wrong.** It said he
     "played the wild-card game". He did not — **Ryan Jensen missed all 17
     regular-season games injured and returned for the playoffs** (his only 2022
     row is week 19, 82 snaps, 100%), taking the centre job back. The fixture is
     corrected with a note. The expectation `expect_exit: false` still stands,
     but Hainsey is now a **named false positive** for the shipped `early_exit`
     composite: he started, was rested, missed the next game, was never listed
     injured and never hit the reserve list. This is exactly CLAUDE.md's
     "rested starter looks identical to an injury exit", with a name — and
     phase 7 must confront it rather than assume corroboration settles the
     question.

  2. **`chubb_2023_w2`: `baseline_share` is 0.49, just under Gate A's 0.5.** Not
     a bug and not a wrong number. His recent window is the single week-1 game at
     49%, which beats his 2022 season mean of 0.564. Running backs rotate, so a
     **position-blind snap-share floor is the wrong shape** for the shipped
     definition. The threshold was *not* lowered — the protocol forbids it — and
     the case is recorded as a documented Gate A exception with a test asserting
     exactly why it sits where it does. `recent_games` exists so a definition can
     require a sturdier baseline; phase 7 should use it.

- **2026-09-08 · phase 4d · two mechanics the plan did not specify.**
  `played_team_prev_game` and `played_team_next_game` ask whether the player
  appears in another row of the table being built, so `player_game` is a
  two-pass build: a temp `_pg_core` with everything else, then a self-join.
  And `recent_snap_share` needs "the last four games **in which he played**",
  which is `lag(x, n IGNORE NULLS)` — DuckDB puts `IGNORE NULLS` **inside the
  offset argument**, not after the call, and the usual spelling is a parse error.

- **2026-09-07 · phase 4c · `player_play` must be restricted to scrimmage
  plays.** The plan says to build it from `participation`'s offence and defence
  lists and index the plays "where that unit was on the field". Those lists are
  also filled on kickoffs, punts and kicks — with the *special-teams* personnel.
  Since a kickoff opens the game, **3,114 of 5,522 "first offensive plays" were
  kickoffs**, and a linebacker on the kickoff unit looked like an offensive
  starter (Eric Wilson, 2024: 31 first-unit games in a 17-game season).
  `player_play` now joins `pbp` and keeps `play_type NOT IN (kickoff, punt,
  field_goal, extra_point)`, which also makes it consistent with `snaps_unit`,
  which excludes special teams by definition (D12). 9.89M rows becomes 8.26M.
  This matters directly for 4d: `pp_first_idx`, `pp_last_idx` and every
  `pp_*_frac` are meaningless if special teams is in the denominator.

- **2026-09-07 · phase 4c · three source disagreements, recorded not
  reconciled.** Each is genuine upstream, and forcing any of them would be
  hiding something true:
  1. `games_played_share` can exceed 1 for a player traded mid-season, who can
     play more games than either of his teams did — Emmanuel Sanders played 17
     in 2019, when each team played 16. Only ever above 1 when `teams_count > 1`,
     which a test asserts.
  2. `is_rookie` (from `players.rookie_season`) and `years_exp` (from
     `rosters_weekly`) disagree for a few dozen players a season, typically ones
     who accrued time in another league or on a practice squad. The two fields
     count different things; both are kept.
  3. `first_unit_play_games` exceeds `games` for **3 of 27,110** player-seasons,
     each by one game, because NFL participation has a player on the field in a
     game where Pro Football Reference has no snap-count row (Sauce Gardner,
     Indianapolis, 2025 week 10, after a mid-season trade).

- **2026-09-07 · phase 4c · contracts are sparse before 2017.** About **65%** of
  player-seasons have a contract in force in 2013, rising through 80% (2015) and
  89% (2016) to ~99% from 2017. `coverage_columns` records this as
  `non_null_rows / total_rows`; `first_season` alone would say 2013 and imply
  the column is usable there. **`star_by_contract` (D2) is materially weaker
  before 2017** and phases 5 and 7 should say so when proposing it.

- **2026-09-07 · phase 4c · no 2012 `player_season` row exists.** Following from
  the phase 2 amendment: `snap_counts` has no 2012 file, and `player_season` is
  built from snap counts, so the table starts at 2013 and **a `prior_season`
  snap definition first has data in 2014**. `rosters_weekly` and `player_stats`
  do have 2012, so a prior-season *production* baseline for 2013 is possible;
  only the snap-derived ones are missing.

- **2026-09-07 · phase 4b · nflverse uses two team-abbreviation conventions, and
  mixing them fails silently.** `pbp` and `player_stats` always use the
  **current** franchise code (32 values, no exceptions). `schedules`,
  `snap_counts`, `injuries`, `participation` and `teams` use the code that was
  correct *at the time* — OAK through 2019, SD through 2016, STL through 2015 —
  and `rosters_weekly` adds PFR-style spellings of its own (`SL`, `ARZ`, `BLT`,
  `CLV`, `HST`). `teams` carries both `LA` and `LAR` for the Rams.

  Joining across the two does not error, it matches nothing: **every Oakland,
  San Diego and St. Louis team-game had zero offensive plays, zero EPA and zero
  yards** until this was found, because `team_game.team` said OAK and
  `pbp.posteam` said LV. 414 of 7,668 team-game rows were affected.

  `features/team_abbr.py` now holds the nine aliases and the 32 canonical codes,
  and every builder normalizes a team column read from a non-`pbp` source. The
  canonical spelling is the current one. `teams` is de-duplicated on the way in
  so the `LA`/`LAR` pair cannot fan out the `team_season` join. A data test
  asserts no derived table contains a non-canonical code, and another asserts no
  completed team-game has zero offensive plays. **Phases 4c and 4d must
  normalize `snap_counts.team`, `injuries.team`, `rosters_weekly.team` and
  `participation.possession_team` the same way** — the plan does not mention
  this anywhere and it silently corrupts any join that skips it.

- **2026-09-07 · phase 4b · `spread_line` is the negation of the conventional
  spread.** Verified as the plan requires. nflverse states it home-relative and
  **positive means the home team is favoured**: Super Bowl LVIII (home KC, away
  SF) is `-1.5` with SF favoured, and across 3,407 regular-season games
  home-favoured games are won by the home team 67.3% of the time (mean margin
  +5.77) against 34.5% when away-favoured. `team_game.spread` therefore negates
  it for the home row, so that negative means *this* team is favoured — the
  conventional reading. Recorded in the catalog description of both columns.
  Two tests defend the sign: favourites win 60-75% of the time, and the spread
  is covered 45-55% of the time. A flipped sign breaks both.

- **2026-09-07 · phase 4b · `season_status` is split out of `coverage.build`.**
  `game_ctx.reg_weeks` reads `season_status`, but coverage runs *after* features
  — a cycle. `coverage.build_season_status()` computes it from `schedules`
  alone and runs before the feature layer; column coverage still runs last, as
  the plan requires, and recomputes season_status harmlessly.

- **2026-09-07 · phase 4b · `team_game.is_final` added.** Not in the plan's
  column list, but `won`/`lost`/`tied` are already NULL until a game is played
  and `team_season` needs to filter on it. Scheduled-but-unplayed games are in
  these tables by design (`schedules` carries next season), so the flag is the
  honest way to exclude them.

- **2026-09-07 · phase 4a · `snap_counts.position` has 48 values, not 19.** The
  plan's value set (`C CB DE DT FB FS G K LB LS NT P QB RB SS T TE WR`) is the
  common head of a longer tail. PFR also emits `S DB OL DL HB OT OG OLB ILB MLB`
  and ~600 rows of compound strings (`C/G`, `G/T`, `DE/L`, `G/OT`, `RB/W`,
  `K/P`, …). `xwalk.POSITION_GROUP` therefore resolves on the first token before
  `/` and maps all 28 base tokens, not the 19 listed. The fallback only fires
  when `players` has no row for the id, which is rare, but a NULL
  `position_group` would put a player in no unit at all.

- **2026-09-07 · phase 4a · `rosters_weekly.pfr_id` is 50% populated, not 64%.**
  283,396 of 562,246 rows from 2013 on. D6's conclusion is unaffected and in
  fact strengthened — it is a supplementary source, not the crosswalk. In
  practice it contributes **2** ids that `players` does not already have.
  `players` alone resolves 99.93% of snap-count rows (226 unresolved of
  324,611), comfortably past the ≥ 99% bar.

- **2026-09-07 · phase 4a · feature builders declare their inputs.** A partial
  build (`chalktalk build --only teams`) has no `players` table, and
  `build_all` aborted the whole build on the missing table. Each entry in
  `features.BUILDERS` now carries a `requires` tuple; a builder whose inputs are
  absent is skipped with a warning. Same shape as the phase 3 `schedules` guard
  — partial builds are a supported mode and every stage of phase 4 must respect
  it.

- **2026-09-07 · phase 4a · `lift_namespace` and the `prior` namespace.** A
  `prior_season` definition's own row is already `season - 1`, so its `prior`
  namespace would be two seasons back. There is no such join, so it is refused
  for every plan entity rather than silently resolving to `prior` — which is
  what the plan's parenthetical "(then `prior` ns is unavailable)" means, made
  explicit. `LIFTS` is unchanged.

- **2026-09-07 · phase 4 · worked in four stages, not renumbered.** 04-features.md
  now carries a **Stages** section: 4a foundations · 4b team side · 4c player
  season · 4d player game and Gate A, each its own commit. The phase keeps one
  ledger row, `in progress` until 4d is green. Measured first: `player_play`
  unnests to ~9.9M rows, so it is not a volume risk and needs no stage of its
  own; the risk is `player_game` and Gate A, and the bulk of the work is the
  ~250 catalog entries. Two ordering constraints are recorded there: the catalog
  completeness test and `tests/fixtures/exits.yaml` both land in 4a, the latter
  so its expected answers cannot be back-fitted to the code's output.

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
