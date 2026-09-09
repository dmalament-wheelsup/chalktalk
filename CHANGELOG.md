# Changelog

Notable changes per release. Dates are the release date; the phase ledger in
[`docs/plan/00-index.md`](docs/plan/00-index.md) records when each piece landed
and every decision behind it.

## 0.1.0 — 2026-09-08

First release. An MCP server for composite NFL questions, where the hard part is
agreeing what the words mean.

### What it does

- **The definition gate.** A query containing a fuzzy word is refused
  structurally, with candidates and a next step, rather than answered from a
  default the server picked. `propose_definition` offers what the data can
  actually compute; the user chooses; the choice is saved and reused, and every
  answer names the definitions behind it.
- **Ingest.** `chalktalk build` pulls twelve nflverse datasets from 2013 into one
  immutable DuckDB artifact, published atomically. About a minute, roughly
  750 MB.
- **Feature layer.** Six entity tables and 241 documented attributes, each with a
  coverage window generated from the data rather than maintained by hand.
- **Five signals** — `rule`, `percentile`, `rank`, `delta`, `composite` — and
  nothing else. 45 definitions ship, all expressed in them.
- **MCP server.** Eleven tools over stdio, a guarded read-only SQL escape hatch,
  and an audit log that surfaces which questions the vocabulary cannot yet say.

### Known limitations, measured rather than hidden

- `early_exit` matches Robert Hainsey's rested 2022 week 18. He started, was
  rested, and missed the next game because Ryan Jensen returned from injury and
  took his job back — nothing in the data distinguishes that from an exit. This
  is why matched rows always come back.
- `early_exit` misses Nick Chubb's 2023 week 2, whose baseline share of 0.49
  falls under `regular`'s position-blind 0.5 floor. Running backs rotate.
- `star_by_snaps` barely discriminates among quarterbacks: 34 eligible in 2022
  with a median share of 0.965, so the top decile is four players. It works as
  intended for running backs, receivers and defensive linemen.
- Snap counts begin in 2013 and participation in 2016, so a prior-season snap
  definition first has data in 2014 and in-game exit evidence in 2016. The gate
  refuses rather than returning a misleading zero.

Nineteen of twenty-one hand-authored injury cases agree with the shipped
definitions, and all ten generality questions run without a line of code written
for any of them.

### Not included, deliberately

Hosted mode, a `star_player` definition, and depth-chart attributes. Reasoning
in [`docs/decision-history.md`](docs/decision-history.md).
