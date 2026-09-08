"""Gate B — the injury-exit fixtures through the real path.

Gate A checked the raw attributes. This checks the same cases through
definitions, the compiler and SQL, on real data. If a case passed Gate A and
fails here, the bug is in a signal or the compiler, not in the data.

Nineteen of the twenty-one agree with the hand-authored expectation. The two
that do not are recorded, not tuned away: `chubb_2023_w2` sits just under a
position-blind threshold, and `hainsey_2022_w18` is a false positive no
attribute in the data can distinguish from a real exit. Both are asserted
explicitly, with the variant that would change each — because the point of the
system is that those are the user's calls, not the code's.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from chalktalk.definitions.spec import DefinitionIn

pytestmark = pytest.mark.data

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CASES = yaml.safe_load((FIXTURES / "exits.yaml").read_text())["cases"]

#: Adjudicated 2026-09-08 against the real build; see the module docstring.
KNOWN_DISAGREEMENTS = {"chubb_2023_w2", "hainsey_2022_w18"}
AGREEING = [c for c in CASES if c["id"] not in KNOWN_DISAGREEMENTS]


@pytest.fixture(scope="module")
def strict(real_store):
    """`early_exit` without the weakest corroboration path, saved once."""
    real_store.save(
        DefinitionIn(
            name="exit_corroborated_strict",
            entity="player_game",
            signal="composite",
            params={"op": "any_of", "terms": ["listed_injured_next", "on_reserve_soon"]},
            description="Corroborated by the injury report or the reserve list only.",
        )
    )
    real_store.save(
        DefinitionIn(
            name="early_exit_strict",
            entity="player_game",
            signal="composite",
            params={
                "op": "all_of",
                "terms": ["played", "regular", "exit_evidence", "exit_corroborated_strict"],
            },
            description="early_exit without the weakest corroboration path.",
        )
    )
    return "early_exit_strict"


def _matches(ask, case: dict, term: str = "early_exit") -> int:
    out = ask(
        {
            "entity": "player_game",
            "where": [
                {"term": term},
                {"attr": "player_name", "op": "=", "value": case["player"]},
                {"attr": "week", "op": "=", "value": case["week"]},
                {"attr": "team", "op": "=", "value": case["team"]},
            ],
            "seasons": {"from": case["season"], "to": case["season"]},
            "game_types": ["*"],
            "metrics": [{"fn": "count"}],
        }
    )
    assert out.get("ok"), out
    return out["rows"][0]["count"] if out["rows"] else 0


def _explain(ask, case: dict) -> str:
    """Every failure prints the matched row, as the phase 9 protocol requires."""
    out = ask(
        {
            "entity": "player_game",
            "where": [
                {"attr": "player_name", "op": "=", "value": case["player"]},
                {"attr": "week", "op": "=", "value": case["week"]},
                {"attr": "team", "op": "=", "value": case["team"]},
            ],
            "seasons": {"from": case["season"], "to": case["season"]},
            "game_types": ["*"],
            "metrics": [{"fn": "count"}],
            "sample_rows": 1,
        }
    )
    rows = out.get("sample", {}).get("rows", [])
    body = "\n".join(f"    {k:24} {v!r}" for k, v in (rows[0].items() if rows else []))
    return f"\n{case['id']}  ({case['fact']})\n{body}\n"


@pytest.mark.parametrize("case", AGREEING, ids=[c["id"] for c in AGREEING])
def test_the_shipped_definition_agrees_with_football_knowledge(ask, case: dict) -> None:
    want = 1 if case["expect_exit"] else 0
    assert _matches(ask, case) == want, _explain(ask, case)


def test_nineteen_of_twenty_one_agree(ask) -> None:
    """Stated as a number so a regression cannot hide behind a skipped case."""
    agreeing = sum(1 for c in CASES if _matches(ask, c) == (1 if c["expect_exit"] else 0))
    assert agreeing == 19, f"{agreeing} of {len(CASES)}"


# ── the two the shipped definition gets wrong, and why ────────────────────────


def test_a_running_back_falls_under_the_position_blind_threshold(ask, real_store) -> None:
    """`regular` asks for baseline_share >= 0.5. Nick Chubb's was 0.49.

    Running backs rotate, so a single threshold across every position is the
    wrong shape. The number was not lowered to make this pass — that is what the
    phase 9 protocol forbids — but a looser variant is saved here to show the
    miss is a threshold choice and not a broken mechanism.
    """
    case = next(c for c in CASES if c["id"] == "chubb_2023_w2")
    assert _matches(ask, case) == 0, _explain(ask, case)

    real_store.save(
        DefinitionIn(
            name="regular_loose",
            entity="player_game",
            signal="rule",
            params={"rules": [{"attr": "baseline_share", "op": ">=", "value": 0.4}]},
            description="Normally plays at least 40% of his unit's snaps.",
        )
    )
    real_store.save(
        DefinitionIn(
            name="early_exit_loose",
            entity="player_game",
            signal="composite",
            params={
                "op": "all_of",
                "terms": ["played", "regular_loose", "exit_evidence", "exit_corroborated"],
            },
            description="early_exit with a lower bar for what counts as a regular player.",
        )
    )
    assert _matches(ask, case, "early_exit_loose") == 1


def test_a_rested_starter_who_lost_his_job_is_a_false_positive(ask, strict) -> None:
    """Robert Hainsey started week 18, was rested, and missed the wild-card game.

    Not through injury: Ryan Jensen returned from a season-long absence and took
    the centre job back. No attribute in the data separates that from a real
    exit, which is why CLAUDE.md insists on returning matched rows. A stricter
    corroboration excludes him — at a cost, measured in the next test.
    """
    case = next(c for c in CASES if c["id"] == "hainsey_2022_w18")
    assert _matches(ask, case) == 1, _explain(ask, case)
    assert _matches(ask, case, strict) == 0


def test_the_stricter_variant_costs_a_real_exit(ask, strict) -> None:
    """A.J. Brown was never listed injured and never hit the reserve list.

    His only evidence is missing the wild-card game after starting week 18 — the
    same signal that lets Hainsey through. Tightening corroboration to exclude
    the false positive loses this true one. No threshold gets both, which is the
    finding, and the reason this is a definition rather than code.
    """
    brown = next(c for c in CASES if c["id"] == "brown_2023_w18")
    assert _matches(ask, brown) == 1
    assert _matches(ask, brown, strict) == 0


def test_neither_variant_gets_every_case(ask, strict) -> None:
    """Measured: shipped 19 of 21, stricter corroboration also 19 of 21.

    They disagree about which two they get wrong. That trade-off is the user's to
    make, and making it visible is more useful than picking one and being quiet.
    """
    shipped = sum(1 for c in CASES if _matches(ask, c) == (1 if c["expect_exit"] else 0))
    tighter = sum(1 for c in CASES if _matches(ask, c, strict) == (1 if c["expect_exit"] else 0))
    assert (shipped, tighter) == (19, 19)


# ── the paths that make the definition work ───────────────────────────────────


def test_the_report_only_corroboration_path(ask) -> None:
    """Mike Evans played the next game; the injury report is the only evidence."""
    case = next(c for c in CASES if c["id"] == "evans_2020_w17")
    assert _matches(ask, case) == 1
    assert _matches(ask, case, "listed_injured_next") == 1
    assert _matches(ask, case, "missed_next_game") == 0


def test_a_backup_absence_is_not_corroboration(ask) -> None:
    """Teddy Bridgewater sat the wild-card game because Brees was healthy.

    He missed the next game, so `missed_next_game` holds — and `early_exit` still
    does not, because corroboration requires that he started this one.
    """
    case = next(c for c in CASES if c["id"] == "bridgewater_2019_w17")
    assert _matches(ask, case, "missed_next_game") == 1
    assert _matches(ask, case, "missed_next_game_as_starter") == 0
    assert _matches(ask, case) == 0


def test_a_late_exit_needs_a_lower_tail_threshold(ask, real_store) -> None:
    """D14's argument: Kirk Cousins tore his Achilles after 61 snaps at 85% share.

    The shipped 25% tail does not find him and a 3% tail does, which is why the
    threshold is a definition a user can change rather than a constant.
    """
    case = next(c for c in CASES if c["id"] == "cousins_2023_w8")
    assert _matches(ask, case) == 0

    real_store.save(
        DefinitionIn(
            name="left_early_late",
            entity="player_game",
            signal="rule",
            params={"rules": [{"attr": "pp_missed_tail_frac", "op": ">=", "value": 0.03}]},
            description="Any of his unit's plays happened after his last one.",
        )
    )
    real_store.save(
        DefinitionIn(
            name="early_exit_late",
            entity="player_game",
            signal="composite",
            params={
                "op": "all_of",
                "terms": ["played", "regular", "left_early_late", "exit_corroborated"],
            },
            description="early_exit that catches a fourth-quarter injury.",
        )
    )
    assert _matches(ask, case, "early_exit_late") == 1


def test_rested_starters_are_excluded(ask) -> None:
    """D15: they played the next game, which is the fact that separates them."""
    for case_id in ("allen_2019_w17", "williams_2023_w18", "fournette_2022_w18"):
        case = next(c for c in CASES if c["id"] == case_id)
        assert _matches(ask, case) == 0, _explain(ask, case)


def test_the_answer_always_carries_matched_rows(ask) -> None:
    """CLAUDE.md: a rested starter looks identical to an exit, so show the rows."""
    out = ask(
        {
            "entity": "player_game",
            "where": [{"term": "early_exit"}],
            "seasons": {"from": 2023, "to": 2023},
            "metrics": [{"fn": "count"}],
        }
    )
    assert out["ok"]
    assert out["sample"]["total_matched"] > 0
    assert out["sample"]["rows"]
    assert "pp_missed_tail_frac" in out["sample"]["columns"]
