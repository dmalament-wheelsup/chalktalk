# Phase 9 — Testing (cross-cutting)

Read this at phase 1. It defines the tiers, the conftest, the hand-authored
fixtures, and the first-run protocol for data tests. The fixture files below
are the *only* place where football knowledge is encoded, and they are written
by a person, not generated from the system's output.

## Tiers

| tier | marker | needs | runs |
|---|---|---|---|
| unit | (default) | nothing — in-memory mini DB | every commit, CI on every push |
| data | `-m data` | a full build under `CHALKTALK_HOME` | Gate A, Gate B, lossless, coverage, features; locally and in a weekly CI job with a cached home (optional) |

`pyproject` sets `addopts = "-m 'not data'"`. Data tests `pytest.skip` with a
clear message when `read_current()` is `None`.

## `tests/conftest.py` — the mini DB

Build an in-memory DuckDB by running the **production** builders on tiny raw
tables, so schema drift between production SQL and tests is impossible:

1. Create raw tables from small polars frames: two seasons (2022, 2023), two
   teams (`AAA`, `BBB`), a schedule of 4 games per season incl. one postseason
   game, ~8 players across position groups, snap counts hand-written to
   include: one early-exit case (played 3 of 60 unit plays, on reserve next
   week), one rested starter (12 snaps in the last REG week, played the
   postseason game), one backup cameo, one full-time starter; matching
   `participation` rows (offense/defense player lists per play) for the 2023
   games only; `injuries`, `rosters_weekly`, `players`, `contracts`,
   `draft_picks`, `player_stats`, `teams`, minimal `pbp` (a few plays per game
   with `posteam, defteam, play_type, down, epa, success, score_differential,
   qtr, game_seconds_remaining, wp, yardline_100`).
2. Run `features.build_all(conn, settings)` and `coverage.build(conn, settings)`.
3. Fixtures: `mini_conn`, `mini_settings`, `mini_store` (temp definitions dir),
   `mini_ctx` (a ValidationCtx factory), `mini_coverage`. Built 2026-09-08 in
   `tests/mini.py`; `mini_catalog` was not needed, since the catalog is static
   for every entity but `play`.

