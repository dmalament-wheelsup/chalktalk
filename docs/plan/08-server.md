# Phase 8 — MCP server

## Goal

A FastMCP stdio server exposing eleven tools; a guarded raw-SQL escape hatch;
an audit log; hot reload of the database pointer and the definitions
directory. Tool descriptions are product surface — the calling model reads
them, so they are specified here verbatim.

## Prerequisites

Phase 7 done.

## Deliverables

```
src/chalktalk/server.py         FastMCP app, tool registration, lifecycle
src/chalktalk/sqlguard.py       raw SQL validation + bounded execution
src/chalktalk/audit.py          JSONL log + summary
src/chalktalk/cli.py            serve, logs summary
tests/unit/test_sqlguard.py
tests/unit/test_server_tools.py   call tools in-process (check the installed mcp version for the in-memory client API; amend if it differs)
docs/README.md                  "Connect" section
```

## Lifecycle

- On start: `Settings.load()`; `install_shipped`; open `read_current()` with
  `open_ro` (or run in *no-database* mode: every data tool returns
  `no_database` with the message "run `chalktalk build`"); load `Coverage`,
  `DefinitionStore`; log the broken-definitions banner to stderr.
- Before every tool call: if `CURRENT` changed → reopen (rollback is editing
  `CURRENT`); `store.maybe_reload()`.
- All logging to stderr. Nothing writes to stdout except the MCP transport.

## Tools (exact names, inputs, and description text)

**`describe_schema(entity: str | None = None, include_play_columns: bool = False)`**
Returns entities, their namespaces, and attributes `{name, type, family,
description, coverage: {first, last}}`. For `play`, only `PLAY_DOCS` columns
unless `include_play_columns`.
> Describe what can be queried: the six entities (game, team_game, team_season, player_game, player_season, play), their namespaces (e.g. `prior.`, `game.`, `next.`), and every attribute with its coverage window. Attributes are the only raw fields a plan may reference. Anything that is a *concept* rather than a field — "star", "starter", "injury", "blowout" — must be a term: see `list_definitions` and `propose_definition`.

**`coverage(ref: str)`** — `table.column`, `entity.attr`, or a definition name → `{first, last, has_gaps, seasonal}`.

**`list_definitions(include_broken: bool = True, family: str | None = None)`** → summaries with explanation, coverage, source, version, `broken_reason`.
> The user's vocabulary. Every term used in a query must be here. Broken definitions (a column they depend on disappeared) are listed with the reason and cannot be used until fixed.

**`get_definition(name: str)`** → full spec, nested explanation, evidence attributes, coverage, history versions.

**`propose_definition(term: str, context: str | None = None, entity: str | None = None)`** → `Proposal`.
> Call this when a query needs a concept that has no definition yet, or when `query` returns `unresolved_term`. Returns existing near-matches, ready-to-save candidate definitions grounded in what the data can compute (each with an English explanation and coverage), related attributes, and the five signal schemas for composing something new. Present the candidates to the user and let them choose or adjust. Do not pick one silently.

**`save_definition(name, entity, signal, params, aliases=[], description=None, basis=None, copy_of=None, overwrite=False)`** → saved definition + explanation + coverage, or `invalid_definition`.
> Save a definition the user has chosen. `copy_of` copies an existing definition (e.g. `star_by_snaps`) under a new name. Saving is versioned; previous versions are kept. Tell the user what was saved in plain English (the returned explanation).

**`delete_definition(name: str)`**

**`explain_query(plan: dict)`** → everything `query` returns except rows/sample/timing.
> Dry run. Shows the English reading of the plan, which definitions it will use, the seasons it will cover, warnings, and the SQL. Use it to confirm interpretation with the user before running expensive or ambiguous queries.

**`query(plan: dict)`** → envelope.
> Run a structured query. `plan` is `{entity, where, seasons, game_types, group_by, metrics, order_by, limit, allow_partial_coverage, sample_rows, question}`; `where` clauses are `{"term": name}` for concepts, `{"attr": name, "op", "value"}` for raw fields, and `{"not"|"any_of"|"all_of": …}` to combine. Every fuzzy word in the user's question must be a term. If a term has no definition this tool returns `unresolved_term` — do **not** replace it with an attribute rule to get past the error; call `propose_definition` and ask the user. The result includes `definitions_used`, `seasons` actually covered, a `sample` of matched rows, and the SQL; report the definitions and coverage alongside the number — the number is not meaningful without them. If `warnings` mention evidence unavailable for early seasons, say so.

