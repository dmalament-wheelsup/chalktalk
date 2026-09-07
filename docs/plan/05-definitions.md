# Phase 5 — Definitions and the five signals

## Goal

The definition spec; a store with validation, history and quarantine; the five
general signals behind one contract; English explanations; the vocabulary
catalog; and a deterministic `propose_definition`. No MCP yet — Python API and
CLI only. After this phase, any concept expressible over the feature layer can
be saved as a named, versioned definition without new code.

## Prerequisites

Phase 4 done.

## Deliverables

```
src/chalktalk/definitions/spec.py          Definition, DefinitionIn, Provenance
src/chalktalk/definitions/store.py         DefinitionStore
src/chalktalk/definitions/explain.py       English rendering
src/chalktalk/definitions/vocabulary.py    football words → families → suggestions
src/chalktalk/definitions/propose.py       propose()
src/chalktalk/definitions/signals/base.py  Signal contract, Compiled, contexts
src/chalktalk/definitions/signals/{rule,percentile,rank,delta,composite}.py
src/chalktalk/definitions/signals/__init__.py   SIGNALS registry
CLI: chalktalk defs list|show|validate|add|rm|export|import
tests/unit/test_spec.py test_store.py test_signal_rule.py test_signal_percentile.py
           test_signal_rank.py test_signal_delta.py test_signal_composite.py test_propose.py
```

## Definition spec (exact JSON)

```json
{
  "schema_version": 1,
  "name": "star_by_snaps",
  "aliases": ["snap_star"],
  "entity": "player_season",
  "basis": "prior_season",
  "signal": "percentile",
  "params": { "...": "validated by the signal's Params model" },
  "description": "Top 10% prior-season snap share within position group.",
  "provenance": {
    "source": "shipped",
    "created_at": "2026-09-07T00:00:00Z",
    "updated_at": "2026-09-07T00:00:00Z",
    "copied_from": null,
    "note": null
  },
  "version": 1
}
```

Rules: `name` matches `^[a-z][a-z0-9_]{1,63}$`; names and aliases are unique
across the store. `basis` is allowed only when `entity` is `player_season`
(`current_season` default, or `prior_season`) and controls lifting (D16).
`provenance.source` ∈ `shipped | user | imported`.
`DefinitionIn` is the user-facing input: everything except `provenance`,
`version`, `schema_version`.

## Signal contract (`signals/base.py`, exact)

```python
@dataclass(frozen=True)
class AttrRef: namespace: str; name: str                # namespace "self" for the entity's own table

@dataclass
class CTE: name: str; sql: str; params: list[Any]

@dataclass
class Compiled:
    predicate_sql: str            # uses {ns} placeholders, e.g. '{self}."snaps_unit" >= ?'
    params: list[Any]             # bound in order of '?' in predicate_sql (CTE params are separate)
    ctes: list[CTE]
    namespaces_used: set[str]     # definition-relative namespaces
    terms_used: list[str]         # transitive, in dependency order

@dataclass
class ValidationCtx: entity: str; conn: DuckDBPyConnection; catalog: Catalog; coverage: Coverage; store: "DefinitionStore"; settings: Settings
@dataclass
class CompileCtx(ValidationCtx): alias_of: Callable[[str], str]   # definition namespace → SQL alias; raises EntityMismatch

class Signal(ABC):
    id: ClassVar[str]
    Params: ClassVar[type[BaseModel]]
    def validate(self, d: Definition, ctx: ValidationCtx) -> None
    def compile(self, d: Definition, ctx: CompileCtx) -> Compiled
    def explain(self, d: Definition, ctx: ValidationCtx) -> str
    def requires(self, d: Definition, ctx: ValidationCtx) -> list[AttrRef]   # hard: coverage = intersection
    def prefers(self, d: Definition, ctx: ValidationCtx) -> list[AttrRef]    # soft: gate warns when missing
    def evidence(self, d: Definition, ctx: ValidationCtx) -> list[AttrRef]   # columns shown in the matched-row sample

SIGNALS: dict[str, Signal]
```

Attribute names are validated against the catalog (allowlist) and
double-quoted in SQL. Literal values are always bound parameters. `{ns}`
placeholders are resolved by the query compiler (phase 6) through `alias_of`.

### `rule`

```json
{"match": "all", "rules": [{"attr": "snaps_unit", "op": ">=", "value": 1}]}
```
`match` ∈ `all | any`. `op` ∈ `= != < <= > >= in not_in between is_null
is_not_null like`. `attr` may be namespaced. `value` type is checked against the
attribute type (`between` takes a 2-list; `in` a list). Compiles to
AND/OR of `{ns}."attr" op ?`. Explain: `snaps_unit ≥ 1`, joined by "and"/"or".
NULL semantics are SQL's: `!=` does not match NULL; say so in the explanation
when the attribute is nullable.

