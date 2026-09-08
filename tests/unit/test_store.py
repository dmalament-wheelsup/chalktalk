"""The definitions store: round trips, history, quarantine, and reload.

This is the user's accumulated work, so the failure modes that matter are losing
it and silently changing what it means.
"""

from __future__ import annotations

import json

import pytest

from chalktalk.definitions.spec import DefinitionIn, InvalidDefinition

PLAYED = {"rules": [{"attr": "snaps_unit", "op": ">=", "value": 1}]}


def _in(name: str = "played", **kwargs) -> DefinitionIn:
    base = {"name": name, "entity": "player_game", "signal": "rule", "params": PLAYED}
    return DefinitionIn(**(base | kwargs))


# ── saving and loading ────────────────────────────────────────────────────────


def test_an_empty_store_loads_clean(mini_store) -> None:
    report = mini_store.load()
    assert report.loaded == 0
    assert report.ok


def test_save_then_get(mini_store) -> None:
    saved = mini_store.save(_in(description="took a snap"))
    assert saved.version == 1
    assert saved.provenance.source == "user"
    found = mini_store.get("played")
    assert found is not None and found.description == "took a snap"


def test_the_file_is_named_after_the_definition(mini_store) -> None:
    mini_store.save(_in())
    assert (mini_store.directory / "played.json").is_file()


def test_a_definition_is_found_by_alias(mini_store) -> None:
    mini_store.save(_in(aliases=["appeared", "suited_up"]))
    assert mini_store.get("appeared").name == "played"
    assert mini_store.get("suited_up").name == "played"


def test_an_unknown_name_returns_none(mini_store) -> None:
    assert mini_store.get("nonesuch") is None


def test_saving_twice_needs_overwrite(mini_store) -> None:
    mini_store.save(_in())
    with pytest.raises(InvalidDefinition, match="already exists"):
        mini_store.save(_in())


def test_overwrite_bumps_the_version_and_keeps_the_old_one(mini_store) -> None:
    mini_store.save(_in(description="first"))
    mini_store.save(_in(description="second"), overwrite=True)

    assert mini_store.get("played").version == 2
    assert mini_store.get("played").description == "second"

    archived = mini_store.directory / ".history" / "played" / "v1.json"
    assert archived.is_file(), "the previous version must survive"
    assert json.loads(archived.read_text())["description"] == "first"


def test_the_creation_date_survives_an_overwrite(mini_store) -> None:
    first = mini_store.save(_in())
    second = mini_store.save(_in(description="changed"), overwrite=True)
    assert second.provenance.created_at == first.provenance.created_at


def test_a_name_taken_by_another_definitions_alias_is_refused(mini_store) -> None:
    mini_store.save(_in(name="played", aliases=["appeared"]))
    with pytest.raises(InvalidDefinition, match="already used by"):
        mini_store.save(_in(name="appeared"))


def test_an_alias_taken_by_another_definition_is_refused(mini_store) -> None:
    mini_store.save(_in(name="played"))
    with pytest.raises(InvalidDefinition, match="already used by"):
        mini_store.save(_in(name="regular", aliases=["played"]))


def test_delete_removes_it_but_keeps_the_history(mini_store) -> None:
    mini_store.save(_in())
    mini_store.delete("played")
    assert mini_store.get("played") is None
    assert not (mini_store.directory / "played.json").exists()
    assert (mini_store.directory / ".history" / "played" / "v1.json").is_file()


def test_deleting_something_absent_says_so(mini_store) -> None:
    with pytest.raises(InvalidDefinition, match="no such definition"):
        mini_store.delete("nonesuch")


# ── validation on the way in ──────────────────────────────────────────────────


def test_an_unknown_attribute_is_refused_before_it_is_written(mini_store) -> None:
    bad = _in(params={"rules": [{"attr": "nonesuch", "op": ">=", "value": 1}]})
    with pytest.raises(InvalidDefinition):
        mini_store.save(bad)
    assert not (mini_store.directory / "played.json").exists()


def test_an_unknown_signal_names_the_alternatives(mini_store) -> None:
    with pytest.raises(InvalidDefinition, match="unknown signal") as excinfo:
        mini_store.save(_in(signal="vibes"))
    assert "rule" in excinfo.value.did_you_mean


def test_a_type_mismatch_is_refused(mini_store) -> None:
    with pytest.raises(InvalidDefinition, match="is int"):
        mini_store.save(_in(params={"rules": [{"attr": "snaps_unit", "op": ">=", "value": "x"}]}))


# ── quarantine (D10) ──────────────────────────────────────────────────────────


def test_a_definition_referencing_a_missing_column_is_quarantined(mini_store) -> None:
    """A renamed nflverse column must not degrade into a wrong answer."""
    (mini_store.directory).mkdir(parents=True, exist_ok=True)
    (mini_store.directory / "stale.json").write_text(
        json.dumps(
            {
                "name": "stale",
                "entity": "player_game",
                "signal": "rule",
                "params": {"rules": [{"attr": "column_that_went_away", "op": ">=", "value": 1}]},
            }
        )
    )
    report = mini_store.load()
    assert "stale" in report.broken
    assert mini_store.get("stale") is None, "a broken definition must not be usable"


