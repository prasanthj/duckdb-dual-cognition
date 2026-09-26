"""Run the live dual-cognition walkthrough used by the README VHS hero."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "build/extension/dc/dc.duckdb_extension"

BLUE = "\033[38;5;39m"
CYAN = "\033[38;5;44m"
DIM = "\033[38;5;246m"
GREEN = "\033[38;5;42m"
ORANGE = "\033[38;5;214m"
PURPLE = "\033[38;5;141m"
RESET = "\033[0m"
BOLD = "\033[1m"

CASES = [
    (1, "Charged twice for the annual plan; please refund the duplicate invoice."),
    (2, "SSO login fails after yesterday's SCIM configuration change."),
    (3, "API latency tripled after deployment and production requests now time out."),
    (4, "An unknown administrator exported customer records before a forced password reset."),
    (5, "Webhook delivery fails only for invoices currently under chargeback review."),
    (6, "Finance cannot open invoices because the new role permissions deny access."),
    (7, "A suspected compromised token is also causing intermittent data-sync failures."),
    (8, "Please explain the renewal quote and why this month's amount increased."),
]


def heading(text: str) -> None:
    print(f"\n{BOLD}{BLUE}{text}{RESET}")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def configure(db: duckdb.DuckDBPyConnection) -> None:
    db.execute(f"LOAD '{EXTENSION}'")
    db.execute(
        """CREATE TEMPORARY SECRET live_demo (
             TYPE dc,
             SYSTEM_ONE_PROVIDER 'typesafe', SYSTEM_ONE_API_KEY ?,
             SYSTEM_ONE_MODEL ?,
             SYSTEM_TWO_PROVIDER 'openai', SYSTEM_TWO_API_KEY ?,
             SYSTEM_TWO_MODEL ?)""",
        [
            require_env("TYPESAFE_API_KEY"),
            os.environ.get("TYPESAFE_MODEL", "jev-1.13.0"),
            require_env("OPENAI_API_KEY"),
            os.environ.get("OPENAI_MODEL", "gpt-5.6-luna"),
        ],
    )
    db.execute("SET dc_system_one_batch_size=100")
    db.execute("SET dc_system_one_concurrency=10")
    db.execute("SET dc_system_two_batch_size=25")
    db.execute("SET dc_system_two_concurrency=5")


def classify(db: duckdb.DuckDBPyConnection, threshold: float) -> list[tuple[Any, ...]]:
    placeholders = ",".join(["(?, ?)"] * len(CASES))
    params: list[Any] = []
    for case_id, evidence in CASES:
        params.extend([case_id, evidence])
    params.append(threshold)
    return db.execute(
        f"""WITH inputs(id, evidence) AS (VALUES {placeholders}),
             params(confidence_threshold) AS (VALUES (?::DOUBLE)),
             fast AS MATERIALIZED (
               SELECT id, evidence,
                      system_one_choice(
                        evidence,
                        'Classify the primary issue using only the supplied evidence.',
                        '{{"billing":"charges, invoices, refunds, pricing",'
                        '"technical":"product behavior, integrations, availability",'
                        '"security":"identity, permissions, compromise, data exposure"}}'::JSON
                      ) AS result
               FROM inputs
             ), deep AS MATERIALIZED (
               SELECT id, evidence,
                      result.choice AS system_one_choice,
                      result.confidence AS system_one_confidence,
                      result.model AS system_one_model,
                      confidence_threshold,
                      CASE WHEN result.confidence < confidence_threshold THEN
                        system_two_extract(
                          {{
                            'evidence': evidence,
                            'system_one_candidate': result.choice,
                            'allowed_labels': ['billing', 'technical', 'security']
                          }},
                          'Resolve the ambiguous classification. The classification field must be exactly one allowed label.',
                          '["classification"]'::JSON
                        )
                      END AS system_two_result
               FROM fast CROSS JOIN params
             ), resolved AS MATERIALIZED (
               SELECT *,
                      json_extract_string(system_two_result.value, '$.classification') AS system_two_choice
               FROM deep
             )
             SELECT id,
                    evidence AS input,
                    CASE
                      WHEN system_one_confidence >= confidence_threshold THEN system_one_choice
                      WHEN system_two_choice IN ('billing','technical','security') THEN system_two_choice
                      ELSE 'manual_review'
                    END AS classification,
                    struct_pack(
                      source := CASE
                        WHEN system_one_confidence >= confidence_threshold THEN 'system_one'
                        WHEN system_two_choice IN ('billing','technical','security') THEN 'system_two'
                        ELSE 'manual_review'
                      END,
                      system_one_choice := system_one_choice,
                      system_one_confidence := system_one_confidence,
                      system_one_model := system_one_model,
                      confidence_threshold := confidence_threshold,
                      escalated := system_two_result IS NOT NULL,
                      system_two_choice := system_two_choice,
                      system_two_model := system_two_result.model
                    ) AS provenance
             FROM resolved ORDER BY id""",
        params,
    ).fetchall()


def main() -> None:
    threshold = float(os.environ.get("DC_DEMO_THRESHOLD", "0.90"))
    db = duckdb.connect(config={"allow_unsigned_extensions": True, "threads": 4})
    try:
        configure(db)
        print(f"{BOLD}{BLUE}DUCKDB DUAL COGNITION · LIVE{RESET}")
        print(f"{DIM}TypeSafe System One → confidence gate → OpenAI System Two{RESET}")
        heading("INPUT · 8 SUPPORT CASES")
        for case_id, evidence in CASES:
            print(f"{case_id}  {evidence}")
        heading("POLICY")
        print(f"Classify every row with System One. Escalate confidence < {threshold:.2f}.")
        print("Return one final classification plus complete decision provenance.")
        sys.stdout.flush()

        started = time.perf_counter()
        rows = classify(db, threshold)
        elapsed = time.perf_counter() - started

        heading("RESULT")
        print(f"{DIM}id  final       source       System One        System Two{RESET}")
        for case_id, _, classification, provenance in rows:
            source = provenance["source"]
            final_color = GREEN if source == "system_one" else PURPLE
            slow = provenance["system_two_choice"] or "—"
            print(
                f"{case_id:<3} {final_color}{classification:<11}{RESET} "
                f"{source:<12} {provenance['system_one_choice']:<10} "
                f"{provenance['system_one_confidence']:.2f}     {slow}"
            )

        heading("PROVENANCE · ESCALATED ROWS")
        escalated = 0
        for case_id, _, classification, provenance in rows:
            if provenance["escalated"]:
                escalated += 1
                print(
                    f"{ORANGE}↗ row {case_id}{RESET}  "
                    f"{provenance['system_one_choice']} @ "
                    f"{provenance['system_one_confidence']:.2f} → "
                    f"{classification}  {DIM}[{provenance['system_two_model']}]{RESET}"
                )
        if escalated == 0:
            print(f"{GREEN}No row fell below the configured threshold.{RESET}")

        stats = {
            row[0]: row
            for row in db.execute(
                "SELECT system, requests, items, retries, input_tokens, output_tokens "
                "FROM dc_stats() ORDER BY system"
            ).fetchall()
        }
        heading("LIVE PROVIDER WORK")
        one = stats["system_one"]
        two = stats["system_two"]
        print(
            f"{CYAN}System One{RESET}  {one[1]} request · {one[2]} classifications · "
            f"{one[3]} retries · {rows[0][3]['system_one_model']}"
        )
        print(
            f"{PURPLE}System Two{RESET}  {two[1]} request · {two[2]} escalations · "
            f"{two[3]} retries · {two[4] + two[5]} tokens"
        )
        print(f"\n{BOLD}{GREEN}{len(rows)} final rows in {elapsed:.2f}s; {escalated} required deeper reasoning.{RESET}")
        print(f"{DIM}LIVE_DEMO_COMPLETE{RESET}")
        time.sleep(2)
    finally:
        db.close()


if __name__ == "__main__":
    main()
