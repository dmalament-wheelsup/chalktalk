# chalktalk

An MCP server for composite NFL questions where the hard part is agreeing on what
the words mean.

> **Status: working.** Ingest, the feature layer, definitions and the MCP server
> are in place. See [`docs/plan/00-index.md`](docs/plan/00-index.md) for the
> phase ledger.

## The problem

Take a real question:

> "How many times have star players played fewer than 15 snaps before leaving
> with injury? Total by season."

Three of those phrases have no corresponding field anywhere in the data:

- **"star players"** — no such marker exists. Prior-season snap share above some
  percentile? Draft capital? Pro Bowl selection? Contract APY? Each yields a
  different answer.
- **"before leaving with injury"** — nothing records in-game exits. The injuries
  table is the weekly *report* — practice participation and game designation,
  published before kickoff. Exits have to be inferred.
- **"fewer than 15 snaps"** — the only clean one, and it still needs a decision
  about whether special teams counts.

A text-to-SQL system will silently pick definitions and return a confident
number. chalktalk refuses to do that.

## The definition gate

The query tool **rejects** any call containing an unresolved term:

```json
{ "error": "unresolved_term",
  "term": "star_player",
  "message": "No definition found. Call propose_definition first.",
  "suggestions": ["..."] }
```

`propose_definition` then returns candidates grounded in what is actually
computable, each tagged with the seasons it covers. You pick or edit. The choice
is saved and reused silently from then on, and every answer states which
definitions produced it.

Over time you accumulate a personal metric vocabulary you never sat down to
design. This inverts the usual semantic-layer model, where a data team authors
definitions up front.

## Install (development)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
uv run chalktalk doctor
```

`doctor` prints the resolved paths under `CHALKTALK_HOME` (default
`~/.chalktalk`), whether a built database is present, and the versions of duckdb,
nflreadpy and polars it will use.

```bash
uv run pytest
```

The default run is the unit tier and needs no data. Tests that require a built
database are marked `data` and are opt-in: `uv run pytest -m data`.

Once a database exists, install the shipped vocabulary:

```bash
uv run chalktalk defs install
```

That is 45 definitions — `early_exit`, `blowout`, `heavy_carries` and so on —
all ordinary specs you can read with `chalktalk defs show NAME` and change.
`star_player` is deliberately not among them.

## Building the database

```bash
uv run chalktalk build
```

This downloads every nflverse dataset from the 2013 season on and writes one
immutable `~/.chalktalk/data/nfl-YYYYMMDD.duckdb`, then points `CURRENT` at it.
Roughly a minute and 720 MB. `--plan` shows what would be downloaded without
downloading anything; `--seasons` and `--only` narrow it.

The file is disposable — rebuild it weekly and nothing is lost, because
definitions live outside it under `~/.chalktalk/definitions/`.

## Connect

Build the database first, then point an MCP client at the server.

```bash
claude mcp add chalktalk -- uv --directory /path/to/chalktalk run chalktalk serve
```

For Claude Desktop, the equivalent in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "chalktalk": {
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/path/to/chalktalk", "run", "chalktalk", "serve"],
      "env": { "CHALKTALK_HOME": "/Users/you/.chalktalk" }
    }
  }
}
```

Give `command` the **absolute** path to `uv` — `which uv` will tell you, commonly
`~/.local/bin/uv`. A bare `"uv"` works for `claude mcp add`, which inherits your
shell, but Claude Desktop is launched by the OS and does not get your `PATH`; it
fails to start the server with an error that does not mention `PATH`. Quit
Desktop completely and reopen it after editing the file — closing the window is
not enough.

`CHALKTALK_HOME` defaults to `~/.chalktalk` and holds the database, your
definitions and the audit log. Pass it explicitly if you keep them elsewhere.

The server exposes eleven tools. The ones that matter to a person:
`propose_definition` when a word has no agreed meaning yet, `save_definition`
once you have chosen, `query` to ask, and `raw_sql` for the questions the tool
surface cannot yet express. Every `query` answer carries the definitions it
used, the seasons it covered, a sample of matched rows and the SQL.

What a session looks like:

