"""Render the deterministic dual-cognition walkthrough used by the VHS tape."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import EXTENSION, Stub

BLUE = "\033[38;5;39m"
CYAN = "\033[38;5;44m"
DIM = "\033[38;5;246m"
GREEN = "\033[38;5;42m"
ORANGE = "\033[38;5;214m"
PURPLE = "\033[38;5;141m"
RESET = "\033[0m"
BOLD = "\033[1m"


def heading(text: str) -> None:
    print(f"\n{BOLD}{BLUE}{text}{RESET}")


def pause(seconds: float = 0.7) -> None:
    sys.stdout.flush()
    time.sleep(seconds)


def main() -> None:
    stub = Stub()
    stub.mode = "mixed_confidence"
    stub.system_two_value = "technical"
    db = duckdb.connect(config={"allow_unsigned_extensions": True, "threads": 4})
    try:
        db.execute(f"LOAD '{EXTENSION}'")
        db.execute(
            """CREATE TEMPORARY SECRET demo (
                 TYPE dc,
                 SYSTEM_ONE_PROVIDER 'typesafe', SYSTEM_ONE_API_KEY 'local-demo',
                 SYSTEM_ONE_ENDPOINT ?, SYSTEM_ONE_MODEL 'jev-demo',
                 SYSTEM_TWO_PROVIDER 'openai', SYSTEM_TWO_API_KEY 'local-demo',
                 SYSTEM_TWO_ENDPOINT ?, SYSTEM_TWO_MODEL 'luna-demo')""",
            [stub.endpoint, stub.responses_endpoint],
        )

        print(f"{BOLD}{BLUE}DUCKDB DUAL COGNITION{RESET}")
        print(f"{DIM}Fast bounded judgment + selective generative reasoning in SQL{RESET}")
        heading("INPUT")
        print(f"{DIM}id  evidence{RESET}")
        print("0   Duplicate invoice charge")
        print("1   Account access fails intermittently after login")
        pause()

        heading("POLICY")
        print("Classify with System One. Escalate only when confidence < 0.80.")
        print("Write the final answer into one derived column and retain provenance.")
        pause()

        rows = db.execute(
            """WITH params(confidence_threshold) AS (VALUES (0.80::DOUBLE)),
               inputs(id, evidence) AS (
                 VALUES (0, 'Duplicate invoice charge'),
                        (1, 'Account access fails intermittently after login')
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
               SELECT id, evidence,
                      CASE
                        WHEN fast_confidence >= confidence_threshold THEN fast_choice
                        WHEN system_two_choice IN ('billing','technical','security')
                          THEN system_two_choice
                        ELSE 'manual_review'
                      END AS classification,
                      CASE
                        WHEN fast_confidence >= confidence_threshold THEN 'system_one'
                        WHEN system_two_choice IN ('billing','technical','security')
                          THEN 'system_two'
                        ELSE 'manual_review'
                      END AS source,
                      fast_choice, fast_confidence, system_two_choice
               FROM escalated ORDER BY id"""
        ).fetchall()

        heading("RESULT")
        print(f"{DIM}id  final          source       System One         System Two{RESET}")
        for row_id, _, classification, source, fast_choice, confidence, slow_choice in rows:
            final_color = GREEN if source == "system_one" else PURPLE
            slow = slow_choice or "—"
            print(
                f"{row_id:<3} {final_color}{classification:<14}{RESET} "
                f"{source:<12} {fast_choice:<10} {confidence:.2f}     {slow}"
            )
        pause(1.0)

        heading("PROVENANCE")
        print(f"{GREEN}✓ row 0{RESET}  billing @ 0.95 ≥ 0.80  → accepted from System One")
        print(f"{ORANGE}↗ row 1{RESET}  security @ 0.55 < 0.80 → System Two → technical")
        pause()

        stats = {
            row[0]: {"requests": row[1], "items": row[2]}
            for row in db.execute(
                "SELECT system, requests, items FROM dc_stats() ORDER BY system"
            ).fetchall()
        }
        heading("WORK PERFORMED")
        print(
            f"{CYAN}System One{RESET}  {stats['system_one']['requests']} batched request  "
            f"{stats['system_one']['items']} classifications"
        )
        print(
            f"{PURPLE}System Two{RESET}  {stats['system_two']['requests']} request          "
            f"{stats['system_two']['items']} escalated row"
        )
        print(f"\n{BOLD}{GREEN}Only the ambiguous row paid for deeper reasoning.{RESET}")
        print(f"{DIM}DEMO_COMPLETE {json.dumps({'rows': len(rows), 'threshold': 0.8})}{RESET}")
        pause(2.0)
    finally:
        db.close()
        stub.close()


if __name__ == "__main__":
    main()
