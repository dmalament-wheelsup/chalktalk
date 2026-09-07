# Phase 6 — Query plan, gate, compiler, envelope

## Goal

A structured `QueryPlan`; a gate that refuses unresolved terms, unknown
attributes, broken definitions, bad lifts and coverage gaps — structurally,
returning every problem of a kind at once; a compiler to parameterized SQL; an
executor with a timeout; and the result envelope. `explain_query` is the same
pipeline without execution.

## Prerequisites

Phase 5 done.

## Deliverables

```
src/chalktalk/query/plan.py       QueryPlan, Clause, Metric (pydantic)
src/chalktalk/query/gate.py       check(plan, …) -> GateResult
src/chalktalk/query/compile.py    compile(plan, …) -> CompiledQuery
src/chalktalk/query/execute.py    run(conn, compiled, settings) -> rows / timeout
src/chalktalk/query/envelope.py   Envelope, ErrorEnvelope, render_english()
tests/unit/test_plan.py test_gate.py test_compile.py test_execute.py test_envelope.py
tests/fixtures/plans/*.json       the ten generality plans (below) — compiled in unit tests, executed in phase 7
```

## Plan model (exact)

```python
class QueryPlan(BaseModel):
    question: str | None = None                 # echoed; audit only
    entity: Entity
    where: list[Clause] = []
    seasons: SeasonSpan | None = None            # {"from": 2013, "to": 2025}; None = all queryable
    game_types: list[str] = ["REG"]              # subset of REG WC DIV CON SB, or ["*"]
    group_by: list[GroupKey] = []                # attr ref string, or {"term": name, "basis"?: ...}
    metrics: list[Metric] = [Metric(fn="count")]
    order_by: list[OrderKey] = []                # {"key": alias-or-groupkey, "dir": "asc"|"desc"}
    limit: int | None = None                     # default settings.query_default_limit, max query_row_cap
    allow_partial_coverage: bool = False
    sample_rows: int | None = None               # default settings.sample_rows, max 50

Clause = TermRef | AttrRule | Not | AnyOf | AllOf
  TermRef  {"term": "early_exit", "basis": "prior_season"?}      # basis only for player_season definitions
  AttrRule {"attr": "snaps_unit", "op": "<", "value": 15}        # same ops/types as the rule signal
  Not      {"not": Clause}   AnyOf {"any_of": [Clause, …]}   AllOf {"all_of": [Clause, …]}

Metric  {"fn": "count"|"count_distinct"|"sum"|"avg"|"min"|"max", "of"?: AttrRef | TermRef, "as"?: str}
```

Semantics: `avg` of a TermRef is the share of rows where the term holds (a
rate); `count` needs no `of`; `count_distinct` of e.g. `player_key`.
`seasons` filters `self.season`. `game_types` applies to entities with
`game_type` (`game`, `team_game`, `player_game`, `play`); on
`team_season`/`player_season` a non-default value is a warning, not an error.

Clause discrimination is by key. Implement as a pydantic
`model_validator(mode="before")` on a dict, and describe the shape in the
tool description (phase 8) because JSON schema for this union is opaque.

## Gate (`gate.py`) — order matters; return all problems of the first failing kind

```python
@dataclass
class GateResult: ok: bool; error: ErrorEnvelope | None; seasons: SeasonRange; excluded: list[int]; warnings: list[str]; definitions_used: list[Definition]
def check(plan, store, catalog, coverage, settings, conn) -> GateResult
```

