"""The query plan model: clause discrimination and what a plan can be asked for."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chalktalk.query.plan import (
    AllOf,
    AnyOf,
    AttrRule,
    Metric,
    Not,
    QueryPlan,
    TermKey,
    TermRef,
    parse_clause,
)


def _plan(**kwargs) -> QueryPlan:
    return QueryPlan.model_validate({"entity": "player_game", **kwargs})


def test_the_defaults_are_a_regular_season_count() -> None:
    plan = _plan()
    assert plan.game_types == ["REG"]
    assert [m.fn for m in plan.metrics] == ["count"]
    assert plan.where == []


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"term": "early_exit"}, TermRef),
        ({"attr": "snaps_unit", "op": "<", "value": 15}, AttrRule),
        ({"not": {"term": "rested"}}, Not),
        ({"any_of": [{"term": "a_term"}]}, AnyOf),
        ({"all_of": [{"term": "a_term"}]}, AllOf),
    ],
)
def test_clauses_are_discriminated_by_key(payload: dict, expected: type) -> None:
    assert isinstance(parse_clause(payload), expected)


def test_a_clause_with_no_recognisable_key_says_what_it_wanted() -> None:
    with pytest.raises(ValueError, match="a clause needs one of"):
        parse_clause({"nonesuch": 1})


def test_a_clause_must_be_an_object() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        parse_clause("early_exit")


def test_clauses_nest() -> None:
    plan = _plan(
        where=[{"not": {"any_of": [{"term": "a_term"}, {"attr": "week", "op": "=", "value": 1}]}}]
    )
    assert isinstance(plan.where[0], Not)
    assert isinstance(plan.where[0].not_, AnyOf)


def test_terms_are_found_wherever_they_appear() -> None:
    """Including inside nested clauses, group keys and metrics."""
    plan = _plan(
        where=[{"all_of": [{"term": "played"}, {"not": {"term": "rested"}}]}],
        group_by=[{"term": "is_home"}],
        metrics=[{"fn": "avg", "of": {"term": "won"}}],
    )
    assert sorted(t.term for t in plan.terms()) == ["is_home", "played", "rested", "won"]


def test_attributes_include_metrics_and_grouping() -> None:
    """A coverage check that only looked at `where` would miss these."""
    plan = _plan(
        where=[{"attr": "snaps_unit", "op": ">=", "value": 1}],
        group_by=["season", "team"],
        metrics=[{"fn": "avg", "of": "snap_share_unit"}],
    )
    assert sorted(plan.attributes()) == ["season", "snap_share_unit", "snaps_unit", "team"]


def test_seasons_use_from_and_to() -> None:
    plan = _plan(seasons={"from": 2016, "to": 2020})
    assert (plan.seasons.first, plan.seasons.last) == (2016, 2020)


def test_backwards_seasons_are_refused() -> None:
    with pytest.raises(ValidationError, match="backwards"):
        _plan(seasons={"from": 2020, "to": 2016})


def test_count_takes_no_argument() -> None:
    with pytest.raises(ValidationError, match="count takes no"):
        Metric.model_validate({"fn": "count", "of": "snaps_unit"})


def test_other_metrics_need_one() -> None:
    with pytest.raises(ValidationError, match="needs an `of`"):
        Metric.model_validate({"fn": "avg"})


def test_summing_a_term_makes_no_sense() -> None:
    """avg of a term is a rate; sum of one is nothing."""
    with pytest.raises(ValidationError, match="share of rows"):
        Metric.model_validate({"fn": "sum", "of": {"term": "won"}})


def test_metric_names_are_derived_when_not_given() -> None:
    assert Metric.model_validate({"fn": "count"}).name == "count"
    assert Metric.model_validate({"fn": "avg", "of": "margin"}).name == "avg_margin"
    assert Metric.model_validate({"fn": "avg", "of": "opp.margin"}).name == "avg_opp__margin"
    assert Metric.model_validate({"fn": "avg", "of": "margin", "as": "m"}).name == "m"


def test_an_unknown_entity_lists_the_options() -> None:
    with pytest.raises(ValidationError, match="unknown entity"):
        QueryPlan.model_validate({"entity": "teams"})


def test_a_plan_needs_a_metric() -> None:
    with pytest.raises(ValidationError, match="at least one metric"):
        _plan(metrics=[])


def test_an_empty_where_is_valid() -> None:
    """Counting every row of an entity is a legitimate question."""
    assert _plan(where=[]).where == []


def test_group_by_accepts_a_term() -> None:
    plan = _plan(group_by=[{"term": "is_home"}])
    assert isinstance(plan.group_by[0], TermKey)


def test_unknown_fields_are_refused() -> None:
    """A typo in a plan must not be silently ignored."""
    with pytest.raises(ValidationError):
        QueryPlan.model_validate({"entity": "player_game", "wehre": []})


def test_a_plan_round_trips_through_json() -> None:
    plan = _plan(
        question="how many?",
        where=[{"term": "early_exit"}, {"attr": "snaps_unit", "op": "<", "value": 15}],
        group_by=["season"],
    )
    assert QueryPlan.model_validate_json(plan.model_dump_json()) == plan
