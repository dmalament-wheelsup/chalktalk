"""The hand-authored fixtures are well-formed.

`exits.yaml` is football knowledge written by a person, copied verbatim from
09-testing.md before any player table existed so its expected answers cannot
have been fitted to what the code produces. This file checks only its shape —
whether the expectations *hold* is Gate A (stage 4d).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REQUIRED = {
    "id",
    "player",
    "season",
    "week",
    "team",
    "unit",
    "snaps_unit",
    "share",
    "expect_exit",
    "fact",
}


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return yaml.safe_load((FIXTURES / "exits.yaml").read_text())["cases"]


def test_the_fixture_file_exists() -> None:
    assert (FIXTURES / "exits.yaml").is_file()


def test_every_case_has_the_required_keys(cases: list[dict]) -> None:
    for case in cases:
        assert REQUIRED <= set(case), f"{case.get('id')}: missing {REQUIRED - set(case)}"


def test_case_ids_are_unique(cases: list[dict]) -> None:
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))


def test_both_outcomes_are_represented(cases: list[dict]) -> None:
    """A suite of only positives proves nothing about false positives."""
    assert sum(1 for c in cases if c["expect_exit"]) >= 5
    assert sum(1 for c in cases if not c["expect_exit"]) >= 5


def test_every_case_is_inside_the_queryable_era(cases: list[dict]) -> None:
    for case in cases:
        assert 2013 <= case["season"] <= 2025, case["id"]


def test_units_are_offense_or_defense(cases: list[dict]) -> None:
    assert {c["unit"] for c in cases} <= {"offense", "defense"}


def test_snap_counts_and_shares_are_plausible(cases: list[dict]) -> None:
    for case in cases:
        assert 0 <= case["snaps_unit"] <= 100, case["id"]
        assert 0.0 <= case["share"] <= 1.0, case["id"]


def test_every_case_states_why(cases: list[dict]) -> None:
    """The `fact` is the football knowledge; without it the case cannot be adjudicated.

    Short is fine — "ACL, Q1 at NYJ; IR" says everything needed. Empty is not.
    """
    for case in cases:
        assert len(case["fact"].strip()) > 10, case["id"]


def test_the_adversarial_cases_are_present(cases: list[dict]) -> None:
    """The cases a naive low-snap heuristic gets wrong. The suite is pointless without them."""
    ids = {c["id"] for c in cases}
    assert {"cousins_2023_w8", "bridgewater_2019_w17", "williams_2023_w18", "evans_2020_w17"} <= ids


def test_the_late_exit_case_is_marked_not_an_exit_by_the_shipped_threshold(
    cases: list[dict],
) -> None:
    """Cousins tore his Achilles after 61 snaps (85%) — D14's whole argument."""
    cousins = next(c for c in cases if c["id"] == "cousins_2023_w8")
    assert cousins["expect_exit"] is False
    assert cousins["expect_exit_with_tail_0_03"] is True
    assert cousins["snaps_unit"] >= 15


def test_the_low_snap_exits_really_are_low(cases: list[dict]) -> None:
    """The eight cases the target question is about: injury exits under 15 snaps."""
    under_15 = [c for c in cases if c["expect_exit"] and c["snaps_unit"] < 15]
    assert len(under_15) >= 8


def test_there_are_exits_above_the_15_snap_line(cases: list[dict]) -> None:
    """Otherwise `early_exit` and `snaps_unit < 15` would be indistinguishable."""
    assert [c for c in cases if c["expect_exit"] and c["snaps_unit"] >= 15]