A unit test asserts the mini DB's derived tables have exactly the catalog's
columns (phase 4's completeness test runs here too).

## Hand-authored fixtures

### `tests/fixtures/exits.yaml`

```yaml
# Hand-authored. snaps_unit and share were read from nflverse snap_counts on 2026-09-07.
# `fact` is football knowledge; it must hold up against the data on first run (see protocol below).
cases:
  # injury exits with fewer than 15 unit snaps — must match early_exit AND snaps_unit < 15
  - {id: rodgers_2023_w1,   player: "Aaron Rodgers",   season: 2023, week: 1,  team: NYJ, unit: offense, snaps_unit: 4,  share: 0.07, expect_exit: true,  fact: "Achilles on the 4th snap vs BUF (MNF); IR the next week"}
  - {id: murray_2022_w14,   player: "Kyler Murray",    season: 2022, week: 14, team: ARI, unit: offense, snaps_unit: 3,  share: 0.04, expect_exit: true,  fact: "ACL on the first drive vs NE (MNF); IR"}
  - {id: bosa_2020_w2,      player: "Nick Bosa",       season: 2020, week: 2,  team: SF,  unit: defense, snaps_unit: 11, share: 0.17, expect_exit: true,  fact: "ACL, Q1 at NYJ; IR"}
  - {id: rodgers_2017_w6,   player: "Aaron Rodgers",   season: 2017, week: 6,  team: GB,  unit: offense, snaps_unit: 8,  share: 0.12, expect_exit: true,  fact: "collarbone, Q1 at MIN; IR"}
  - {id: barkley_2020_w2,   player: "Saquon Barkley",  season: 2020, week: 2,  team: NYG, unit: offense, snaps_unit: 8,  share: 0.12, expect_exit: true,  fact: "ACL, Q2 at CHI; IR"}
  - {id: watt_2017_w5,      player: "J.J. Watt",       season: 2017, week: 5,  team: HOU, unit: defense, snaps_unit: 12, share: 0.16, expect_exit: true,  fact: "tibial plateau fracture, Q1 vs KC (SNF); IR"}
  - {id: shazier_2017_w13,  player: "Ryan Shazier",    season: 2017, week: 13, team: PIT, unit: defense, snaps_unit: 3,  share: 0.05, expect_exit: true,  fact: "spinal injury, Q1 at CIN (MNF); never played again"}
  - {id: brown_2023_w18,    player: "A.J. Brown",      season: 2023, week: 18, team: PHI, unit: offense, snaps_unit: 12, share: 0.18, expect_exit: true,  fact: "knee vs NYG in week 18; missed the wild-card game. Corroboration comes from the postseason game (week 19)"}
  # injury exits with 15+ unit snaps — must match early_exit; must NOT match snaps_unit < 15
  - {id: lance_2022_w2,      player: "Trey Lance",         season: 2022, week: 2,  team: SF,  unit: offense, snaps_unit: 16, share: 0.21, expect_exit: true, fact: "ankle fracture, Q1 vs SEA; IR"}
  - {id: chubb_2023_w2,      player: "Nick Chubb",         season: 2023, week: 2,  team: CLE, unit: offense, snaps_unit: 18, share: 0.21, expect_exit: true, fact: "knee, Q2 at PIT (MNF); IR"}
  - {id: richardson_2023_w5, player: "Anthony Richardson", season: 2023, week: 5,  team: IND, unit: offense, snaps_unit: 22, share: 0.33, expect_exit: true, fact: "AC joint sprain, Q2 vs TEN; IR later"}
  - {id: burrow_2023_w11,    player: "Joe Burrow",         season: 2023, week: 11, team: CIN, unit: offense, snaps_unit: 27, share: 0.44, expect_exit: true, fact: "wrist, Q2 at BAL (TNF); IR"}
  # injury exit corroborated by the injury report only — he PLAYED the next game (tests listed_injured_next)
  - {id: evans_2020_w17,    player: "Mike Evans",       season: 2020, week: 17, team: TB,  unit: offense, snaps_unit: 11, share: 0.16, expect_exit: true,  fact: "hyperextended knee vs ATL in the final regular-season week; listed Questionable/Limited (Knee) for the wild-card game and played 61 snaps in it"}
  # late exit: shipped left_early (>= 25% of unit plays missed) must NOT flag; a 3% threshold must
  - {id: cousins_2023_w8, player: "Kirk Cousins", season: 2023, week: 8, team: MIN, unit: offense, snaps_unit: 61, share: 0.85, expect_exit: false, expect_exit_with_tail_0_03: true, fact: "Achilles, Q4 at GB; IR"}
  # rested starters in the final regular-season week (17 through 2020, 18 from 2021): played the next game — must NOT match
  - {id: allen_2019_w17,     player: "Josh Allen",        season: 2019, week: 17, team: BUF, unit: offense, snaps_unit: 7,  share: 0.11, expect_exit: false, fact: "rested with the 5 seed locked (Barkley finished); played all 85 snaps in the wild-card game"}
  - {id: williams_2023_w18,  player: "Trent Williams",    season: 2023, week: 18, team: SF, unit: offense, snaps_unit: 12, share: 0.20, expect_exit: false, fact: "rested, 1 seed clinched; played the divisional round"}
  - {id: hainsey_2022_w18,   player: "Robert Hainsey",    season: 2022, week: 18, team: TB, unit: offense, snaps_unit: 13, share: 0.24, expect_exit: false, fact: "rested, division clinched; played the wild-card game"}
  - {id: fournette_2022_w18, player: "Leonard Fournette", season: 2022, week: 18, team: TB, unit: offense, snaps_unit: 3,  share: 0.05, expect_exit: false, fact: "rested; played the wild-card game"}
  # backup cameos: low snaps because not a starter — must NOT match
  - {id: bridgewater_2019_w17, player: "Teddy Bridgewater", season: 2019, week: 17, team: NO, unit: offense, snaps_unit: 11, share: 0.16, expect_exit: false, fact: "Brees started; Bridgewater's recent window (two starts at ~96% plus two cameos) averages 0.52, and he did not play in the wild-card game because Brees was healthy. This case is why missed_next_game requires `started` — absence alone would flag him"}
  - {id: devito_2023_w18,   player: "Tommy DeVito",    season: 2023, week: 18, team: NYG, unit: offense, snaps_unit: 4, share: 0.06, expect_exit: false, fact: "relief appearance; Giants' season ended, so no next game (uncorroborable)"}
  - {id: brissett_2022_w18, player: "Jacoby Brissett", season: 2022, week: 18, team: CLE, unit: offense, snaps_unit: 1, share: 0.02, expect_exit: false, fact: "backup cameo behind Watson"}
```

### `tests/fixtures/stars.yaml`

Expected results of the three shipped star definitions with `basis:
prior_season` (evaluated on the season *before* the one listed). `assert` =
football knowledge; `record` = capture from data on first run after a human
sanity check, then freeze by replacing `record` with `assert`.

```yaml
- {player: "Aaron Rodgers",      season: 2023, star_by_snaps: {assert: true},  star_by_contract: {assert: true},  star_by_draft: {assert: true}}
- {player: "Kyler Murray",       season: 2022, star_by_snaps: {assert: true},  star_by_contract: {record: null},  star_by_draft: {assert: true}}   # 2021 was the rookie deal
- {player: "Nick Bosa",          season: 2020, star_by_snaps: {record: null},  star_by_contract: {record: null},  star_by_draft: {assert: true}}
- {player: "Aaron Rodgers",      season: 2017, star_by_snaps: {assert: true},  star_by_contract: {assert: true},  star_by_draft: {assert: true}}
- {player: "Saquon Barkley",     season: 2020, star_by_snaps: {assert: true},  star_by_contract: {record: null},  star_by_draft: {assert: true}}
- {player: "J.J. Watt",          season: 2017, star_by_snaps: {assert: false, why: "2016 was 3 of 16 games (back); games_played_share 0.19, not eligible"}, star_by_contract: {assert: true}, star_by_draft: {assert: true}}
- {player: "Ryan Shazier",       season: 2017, star_by_snaps: {record: null},  star_by_contract: {record: null},  star_by_draft: {assert: true}}
- {player: "Trey Lance",         season: 2022, star_by_snaps: {assert: false}, star_by_contract: {assert: false}, star_by_draft: {assert: true}}
- {player: "Nick Chubb",         season: 2023, star_by_snaps: {assert: true},  star_by_contract: {record: null},  star_by_draft: {assert: false, why: "round 2, pick 35"}}
- {player: "Anthony Richardson", season: 2023, star_by_snaps: {assert: false, why: "rookie — no prior season"}, star_by_contract: {assert: false}, star_by_draft: {assert: true}}
- {player: "Joe Burrow",         season: 2023, star_by_snaps: {assert: true},  star_by_contract: {assert: false, why: "the 2022 contract was the rookie deal; the extension was signed in September 2023"}, star_by_draft: {assert: true}}
- {player: "Kirk Cousins",       season: 2023, star_by_snaps: {assert: true},  star_by_contract: {assert: true},  star_by_draft: {assert: false, why: "round 4"}}
```

These rows are deliberately sensitive to the definition: Watt and Burrow are
"stars" by two of three and not the third, for reasons a football reader
recognises. That sensitivity is the product.

### `tests/fixtures/plans/*.json`

The ten plans from [06-query.md](06-query.md).

## First-run protocol for data tests

1. Run the test; for each failure, print the full `player_game` row (and the
   `next` row) — every data test must do this, not just report a boolean.
2. Classify: **join bug** (row missing, ids not crosswalked, wrong week) →
   fix the feature builder · **data quirk** (upstream value differs from the
   fixture's number) → verify against the nflverse release directly; if
   upstream changed, update the fixture with a note citing the date · **fixture
   wrong** (the football fact is misremembered) → correct it with a note. Do
   not weaken a threshold to make a fixture pass.
3. Never regenerate an `assert` field from the system's own output.

## CI

`.github/workflows/ci.yml`: on push/PR — `uv sync`, `ruff check`, `ruff
format --check`, `uv run pytest` (unit tier). A second, manually-triggered
workflow `data.yml` runs `chalktalk build` with `CHALKTALK_HOME` cached by
`(nflreadpy version, ISO week)` and then `pytest -m data`. Keep it optional;
the local `-m data` run is the gate.

## Done when

- [ ] conftest mini DB in place (phase 4) and used by every unit test that
      needs data.
- [ ] Fixture files present and unchanged except through the protocol.
- [ ] CI workflow green on `main`.
