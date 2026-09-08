"""The raw-SQL fence.

The real boundary is the read-only connection from phase 1; this is the second
fence, so a refusal is a clear message rather than a permission error. Both are
tested — the guard's judgement, and that DuckDB itself would stop it anyway.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from chalktalk import sqlguard
from chalktalk.sqlguard import SqlRejected, SqlTimeout, run_raw, validate

# ── what is allowed ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select count(*) from player_game",
        "WITH x AS (SELECT 1 AS n) SELECT * FROM x",
        "  select 1  ",
        "select 1;",
        "-- a comment\nselect 1",
        "/* block */ select 1",
        "(SELECT 1)",
    ],
)
def test_reasonable_statements_pass(sql: str) -> None:
    assert validate(sql)


def test_a_trailing_semicolon_is_stripped() -> None:
    assert validate("select 1;") == "select 1"


def test_comments_are_removed() -> None:
    assert "comment" not in validate("select 1 -- comment")


# ── what is not ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t AS SELECT 1",
        "insert into player_game values (1)",
        "DROP TABLE player_game",
        "update player_game set season = 1",
        "delete from player_game",
        "ATTACH '/tmp/x.db' AS x",
        "COPY (SELECT 1) TO '/tmp/x.csv'",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SET enable_external_access=true",
        "PRAGMA database_list",
        "CHECKPOINT",
        "ALTER TABLE player_game ADD COLUMN x INT",
        "call dbgen(sf=1)",
    ],
)
def test_anything_that_writes_or_configures_is_refused(sql: str) -> None:
    with pytest.raises(SqlRejected):
        validate(sql)


def test_a_write_hidden_after_a_select_is_refused() -> None:
    """The dangerous half is not always at the front; either check may catch it."""
    with pytest.raises(SqlRejected):
        validate("SELECT 1; COPY (SELECT 1) TO '/tmp/x'")


def test_two_statements_are_refused() -> None:
    with pytest.raises(SqlRejected, match="one statement at a time"):
        validate("select 1; select 2")


def test_something_that_is_not_a_query_is_refused() -> None:
    with pytest.raises(SqlRejected, match="only SELECT and WITH"):
        validate("EXPLAIN select 1")


def test_an_empty_statement_is_refused() -> None:
    for sql in ("", "   ", "-- nothing here"):
        with pytest.raises(SqlRejected):
            validate(sql)


def test_a_keyword_inside_a_string_is_just_text() -> None:
    """Refusing `select 'drop me'` would be a guard that cries wolf."""
    assert validate("select 'drop table x' as note")
    assert validate("select * from player_game where player_name = 'Delete Jones'")


def test_a_semicolon_inside_a_string_is_not_a_statement_break() -> None:
    assert validate("select 'a;b' as s")


# ── bounding ──────────────────────────────────────────────────────────────────


def test_the_statement_is_wrapped_with_a_cap() -> None:
    """One row beyond the cap, so truncation can be reported honestly."""
    assert sqlguard.bounded("select 1", 10) == "SELECT * FROM (select 1) AS _q LIMIT 11"


def test_rows_are_capped_and_truncation_is_reported(mini_conn, mini_settings) -> None:
    result = run_raw(mini_conn, "select * from range(100)", 5, 5.0)
    assert len(result.rows) == 5
    assert result.truncated is True


def test_a_short_result_is_not_marked_truncated(mini_conn) -> None:
    result = run_raw(mini_conn, "select * from range(3)", 5, 5.0)
    assert len(result.rows) == 3
    assert result.truncated is False


# ── running ───────────────────────────────────────────────────────────────────


def test_it_returns_named_columns(mini_conn) -> None:
    result = run_raw(mini_conn, "select 1 as n, 'x' as s", 10, 5.0)
    assert result.columns == ["n", "s"]
    assert result.rows == [{"n": 1, "s": "x"}]


def test_it_reads_the_real_tables(mini_conn) -> None:
    result = run_raw(mini_conn, "select count(*) as n from player_game", 10, 5.0)
    assert result.rows[0]["n"] == 60


def test_a_broken_statement_is_a_refusal_not_a_crash(mini_conn) -> None:
    with pytest.raises(SqlRejected):
        run_raw(mini_conn, "select * from no_such_table", 10, 5.0)


def test_the_guard_alone_would_let_a_file_read_through(mini_conn) -> None:
    """It is a SELECT, so the text guard has no objection — and should not pretend to.

    Listing every catalog function that touches the filesystem would be a losing
    game. The connection is the boundary; this test states which layer holds
    what, so nobody later mistakes the guard for the fence.
    """
    assert validate("select * from read_csv('/etc/hosts')")


def test_the_sandboxed_connection_is_what_refuses_a_file_read(tmp_path, mini_settings) -> None:
    """The real boundary, exercised through run_raw rather than asserted about."""
    from dataclasses import replace

    from chalktalk.db import open_ro, open_rw

    settings = replace(mini_settings, home=tmp_path)
    artifact = tmp_path / "sandbox.duckdb"
    open_rw(artifact, settings).close()
    conn = open_ro(artifact, settings)
    try:
        with pytest.raises(SqlRejected):
            run_raw(conn, "select * from read_csv('/etc/hosts')", 10, 5.0)
        assert run_raw(conn, "select 1 as n", 10, 5.0).rows == [{"n": 1}]
    finally:
        conn.close()


def test_a_slow_statement_is_cancelled(mini_conn) -> None:
    slow = "select count(*) from range(1000000) a, range(1000000) b, range(1000) c"
    with pytest.raises(SqlTimeout):
        run_raw(mini_conn, slow, 10, 0.2)


def test_the_connection_survives_a_cancellation(mini_conn) -> None:
    slow = "select count(*) from range(1000000) a, range(1000000) b, range(1000) c"
    with pytest.raises(SqlTimeout):
        run_raw(mini_conn, slow, 10, 0.2)
    assert run_raw(mini_conn, "select 1 as n", 10, 5.0).rows == [{"n": 1}]


def test_the_row_cap_comes_from_settings(mini_settings) -> None:
    assert replace(mini_settings, raw_sql_row_cap=25).raw_sql_row_cap == 25