### `percentile`

```json
{"attr": "snap_share_mean", "cohort": ["season", "position_group"], "pctile": 90,
 "direction": "top", "eligible": {"match": "all", "rules": [{"attr": "games_with_snaps", "op": ">=", "value": 8}]}}
```
`attr` and `cohort` are `self` attributes of the definition's entity.
`direction` ∈ `top | bottom`. `eligible` is optional `rule` params; default
for `player_season` is `games_with_snaps >= settings.pctile_default_min_games`,
for other entities none.

Semantics (ties matter — see Pitfalls): `top` with `pctile = P` means
`cume_dist() OVER (PARTITION BY cohort ORDER BY attr ASC) >= P/100`;
`bottom` with `pctile = P` means `cume_dist() OVER (… ORDER BY attr DESC) >=
(100 - P)/100`, i.e. at or below the P-th percentile.

Compiles to a CTE over the entity table (`WHERE attr IS NOT NULL AND
<eligible>`) selecting the entity key plus `cd`, and the predicate
`({self}.k1, {self}.k2) IN (SELECT k1, k2 FROM <cte> WHERE cd >= ?)`. CTE names
are `pct_<defname>` (unique per definition; composites may include several).
Explain: "snap_share_mean at or above the 90th percentile within season and
position_group (among rows with games_with_snaps ≥ 8)".

### `rank`

```json
{"attr": "carries", "cohort": ["season", "team_primary"], "top_n": 1, "direction": "top", "eligible": null}
```
Like `percentile` with `row_number() … <= top_n`; deterministic tiebreak by
entity key. Explain: "#1 by carries within season and team_primary".

### `delta`

```json
{"attr": "snap_share_unit", "baseline": "baseline_share", "kind": "ratio", "op": "<=", "value": 0.5, "min_baseline": 0.3}
```
`kind` ∈ `ratio | diff`. `attr` and `baseline` may be namespaced, so
"usage change vs prior season" is `attr: snap_share_mean, baseline:
prior.snap_share_mean` on `player_season`. Compiles NULL-safe:
`{a} IS NOT NULL AND {b} IS NOT NULL AND {b} > ? AND {a} <= ? * {b}` (ratio) or
`{a} - {b} op ?` (diff). Explain: "snap_share_unit ≤ 0.5 × baseline_share
(baseline > 0.3)".

### `composite`

```json
{"op": "all_of", "terms": ["played", "regular", "exit_evidence", "exit_corroborated"]}
```
`op` ∈ `all_of | any_of | not` (`not` takes exactly one term). Validation:
each term exists and is not broken; no cycles; each term's entity lifts into
this definition's entity (D16). Compiles the referenced definitions in this
definition's context with the lift mapping applied, combines with
AND/OR/NOT, and merges CTEs and `terms_used`. Explain: nested, e.g.
"played and regular and (left_early or snap_drop) and (listed_injured_next or
on_reserve_soon or missed_next_game)".

## Lifting

```python
def lift_alias_map(defn: Definition, target_entity: str, basis: str | None, target_alias_of) -> Callable[[str], str]
```
Uses `entities.LIFTS`. Raises `EntityMismatch(defn, target_entity, allowed=[…])`
with a message listing which entities the definition *can* be used in.

## Store (`store.py`, exact)

```python
class DefinitionStore:
    def __init__(self, directory: Path, ctx_factory: Callable[[], ValidationCtx])
    def load(self) -> LoadReport                    # parses every *.json; validates; fills self.broken
    def maybe_reload(self) -> bool                  # re-load if directory mtime changed
    def get(self, name_or_alias: str) -> Definition | None
    def list(self, include_broken: bool = True) -> list[DefinitionSummary]
    def save(self, d: DefinitionIn, *, overwrite: bool = False, copied_from: str | None = None) -> Definition
    def delete(self, name: str) -> None
    def export(self, path: Path) -> int
    def import_(self, path: Path, *, source: str = "imported", overwrite: bool = False) -> ImportReport
    def explain(self, name: str) -> Explanation
    def coverage_of(self, name: str) -> SeasonRange | None
    broken: dict[str, str]                          # name → reason
```

`save`: validate (signal exists → params → attributes exist → term refs
resolve → lifts valid → dry compile → coverage computable) → if exists and
not `overwrite` → `invalid_definition("exists")` → move old file to
`.history/<name>/v<N>.json` → write `<name>.json.tmp` then `os.replace` →
reload that entry. `copied_from` copies `entity/basis/signal/params/description`
from an existing definition and records `provenance.copied_from`.

