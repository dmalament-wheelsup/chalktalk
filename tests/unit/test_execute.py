"""Running compiled queries, and stopping them when they run too long."""

from __future__ import annotations

from dataclasses import replace

import pytest

from chalktalk.query.compile import CompiledQuery
from chalktalk.query.execute import QueryTimeout, run


@pytest.fixture
def compiled(mini_gate, mini_store, mini_settings, mini_conn):
    from chalktalk.query.compile import compile_plan

    plan, gate = mini_gate({"group_by": ["season"], "game_types": ["*"]})
    return compile_plan(plan, gate, store=mini_store, settings=mini_settings, conn=mini_conn)


def test_it_returns_rows_as_dictionaries(mini_conn, compiled, mini_settings) -> None:
    result = run(mini_conn, compiled, mini_settings)
    # 7 players in 2022 and 8 in 2023 (the rookie arrives), across 4 games each.
    assert result.rows == [{"season": 2022, "count": 28}, {"season": 2023, "count": 32}]
    assert result.row_count == 2


def test_it_reports_the_matched_total_and_a_sample(mini_conn, compiled, mini_settings) -> None:
    result = run(mini_conn, compiled, mini_settings)
    assert result.total_matched == 60
    assert result.sample_rows
    assert "player_name" in result.sample_columns


def test_timings_are_recorded(mini_conn, compiled, mini_settings) -> None:
    result = run(mini_conn, compiled, mini_settings)
    assert set(result.timing_ms) == {"main", "sample", "count"}
    assert all(v >= 0 for v in result.timing_ms.values())


def test_the_sample_can_be_skipped(mini_conn, compiled, mini_settings) -> None:
    result = run(mini_conn, compiled, mini_settings, with_sample=False)
    assert result.sample_rows == []
    assert "sample" not in result.timing_ms


def test_truncation_is_reported(mini_conn, mini_gate, mini_store, mini_settings) -> None:
    from chalktalk.query.compile import compile_plan

    plan, gate = mini_gate({"group_by": ["season"], "game_types": ["*"], "limit": 1})
    compiled = compile_plan(plan, gate, store=mini_store, settings=mini_settings, conn=mini_conn)
    result = run(mini_conn, compiled, mini_settings)
    assert result.truncated is True


def test_a_query_that_fits_is_not_truncated(mini_conn, compiled, mini_settings) -> None:
    assert run(mini_conn, compiled, mini_settings).truncated is False


def test_a_slow_query_is_cancelled(mini_conn, mini_settings) -> None:
    """DuckDB has no statement timeout, so this is armed by hand.

    Without it, one badly shaped plan holds the single process and the user's
    whole session with it.
    """
    slow = CompiledQuery(
        sql=(
            "SELECT count(*) FROM range(1000000) a, range(1000000) b, range(1000) c "
            "WHERE a.range + b.range + c.range > 0"
        ),
        params=[],
        sample_sql="SELECT 1",
        sample_params=[],
        count_sql="SELECT 1",
        count_params=[],
    )
    impatient = replace(mini_settings, query_timeout_s=0.2)
    with pytest.raises(QueryTimeout) as excinfo:
        run(mini_conn, slow, impatient)
    assert excinfo.value.seconds == 0.2


def test_the_connection_still_works_after_a_timeout(mini_conn, mini_settings) -> None:
    """An interrupted query must not poison the session."""
    slow = CompiledQuery(
        sql="SELECT count(*) FROM range(1000000) a, range(1000000) b, range(1000) c",
        params=[],
        sample_sql="SELECT 1",
        sample_params=[],
        count_sql="SELECT 1",
        count_params=[],
    )
    with pytest.raises(QueryTimeout):
        run(mini_conn, slow, replace(mini_settings, query_timeout_s=0.2))
    assert mini_conn.execute("SELECT 1").fetchone() == (1,)