```
you   How many times have star players played fewer than 15 snaps
      before leaving with injury? By season.

      → unresolved_term: star_player
          star_by_snaps     top 10% of snap share last season, within position group
          star_by_contract  top 10% of pay as a share of the cap, within position group
          star_by_draft     a first-round pick

you   the contract one

      → saved star_player, copied from star_by_contract
      → Count of player-games (REG, 2013–2025) where star_player [prior season]
        and early_exit and snaps_unit < 15, by season.

        2013  2    2016  5    2019  3    2022  6
        2014  4    2017  7    2020  4    2023  5
        …

        definitions used: star_player, early_exit, played, regular, left_early,
        snap_drop, exit_evidence, listed_injured_next, on_reserve_soon,
        started, missed_next_game_as_starter, exit_corroborated
        warning: participation evidence is unavailable before 2016; snap_drop
                 carries the evidence in 2013–2015
```

The refusal is the point. Ask again next week and it answers straight away,
because `star_player` now means the thing you chose — and every answer says so.

Three things always come back with the number: the definitions behind it, the
seasons it could actually cover, and a sample of the matched rows. That last one
matters more than it sounds. A rested starter in the final week looks identical
in the data to an injury exit, so the rows are how you catch a wrong one.

## Your definitions

They live in `$CHALKTALK_HOME/definitions/`, one JSON file each, outside the
database — which is discarded and rebuilt every week. Losing them to a data
refresh would be the worst bug this could have, so they are kept somewhere a
rebuild cannot reach.

```bash
cd ~/.chalktalk/definitions && git init && git add -A && git commit -m "my vocabulary"
```

That is the whole backup story: they are plain JSON, so they diff, they merge,
and you can edit one by hand and the server picks it up. Previous versions are
kept under `.history/` whenever you overwrite or delete one.

```bash
uv run chalktalk defs list              # what you have
uv run chalktalk defs show early_exit   # what it means, all the way down
uv run chalktalk defs export ~/backup   # or import, for sharing a set
```

`chalktalk logs terms` tells you which of them you actually reach for, and which
raw fields you keep spelling out instead of naming.

## Rebuilding

The database is disposable and versioned by date. Rebuild it weekly during the
season — the whole thing takes about a minute and the old artifact stays on disk.

```bash
0 6 * * 3 /Users/you/.local/bin/uv --directory /path/to/chalktalk run chalktalk build
```

On macOS, launchd is more reliable than cron for a laptop that sleeps. Save this
as `~/Library/LaunchAgents/com.chalktalk.build.plist` and
`launchctl load` it:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>com.chalktalk.build</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/.local/bin/uv</string>
    <string>--directory</string><string>/path/to/chalktalk</string>
    <string>run</string><string>chalktalk</string><string>build</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>6</integer></dict>
  <key>StandardErrorPath</key><string>/tmp/chalktalk-build.log</string>
</dict></plist>
```

**Rollback is editing one file.** `data/CURRENT` holds the filename the server
opens; point it at the previous artifact and the next tool call picks it up
without a restart.

```bash
ls ~/.chalktalk/data/                       # the last three builds are kept
echo nfl-20260901.duckdb > ~/.chalktalk/data/CURRENT
```

`chalktalk doctor` will tell you what is currently loaded, how old it is, and
whether any of your definitions stopped compiling against it.

## Design

Architecture and rationale: [`CLAUDE.md`](CLAUDE.md).
Rejected alternatives and why: [`docs/decision-history.md`](docs/decision-history.md).

The short version: one process, one DuckDB file on local disk, 2013 season floor,
Python. Definitions are stored as specs rather than SQL and live outside the
database, which is discarded and rebuilt weekly.

## Data and attribution

Data comes from [nflverse](https://github.com/nflverse/nflverse-data), via
`nflreadpy`. chalktalk distributes code, not data — you pull from nflverse
yourself when you build the database.

Two distinct layers, worth keeping separate:

- The **compiled nflverse dataset** is licensed CC BY 4.0 (attribution, no
  ShareAlike).
- The **underlying NFL data** belongs to its respective owners and is governed by
  their terms of use. nflverse does not claim to grant rights to it, and neither
  does this project.

**The MIT license on this repository covers this project's code only.** It grants
no rights to NFL data.

## Credits

- [nflverse](https://github.com/nflverse) for the data infrastructure this is
  built on.
- [`nfl-mcp`](https://github.com/ebhattad/nfl-mcp) (MIT) — the ingest module here
  is adapted from theirs, with the notice retained. It solves a different problem
  well: if you want a fantasy football tool, use it rather than this.

## License

MIT — see [`LICENSE`](LICENSE).