`load`: any failure quarantines with a one-line reason; log a WARNING banner
listing them. Never raise.

## `explain.py`

```python
@dataclass
class Explanation: text: str; coverage: SeasonRange | None; warnings: list[str]; evidence: list[AttrRef]; parts: list["Explanation"]
def explain(defn, store) -> Explanation
```

## `vocabulary.py` and `propose.py`

```python
@dataclass(frozen=True)
class Family: id: str; words: tuple[str, ...]; suggest_definitions: tuple[str, ...]; suggest_attrs: tuple[str, ...]; not_computable: str | None
VOCABULARY: list[Family]
```

Ship at least these families: `star` (star, elite, stud, top, best) ·
`starter` (starter, first string, started) → `is_starting_qb, pp_first_idx,
snap_share_unit, baseline_share` · `backup` · `rookie` · `veteran` (years_exp)
· `injury` (injured, hurt, exit, left the game, went down) → exit family ·
`rest` (rested, sat, benched) · `workhorse` (bellcow, feature back) →
`carries`, `heavy_carries` · `primetime` (night, SNF, MNF, TNF) · `blowout`
(rout, laugher) · `close` (one score, nail-biter) · `favorite/underdog` ·
`playoff team` · `division` · `home/road` · `short week` · `weather` (cold,
wind, dome) · `garbage time` (play: `wp`) · `red zone` · `two minute` (play:
`half_seconds_remaining`) · `fourth down` · `deep ball` (play: `air_yards`) ·
`pro bowl / all-pro` → `not_computable` (D2) · `pressure` → `not_computable`
in v1 (participation `was_pressure` is 2018+ and not yet exposed).

```python
@dataclass
class Proposal:
    term: str
    matches: list[DefinitionSummary]           # fuzzy (difflib ratio ≥ 0.8) on names/aliases
    families: list[str]
    suggestions: list[Suggestion]              # ready-to-save DefinitionIn + explanation + coverage
    related_attributes: list[Attribute]        # catalog search on term/context tokens, ranked
    not_computable: list[str]
    signals: dict[str, dict]                   # id → JSON schema of Params
def propose(term: str, context: str | None, entity: str | None, store, catalog, coverage) -> Proposal
```
Deterministic; no model.

## CLI

```
chalktalk defs list [--broken] | show NAME | validate | add FILE.json [--overwrite] | rm NAME | export DIR | import PATH [--overwrite]
```

## Acceptance (unit tier, mini DB)

- Each signal compiles to SQL that executes on the mini DB and returns the
  expected keys for a tiny hand-made dataset.
- Percentile tie test: a 10-row cohort with 4 rows tied at the top value —
  `top 90` returns all 4; `top 70` returns 4 (not 3). Bottom likewise.
- Composite cycle → `invalid_definition`; unknown attribute → `invalid_definition`
  with `did_you_mean`; type mismatch → `invalid_definition`.
- Store: round trip, history file created on overwrite, quarantine when a
  definition references a column absent from the mini DB, `maybe_reload`.
- `propose("star player")` returns the three `star_by_*` (once phase 7 ships
  them; before that, the family and attributes). `propose("bellcow")` returns
  `carries` attrs and a `percentile` suggestion. `propose("pro bowler")`
  returns `not_computable`.

```bash
uv run pytest tests/unit -q
uv run chalktalk defs validate        # 0 broken on an empty store
```

## Pitfalls

- **`percent_rank` is wrong for ties.** With 70 eligible QBs and 15 tied at
  `snap_share_mean = 1.0`, `percent_rank` gives the tied group ≈ 0.80 and
  "top 10%" returns nobody. `cume_dist` returns the whole tied group. This is
  why the semantics above use `cume_dist`.
- The percentile CTE is over the *definition's* entity table even when lifted;
  the `IN` predicate uses the lifted alias's keys (`{self}` resolves to the
  `prior`/`cur` alias in a `player_game` plan).
- `basis: prior_season` on a `player_season` definition used in a
  `player_season` plan maps `self → prior`; the definition's own `prior`
  namespace is then unavailable (`entity_mismatch`, explained).
- Aliases and attribute names live in different namespaces (`{"term"}` vs
  `{"attr"}` in plans); collisions are not a problem.
- Never let a definition's file name differ from its `name`.

## Done when

- [ ] Five signals, store, explain, propose implemented and unit-tested.
- [ ] `phase-5:` commits; ledger updated.