def test_a_broken_definition_still_appears_in_the_listing(mini_store) -> None:
    """Silently vanishing would be worse than being listed as broken."""
    mini_store.directory.mkdir(parents=True, exist_ok=True)
    (mini_store.directory / "stale.json").write_text(
        json.dumps(
            {
                "name": "stale",
                "entity": "player_game",
                "signal": "rule",
                "params": {"rules": [{"attr": "gone", "op": ">=", "value": 1}]},
            }
        )
    )
    mini_store.load()
    listed = {s.name: s for s in mini_store.list()}
    assert listed["stale"].broken
    assert "stale" not in {s.name for s in mini_store.list(include_broken=False)}


def test_unparseable_json_is_quarantined_not_fatal(mini_store) -> None:
    mini_store.directory.mkdir(parents=True, exist_ok=True)
    (mini_store.directory / "junk.json").write_text("{not json")
    mini_store.save(_in())
    assert "junk" in mini_store.broken
    assert mini_store.get("played") is not None, "one bad file must not hide the good ones"


def test_a_file_whose_name_disagrees_with_its_definition_is_quarantined(mini_store) -> None:
    mini_store.directory.mkdir(parents=True, exist_ok=True)
    (mini_store.directory / "wrong_name.json").write_text(
        json.dumps({"name": "played", "entity": "player_game", "signal": "rule", "params": PLAYED})
    )
    report = mini_store.load()
    assert "wrong_name" in report.broken
    assert "named" in report.broken["wrong_name"]


# ── reloading ─────────────────────────────────────────────────────────────────


def test_maybe_reload_notices_a_hand_edit(mini_store) -> None:
    """The store is plain JSON precisely so it can be edited by hand."""
    mini_store.save(_in(description="first"))
    assert mini_store.maybe_reload() is False

    path = mini_store.directory / "played.json"
    stored = json.loads(path.read_text())
    stored["description"] = "edited by hand"
    path.write_text(json.dumps(stored))

    assert mini_store.maybe_reload() is True
    assert mini_store.get("played").description == "edited by hand"


# ── moving definitions around (D3) ────────────────────────────────────────────


def test_export_then_import_round_trips(mini_store, tmp_path) -> None:
    mini_store.save(_in(description="took a snap"))
    mini_store.save(
        _in(name="regular", params={"rules": [{"attr": "game_type", "op": "=", "value": "REG"}]})
    )

    out = tmp_path / "shared"
    assert mini_store.export(out) == 2

    mini_store.delete("played")
    mini_store.delete("regular")
    assert mini_store.list() == []

    report = mini_store.import_(out)
    assert sorted(report.added) == ["played", "regular"]
    assert mini_store.get("played").description == "took a snap"


def test_import_marks_where_a_definition_came_from(mini_store, tmp_path) -> None:
    mini_store.save(_in())
    out = tmp_path / "shared"
    mini_store.export(out)
    mini_store.delete("played")

    mini_store.import_(out)
    assert mini_store.get("played").provenance.source == "imported"


def test_import_does_not_clobber_by_default(mini_store, tmp_path) -> None:
    mini_store.save(_in(description="mine"))
    out = tmp_path / "shared"
    (out).mkdir()
    (out / "played.json").write_text(
        json.dumps(
            {
                "name": "played",
                "entity": "player_game",
                "signal": "rule",
                "params": PLAYED,
                "description": "theirs",
            }
        )
    )
    report = mini_store.import_(out)
    assert report.skipped == ["played"]
    assert mini_store.get("played").description == "mine"


def test_import_reports_what_it_could_not_take(mini_store, tmp_path) -> None:
    out = tmp_path / "shared"
    out.mkdir()
    (out / "broken.json").write_text("{")
    report = mini_store.import_(out)
    assert "broken" in report.failed


# ── copying (D2's save_definition(copy_of=...)) ───────────────────────────────


def test_copy_takes_the_original_meaning_under_a_new_name(mini_store) -> None:
    """This is how a proposed star_by_* becomes the user's own star_player."""
    mini_store.save(_in(name="star_by_snaps", description="top snap share"))
    copied = mini_store.save(
        DefinitionIn(name="star_player", entity="player_game", signal="rule", params={}),
        copied_from="star_by_snaps",
    )
    assert copied.params == PLAYED
    assert copied.provenance.copied_from == "star_by_snaps"
    assert copied.description == "top snap share"


def test_copying_something_absent_says_so(mini_store) -> None:
    with pytest.raises(InvalidDefinition, match="no such definition"):
        mini_store.save(_in(name="star_player"), copied_from="nonesuch")


# ── coverage ──────────────────────────────────────────────────────────────────


def test_coverage_of_a_definition_comes_from_its_attributes(mini_store) -> None:
    mini_store.save(_in())
    span = mini_store.coverage_of("played")
    assert span is not None
    assert (span.first, span.last) == (2022, 2023)


def test_a_participation_definition_is_bounded_by_participation(mini_store) -> None:
    """The mini league has participation for 2023 only, as the real one has 2016 on."""
    mini_store.save(
        _in(
            name="left_early",
            params={"rules": [{"attr": "pp_missed_tail_frac", "op": ">=", "value": 0.25}]},
        )
    )
    span = mini_store.coverage_of("left_early")
    assert span is not None
    assert span.first == 2023


def test_coverage_of_an_unknown_definition_is_none(mini_store) -> None:
    assert mini_store.coverage_of("nonesuch") is None
