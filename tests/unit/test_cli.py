"""CLI surface: stubs exit 2 and say which phase, doctor works with no database."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from chalktalk import db, paths
from chalktalk.cli import main
from chalktalk.config import Settings


@pytest.mark.parametrize(
    ("command", "phase"),
    [("serve", 8), ("logs", 8)],
)
def test_stubs_exit_two(command: str, phase: int) -> None:
    result = CliRunner().invoke(main, [command])
    assert result.exit_code == 2
    assert f"not implemented (phase {phase})" in result.output


def test_help_lists_every_subcommand() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ("build", "serve", "coverage", "defs", "logs", "doctor"):
        assert command in result.output


def test_doctor_without_a_database(tmp_settings: Settings) -> None:
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "no database" in result.output
    assert str(tmp_settings.home) in result.output


def test_doctor_reports_the_current_artifact(tmp_settings: Settings) -> None:
    artifact = paths.artifact_path(tmp_settings, date(2026, 9, 7))
    db.open_rw(artifact, tmp_settings).close()
    db.write_current(tmp_settings, artifact)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "no database" not in result.output
    assert artifact.name in result.output


def test_build_plan_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from chalktalk.ingest import loaders

    monkeypatch.setattr(loaders, "current_season", lambda: 2025)
    monkeypatch.setattr(
        loaders, "load", lambda d, season: pytest.fail("--plan must not load anything")
    )
    result = CliRunner().invoke(main, ["build", "--plan"])
    assert result.exit_code == 0
    assert "pbp" in result.output
    assert "season floor 2013" in result.output


def test_build_rejects_an_unknown_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    from chalktalk.ingest import loaders

    monkeypatch.setattr(loaders, "current_season", lambda: 2025)
    result = CliRunner().invoke(main, ["build", "--plan", "--only", "nonesuch"])
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_build_rejects_a_bad_season_range() -> None:
    result = CliRunner().invoke(main, ["build", "--seasons", "last-year"])
    assert result.exit_code != 0
    assert "season" in result.output


def test_coverage_without_a_database(tmp_settings: Settings) -> None:
    result = CliRunner().invoke(main, ["coverage"])
    assert result.exit_code != 0
    assert "no database" in result.output


def test_coverage_reads_the_registry(tmp_settings: Settings) -> None:
    from chalktalk import coverage as coverage_mod

    artifact = paths.artifact_path(tmp_settings, date(2026, 9, 7))
    conn = db.open_rw(artifact, tmp_settings)
    conn.execute("CREATE TABLE facts (season INTEGER, epa DOUBLE)")
    conn.execute("INSERT INTO facts VALUES (2023, 1.5)")
    conn.execute(
        "CREATE TABLE schedules (season INTEGER, week INTEGER, game_type VARCHAR, "
        "home_team VARCHAR, away_team VARCHAR, home_score INTEGER)"
    )
    conn.execute("INSERT INTO schedules VALUES (2023, 1, 'REG', 'AAA', 'BBB', 20)")
    coverage_mod.build(conn, tmp_settings)
    conn.close()
    db.write_current(tmp_settings, artifact)

    result = CliRunner().invoke(main, ["coverage", "facts", "--column", "epa"])
    assert result.exit_code == 0, result.output
    assert "epa" in result.output

    seasons = CliRunner().invoke(main, ["coverage", "--seasons"])
    assert seasons.exit_code == 0
    assert "2023" in seasons.output

    missing = CliRunner().invoke(main, ["coverage", "nonesuch"])
    assert missing.exit_code != 0
    assert "nothing in the registry" in missing.output


def _mini_artifact(tmp_settings: Settings):
    """A published artifact built from the mini league, for the defs commands."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dataclasses import replace

    from mini import build_mini

    mini = build_mini(replace(tmp_settings, season_floor=2022))
    artifact = paths.artifact_path(tmp_settings, date(2026, 9, 8))
    mini.execute(f"ATTACH '{artifact}' AS out")
    for (table,) in mini.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall():
        mini.execute(f'CREATE TABLE out."{table}" AS SELECT * FROM "{table}"')
    mini.execute("DETACH out")
    mini.close()
    db.write_current(tmp_settings, artifact)


def test_defs_list_is_empty_to_begin_with(tmp_settings: Settings) -> None:
    _mini_artifact(tmp_settings)
    result = CliRunner().invoke(main, ["defs", "list"])
    assert result.exit_code == 0, result.output
    assert "no definitions" in result.output


def test_defs_add_show_and_remove(tmp_settings: Settings, tmp_path: Path) -> None:
    _mini_artifact(tmp_settings)
    spec = tmp_path / "played.json"
    spec.write_text(
        '{"name": "played", "entity": "player_game", "signal": "rule", '
        '"params": {"rules": [{"attr": "snaps_unit", "op": ">=", "value": 1}]}, '
        '"description": "took a snap"}'
    )

    added = CliRunner().invoke(main, ["defs", "add", str(spec)])
    assert added.exit_code == 0, added.output
    assert "saved played v1" in added.output

    listed = CliRunner().invoke(main, ["defs", "list"])
    assert "played" in listed.output and "took a snap" in listed.output

    shown = CliRunner().invoke(main, ["defs", "show", "played"])
    assert shown.exit_code == 0
    assert "snaps_unit ≥ 1" in shown.output

    validated = CliRunner().invoke(main, ["defs", "validate"])
    assert validated.exit_code == 0
    assert "1 valid, 0 broken" in validated.output

    removed = CliRunner().invoke(main, ["defs", "rm", "played"])
    assert removed.exit_code == 0
    assert "removed played" in removed.output


def test_defs_add_reports_an_invalid_definition(tmp_settings: Settings, tmp_path: Path) -> None:
    _mini_artifact(tmp_settings)
    spec = tmp_path / "bad.json"
    spec.write_text(
        '{"name": "bad_one", "entity": "player_game", "signal": "rule", '
        '"params": {"rules": [{"attr": "nonesuch", "op": ">=", "value": 1}]}}'
    )
    result = CliRunner().invoke(main, ["defs", "add", str(spec)])
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_defs_show_reports_an_unknown_name(tmp_settings: Settings) -> None:
    _mini_artifact(tmp_settings)
    result = CliRunner().invoke(main, ["defs", "show", "nonesuch"])
    assert result.exit_code != 0
    assert "no definition named" in result.output


def test_defs_propose_says_when_something_is_not_computable(tmp_settings: Settings) -> None:
    _mini_artifact(tmp_settings)
    result = CliRunner().invoke(main, ["defs", "propose", "pro bowler"])
    assert result.exit_code == 0, result.output
    assert "not computable" in result.output


def test_defs_without_a_database(tmp_settings: Settings) -> None:
    result = CliRunner().invoke(main, ["defs", "list"])
    assert result.exit_code != 0
    assert "no database" in result.output
