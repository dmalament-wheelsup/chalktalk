# Phase 7 — Shipped vocabulary and Gate B

## Goal

Ship the default definitions as JSON files, expressed only in the five
signals. Then **Gate B**: the hand-authored injury-exit fixtures pass through
the real path (definitions → compiler → SQL), the star expectations hold, the
ten generality plans run on real data, and the gate refuses what it should.

## Prerequisites

Phase 6 done; a full build present.

## Deliverables

```
src/chalktalk/definitions/shipped/*.json      one file per definition below
src/chalktalk/definitions/shipped.py          install_shipped(store) — copy into the user dir on first run
tests/fixtures/stars.yaml                     (content in 09-testing.md)
tests/data/test_gate_b_exits.py
tests/data/test_gate_b_stars.py
tests/data/test_generality.py
tests/data/test_refusals.py
```

## How shipped definitions reach the user

On `chalktalk serve`, `chalktalk build`, and `chalktalk defs list`, call
`install_shipped(store)`: for each shipped file, if no definition with that
name exists in the user's directory, copy it (`provenance.source = "shipped"`).
Never overwrite a file the user has edited (`source = "user"`). `chalktalk defs
reset NAME` restores the shipped version (moving the user's to history). One
store, one directory, user edits win.

## Shipped definitions (exact)

Cohorts and position shorthands (entity `player_season`, `rule`, so they lift
into `player_game` via `cur`):

| name | params |
|---|---|
| `qb` `rb` `wr` `te` `ol` `dl` `lb` `db` | `position_group = <X>` |
| `rookie` | `is_rookie = true` |
| `first_round_pick` | `draft_round = 1` |
| `undrafted` | `undrafted = true` |
| `heavy_carries` | `percentile`: `carries`, cohort `[season, position_group]`, top 90, eligible `games_with_snaps >= 8` |
| `leading_rusher` | `rank`: `carries`, cohort `[season, team_primary]`, top 1 |
| `star_by_snaps` | basis `prior_season`; `percentile`: `snap_share_mean`, cohort `[season, position_group]`, top 90, eligible `games_with_snaps >= 8` |
| `star_by_contract` | basis `prior_season`; `percentile`: `apy_cap_pct`, cohort `[season, position_group]`, top 90, eligible `apy_cap_pct is_not_null` |
| `star_by_draft` | basis `prior_season`; `rule`: `draft_round = 1` |

Player-game (entity `player_game`):

| name | signal | params |
|---|---|---|
| `played` | rule | `snaps_unit >= 1` |
| `regular` | rule | `baseline_share >= 0.5` — "normally plays at least half the unit's snaps" |
| `left_early` | rule | `pp_missed_tail_frac >= 0.25` — prefers participation |
| `snap_drop` | delta | `snap_share_unit` ratio `<= 0.5` × `baseline_share`, `min_baseline 0.3` |
| `exit_evidence` | composite | `any_of [left_early, snap_drop]` |
| `listed_injured_next` | rule | `next.inj_listed = true` |
| `on_reserve_soon` | rule | `reserve_within_3_games = true` |
| `missed_next_game` | rule | `played_team_next_game = false` |
| `exit_corroborated` | composite | `any_of [listed_injured_next, on_reserve_soon, missed_next_game]` |
| `early_exit` | composite | `all_of [played, regular, exit_evidence, exit_corroborated]` |

Team-game (entity `team_game`): `won` (`won = true`), `lost` (`lost = true`),
`home` (`home = true`), `road` (`home = false`), `favorite` (`favorite =
true`), `underdog` (`favorite = false`), `short_week` (`rest_days <= 5`).

Team-season (entity `team_season`): `playoff_team` (`made_playoffs = true`).

Game (entity `game`): `primetime` (`gametime >= '20:00'`), `division_game`
(`div_game = true`), `postseason` (`is_postseason = true`), `blowout`
(`margin_abs >= 17`), `one_score` (`margin_abs <= 8`).

Play (entity `play`): `went_for_it` (`all: down = 4, play_type in [pass, run]`),
`red_zone` (`yardline_100 <= 20`), `garbage_time` (`any: wp <= 0.05, wp >= 0.95`).

Every shipped file has a one-sentence `description` written for a football
reader, and `provenance.note` explaining any non-obvious threshold (e.g.
`regular`'s 0.5 excludes rotational linemen and committee backs — that is the
user's call to change).

**Not shipped, on purpose:** `star_player`, `starter`, `injury_exit`. They are
the vocabulary's on-ramp: the gate fires, `propose_definition` offers the
pieces, the user decides.

## Gate B

### `test_gate_b_exits.py`
For every case in `tests/fixtures/exits.yaml`, run through the real pipeline:
`QueryPlan(entity=player_game, where=[{term: early_exit}, {attr: player_name, op: "=", value: …}, {attr: week …}, {attr: team …}], seasons=that season, game_types=["*"], metrics=[count])`.
Expect `count == 1` when `expect_exit` else `0`. For `cousins_2023_w8`
additionally: save `left_early_late` (`pp_missed_tail_frac >= 0.03`) and
`early_exit_late` (`all_of [played, regular, left_early_late,
exit_corroborated]`) into a temporary definitions dir → expect 1.

### `test_gate_b_stars.py`
For every row in `tests/fixtures/stars.yaml`: evaluate the three `star_by_*`
definitions with `basis: prior_season` for that player's `player_game` rows
in that season. Fields marked `assert` must match; fields marked `record`
are printed and, on first run, written back into the YAML by a human after a
sanity check (never by the test).

### `test_generality.py`
Run the ten plans from `tests/fixtures/plans/` against the real build (after
copying `star_by_snaps` to `star_player` in a temporary store). Each must
return `ok: true`; those with terms must have non-empty `definitions_used`;
print a compact table `plan · rows · sample total · ms` for the human. Row
counts are not asserted (they change with data) except: plan 1 has a 2023 row
with `count >= 3` (Rodgers, Chubb… no — Chubb is 18 snaps; at minimum Rodgers
and A.J. Brown in REG if Brown's baseline holds), and plan 5 returns > 0 rows.

### `test_refusals.py`
- `{term: star_player}` without a saved definition → `unresolved_term` with
  exactly the three `star_by_*` suggestions.
- `early_exit` with `seasons 2005–2025` → `coverage_gap` naming a baseline
  attribute at 2013; with `allow_partial_coverage` → ok, `excluded = 2005…2012`.
- `{term: qb}` on entity `team_game` → `entity_mismatch` listing
  `player_season, player_game`.
- A `player_game` plan with `{term: leading_rusher, basis: prior_season}` on a
  `player_season` definition that uses no `prior` namespace → ok; a definition
  that *does* use `prior` with `basis: prior_season` → `entity_mismatch`.

## Acceptance

```bash
uv run chalktalk defs list                     # ~40 definitions, 0 broken
uv run chalktalk defs show early_exit          # nested explanation, coverage 2013–2025, warning re participation < 2016
uv run pytest -m data tests/data/test_gate_b_exits.py tests/data/test_gate_b_stars.py tests/data/test_generality.py tests/data/test_refusals.py
```

If an exit fixture fails here but passed Gate A, the bug is in a signal or the
compiler, not the data. If both fail, see the first-run protocol in phase 9.

## Pitfalls

- `leading_rusher` uses `team_primary`; a mid-season trade gives a player one
  team. Say so in the description.
- Copying shipped files must be idempotent and must never touch `source =
  "user"` files.
- `garbage_time` on `wp` is a *definition*: 5%/95% is one convention; the
  description should say the number so the user can argue with it.
- Plan 4 relies on `basis: prior_season` lifting within `player_season`
  (`self → prior`). Make sure phase 5 implemented that row of `LIFTS`.

## Done when

- [ ] All shipped definitions load with 0 broken.
- [ ] Gate B tests green; generality table printed and eyeballed.
- [ ] `phase-7:` commits; ledger updated; `record` fields in `stars.yaml` filled.
