"""The audit log, and what it is for.

Not debugging. `raw_sql` and repeated attribute rules are both signs of a
question the vocabulary cannot yet say, so the summaries are a roadmap: a shape
written by hand over and over should become a first-class attribute or a
definition.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from chalktalk import audit


def _query(**kwargs):
    return {"tool": "query", "plan": {}, "definitions_used": [], **kwargs}


# ── writing ───────────────────────────────────────────────────────────────────


def test_a_call_is_recorded_with_a_timestamp(tmp_settings) -> None:
    audit.record(tmp_settings, {"tool": "query", "entity": "player_game"})
    entries = audit.read(tmp_settings)
    assert len(entries) == 1
    assert entries[0]["tool"] == "query"
    assert entries[0]["ts"].endswith("+00:00")


def test_a_failed_write_never_fails_the_query(tmp_settings, monkeypatch) -> None:
    """A log that cannot be written must not take the answer down with it."""

    def refuse(*args, **kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr("pathlib.Path.open", refuse)
    audit.record(tmp_settings, {"tool": "query"})  # must not raise


def test_unreadable_lines_are_skipped(tmp_settings) -> None:
    audit.record(tmp_settings, {"tool": "query"})
    audit.path_for(tmp_settings).open("a").write("{ not json\n")
    audit.record(tmp_settings, {"tool": "raw_sql"})
    assert [e["tool"] for e in audit.read(tmp_settings)] == ["query", "raw_sql"]


def test_the_window_excludes_older_entries(tmp_settings) -> None:
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    audit.record(tmp_settings, {"tool": "query"})
    with audit.path_for(tmp_settings).open("a") as handle:
        handle.write(json.dumps({"ts": old, "tool": "raw_sql"}) + "\n")
    recent = audit.read(tmp_settings, timedelta(days=30))
    assert [e["tool"] for e in recent] == ["query"]


# ── raw SQL fingerprints ──────────────────────────────────────────────────────


def test_the_same_query_shape_collapses_to_one_fingerprint() -> None:
    a = audit.fingerprint("select count(*) from pbp where season = 2023")
    b = audit.fingerprint("SELECT count(*)  FROM pbp WHERE season = 2024")
    assert a == b


def test_strings_are_collapsed_too() -> None:
    a = audit.fingerprint("select * from t where name = 'Aaron Rodgers'")
    b = audit.fingerprint("select * from t where name = 'Kirk Cousins'")
    assert a == b


def test_genuinely_different_queries_stay_apart() -> None:
    assert audit.fingerprint("select 1 from a") != audit.fingerprint("select 1 from b")


def test_repeated_raw_sql_is_surfaced(tmp_settings) -> None:
    for season in (2022, 2023, 2024):
        audit.record(
            tmp_settings,
            {"tool": "raw_sql", "sql": f"select count(*) from pbp where season = {season}"},
        )
    audit.record(tmp_settings, {"tool": "raw_sql", "sql": "select 1"})
    shapes = {shape: count for shape, count, _ in audit.summarize(tmp_settings).raw_shapes}
    assert max(shapes.values()) == 3


def test_the_summary_counts_calls_and_refusals(tmp_settings) -> None:
    audit.record(tmp_settings, {"tool": "query"})
    audit.record(tmp_settings, {"tool": "query", "error": "unresolved_term"})
    audit.record(tmp_settings, {"tool": "raw_sql", "sql": "select 1"})
    summary = audit.summarize(tmp_settings)
    assert summary.calls["query"] == 2
    assert summary.errors["unresolved_term"] == 1
    assert summary.total == 3


@pytest.mark.parametrize(
    ("text", "expected"),
    [("30d", timedelta(days=30)), ("12h", timedelta(hours=12)), ("90m", timedelta(minutes=90))],
)
def test_windows_parse(text: str, expected: timedelta) -> None:
    assert audit.parse_since(text) == expected


@pytest.mark.parametrize("text", ["yesterday", "30", "d30", "", "30y"])
def test_nonsense_windows_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match="30d"):
        audit.parse_since(text)


# ── term usage ────────────────────────────────────────────────────────────────


def test_terms_and_attributes_are_told_apart() -> None:
    terms, attributes = audit.plan_usage(
        {
            "where": [
                {"term": "early_exit"},
                {"attr": "snaps_unit", "op": "<", "value": 15},
            ]
        }
    )
    assert terms == {"early_exit": 1}
    assert attributes == {"snaps_unit": 1}


def test_nested_clauses_are_walked() -> None:
    terms, attributes = audit.plan_usage(
        {
            "where": [
                {
                    "all_of": [
                        {"term": "played"},
                        {"not_": {"term": "rested"}},
                        {"any_of": [{"attr": "week", "op": "=", "value": 1}]},
                    ]
                }
            ]
        }
    )
    assert set(terms) == {"played", "rested"}
    assert set(attributes) == {"week"}


def test_both_spellings_of_not_are_handled() -> None:
    """A dumped plan says `not_`; one that arrived as JSON says `not`."""
    for key in ("not", "not_"):
        terms, _ = audit.plan_usage({"where": [{key: {"term": "rested"}}]})
        assert terms == {"rested": 1}


def test_grouping_and_metrics_count_too() -> None:
    terms, attributes = audit.plan_usage(
        {
            "group_by": ["season", {"term": "is_home"}],
            "metrics": [
                {"fn": "count"},
                {"fn": "avg", "of": {"term": "won"}},
                {"fn": "avg", "of": "margin"},
            ],
        }
    )
    assert set(terms) == {"is_home", "won"}
    assert set(attributes) == {"season", "margin"}


def test_usage_separates_named_terms_from_definitions_reached(tmp_settings) -> None:
    """A composite pulls in definitions the user never typed; both are worth seeing."""
    audit.record(
        tmp_settings,
        _query(
            plan={"where": [{"term": "early_exit"}]},
            definitions_used=[
                {"name": "played", "version": 1},
                {"name": "left_early", "version": 1},
                {"name": "early_exit", "version": 1},
            ],
        ),
    )
    usage = audit.term_usage(tmp_settings)
    assert usage.terms == {"early_exit": 1}
    assert set(usage.definitions) == {"played", "left_early", "early_exit"}


def test_usage_counts_how_many_queries_reached_for_a_term(tmp_settings) -> None:
    audit.record(tmp_settings, _query(plan={"where": [{"term": "early_exit"}]}))
    audit.record(
        tmp_settings, _query(plan={"where": [{"attr": "snaps_unit", "op": "<", "value": 15}]})
    )
    usage = audit.term_usage(tmp_settings)
    assert (usage.queries, usage.with_terms) == (2, 1)


def test_a_refused_term_is_recorded_as_such(tmp_settings) -> None:
    audit.record(
        tmp_settings,
        _query(plan={"where": [{"term": "star_player"}]}, error="unresolved_term"),
    )
    usage = audit.term_usage(tmp_settings)
    assert usage.unresolved == {"star_player": 1}


def test_raw_sql_calls_are_not_counted_as_queries(tmp_settings) -> None:
    audit.record(tmp_settings, {"tool": "raw_sql", "sql": "select 1"})
    assert audit.term_usage(tmp_settings).queries == 0


def test_a_repeated_attribute_is_visible_as_a_candidate(tmp_settings) -> None:
    """The point of the report: a field you keep spelling out wants a name."""
    for value in (10, 15, 20):
        audit.record(
            tmp_settings,
            _query(plan={"where": [{"attr": "snaps_unit", "op": "<", "value": value}]}),
        )
    assert audit.term_usage(tmp_settings).attributes["snaps_unit"] == 3
