import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import duckdb
import pytest
from conftest import Stub, connect


def test_primitives(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    result = db.execute(
        "SELECT system_one_noul({'i': 9}, 'urgent?'), system_one_choice({'i': 1}, 'route?', "
        '\'{"a":"alpha","b":"beta"}\'::JSON), '
        "system_one_score('message', 'sentiment?', '[\"low\",\"high\"]'::JSON)"
    ).fetchone()
    assert result is not None
    assert result[0] == {"noul": 0.9, "model": "jev-stub-pinned", "cache_hit": False}
    assert result[1]["choice"] == "b" and result[1]["probabilities"] == {"a": 0.0, "b": 1.0}
    assert result[2]["score"] == 0.5 and result[2]["confidence"] == 0.5
    assert json.loads(result[2]["legend"]) == {"0": "low", "1": "high"}
    assert len(stub.calls) == 3
    assert all(call["authorization"] == "Bearer test-system-one" for call in stub.calls)


def test_resolution_returns_foreign_key_or_abstains(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    rows = db.execute(
        """
        SELECT requested, result.selected_id, result.status, result.confidence,
               result.probabilities, result.model, result.cache_hit
        FROM (
          SELECT requested, system_one_resolve(
            {'select': requested},
            '{"supplier_1":"Acme Industrial Supply"}'::JSON,
            'Resolve the canonical supplier.'
          ) AS result
          FROM (VALUES ('supplier_1'), ('ambiguous'), ('no_match')) AS input(requested)
        )
        ORDER BY requested
        """
    ).fetchall()

    assert [(row[0], row[1], row[2]) for row in rows] == [
        ("ambiguous", None, "ambiguous"),
        ("no_match", None, "no_match"),
        ("supplier_1", "supplier_1", "matched"),
    ]
    assert all(row[3] == 1.0 and row[5] == "jev-stub-pinned" and not row[6] for row in rows)
    assert all(set(row[4]) == {"supplier_1", "ambiguous", "no_match"} for row in rows)
    assert len(stub.calls) == 1


def test_null_and_explain_never_call(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("EXPLAIN SELECT system_one_noul('hello','urgent?')").fetchall()
    assert db.execute("SELECT system_one_noul(NULL,'urgent?'), system_one_noul('x',NULL)").fetchone() == (None, None)
    assert db.execute("SELECT system_one_noul(i::varchar,'x') FROM range(0) t(i)").fetchall() == []
    assert not stub.calls


def test_null_struct_result_has_null_children(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    """A NULL judgment must not expose fabricated values through struct extraction."""
    choice = db.execute(
        "SELECT result IS NULL, result.choice IS NULL, result.confidence IS NULL, "
        "result.probabilities IS NULL, result.model IS NULL, result.cache_hit IS NULL "
        "FROM (SELECT system_one_choice(NULL::VARCHAR, 'route', "
        "'{\"billing\":null,\"technical\":null}'::JSON) AS result)"
    ).fetchone()
    assert choice == (True, True, True, True, True, True)
    assert not stub.calls


def test_constant_dedup(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    rows = db.execute("SELECT system_one_noul('same','urgent?') FROM range(100)").fetchall()
    assert all(r[0]["noul"] == 0.9 for r in rows)
    assert sum(r[0]["cache_hit"] for r in rows) == 99
    assert len(stub.calls) == 1


def test_nested_evidence_null_fields(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute(
        "SELECT system_one_noul({'i':9,'nested':{'text':'hi','missing':NULL},'items':[1,2,NULL]}, 'question')"
    ).fetchone()
    evidence = stub.calls[0]["body"]["questions"]["q0"]["instructions"]["evidence"]
    assert evidence == {"i": 9, "nested": {"text": "hi", "missing": None}, "items": [1, 2, None]}


def test_selection_and_multichunk_order(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    rows = db.execute(
        "SELECT i, (system_one_noul({'i':i},'question')).noul FROM range(6000) t(i) WHERE i%3<>0 ORDER BY i DESC"
    ).fetchall()
    assert len(rows) == 4000
    assert all(value == (i % 10) / 10 for i, value in rows)
    assert all(len(c["body"]["questions"]) <= 25 for c in stub.calls)
    assert len(stub.sockets) <= 10  # Connection pool persists across chunks.




@pytest.mark.parametrize(
    "sql",
    [
        "SELECT system_one_choice('x','p','{}')",
        "SELECT system_one_score('x','p','[\"one\"]')",
        "SELECT system_one_resolve('x','{}','resolve')",
        "SELECT system_one_resolve('x','{\"no_match\":\"reserved\"}','resolve')",
        "SELECT system_one_noul('null'::JSON,'p')",
        "SELECT system_one_noul({'x':'NaN'::DOUBLE},'p')",
    ],
)
def test_invalid_input_fails_without_calls(db: duckdb.DuckDBPyConnection, stub: Stub, sql: str) -> None:
    with pytest.raises(duckdb.Error):
        db.execute(sql).fetchall()
    assert not stub.calls


@pytest.mark.parametrize("mode", ["missing", "range", "malformed"])
def test_protocol_error_is_not_false(db: duckdb.DuckDBPyConnection, stub: Stub, mode: str) -> None:
    stub.mode = mode
    with pytest.raises(duckdb.Error):
        db.execute("SELECT system_one_noul('x','p')").fetchall()
    assert len(stub.calls) == 1


def test_retryable_failures_recover_and_are_counted(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    before = db.execute("SELECT requests, retries, errors FROM dc_stats() WHERE system='system_one'").fetchone()
    assert before is not None
    db.execute("SET dc_system_one_retry_base_ms=1")
    db.execute("SET dc_system_one_retry_max_delay_ms=1")
    stub.status_sequence = [429, 503, 200]
    stub.retry_after = "0"
    assert db.execute("SELECT (system_one_noul('x','p')).noul").fetchone() == (0.9,)
    after = db.execute("SELECT requests, retries, errors FROM dc_stats() WHERE system='system_one'").fetchone()
    assert after is not None
    assert tuple(after[i] - before[i] for i in range(3)) == (1, 2, 0)
    assert [call["status"] for call in stub.calls] == [429, 503, 200]


def test_permanent_http_failure_is_not_retried(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    stub.status = 400
    with pytest.raises(duckdb.Error, match="400.*1 attempt"):
        db.execute("SELECT system_one_noul('x','p')").fetchall()
    assert len(stub.calls) == 1


def test_external_access_disabled(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET enable_external_access=false")
    with pytest.raises(duckdb.Error, match="external access"):
        db.execute("SELECT system_one_noul('x','p')").fetchall()
    assert not stub.calls


def test_exact_byte_budget(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET dc_system_one_max_request_bytes=900")
    db.execute("SELECT system_one_noul({'i':i,'text':repeat('雪\\\"',30)},'p') FROM range(40) t(i)").fetchall()
    assert all(c["bytes"] <= 900 for c in stub.calls)
    previous = len(stub.calls)
    with pytest.raises(duckdb.Error, match="byte budget"):
        db.execute("SELECT system_one_noul(repeat('x',901),'p')").fetchall()
    assert len(stub.calls) == previous


def test_global_ceiling_multiple_connections(stub: Stub) -> None:
    stub.delay = 0.02

    def query(_: int) -> list[Any]:
        con = connect(stub)
        try:
            con.execute("SET dc_system_one_batch_size=1")
            return con.execute("SELECT system_one_noul({'i':i},'p') FROM range(30) t(i)").fetchall()
        finally:
            con.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert all(len(result) == 30 for result in pool.map(query, range(3)))
    assert 2 <= stub.peak <= 10
    assert len(stub.calls) == 90


def test_null_skips_invalid_constant_criteria(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    assert db.execute("SELECT system_one_choice(NULL::VARCHAR,'p','not-json')").fetchone() == (None,)
    assert not stub.calls


def test_timeout_stops_queued_work(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET dc_system_one_batch_size=1")
    db.execute("SET dc_system_one_concurrency=1")
    db.execute("SET dc_system_one_timeout_ms=30")
    db.execute("SET dc_system_one_max_retries=0")
    stub.delay = 0.15
    with pytest.raises(duckdb.Error, match="transport failed"):
        db.execute("SELECT system_one_noul({'i':i},'p') FROM range(200) t(i)").fetchall()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with stub.lock:
            if stub.active == 0:
                break
        time.sleep(0.01)
    with stub.lock:
        assert len(stub.calls) == 1 and stub.active == 0
    stub.delay = 0
    db.execute("SET dc_system_one_timeout_ms=30000")
    assert db.execute("SELECT (system_one_noul({'i':9},'p')).noul").fetchone() == (0.9,)


def test_interrupt_stops_queued_work(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET dc_system_one_batch_size=1")
    db.execute("SET dc_system_one_concurrency=1")
    stub.delay = 0.2
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: db.execute("SELECT system_one_noul({'i':i},'p') FROM range(500) t(i)").fetchall())
        deadline = time.monotonic() + 3
        while not stub.calls and time.monotonic() < deadline:
            time.sleep(0.005)
        assert stub.calls
        db.interrupt()
        with pytest.raises(duckdb.Error):
            future.result(timeout=5)
    time.sleep(0.3)
    assert stub.active == 0 and len(stub.calls) <= 1
    stub.delay = 0
    assert db.execute("SELECT (system_one_noul({'i':9},'p')).noul").fetchone() == (0.9,)


def test_failure_stops_queued_requests(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET dc_system_one_batch_size=1")
    db.execute("SET dc_system_one_concurrency=1")
    stub.mode = "missing"
    with pytest.raises(duckdb.Error):
        db.execute("SELECT system_one_noul({'i':i},'p') FROM range(500) t(i)").fetchall()
    time.sleep(0.05)
    assert len(stub.calls) == 1 and stub.active == 0


def test_slow_context_does_not_occupy_all_workers(stub: Stub) -> None:
    stub.delay = 0.02

    def query(concurrency: int, count: int) -> None:
        con = connect(stub)
        try:
            con.execute("SET dc_system_one_batch_size=1")
            con.execute(f"SET dc_system_one_concurrency={concurrency}")
            con.execute(f"SELECT system_one_noul({{'i':i,'lane':{concurrency}}},'p') FROM range({count}) t(i)").fetchall()
        finally:
            con.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(query, 1, 100)
        deadline = time.monotonic() + 3
        while not stub.calls and time.monotonic() < deadline:
            time.sleep(0.005)
        fast = pool.submit(query, 4, 12)
        fast.result(timeout=5)
        assert not slow.done()
        # Fair scheduling is asserted above; draining the serial query is cleanup.
        # Shared CI runners can take longer than ten seconds for 100 HTTP calls.
        slow.result(timeout=60)
    assert 4 <= stub.peak <= 5








def test_stats_reports_requests_questions_tokens_and_bytes(
    db: duckdb.DuckDBPyConnection, stub: Stub
) -> None:
    columns = "requests,items,cache_hits,retries,errors,input_tokens,output_tokens,request_bytes,response_bytes"
    before = db.execute(f"SELECT {columns} FROM dc_stats() WHERE system='system_one'").fetchone()
    assert before is not None
    rows = db.execute("SELECT system_one_noul({'i':i},'usage') FROM range(3) t(i)").fetchall()
    assert len(rows) == 3
    after = db.execute(f"SELECT {columns} FROM dc_stats() WHERE system='system_one'").fetchone()
    assert after is not None
    delta = tuple(after[i] - before[i] for i in range(len(before)))
    assert delta[:7] == (1, 3, 0, 0, 0, 10, 5)
    assert delta[7] > 0 and delta[8] > 0


@pytest.mark.parametrize(
    ("setting", "value", "sql", "message"),
    [
        (
            "dc_system_one_max_questions_per_query",
            10,
            "SELECT system_one_noul({'i':i},'budget') FROM range(11) t(i)",
            "item budget",
        ),
        (
            "dc_system_one_max_requests_per_query",
            1,
            "SELECT system_one_noul({'i':i},'budget') FROM range(26) t(i)",
            "request budget",
        ),
    ],
)
def test_query_budgets_fail_before_scalar_dispatch(
    db: duckdb.DuckDBPyConnection,
    stub: Stub,
    setting: str,
    value: int,
    sql: str,
    message: str,
) -> None:
    db.execute("SET dc_system_one_batch_size=25")
    db.execute(f"SET {setting}={value}")
    with pytest.raises(duckdb.Error, match=message):
        db.execute(sql).fetchall()
    assert not stub.calls
