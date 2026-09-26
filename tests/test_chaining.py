import duckdb
from conftest import Stub


def test_system_one_output_feeds_system_two(
    db: duckdb.DuckDBPyConnection, stub: Stub
) -> None:
    row = db.execute(
        """WITH classified AS MATERIALIZED (
               SELECT (system_one_choice({'i':1,'ticket':'refund'}, 'route',
                       '{"billing":"money","support":"product"}'::JSON)).choice AS route
           )
           SELECT route, (system_two_generate({'route':route,'ticket':'refund'},
                  'draft a response')).value FROM classified"""
    ).fetchone()
    assert row is not None and row[0] == "support"
    assert "'route': 'support'" in row[1]
    assert [call["path"] for call in stub.calls] == ["/v1/systemone", "/v1/responses"]


def test_system_two_output_feeds_system_one(
    db: duckdb.DuckDBPyConnection, stub: Stub
) -> None:
    row = db.execute(
        """WITH summarized AS MATERIALIZED (
               SELECT (system_two_summarize('customer cannot log in',
                       'summarize incident')).value AS summary
           )
           SELECT summary, (system_one_choice(summary, 'route',
                  '{"billing":"money","support":"product"}'::JSON)).choice
           FROM summarized"""
    ).fetchone()
    assert row is not None and "cannot log in" in row[0]
    assert row[1] == "support"
    assert [call["path"] for call in stub.calls] == ["/v1/responses", "/v1/systemone"]


def test_multi_stage_chain_works_in_one_sql_statement(
    db: duckdb.DuckDBPyConnection, stub: Stub
) -> None:
    row = db.execute(
        """WITH first AS MATERIALIZED (
               SELECT (system_one_score('angry customer', 'sentiment',
                       '["negative","neutral","positive"]'::JSON)).score AS score
           ), second AS MATERIALIZED (
               SELECT (system_two_generate({'score':score},
                       'write recovery action')).value AS action FROM first
           )
           SELECT action, (system_one_noul(action, 'safe to send')).noul FROM second"""
    ).fetchone()
    assert row is not None and "write recovery action" in row[0]
    assert row[1] == 0.9
    assert [call["path"] for call in stub.calls] == [
        "/v1/systemone",
        "/v1/responses",
        "/v1/systemone",
    ]
