# Phase 1 — Scaffold

## Goal

A runnable, testable, empty package: `uv run chalktalk --help` works, `uv run
pytest` passes, ruff is clean, configuration and paths are resolved in one
place, and there is a DuckDB connection helper that later phases build on.

## Prerequisites

None. Read [09-testing.md](09-testing.md) §Tiers before starting so the test
layout is right from the first commit.

## Deliverables

```
pyproject.toml
.gitignore                      (add entries; keep what exists)
src/chalktalk/__init__.py       __version__
src/chalktalk/config.py         Settings
src/chalktalk/paths.py          path resolution under CHALKTALK_HOME
src/chalktalk/db.py             connections + CURRENT pointer
src/chalktalk/cli.py            click group with stub subcommands
src/chalktalk/log.py            logging setup (stderr; MCP owns stdout)
tests/conftest.py
tests/unit/test_paths.py
tests/unit/test_db.py
```

## Steps

1. **`pyproject.toml`.** Build backend `hatchling`. Name `chalktalk`, version
   `0.1.0`, `requires-python = ">=3.12"`, license `MIT`, description = README
   line 1. Script: `chalktalk = "chalktalk.cli:main"`.

   Runtime dependencies (lower bounds are what was verified):
   ```
   mcp>=1.2,<2
   duckdb>=1.5
   nflreadpy>=0.1.5
   polars>=1.30
   pyarrow>=17
   pydantic>=2.7
   click>=8.1
   platformdirs>=4
   pyyaml>=6
   ```
   Dev group: `pytest>=8`, `pytest-cov`, `ruff>=0.6`.

   Ruff: `line-length = 100`, select `E F I UP B T20`, `target-version =
   "py312"`; per-file ignore `T20` for `src/chalktalk/cli.py` and `tests/`.
   (`T20` bans `print` — MCP stdio owns stdout.)

   Pytest: `testpaths = ["tests"]`, `markers = ["data: requires a built
   database under CHALKTALK_HOME"]`, `addopts = "-m 'not data'"`. The data
   tier is opt-in: `uv run pytest -m data`.

2. **`.gitignore`** — ensure: `.venv/`, `*.duckdb`, `*.duckdb.wal`,
   `*.duckdb.building`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`,
   `dist/`, `.chalktalk/`.

3. **`config.py`.** One frozen dataclass built by `Settings.load()`; env
   override names in comments:

   ```python
   @dataclass(frozen=True)
   class Settings:
       home: Path                         # CHALKTALK_HOME, default ~/.chalktalk
       season_floor: int = 2013           # CHALKTALK_SEASON_FLOOR
       baseline_lookback: int = 1         # seasons of history for prior-season attributes
       pctile_default_min_share: float = 0.5   # D13/D24: share of team games for percentile eligibility
       raw_sql_row_cap: int = 500         # CHALKTALK_RAW_SQL_ROW_CAP
       raw_sql_timeout_s: float = 10.0    # CHALKTALK_RAW_SQL_TIMEOUT
       query_row_cap: int = 1000
       query_default_limit: int = 200
       query_timeout_s: float = 30.0
       sample_rows: int = 10
       duckdb_memory_limit: str | None = None   # CHALKTALK_DUCKDB_MEMORY_LIMIT, e.g. "6GB"
       duckdb_threads: int | None = None        # CHALKTALK_DUCKDB_THREADS
   ```

4. **`paths.py`.** Pure functions over `Settings`: `data_dir`, `definitions_dir`,
   `history_dir`, `logs_dir`, `cache_dir`, `current_pointer` (`data/CURRENT`),
   `artifact_path(s, built: date)` (`data/nfl-YYYYMMDD.duckdb`),
   `ensure_layout(s)` (mkdir -p). Never create files here.

5. **`db.py`.**
   ```python
   class NoDatabase(Exception): ...
   def read_current(s: Settings) -> Path | None     # None if CURRENT absent or target missing
   def write_current(s: Settings, artifact: Path)   # write CURRENT.tmp then os.replace
   def open_rw(path: Path, s: Settings) -> duckdb.DuckDBPyConnection   # memory/threads pragmas
   def open_ro(path: Path, s: Settings) -> duckdb.DuckDBPyConnection
       # duckdb.connect(path, read_only=True); then, in this order:
       # SET enable_external_access=false; SET lock_configuration=true
   ```
   `open_ro` is the only way the server ever opens the file. Test that
   `SELECT * FROM read_csv('/etc/hosts')` raises on an `open_ro` connection.

6. **`log.py`.** `setup_logging(level)` → stderr handler only.

7. **`cli.py`.** `click` group `main`; subcommands `build`, `serve`,
   `coverage`, `defs`, `logs`, `doctor`. All but `doctor` print
   `not implemented (phase N)` and exit 2. `doctor` prints resolved paths,
   whether `CURRENT` points at an existing file, and Python / duckdb /
   nflreadpy / polars versions.

8. **Tests.** `test_paths.py`: env override respected; defaults under home.
   `test_db.py`: `write_current`/`read_current` round-trip; `open_ro` blocks
   `read_csv`.

9. **README.** Add "Install (development)": `uv sync` · `uv run chalktalk
   doctor` · `uv run pytest`.

## Interfaces

Everything in `config.py`, `paths.py`, `db.py` above is a contract. Add to
them; do not rename.

## Acceptance

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest -q                 # all pass; nothing skipped for a missing DB
uv run chalktalk doctor          # exit 0 with no database
uv run chalktalk build           # "not implemented (phase 2)"; exit 2
```

## Pitfalls

- `duckdb.connect(path, read_only=True)` fails if another process has the file
  open read-write. Builds write to a new filename, so this only bites if
  something opens `CURRENT`'s target with `open_rw`. Don't.
- `lock_configuration` must be the last `SET` in `open_ro`.
- Nothing in this phase imports nflreadpy.

## Done when

- [ ] Acceptance passes.
- [ ] `phase-1:` commits in `git log`.
- [ ] Ledger row updated in `00-index.md`.