1. `invalid_plan` — pydantic errors, `limit` over cap, unknown `game_types`.
2. `unknown_attribute` — every attr in `where`, `group_by`, `metrics`,
   `order_by` resolves in the catalog for this entity/namespace. Include
   `did_you_mean` (difflib over the entity's attribute names) per miss.
3. `unresolved_term` — **all** unresolved terms at once. For each:
   `{"term", "suggestions": [{name, explanation}…], "families": […]}` from
   `propose()`, plus top-level `next_step: "propose_definition"`.
4. `definition_broken` — any referenced definition (transitively) in
   quarantine: `{name: reason}`.
5. `entity_mismatch` — lift failure: definition entity, plan entity, allowed
   plan entities.
6. `coverage_gap` — requested range vs `coverage.intersect` over every attr
   used anywhere in the plan plus every used definition's `requires()`. If a
   gap and not `allow_partial_coverage`: error with `{requested, covered,
   limiting: [{ref, first, last}]}`. Else clamp, record `excluded`, warn.
7. Warnings (never errors): `prefers()` refs not covering the whole range
   (e.g. participation evidence unavailable before 2016); the current season
   is `in_progress` and included; attributes with `has_gaps`.

## Compiler (`compile.py`)

```python
@dataclass
class CompiledQuery:
    sql: str; params: list[Any]
    sample_sql: str; sample_params: list[Any]
    count_sql: str; count_params: list[Any]
    definitions_used: list[Definition]; seasons: SeasonRange; warnings: list[str]
    columns: list[str]                          # output column names of sql
def compile(plan, gate: GateResult, store, catalog, entities, settings) -> CompiledQuery
```

Shape:

```sql
WITH <definition CTEs…>,
base AS (
  SELECT <alias>.*, <ns.col AS ns__col …only what is referenced>
  FROM <entity table> <alias>
  LEFT JOIN … <only namespaces referenced by the plan or lifted definitions>
  WHERE <alias>.season BETWEEN ? AND ?
    [AND <alias>.game_type IN (?, …)]
    AND <where clauses>
)
SELECT <group keys>, <metrics> FROM base
GROUP BY <group keys> ORDER BY <order_by or group keys> LIMIT ?
```

- TermRef → `signal.compile(defn, ctx)` with `alias_of` built from the
  entity's namespaces and the lift map (D16). CTEs are hoisted and deduped by
  name.
- Group-by on a TermRef compiles to the predicate expression aliased with the
  term name.
- `avg(term)` → `avg(CASE WHEN <pred> THEN 1.0 WHEN NOT (<pred>) THEN 0.0 END)`
  — NULL predicates are excluded from the rate. Document in the envelope's
  English rendering.
- Attribute names validated and double-quoted; every literal is a `?` param.
- `sample_sql`: `SELECT <entity.identifying>, <evidence attrs of every
  definition used, deduped> FROM base ORDER BY season, week LIMIT ?`;
  `count_sql`: `SELECT count(*) FROM base`.

## Executor (`execute.py`)

```python
def run(conn, compiled, settings) -> RunResult      # rows as list[dict], row_count, truncated, sample rows, total_matched, timing
```
Timeout: start a `threading.Timer(settings.query_timeout_s, conn.interrupt)`;
on `duckdb.InterruptException` raise `QueryTimeout` → `sql_timeout` envelope.
`truncated = len(rows) == limit`.

## Envelope (`envelope.py`, exact)

```json
{
  "ok": true,
  "question": "…",
  "entity": "player_game",
  "english": "Count of player-games (REG, 2013–2025) where star_player [prior season] and early_exit and snaps_unit < 15, by season.",
  "rows": [ {"season": 2023, "count": 4}, … ],
  "row_count": 13, "truncated": false,
  "definitions_used": [
    {"name": "star_player", "version": 1, "entity": "player_season", "basis": "prior_season", "signal": "percentile",
     "explanation": "…", "coverage": {"first": 2013, "last": 2025}}
  ],
  "seasons": {"requested": [2013, 2025], "covered": [2013, 2025], "excluded": [], "partial": false, "includes_incomplete_season": false},
  "game_types": ["REG"],
  "sample": {"total_matched": 61, "columns": ["player_name", "season", "week", "team", "opponent", "snaps_unit", "baseline_share", "pp_missed_tail_frac", "…"], "rows": [ … ]},
  "sql": "…", "sql_params": [2013, 2025, "REG", 15, …], "sample_sql": "…",
  "timing_ms": {"main": 41, "sample": 9, "count": 6},
  "build": {"artifact": "nfl-20260907.duckdb", "built_at": "2026-09-07T10:12:00Z"},
  "warnings": ["participation evidence (left_early) unavailable before 2016; snap_drop evidence used there"]
}
```
Errors: `{"ok": false, "error": "<code>", "message": "…", …code-specific fields}`.

`render_english(plan, definitions_used)` produces the `english` line: entity
noun, filters (terms by name with basis, attr rules verbatim), seasons, game
types, group keys, metrics.

`explain_query(plan)` returns everything except `rows`, `sample`, `timing_ms`.

## The ten generality plans (`tests/fixtures/plans/`)

Each is a JSON `QueryPlan`. They reference shipped definitions from phase 7;
in this phase they must **parse and compile** against the mini DB (with the
shipped definitions loaded from `definitions/shipped/`); in phase 7 they
execute against real data.

1. `01-star-exits.json` — the `CLAUDE.md` question.
   `player_game`; where `[{term: star_player, basis: prior_season}, {term: early_exit}, {attr: snaps_unit, op: "<", value: 15}]`; group `[season]`; metrics `[count]`.
   (In tests, `star_player` is created by copying `star_by_snaps` first — the
   fixture also asserts that *without* that copy the gate returns
   `unresolved_term` with three suggestions.)
2. `02-rookie-qb-road-primetime.json` — win rate of rookie starting QBs on the road in primetime, by season.
   `player_game`; where `[{term: rookie}, {term: qb}, {attr: is_starting_qb, op: "=", value: true}, {term: road}, {term: primetime}]`; group `[season]`; metrics `[count, {fn: avg, of: {term: won}, as: win_rate}]`.
3. `03-fourth-down-aggression.json` — go rate on 4th down trailing by one score in Q4, by season.
   `play`; where `[{attr: down, op: "=", value: 4}, {attr: qtr, op: "=", value: 4}, {attr: score_differential, op: between, value: [-8, -1]}, {attr: play_type, op: in, value: [pass, run, punt, field_goal]}]`; group `[season]`; metrics `[{fn: avg, of: {term: went_for_it}, as: go_rate}, count]`.
4. `04-rb-usage-after-heavy-season.json` — RB snap share the season after a top-10% carries season, versus that prior season.
   `player_season`; where `[{term: rb}, {term: heavy_carries, basis: prior_season}]`; group `[season]`; metrics `[count, {fn: avg, of: snap_share_mean}, {fn: avg, of: prior.snap_share_mean}]`.
5. `05-favorites-blown-out.json` — favourites losing by 17+, by season.
   `team_game`; where `[{term: favorite}, {term: lost}, {term: blowout}]`; group `[season]`; metrics `[count]`.
6. `06-first-round-rookie-wr-week1.json` — first-round rookie WRs playing ≥ 80% of snaps in week 1.
   `player_game`; where `[{term: rookie}, {term: wr}, {term: first_round_pick}, {attr: week, op: "=", value: 1}, {attr: snap_share_unit, op: ">=", value: 0.8}]`; metrics `[count, {fn: count_distinct, of: player_key}]`.
7. `07-games-lost-to-injury-by-team.json` — regular players who missed the next game after being listed injured, by team-season.
   `player_game`; where `[{term: regular}, {term: missed_next_game}, {term: listed_injured_next}]`; group `[season, team]`; metrics `[count]`; order `count desc`; limit 20.
8. `08-short-week-vs-rested.json` — margin and win rate on ≤ 5 days rest against opponents on ≥ 7.
   `team_game`; where `[{attr: rest_days, op: "<=", value: 5}, {attr: opp.rest_days, op: ">=", value: 7}]`; group `[season]`; metrics `[count, {fn: avg, of: margin}, {fn: avg, of: {term: won}}]`.
9. `09-playoff-team-backup-qb-starts.json` — starts by QBs who started < 8 games the prior season, for playoff teams.
   `player_game`; where `[{term: qb}, {attr: is_starting_qb, op: "=", value: true}, {attr: prior.qb_starts, op: "<", value: 8}, {attr: team_season.made_playoffs, op: "=", value: true}]`; group `[season, team]`; metrics `[count]`.
10. `10-leading-rusher-benched-in-blowout-win.json` — a team's leading rusher under 30% of snaps in a 17+ point win.
    `player_game`; where `[{term: leading_rusher}, {term: played}, {attr: snap_share_unit, op: "<", value: 0.3}, {attr: team.margin, op: ">=", value: 17}]`; group `[season]`; metrics `[count]`.

None of these needed a line of code beyond the five signals. If one does,
that is the finding — fix the feature layer or a signal, not the plan.

## Acceptance (unit tier)

```bash
uv run pytest tests/unit/test_plan.py tests/unit/test_gate.py tests/unit/test_compile.py tests/unit/test_execute.py tests/unit/test_envelope.py
```
- Each error code produced by a crafted plan; `unresolved_term` lists two
  missing terms in one response.
- All ten plans parse and compile; the SQL executes on the mini DB (row
  counts may be 0).
- Envelope validates against its pydantic model; `sql_params` length equals
  the number of `?` in `sql`.
- Timeout test with a deliberately slow query (cross join) and
  `query_timeout_s = 0.2`.

## Pitfalls

- Coverage must include attrs used in `metrics`, `group_by` and `order_by`,
  not only `where`.
- Default `seasons` = all queryable; if the latest is `in_progress`, warn.
- A plan with an empty `where` is valid.
- `LIMIT` on the aggregate query, `sample_rows` on the sample — two different
  caps.
- Never build SQL with f-strings around user values. Attribute names are the
  only interpolated identifiers and they come from the catalog.

## Done when

- [ ] Gate, compiler, executor, envelope implemented; ten plans compile.
- [ ] `phase-6:` commits; ledger updated.
