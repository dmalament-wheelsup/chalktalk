"""CLI surface: stubs exit 2 and say which phase, doctor works with no database."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from click.testing import CliRunner

from chalktalk import db, paths
from chalktalk.cli import main
from chalktalk.config import Settings


def test_no_subcommand_is_a_stub_any_more() -> None:
    """Every phase has landed; nothing should still be exiting 2 with a phase number."""
    for command in ("build", "coverage", "defs", "logs", "serve", "doctor"):
        result = CliRunner().invoke(main, [command, "--help"])
        assert result.exit_code == 0, command
        assert "not implemented" not in result.output


def test_help_lists_every_subcommand() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ("build", "serve", "coverage", "defs", "logs", "doctor"):
        assert command in result.output


def test_doctor_without_a_database(tmp_settings: Settings) -> None:
    """A fresh install is not a fault, so this stays exit 0 with instructions."""
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "no database yet" in result.output
    assert "chalktalk build" in result.output
    assert str(tmp_settings.home) in result.output


def test_doctor_reports_the_current_artifact(tmp_settings: Settings) -> None:
    artifact = _publish_mini(tmp_settings)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "no database" not in result.output
    assert artifact.name in result.output


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


def test_logs_summary_reads_the_audit_log(tmp_settings: Settings) -> None:
    from chalktalk import audit

    audit.record(
        tmp_settings, {"tool": "raw_sql", "sql": "select count(*) from pbp where season = 2023"}
    )
    audit.record(
        tmp_settings, {"tool": "raw_sql", "sql": "select count(*) from pbp where season = 2024"}
    )
    audit.record(tmp_settings, {"tool": "query", "entity": "player_game"})

    result = CliRunner().invoke(main, ["logs", "summary"])
    assert result.exit_code == 0, result.output
    assert "3 call(s)" in result.output
    assert "recurring raw SQL" in result.output
    assert "2x" in result.output, "the two season queries share a shape"


def test_logs_summary_on_an_empty_log(tmp_settings: Settings) -> None:
    result = CliRunner().invoke(main, ["logs", "summary"])
    assert result.exit_code == 0
    assert "nothing logged" in result.output


def test_logs_summary_rejects_a_nonsense_window(tmp_settings: Settings) -> None:
    result = CliRunner().invoke(main, ["logs", "summary", "--since", "yesterday"])
    assert result.exit_code != 0
    assert "30d" in result.output


def test_logs_terms_separates_vocabulary_from_raw_fields(tmp_settings: Settings) -> None:
    from chalktalk import audit

    audit.record(
        tmp_settings,
        {
            "tool": "query",
            "plan": {
                "where": [{"term": "early_exit"}, {"attr": "snaps_unit", "op": "<", "value": 15}]
            },
            "definitions_used": [
                {"name": "played", "version": 1},
                {"name": "early_exit", "version": 1},
            ],
        },
    )
    result = CliRunner().invoke(main, ["logs", "terms"])
    assert result.exit_code == 0, result.output
    assert "1 of them named at least one term" in result.output
    assert "early_exit" in result.output
    assert "candidates for a definition" in result.output
    assert "snaps_unit" in result.output


def test_logs_terms_flags_a_term_that_was_refused(tmp_settings: Settings) -> None:
    from chalktalk import audit

    audit.record(
        tmp_settings,
        {
            "tool": "query",
            "plan": {"where": [{"term": "star_player"}]},
            "error": "unresolved_term",
        },
    )
    result = CliRunner().invoke(main, ["logs", "terms"])
    assert "refused as undefined" in result.output


def test_logs_terms_with_nothing_logged(tmp_settings: Settings) -> None:
    result = CliRunner().invoke(main, ["logs", "terms"])
    assert result.exit_code == 0
    assert "no queries" in result.output


def _publish_mini(tmp_settings: Settings, *, mtime_days_ago: int = 0):
    """A published artifact built from the mini league, optionally aged."""
    import os
    import sys
    import time
    from dataclasses import replace

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
    if mtime_days_ago:
        old = time.time() - mtime_days_ago * 86400
        os.utime(artifact, (old, old))
    return artifact


def test_doctor_reports_seasons_and_definitions(tmp_settings: Settings) -> None:
    _publish_mini(tmp_settings)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "seasons 2022-2023" in result.output
    assert "none saved yet" in result.output


def test_doctor_counts_installed_definitions(tmp_settings: Settings) -> None:
    from chalktalk.db import open_ro
    from chalktalk.definitions.context import open_store
    from chalktalk.definitions.vocabulary_install import install

    artifact = _publish_mini(tmp_settings)
    conn = open_ro(artifact, tmp_settings)
    install(open_store(conn, tmp_settings))
    conn.close()

    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "valid, 0 broken" in result.output


def test_doctor_fails_when_a_definition_is_broken(tmp_settings: Settings) -> None:
    """Exit non-zero so a cron or CI run notices, and name the definition."""
    _publish_mini(tmp_settings)
    definitions = paths.definitions_dir(tmp_settings)
    definitions.mkdir(parents=True, exist_ok=True)
    (definitions / "stale.json").write_text(
        '{"name": "stale", "entity": "player_game", "signal": "rule", '
        '"params": {"rules": [{"attr": "gone_away", "op": ">=", "value": 1}]}}'
    )
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 1
    assert "stale" in result.output
    assert "no longer compile" in result.output


def test_doctor_fails_when_current_points_at_something_else(
    tmp_settings: Settings,
) -> None:
    """A file that is not a chalktalk build should say so, not raise a catalog error."""
    artifact = paths.artifact_path(tmp_settings, date(2026, 9, 8))
    db.open_rw(artifact, tmp_settings).close()
    db.write_current(tmp_settings, artifact)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 1
    assert "not a chalktalk build" in result.output


def test_doctor_does_not_nag_about_a_stale_build_out_of_season(
    tmp_settings: Settings,
) -> None:
    """The mini league's latest season is complete, so age does not matter."""
    _publish_mini(tmp_settings, mtime_days_ago=30)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "30 days old" not in result.output
