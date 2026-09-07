# Phase 10 — Release

## Goal

Installable, documented, rebuildable. Everything a stranger needs to run this
from a fresh machine, and the backlog for what comes after.

## Prerequisites

Phase 8 done; Gate B green.

## Steps

1. **Packaging.** Complete `pyproject` metadata (urls, classifiers, readme).
   `uv build`; `uvx --from ./dist/chalktalk-0.1.0-py3-none-any.whl chalktalk
   doctor` works. Check the name `chalktalk` on PyPI before publishing; if
   taken, publish as `chalktalk-mcp` and keep the import name. Publishing is
   optional for v1.

2. **README** — final shape:
   - What it is (existing copy) · Status.
   - Install: `uvx chalktalk doctor` / dev install.
   - Build: `chalktalk build` (time, disk ~2 GB, `CHALKTALK_HOME`).
   - Connect: `claude mcp add …` and Claude Desktop JSON.
   - A worked session: the injury-exit question end to end (refusal →
     propose → save → envelope), then one generality-suite question.
   - Your definitions: where they live, `git init` there, export/import.
   - Rebuild weekly: cron `0 6 * * 3 /path/to/uvx chalktalk build` and a
     launchd plist; rollback by editing `data/CURRENT`.
   - Data and attribution (existing), Credits, License.

3. **`CLAUDE.md` sync.** Update the Architecture section to name the six
   entities and five signals; fix participation coverage (D4); replace
   `query(question)` with `query(plan)`; point "Open questions" at
   `docs/plan/00-index.md` D1–D3. Keep it short — `CLAUDE.md` is the spec, the
   plan is the how.

4. **Versioning.** Tag `v0.1.0`. Add `CHANGELOG.md`.

5. **Doctor.** `chalktalk doctor` should now also: validate definitions,
   report broken ones, show build age and whether the current season is in
   progress, and warn if `CURRENT` is older than 8 days during the season.

## v1.1 backlog (not decisions — candidates)

- `chalktalk defs import <git-url>` for community definition repos (D3).
- Participation-derived play attributes on `player_play` → `play` entity:
  `was_pressure`, `route`, `defense_coverage_type` (2018+ per nflverse).
- Team-game lift into `play` for definitions (`off.`/`def.` are already
  namespaces for attributes; allow `{term: …, as: "off"}`).
- A `drive` entity from pbp `drive` ids.
- A sixth signal, `streak` (N consecutive games/plays satisfying a term).
  Requires a decision record in `decision-history.md` first (D22).
- Depth-chart attributes once the upstream schema is stable (D23).
- `chalktalk build --refresh-current` to rebuild only the in-progress season
  and derived tables.
- MCP elicitation where the client supports it (D19).
- Hosted mode: **no** — see `decision-history.md`.

## Done when

- [ ] Fresh-machine install → build → connect → the worked session works as documented.
- [ ] `CLAUDE.md` and the plan agree on every name.
- [ ] `v0.1.0` tagged; ledger complete.
