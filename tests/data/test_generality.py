"""The generality suite on real data.

Ten unrelated questions — fourth-down aggression, rookie quarterbacks in
primetime, favourites blown out, running-back usage after a heavy season — all
answered by the same machinery, with no code written for any of them. That
suite, not the injury example, is the acceptance test for the architecture.

Row counts are mostly not asserted: they move with the data. What is asserted is
that every plan runs, names the definitions behind it, and returns rows a person
can check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chalktalk.definitions.spec import DefinitionIn

pytestmark = pytest.mark.data

PLANS = sorted((Path(__file__).resolve().parents[1] / "fixtures" / "plans").glob("*.json"))
IDS = [p.stem for p in PLANS]


@pytest.fixture(scope="module")
def with_star(real_store):
    """`star_player` is not shipped; the user copies one of the three (D2)."""
    real_store.save(
        DefinitionIn(
            name="star_player",
            entity="player_season",
            basis="prior_season",
            signal="percentile",
            params={},
            description="A star, by prior-season snap share.",
        ),
        copied_from="star_by_snaps",
    )
    return real_store


def _run(ask, path: Path):
    return ask(json.loads(path.read_text()))


@pytest.mark.parametrize("path", PLANS, ids=IDS)
def test_it_answers(ask, with_star, path: Path) -> None:
    out = _run(ask, path)
    assert out.get("ok"), f"{path.stem}: {out}"
    assert out["english"]
    assert out["sql"].count("?") == len(out["sql_params"])


@pytest.mark.parametrize("path", PLANS, ids=IDS)
def test_it_names_the_definitions_behind_it(ask, with_star, path: Path) -> None:
    payload = json.loads(path.read_text())
    uses_terms = any("term" in c for c in payload.get("where", [])) or any(
        isinstance(m.get("of"), dict) for m in payload.get("metrics", [])
    )
    out = _run(ask, path)
    if uses_terms:
        assert out["definitions_used"], path.stem
        for definition in out["definitions_used"]:
            assert definition["explanation"], definition["name"]


def test_the_headline_question_answers(ask, with_star) -> None:
    """The question CLAUDE.md opens with, end to end."""
    out = _run(ask, PLANS[0])
    assert out["ok"]
    by_season = {r["season"]: r["count"] for r in out["rows"]}
    assert by_season, "no seasons came back at all"
    assert sum(by_season.values()) > 0
    names = {d["name"] for d in out["definitions_used"]}
    assert {"star_player", "early_exit", "played", "regular"} <= names


def test_favourites_do_get_blown_out(ask, with_star) -> None:
    """Plan 5 must return rows: it happens several times a season."""
    out = _run(ask, next(p for p in PLANS if p.stem.startswith("05")))
    assert out["ok"]
    assert out["rows"], "favourites losing by 17+ should not be empty"
    assert sum(r["count"] for r in out["rows"]) > 20


def test_fourth_down_aggression_has_risen(ask, with_star) -> None:
    """Plan 3 against a trend anyone following the sport would recognise."""
    out = _run(ask, next(p for p in PLANS if p.stem.startswith("03")))
    assert out["ok"]
    rates = {r["season"]: r["go_rate"] for r in out["rows"] if r["go_rate"] is not None}
    early = [v for s, v in rates.items() if s <= 2015]
    late = [v for s, v in rates.items() if s >= 2022]
    assert early and late
    assert sum(late) / len(late) > sum(early) / len(early)


def test_every_plan_reports_the_seasons_it_could_answer(ask, with_star) -> None:
    for path in PLANS:
        out = _run(ask, path)
        seasons = out["seasons"]
        assert seasons["covered"][0] <= seasons["covered"][1]
        if seasons["excluded"]:
            assert out["warnings"], f"{path.stem} excluded seasons without saying so"


def test_the_suite_summary(ask, with_star, capsys) -> None:
    """Prints a table for a person to eyeball, as the plan asks."""
    lines = ["", f"{'plan':<42}{'rows':>6}{'matched':>9}{'ms':>7}"]
    for path in PLANS:
        out = _run(ask, path)
        matched = out.get("sample", {}).get("total_matched", 0)
        ms = sum(out.get("timing_ms", {}).values())
        lines.append(f"{path.stem:<42}{out['row_count']:>6}{matched:>9}{ms:>7}")
    with capsys.disabled():
        print("\n".join(lines))
