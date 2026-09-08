"""The ten generality plans.

The acceptance test for the architecture is not the injury question — it is that
ten unrelated questions run on definitions alone, with no code written for any of
them. If one of these needs a new signal or a special case, that is the finding:
the feature layer is missing an attribute, or a signal is too narrow.

Here they must parse, gate and compile, and their SQL must execute. Phase 7 runs
them against real data and checks the numbers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chalktalk.coverage import Coverage
from chalktalk.definitions.spec import DefinitionIn
from chalktalk.query.compile import compile_plan
from chalktalk.query.gate import check
from chalktalk.query.plan import QueryPlan

PLANS = Path(__file__).resolve().parents[1] / "fixtures" / "plans"
PLAN_FILES = sorted(PLANS.glob("*.json"))
PLAN_IDS = [p.stem for p in PLAN_FILES]


def _plan(path: Path) -> QueryPlan:
    return QueryPlan.model_validate(json.loads(path.read_text()))


@pytest.fixture
def with_star(shipped_store):
    """`star_player` is not shipped (D2); the user copies one of the three.

    This is the flow the whole product is built around, so the fixtures exercise
    it rather than pretending the term was always there.
    """
    shipped_store.save(
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
    return shipped_store


def _gate(plan, store, mini_conn, mini_settings):
    return check(
        plan,
        store=store,
        coverage=Coverage(mini_conn, mini_settings),
        settings=mini_settings,
        conn=mini_conn,
    )


def test_there_are_ten() -> None:
    assert len(PLAN_FILES) == 10


@pytest.mark.parametrize("path", PLAN_FILES, ids=PLAN_IDS)
def test_it_parses(path: Path) -> None:
    plan = _plan(path)
    assert plan.question, "each plan carries the question it came from"


@pytest.mark.parametrize("path", PLAN_FILES, ids=PLAN_IDS)
def test_it_passes_the_gate(path: Path, with_star, mini_conn, mini_settings) -> None:
    plan = _plan(path)
    gate = _gate(plan, with_star, mini_conn, mini_settings)
    assert gate.ok, f"{path.stem}: {gate.error.model_dump() if gate.error else None}"


@pytest.mark.parametrize("path", PLAN_FILES, ids=PLAN_IDS)
def test_it_compiles_and_runs(path: Path, with_star, mini_conn, mini_settings) -> None:
    """Row counts may be zero — the mini league is eight players — but the SQL must run."""
    plan = _plan(path)
    gate = _gate(plan, with_star, mini_conn, mini_settings)
    assert gate.ok, gate.error
    compiled = compile_plan(plan, gate, store=with_star, settings=mini_settings, conn=mini_conn)
    assert compiled.sql.count("?") == len(compiled.params)
    mini_conn.execute(compiled.sql, compiled.params).fetchall()
    mini_conn.execute(compiled.sample_sql, compiled.sample_params).fetchall()
    mini_conn.execute(compiled.count_sql, compiled.count_params).fetchall()


@pytest.mark.parametrize("path", PLAN_FILES, ids=PLAN_IDS)
def test_it_needed_no_code_of_its_own(path: Path) -> None:
    """Every filter is a term or an attribute rule. Nothing else is expressible."""
    payload = json.loads(path.read_text())
    for clause in payload.get("where", []):
        assert set(clause) & {"term", "attr", "not", "any_of", "all_of"}, clause


def test_the_headline_question_is_refused_until_star_player_is_defined(
    shipped_store, mini_conn, mini_settings
) -> None:
    """D2: the three star definitions ship, `star_player` does not.

    So the gate fires on first use, propose_definition offers the three, and the
    user's choice is what the answer is then reported against. That refusal is
    the product.
    """
    plan = _plan(PLANS / "01-star-exits.json")
    gate = _gate(plan, shipped_store, mini_conn, mini_settings)
    assert not gate.ok
    payload = gate.error.model_dump()
    assert payload["error"] == "unresolved_term"
    assert [t["term"] for t in payload["terms"]] == ["star_player"]
    offered = {s["name"] for s in payload["terms"][0]["suggestions"]}
    assert offered == {"star_by_snaps", "star_by_contract", "star_by_draft"}


def test_copying_a_suggestion_resolves_it(with_star, mini_conn, mini_settings) -> None:
    plan = _plan(PLANS / "01-star-exits.json")
    assert _gate(plan, with_star, mini_conn, mini_settings).ok
    copied = with_star.get("star_player")
    assert copied.provenance.copied_from == "star_by_snaps"
    assert copied.params["attr"] == "snap_share_mean"


def test_the_headline_answer_names_every_definition_behind_it(
    with_star, mini_conn, mini_settings
) -> None:
    """Including the ones the user never typed, reached through early_exit."""
    plan = _plan(PLANS / "01-star-exits.json")
    gate = _gate(plan, with_star, mini_conn, mini_settings)
    names = {d.name for d in gate.definitions_used}
    assert {"star_player", "early_exit", "left_early", "snap_drop", "played"} <= names
