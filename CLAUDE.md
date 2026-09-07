# chalktalk

An MCP server for composite NFL questions where the hard part is agreeing on
what the words mean.

Design rationale and rejected alternatives: [`docs/decision-history.md`](docs/decision-history.md).
Consult it before reopening a settled decision — several of the rejected paths
(serverless, text-to-SQL, forking `nfl-mcp`, 1999 coverage) look attractive on
first principles and were dropped for specific reasons.

Implementation plan: [`docs/plan/00-index.md`](docs/plan/00-index.md) — phases,
status ledger, and every resolved decision (D1–D23). Start there before writing
code. Where this file and the plan disagree on a name, the plan is current.

## The bet

The valuable unit is not the answer. It's the **definition** the answer rests on.

Take a real target question:

> "How many times have star players played fewer than 15 snaps before leaving
> with injury? Total by season."

Three phrases have no corresponding field anywhere in nflverse:

- **"star players"** — no such marker exists. Could mean prior-season snap share
  above a percentile, draft capital, Pro Bowl selection, contract APY
  percentile, prior-season fantasy finish. Each yields a different answer.
- **"before leaving with injury"** — nothing records in-game exits. The injuries
  table is the weekly *report* (practice participation, game designation),
  published before kickoff. Injury exits must be inferred: anomalously low snaps
  relative to that player's own baseline, followed by an injury designation or
  absence the next week.
- **"fewer than 15 snaps"** — the only clean one, and it still needs a decision
  about whether special teams counts.

Any text-to-SQL system will silently pick definitions and return a confident
number. This project refuses to do that.

## What this is not

- Not a fantasy football tool. `nfl-mcp` (MIT, ebhattad/nfl-mcp) owns that
  space and does it well — its tool surface is shaped around answering "who do I
  start this week."
- Not a stats website.
- Not natural-language SQL.
- Not hosted. Open source, self-run, so model cost belongs to the user and the
  data-rights question stays clean.

## Core mechanism: the definition gate

The query tool **rejects** any call containing an unresolved term. This is
structural, not a prompt instruction — models are eager and will otherwise pick
a reasonable-looking default and move on.

```
{ "error": "unresolved_term",
  "term": "star_player",
  "message": "No definition found. Call propose_definition first.",
  "suggestions": [...] }
```

Flow:

```
MCP client
    |
    v
query(plan)
    |
    v
unresolved term? ---- yes ----> propose / save ----> definitions store
    |                                                (outlives rebuilds)
    no
    |
    v
compile to SQL --------------------------------> nfl.duckdb
                                                 (rebuilt weekly)
```

`plan` is structured, not natural language: the calling model parses the
user's question into entity, terms, attribute rules, grouping and metrics. The
server never interprets English, so the gate is a lookup, not an NLP problem.

On an unresolved term, `propose_definition` returns candidates **grounded in
what is actually computable**, each tagged with its coverage window. The user
picks or edits. The choice is saved as a named definition and reused silently
thereafter, with the answer stating which definition it used.

Over time the user accumulates a personal metric vocabulary they never sat down
to design. This inverts the usual semantic-layer model (Cube, dbt), where a data
team authors definitions up front.

## Architecture

Four layers. A **feature layer** of six entity tables (`game`, `team_game`,
`team_season`, `player_game`, `player_season`, `play`) exposing many documented
attributes, each with a coverage window. **Five general signals** — `rule`,
`percentile`, `rank`, `delta`, `composite` — the only ways a definition can be
expressed; none knows what a "star" or an "injury" is. **Definitions** built
from those signals, shipped and user-authored alike (`early_exit` is a
composite of six smaller definitions, not code). A **gate, compiler and
envelope** over structured plans. No concept ever gets its own code: if it
cannot be expressed in the five signals, the feature layer is missing an
attribute. The injury question above is one instance; the acceptance test is
ten unrelated questions running on definitions alone (plan, phase 7).

Single process. One DuckDB file on local disk. No object storage in the query
path, no serverless, no cold starts. The dataset is ~1–1.5M plays; this is not a
big-data problem and should not be built like one.

### Two stores, not one

The DuckDB file is discarded and rebuilt weekly. Definitions **must not** live
inside it — they are the user's accumulated work, and losing them on a data
refresh would be the worst bug in the product. Separate SQLite file or plain
JSON. Versioned, exportable, ideally a git repo so history and remote backup
come free.

### Definitions are specs, not SQL

Store `{signal: "snap_share", basis: "prior_season", threshold: "p90"}` and
compile at query time. Storing raw SQL makes it impossible to validate against
coverage, migrate on schema change, or explain in plain English.

Validate the entire definition store on load. Fail loudly on anything that no
longer compiles — a definition referencing a renamed or dropped nflverse column
must not degrade silently into a wrong answer.

### Coverage registry

First and last season for every column, **generated at build time from the
actual data**, never hand-maintained. This is what lets the gate refuse a 2005
query against a snap-share definition instead of returning a misleading zero.

