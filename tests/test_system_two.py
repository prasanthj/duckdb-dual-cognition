import json

import duckdb
import pytest
from conftest import EXTENSION, Stub


def test_generate_summarize_and_extract(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    row = db.execute(
        "SELECT system_two_generate('customer note', 'write a reply'), "
        "system_two_summarize({'ticket':'late shipment'}, 'one sentence'), "
        "system_two_extract('Alice is 42', 'extract fields', '[\"name\",\"age\"]'::JSON)"
    ).fetchone()
    assert row is not None
    assert row[0]["value"] == "write a reply | customer note"
    assert "late shipment" in row[1]["value"]
    assert json.loads(row[2]["value"]) == {
        "age": "age:Alice is 42",
        "name": "name:Alice is 42",
    }
    assert all(value["model"] == "gpt-5.6-luna-stub" for value in row)
    assert len(stub.calls) == 3
    assert all(call["path"] == "/v1/responses" for call in stub.calls)
    assert all(call["authorization"] == "Bearer test-system-two" for call in stub.calls)


def test_vector_batching_concurrency_and_order(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET dc_system_two_batch_size=10")
    db.execute("SET dc_system_two_concurrency=4")
    stub.delay = 0.02
    rows = db.execute(
        "SELECT i, (system_two_summarize({'row':i}, 'summarize')).value "
        "FROM range(53) t(i) ORDER BY i DESC"
    ).fetchall()
    assert len(rows) == 53
    assert all(f"'row': {i}" in value for i, value in rows)
    assert len(stub.calls) == 6
    assert 2 <= stub.peak <= 4


def test_duplicate_rows_coalesce_and_connection_cache_is_explicit(
    db: duckdb.DuckDBPyConnection, stub: Stub
) -> None:
    db.execute("SET dc_system_two_session_cache_bytes=1048576")
    rows = db.execute(
        "SELECT system_two_generate('same', 'reply') FROM range(100)"
    ).fetchall()
    assert len(stub.calls) == 1
    assert sum(row[0]["cache_hit"] for row in rows) == 99
    assert db.execute(
        "SELECT (system_two_generate('same', 'reply')).cache_hit"
    ).fetchone() == (True,)
    assert len(stub.calls) == 1
    assert db.execute("SELECT dc_cache_clear()").fetchone() == (True,)
    assert db.execute(
        "SELECT (system_two_generate('same', 'reply')).cache_hit"
    ).fetchone() == (False,)
    assert len(stub.calls) == 2


@pytest.mark.parametrize("mode", ["missing", "malformed"])
def test_invalid_provider_response_never_becomes_data(
    db: duckdb.DuckDBPyConnection, stub: Stub, mode: str
) -> None:
    stub.mode = mode
    with pytest.raises(duckdb.Error):
        db.execute("SELECT system_two_generate('evidence', 'instruction')").fetchall()
    assert len(stub.calls) == 1


def test_retry_and_metrics(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET dc_system_two_retry_base_ms=1")
    db.execute("SET dc_system_two_retry_max_delay_ms=1")
    stub.status_sequence = [429, 503, 200]
    stub.retry_after = "0"
    before = db.execute(
        "SELECT requests,retries,errors FROM dc_stats() WHERE system='system_two'"
    ).fetchone()
    assert db.execute(
        "SELECT (system_two_generate('evidence', 'instruction')).value"
    ).fetchone() == ("instruction | evidence",)
    after = db.execute(
        "SELECT requests,retries,errors FROM dc_stats() WHERE system='system_two'"
    ).fetchone()
    assert before is not None and after is not None
    assert tuple(after[i] - before[i] for i in range(3)) == (1, 2, 0)


def test_nulls_and_invalid_schema_do_not_dispatch(
    db: duckdb.DuckDBPyConnection, stub: Stub
) -> None:
    assert db.execute(
        "SELECT system_two_generate(NULL, 'x'), system_two_extract('x','y',NULL)"
    ).fetchone() == (None, None)
    with pytest.raises(duckdb.Error, match="schema"):
        db.execute("SELECT system_two_extract('x','y','[]'::JSON)").fetchall()
    assert not stub.calls


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("dc_system_two_batch_size", 0),
        ("dc_system_two_concurrency", 11),
        ("dc_system_two_cache_bytes", -1),
        ("dc_system_two_session_cache_ttl_ms", 0),
    ],
)
def test_invalid_settings_fail_before_dispatch(
    db: duckdb.DuckDBPyConnection, stub: Stub, setting: str, value: int
) -> None:
    db.execute(f"SET {setting}={value}")
    with pytest.raises(duckdb.Error, match="invalid System Two settings"):
        db.execute("SELECT system_two_generate('evidence', 'instruction')").fetchall()
    assert not stub.calls


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("dc_system_two_max_items_per_query", 3, "item budget"),
        ("dc_system_two_max_requests_per_query", 1, "request budget"),
    ],
)
def test_query_budgets_fail_before_dispatch(
    db: duckdb.DuckDBPyConnection,
    stub: Stub,
    setting: str,
    value: int,
    message: str,
) -> None:
    db.execute("SET dc_system_two_batch_size=2")
    db.execute(f"SET {setting}={value}")
    with pytest.raises(duckdb.Error, match=message):
        db.execute(
            "SELECT system_two_generate({'row':i}, 'instruction') FROM range(4) t(i)"
        ).fetchall()
    assert not stub.calls


def test_combined_secret_requires_both_systems_and_redacts_keys(stub: Stub) -> None:
    con = duckdb.connect(config={"allow_unsigned_extensions": True})
    try:
        con.execute(f"LOAD '{EXTENSION}'")
        with pytest.raises(duckdb.Error, match="system_two_api_key"):
            con.execute(
                "CREATE TEMPORARY SECRET incomplete (TYPE dc, "
                "SYSTEM_ONE_PROVIDER 'typesafe', SYSTEM_ONE_API_KEY 'one', "
                "SYSTEM_ONE_MODEL 'jev', SYSTEM_TWO_PROVIDER 'openai', "
                "SYSTEM_TWO_MODEL 'luna')"
            )
        con.execute(
            """CREATE TEMPORARY SECRET complete (
            TYPE dc,
            SYSTEM_ONE_PROVIDER 'typesafe', SYSTEM_ONE_API_KEY 'one-secret',
            SYSTEM_ONE_ENDPOINT ?, SYSTEM_ONE_MODEL 'jev',
            SYSTEM_TWO_PROVIDER 'openai', SYSTEM_TWO_API_KEY 'two-secret',
            SYSTEM_TWO_ENDPOINT ?, SYSTEM_TWO_MODEL 'luna')""",
            [stub.endpoint, stub.responses_endpoint],
        )
        rendered = con.execute(
            "SELECT secret_string FROM duckdb_secrets() WHERE name='complete'"
        ).fetchone()
        assert rendered is not None
        assert "one-secret" not in rendered[0] and "two-secret" not in rendered[0]
    finally:
        con.close()
