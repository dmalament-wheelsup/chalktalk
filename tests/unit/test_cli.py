"""CLI surface: stubs exit 2 and say which phase, doctor works with no database."""

from __future__ import annotations

from datetime import date

import pytest
from click.testing import CliRunner

from chalktalk import db, paths
from chalktalk.cli import main
from chalktalk.config import Settings


@pytest.mark.parametrize(
    ("command", "phase"),
    [("build", 2), ("coverage", 3), ("defs", 5), ("serve", 8), ("logs", 8)],
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