### Result envelope

Every result returns: rows, definitions used, seasons covered, matched-row
sample, and generated SQL. The model narrates; the envelope makes the narration
checkable. For this class of question an unverifiable number is worse than no
number.

### Escape hatch

Raw SQL tool: read-only connection, row cap, statement timeout, no extensions.
Log every call — recurring raw queries are the roadmap for what should become a
first-class tool.

## Data

Source: nflverse (github.com/nflverse/nflverse-data), via nflreadpy.

**Season floor: 2013.** Not arbitrary. Snap counts come from Pro Football
Reference and start ~2012; participation data is 2016–2025 (verified 2026-09-07;
the build-time coverage registry is the runtime authority). Play-by-play reaches
back to 1999, but the questions this project targets bottom out at snap-level
data, so earlier seasons add cleaning burden without adding answerable
questions.

Build step produces a single `.duckdb` artifact: narrow tables plus derived
columns (prior-season baselines, rolling shares) that would otherwise be
recomputed constantly. Immutable, versioned by date, rebuilt weekly during
season. Rollback is a redeploy.

## Language and starting point

Python. nflreadpy is the maintained nflverse Python port, the DuckDB Python
client is solid, and the MCP Python SDK is mature.

**Vendor the ingest, do not fork the repo.** Copy `nfl-mcp`'s ingest module in
with attribution (MIT — retain the notice) and own it outright. The valuable
part is a few hundred lines encoding which nflverse releases to pull, per-table
season floors, and idempotent re-ingest. The rest of that codebase is a fantasy
tool that would be deleted.

Before relying on it, **check whether their ingest is lossless** — type coercion
or dropped columns would be inherited silently and surface as a broken
definition months later.

Do not run `nfl-mcp` alongside this server. Its `nfl_query` tool lets a model
route around the definition gate entirely.

## Testing

**The definition-compiler test suite must be hand-authored, not generated.**

Write fixture questions and their expected answers from football knowledge,
including specific early-exit cases known by name. Everything else in this
project is checkable by reading it. Whether the compiler produces the *right*
number is only checkable against answers already known, and it is exactly where
generated code drifts unnoticed.

Validate the early-exit logic against five remembered injury exits before
building anything on top of it. If it can't find the ones already known, no
amount of architecture saves it.

## Known false-positive risk

A rested starter in a Week 18 game with nothing at stake looks identical in the
data to an injury exit. Return matched rows, always.

## Licensing

MIT (see `LICENSE`). Chosen to match what this vendors — `nfl-mcp` is MIT, as is
`nflreadpy` — so there is no compatibility question and no dual-license section
to maintain. Copyleft was considered and dropped: a code license would not
protect the definitions vocabulary, which is the only thing here plausibly worth
protecting, and it would cost adoption for a tool whose whole distribution model
is people running it themselves.

Two things the repo license does *not* cover, both easy to get wrong:

- **The data.** MIT covers this project's code. It grants nothing over nflverse
  data or the NFL data underneath it. The README states this explicitly, because
  a permissive license at the repo root otherwise reads as blessing everything in
  scope.
- **The definitions.** If the community definitions store ships, it is content,
  not software — CC0 or CC BY fits it, and a code license would be a category
  error. Decide this deliberately rather than letting it inherit MIT by default.

Retain `nfl-mcp`'s copyright notice in the vendored ingest module.

## Open questions

All three are resolved in the implementation plan — `docs/plan/00-index.md`
D1 (license and attribution), D2 (star-player defaults), D3 (community
definitions). Kept here for the reasoning; do not treat them as open.

- **nflverse attribution (courtesy, not a blocker).** Since this ships as open
  source that users run themselves, the project distributes code rather than
  redistributing data — each user pulls from nflverse directly, same as
  `nfl-mcp` already does. Worth a GitHub issue on nflverse-data or a note in
  their Discord describing what's being built and asking what attribution they'd
  like. Whatever comes back, honor it in the README.

  The license half of this is **settled** (2026-09-07, read from
  nflverse-data's `LICENSE.md`): Creative Commons **Attribution 4.0
  International** — plain BY, *not* BY-SA. The open worry was ShareAlike, which
  would have propagated obligations into anything built over the data. It does
  not apply. Attribution is the only requirement, and the README carries it.

  Unchanged by that finding: the CC license covers the *compiled dataset*. The
  nflverse project states the underlying NFL data belongs to its respective
  owners and is governed by their terms of use — they do not claim to grant
  rights to it. That distinction would matter for a hosted or monetized service.
  It does not much matter for this scope, and would need revisiting if the scope
  changes.
- Which "star player" definitions ship as suggested defaults.
- Whether a shared/community definitions repo is worth building. This is
  probably the only real moat — the data is public and the code is forkable, but
  a curated set of definitions people trust and cite is hard to displace.