**`raw_sql(sql: str, limit: int = 500)`** → `{columns, rows, truncated, timing_ms}` or `sql_rejected` / `sql_timeout`.
> Escape hatch: read-only SQL over every raw and derived table (see `describe_schema` and `coverage`). Single SELECT/WITH statement, row cap, timeout, no file or network access. Results carry no definitions — prefer `query` for anything that involves a concept, and tell the user when a number came from raw SQL. Every call is logged; recurring raw queries are how new attributes get prioritised.

**`build_status()`** → artifact, `built_at`, versions, seasons (first/last, in-progress), definitions (count, broken), `CHALKTALK_HOME`.

Nested `Clause` shapes are hard to express in JSON schema; accept `plan: dict`
and validate with pydantic, returning `invalid_plan` with the pydantic error
list. Put the shape in the description as above.

## `sqlguard.py`

```python
class SqlRejected(Exception)
def validate(sql: str) -> str                       # returns the cleaned single statement or raises
def bounded(sql: str, limit: int) -> str            # SELECT * FROM (<sql>) AS _q LIMIT ?  (limit + 1 to detect truncation)
def run_raw(conn_ro, sql, limit, timeout_s) -> RawResult
```
`validate`: strip comments; exactly one statement (a trailing `;` allowed);
must begin with `SELECT` or `WITH`; reject word-boundary matches of `ATTACH
COPY EXPORT IMPORT INSTALL LOAD PRAGMA SET RESET CREATE INSERT UPDATE DELETE
DROP ALTER CALL CHECKPOINT VACUUM`. This is defence in depth — the real
boundary is `open_ro` (read-only, `enable_external_access=false`,
`lock_configuration=true`, phase 1). Timeout via `threading.Timer →
conn.interrupt()`.

## `audit.py`

Append one JSON line per `query`, `explain_query`, `raw_sql`:
`{ts, tool, question, entity, plan | sql, definitions_used: [{name, version}],
row_count, timing_ms, error}`. `chalktalk logs summary [--since 30d]`:
calls per tool, top 20 `raw_sql` fingerprints (whitespace collapsed, numbers
→ `N`, quoted strings → `'S'`) with counts and last-seen — "recurring raw
queries are the roadmap for what should become a first-class attribute."

## Connect

Development: `claude mcp add chalktalk -- uv --directory /path/to/chalktalk run chalktalk serve`.
After publishing: `uvx chalktalk serve`. Document `CHALKTALK_HOME` passthrough
(`-e CHALKTALK_HOME=…`). Claude Desktop config JSON equivalent in the README.

## Acceptance

```bash
uv run pytest tests/unit/test_sqlguard.py tests/unit/test_server_tools.py
uv run chalktalk serve            # starts; Ctrl-C exits cleanly
```
In-process tests: `list_definitions` returns the shipped set; `query` with
`{term: star_player}` → `unresolved_term`; after `save_definition(name=
"star_player", copy_of="star_by_snaps")` the same plan runs; `raw_sql("select
count(*) from player_game")` works; `raw_sql("copy (select 1) to '/tmp/x'")`
→ `sql_rejected`; `raw_sql("select * from read_csv('/etc/passwd')")` → error,
never rows; `raw_sql` on a cross join with a 0.2 s timeout → `sql_timeout`.

Manual: connect from Claude Desktop or Claude Code; ask the injury-exit
question; observe `unresolved_term` → `propose_definition` → user choice →
`save_definition` → `query` envelope; then ask two of the generality-suite
questions in natural language and confirm the model reaches for terms rather
than inventing attribute rules.

## Pitfalls

- stdout hygiene: one stray `print` corrupts the transport.
- Keep envelopes bounded: rows ≤ `query_row_cap`, sample ≤ 50, `sql` included
  once.
- FastMCP derives JSON schema from type hints; `dict` inputs get an open
  schema, which is why the description carries the shape.
- Reopening the DB on `CURRENT` change must close the old connection.

## Done when

- [ ] Eleven tools registered with the descriptions above; tests green.
- [ ] Manual session completed and any friction noted in `00-index.md` amendments.
- [ ] `phase-8:` commits; ledger updated.
