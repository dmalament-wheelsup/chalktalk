# Decision history

Condensed record of the design conversation that produced [`CLAUDE.md`](../CLAUDE.md).
Source: a claude.ai thread ("NFL data architecture advice"), 22 exchanges, imported
2026-09-07. Kept because CLAUDE.md states conclusions but not what was rejected —
and several of the rejected paths look attractive enough to be re-proposed.

The design changed direction substantially. Read this before reopening a settled
question.

---

## How the target changed

| | Started as | Ended as |
|---|---|---|
| Product | Web app: Next.js + assistant-ui over nflverse | Open-source MCP server, self-run |
| Infra | Cloudflare Workers + Containers + R2 + DuckDB | Single always-on process, one local DuckDB file |
| Interface | Text-to-SQL over ~370 pbp columns | Definition gate + compiled specs |
| Coverage | 1999–2026 (the "history moat") | 2013+ |
| Language | TypeScript | Python |
| Reuse | Fork `nfl-mcp` | Vendor its ingest module only |
| Monetization | Possible hosted/directory listing | None; self-run |

---

## Rejected: the Cloudflare architecture

The thread opened by importing a ChatGPT conversation that recommended Cloudflare
Workers + Containers + R2 with Parquet byte-range reads. Two of its calls were
right — don't run DuckDB inside a Worker, and R2 + Parquet is a sound storage
shape. It was rejected anyway:

- **It answered the wrong question.** The whole thread was a compute-placement
  debate; nobody asked whether a live query engine was needed at all. At ~1–1.5M
  plays the dataset plausibly fits in RAM.
- **Scale-to-zero fights this workload.** Container disk is ephemeral, so every
  cold wake re-reads Parquet metadata from R2 and rebuilds state. First question
  after idle pays boot + DuckDB init + footer fetches before the model sees a row.
  It optimized cost, which was never the painful axis; latency was.
- **More moving parts, not fewer.** A Worker in front of a Container is more
  machinery than one Node/Python process with the file on local disk, at roughly
  the same cost.

If serverless or object-storage-in-the-query-path comes back up, this is why it
was dropped.

## Rejected: text-to-SQL as the interaction model

Superseded by the definition gate. Relevant nuance, since it cuts slightly against
the decision: dbt reran their semantic-layer-vs-text-to-SQL benchmark in April
2026 — Sonnet 4.6 and GPT-5.3 Codex both scored 64.5% overall on text-to-SQL and
100% on questions inside a well-modeled semantic layer's scope. So structured
layers still win decisively where they have coverage, but raw SQL is more viable
than older conventional wisdom suggests. That argues for a **smaller** starting
metric set with the SQL escape hatch carrying more weight — not for abandoning
the gate.

The security note that drove the escape hatch's constraints: an unguarded
`queryNFL(sql)` hands a model arbitrary SQL against a DuckDB instance that can
read local files and, with httpfs, reach the network.

## Rejected: exhaustive named parameters

An earlier design had every assumption as an explicit tool parameter
(`starDefinition`, `snapThreshold`, `countSpecialTeams`, `exitEvidence`, ...).
Replaced by the gate because it front-loads thinking into parameters nobody has
needed yet, where the gate defers it to the moment it matters.

Three failure modes the gate has to be designed against:

- **Confirmation fatigue.** If every question becomes a four-turn negotiation
  it's worse than SQL. Offer a default and let one word override it — "I'll treat
  star as top-50 APY; say otherwise" — not an interrogation. Skip the path
  entirely when a question is already fully specified.
- **Synonym sprawl.** Users produce "star", "elite", "top guys", "studs".
  Fuzzy-match against existing definitions before creating a new one, or you get
  six near-identical entries and no idea which produced which answer.
- **Out-of-window definitions.** Store the valid range with the definition and
  refuse loudly past it, or a 2005 query returns a clean-looking zero.

**MCP elicitation** is built for this flow, but Claude Code only supports it as of
2.1.76, Claude Desktop doesn't, and it's an open request for claude.ai. Build on
plain error returns — they work in every client — and layer elicitation on later
where it exists.

## Rejected: hosted connector in the Claude directory

Briefly the plan, since it moves inference cost onto the user's subscription.
Dropped because:

- **Ownership verification.** Submissions must show you own the API, domain and
  resources the connector touches; wrapping someone else's API without consent
  won't pass. The nflverse data isn't ours.
- **Submission overhead.** OAuth 2.1 with PKCE, streamable HTTP, per-tool
  annotations, a public privacy policy (a missing one is an immediate rejection),
  plus a Team plan for portal access.
- **No visual surface.** A connector returns rows and lets someone else's model
  narrate them. Tool names and descriptions become the entire product.
- **Weak differentiator.** "Hosted, no install" is forkable in a weekend.

Self-hosted open source gets most of the reach at a tenth of the overhead. The
honest cost: a directory listing is one click, `pip install` is not, and
non-technical people won't do it.

If the hosted version ever returns: hard spend cap and per-connection rate limits
from day one, before anyone finds it. Retrofitting limits onto a service people
rely on is much worse.

## Rejected: cloud sync for the definitions store

The premise was right — that file is where accumulated value lives — but sync
means accounts, auth, an operated service, a privacy policy, conflict resolution
and a bill, reintroducing everything self-hosting avoided, for a file measured in
kilobytes.

