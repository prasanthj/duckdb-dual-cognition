"""Paid endpoint smoke tests. Opt in with DC_RUN_LIVE=1 and provider keys."""

import json
import os

import duckdb
import pytest
from conftest import EXTENSION

pytestmark = pytest.mark.skipif(
    os.environ.get("DC_RUN_LIVE") != "1", reason="paid endpoint tests are opt-in"
)


def live_connection() -> duckdb.DuckDBPyConnection:
    system_one_key = os.environ.get("TYPESAFE_API_KEY", "")
    system_two_key = os.environ.get("OPENAI_API_KEY", "")
    if not system_one_key or not system_two_key:
        pytest.skip("TYPESAFE_API_KEY and OPENAI_API_KEY are required")
    con = duckdb.connect(config={"allow_unsigned_extensions": True, "threads": 4})
    con.execute(f"LOAD '{EXTENSION}'")
    con.execute(
        """CREATE TEMPORARY SECRET live_dc (
        TYPE dc,
        SYSTEM_ONE_PROVIDER 'typesafe', SYSTEM_ONE_API_KEY ?, SYSTEM_ONE_MODEL ?,
        SYSTEM_TWO_PROVIDER 'openai', SYSTEM_TWO_API_KEY ?, SYSTEM_TWO_MODEL ?)""",
        [
            system_one_key,
            os.environ.get("TYPESAFE_MODEL", "jev-1.13.0"),
            system_two_key,
            os.environ.get("OPENAI_MODEL", "gpt-5.6-luna"),
        ],
    )
    return con


def test_live_system_one_and_system_two_primitives() -> None:
    con = live_connection()
    try:
        row = con.execute(
            """SELECT
            system_one_noul('Please refund the duplicate charge.',
                'Does the customer request a refund?'),
            system_one_choice('Production login is unavailable.', 'route ticket',
                '{"billing":"payments","technical":"product failures"}'::JSON),
            system_one_score('The outage is blocking every user.', 'severity',
                '["low","medium","critical"]'::JSON),
            system_two_summarize('The customer reports a duplicate charge and asks for a refund.',
                'Summarize in one short sentence'),
            system_two_extract('Order A-42 ships to Seattle on Friday.', 'Extract order facts',
                '["order_id","destination","ship_date"]'::JSON)"""
        ).fetchone()
        assert row is not None
        assert 0 <= row[0]["noul"] <= 1
        assert row[1]["choice"] in {"billing", "technical"}
        assert 0 <= row[2]["score"] <= 2
        assert row[3]["value"].strip()
        assert set(json.loads(row[4]["value"])) == {
            "order_id",
            "destination",
            "ship_date",
        }
    finally:
        con.close()


def test_live_system_one_to_system_two_chain() -> None:
    con = live_connection()
    try:
        row = con.execute(
            """WITH routed AS MATERIALIZED (
                   SELECT (system_one_choice('Customer cannot log in after SSO change',
                       'route ticket',
                       '{"billing":"payments","technical":"product failures"}'::JSON)).choice AS route
               )
               SELECT route, (system_two_generate({'route':route,'issue':'SSO login failure'},
                      'Draft a concise next action')).value FROM routed"""
        ).fetchone()
        assert row is not None and row[0] in {"billing", "technical"}
        assert row[1].strip()
    finally:
        con.close()


def test_live_system_two_to_system_one_chain() -> None:
    con = live_connection()
    try:
        row = con.execute(
            """WITH summarized AS MATERIALIZED (
                   SELECT (system_two_summarize(
                       'Payment failed twice, but the product itself works correctly.',
                       'Summarize the customer issue')).value AS summary
               )
               SELECT summary, (system_one_choice(summary, 'route ticket',
                      '{"billing":"payments","technical":"product failures"}'::JSON)).choice
               FROM summarized"""
        ).fetchone()
        assert row is not None and row[0].strip()
        assert row[1] in {"billing", "technical"}
    finally:
        con.close()
