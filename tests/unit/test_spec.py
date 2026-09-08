"""The definition spec: names, aliases, basis, and the error shape."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chalktalk.definitions.spec import (
    Definition,
    DefinitionIn,
    DefinitionSummary,
    InvalidDefinition,
)


def _in(**kwargs) -> DefinitionIn:
    base = {"name": "star_by_snaps", "entity": "player_season", "signal": "rule", "params": {}}
    return DefinitionIn(**(base | kwargs))


@pytest.mark.parametrize("name", ["star_by_snaps", "ab", "x9", "a_b_c_1"])
def test_valid_names(name: str) -> None:
    assert _in(name=name).name == name


@pytest.mark.parametrize(
    "name", ["A", "9lives", "_leading", "has-dash", "has space", "x", "", "Ünïcode", "a" * 65]
)
def test_invalid_names(name: str) -> None:
    with pytest.raises(ValidationError):
        _in(name=name)


def test_aliases_follow_the_same_rule() -> None:
    with pytest.raises(ValidationError):
        _in(aliases=["Not Valid"])


def test_aliases_may_not_repeat() -> None:
    with pytest.raises(ValidationError):
        _in(aliases=["a_star", "a_star"])


def test_a_name_may_not_also_be_an_alias() -> None:
    with pytest.raises(ValidationError, match="both the name and an alias"):
        _in(aliases=["star_by_snaps"])


def test_unknown_entity_is_rejected_with_the_options() -> None:
    with pytest.raises(ValidationError, match="unknown entity"):
        _in(entity="teams")


def test_basis_belongs_to_player_season_only() -> None:
    """D16: for any other entity the notion has no meaning."""
    assert _in(entity="player_season", basis="prior_season").basis == "prior_season"
    with pytest.raises(ValidationError, match="basis applies only to player_season"):
        _in(entity="team_game", basis="prior_season")


def test_basis_defaults_to_the_current_season() -> None:
    assert Definition(**_in().model_dump()).effective_basis == "current_season"
    assert Definition(**_in(basis="prior_season").model_dump()).effective_basis == "prior_season"


def test_unknown_basis_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _in(basis="two_seasons_ago")


def test_extra_fields_are_refused() -> None:
    """A typo in a field name must not be silently stored and ignored."""
    with pytest.raises(ValidationError):
        DefinitionIn(name="x_y", entity="game", signal="rule", params={}, pctile=90)


def test_a_stored_definition_carries_provenance_and_version() -> None:
    definition = Definition(**_in().model_dump())
    assert definition.version == 1
    assert definition.provenance.source == "user"
    assert definition.provenance.created_at.endswith("Z")
    assert definition.schema_version == 1


def test_names_lists_the_name_and_its_aliases() -> None:
    definition = Definition(**_in(aliases=["snap_star"]).model_dump())
    assert definition.names() == ["star_by_snaps", "snap_star"]


def test_round_trips_through_json() -> None:
    definition = Definition(**_in(aliases=["snap_star"], description="d").model_dump())
    assert Definition.model_validate_json(definition.model_dump_json()) == definition


def test_summary_carries_what_a_listing_needs() -> None:
    summary = DefinitionSummary.of(Definition(**_in().model_dump()), broken="nope")
    assert (summary.name, summary.entity, summary.broken) == (
        "star_by_snaps",
        "player_season",
        "nope",
    )


def test_the_error_carries_structured_detail() -> None:
    """Phase 8's envelope needs the fields, not a prose string."""
    error = InvalidDefinition("bad", name="x_y", field="params.attr", did_you_mean=["attr"])
    assert error.as_error() == {
        "error": "invalid_definition",
        "message": "bad",
        "definition": "x_y",
        "field": "params.attr",
        "did_you_mean": ["attr"],
    }


def test_the_error_omits_what_it_does_not_know() -> None:
    assert InvalidDefinition("bad").as_error() == {"error": "invalid_definition", "message": "bad"}