Cheaper answers: plain file in a predictable location (user's own Dropbox/iCloud/
Syncthing handles it), or a git repo (versioning, diffs, remote backup,
portability free — and "why did this number change last month" becomes `git log`).
First-class export/import regardless.

The real risk to that file is silent rot, not disk failure. Validate on load,
fail loudly. That's worth building before any backup story.

Where the instinct does lead somewhere: **sharing definitions, not syncing them.**
A public repo of community NFL definitions — five accepted meanings of "star
player", each with provenance and coverage window — is the vocabulary layer for
football analytics. Genuinely contested territory nobody owns, and the only real
moat available, since the data is public and the code is forkable.

## Rejected: the 1999 history moat

Held for two exchanges and then abandoned. nflverse pbp reaches back to 1999 and
`nfl-mcp` starts at 2013, so 14 seasons looked like an opening — the entire
pre-analytics-revolution era, era comparisons, rule-change effects, franchise arcs.

It died on contact with the actual target question. **Snap counts come from Pro
Football Reference and start ~2012; participation data is 2016–2024.** The
questions this project exists to answer bottom out at snap-level data, so those
extra seasons add cleaning burden without adding answerable questions. 2013 also
wasn't arbitrary for `nfl-mcp` — it's where every nflverse source is complete and
consistent.

## Rejected: fork `nfl-mcp`

Was the recommendation for one exchange (once Python was settled, the main cost of
forking disappeared), then reversed. The reason that matters:

**Divergence.** Deleting most of their tool surface and adding a definitions layer
is a fork that can't merge back. Their ingest becomes a snapshot, not a maintained
dependency — when nflverse renames a column mid-season and they fix it upstream,
you don't get the fix, you find out from a broken query. This is consistently
underrated because the fork feels like an ongoing relationship for about two
months and then silently isn't one.

Smaller: their schema is broad by design (whole nflverse family) where this design
wants narrow tables plus derived columns, and their ingest has no derived-table
stage. A GitHub fork also carries a visible parent relationship — awkward framing
if this is ever positioned as its own project.

Vendoring gets the same head start with none of it.

---

## On `nfl-mcp` (the thing being differentiated against)

MIT, `ebhattad/nfl-mcp`, `pip install nfl-mcp`. Python, nflreadpy + DuckDB, no
server and no credentials, container image rebuilt weekly by a GitHub workflow
that ingests at build time. Season tables 2013–present; NGS from 2016, FTN
charting from 2022; `teams`, `ff_playerids` and both fantasy rankings tables are
current-only. Ingest is configurable: `nfl-mcp ingest --start 2020 --end 2024`.

Its tool list is unambiguously a fantasy manager's toolkit — `nfl_td_luck`,
`nfl_role_trend`, `nfl_fantasy_rankings`, `nfl_contract_value`,
`nfl_separation_opportunity`. Built for "who do I start this week," not "how has
fourth-down aggression changed since 1999." Their tools are shaped around a week;
historical analysis wants tools shaped around eras. Different verbs entirely.

They already solved the guardrail problem: `nfl_query` takes raw SQL with a
500-row cap and a 10-second timeout.

## Why the gate needs the whole tool surface

If `nfl-mcp` is connected alongside this server, a model can call `nfl_query`
directly and route around the unresolved-term check. A gate with a door next to it
isn't a gate. This is the architectural reason the server has to be ours rather
than a wrapper.

## Other tools looked at

- **Cube Core** — Apache 2.0, governed metrics over SQL/REST/GraphQL/MCP
- **Wren AI** — JSON modeling language for business concepts and relationships
- **Vanna** — no semantic layer; training/retrieval over DDL, docs, past Q→SQL pairs

All assume a data team authors definitions up front. The inversion here is that
the analyst authors them mid-question, as a byproduct of curiosity.

---

## Rejected: copyleft for the code

Considered because one of this thread's own conclusions cuts toward it — "hosted,
no install is forkable in a weekend" is exactly the situation AGPL exists for.

Dropped, because it would protect the wrong asset. The moat identified here is a
curated definitions vocabulary, and that is content, not code; a code copyleft
does not reach it. The cost, meanwhile, is real — copyleft is friction for a tool
whose entire distribution model is other people installing and running it.

MIT instead, matching `nfl-mcp` and `nflreadpy`, so the vendored ingest raises no
compatibility question. Apache-2.0 remains the sane upgrade if a patent grant or
explicit contributor terms ever matter; the switch is cheap while the contributor
list is short.

## Still open

- **nflverse attribution.** Courtesy, not a blocker — see CLAUDE.md. Downgraded
  from "biggest risk in the project" once hosting and monetization came off the
  table. The *license* half is closed: nflverse-data is CC BY 4.0, plain
  attribution, no ShareAlike (verified from `LICENSE.md`, 2026-09-07). What
  remains is asking nflverse how they'd like to be credited.
- Which "star player" definitions ship as suggested defaults.
- Whether the community definitions repo is worth building.

## First thing to build

Ingest and the coverage registry — small and verifiable — not the whole thing at
once. And validate the early-exit logic by hand against five remembered injury
exits before building anything on top of it.
