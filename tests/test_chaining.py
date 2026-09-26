import json

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


def test_low_confidence_classification_escalates_into_same_derived_column(
    db: duckdb.DuckDBPyConnection, stub: Stub
) -> None:
    stub.mode = "mixed_confidence"
    stub.system_two_value = "security"

    rows = db.execute(
        """WITH params(confidence_threshold) AS (VALUES (0.80)),
           inputs(id, evidence) AS (
               VALUES
                 (0, 'Duplicate invoice charge'),
                 (1, 'Unclear report of unusual account access')
           ), fast AS MATERIALIZED (
               SELECT id, evidence,
                      system_one_choice(
                        {'i':id, 'text':evidence},
                        'Classify the case',
                        '{"billing":"payments","technical":"product failures",'
                        '"security":"access and data risk"}'::JSON
                      ) AS result
               FROM inputs
           ), escalated AS MATERIALIZED (
               SELECT id, evidence, result.choice AS fast_choice,
                      result.confidence AS fast_confidence, confidence_threshold,
                      CASE WHEN result.confidence < confidence_threshold THEN
                        (system_two_generate(
                          {'id':id, 'evidence':evidence,
                           'system_one_candidate':result.choice},
                          'Return exactly one label: billing, technical, or security'
                        )).value
                      END AS system_two_choice
               FROM fast CROSS JOIN params
           )
           SELECT id,
                  CASE
                    WHEN fast_confidence >= confidence_threshold THEN fast_choice
                    WHEN system_two_choice IN ('billing','technical','security')
                      THEN system_two_choice
                    ELSE 'manual_review'
                  END AS classification,
                  fast_confidence,
                  system_two_choice IS NOT NULL AS escalated
           FROM escalated
           ORDER BY id"""
    ).fetchall()

    assert rows == [
        (0, "billing", 0.95, False),
        (1, "security", 0.55, True),
    ]
    assert [call["path"] for call in stub.calls] == [
        "/v1/systemone",
        "/v1/responses",
    ]
    items = json.loads(
        stub.calls[1]["body"]["input"][1]["content"][0]["text"]
    )["items"]
    assert len(items) == 1
    assert items[0]["evidence"]["id"] == 1
