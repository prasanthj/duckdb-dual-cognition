"""Local server for the original Chain Command artifact and dual cognition."""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import duckdb

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXTENSION = ROOT / "build/extension/dc/dc.duckdb_extension"

SYSTEM_ONE_INSTRUCTIONS = (
    "You are Jev, the rapid decision engine in a supply-chain control tower. Select the best immediate action using "
    "only the incident, evidence, current network state, recent decisions, action descriptions, and weighted criteria. "
    "Return exactly one offered action ID. Protect service, resilience, and customer trust without accepting "
    "disproportionate margin or inventory damage."
)

SYSTEM_TWO_INSTRUCTIONS = (
    "Write a concise executive decision debrief using only the supplied game record. The deterministic consequence, "
    "metric deltas, score, and best-action comparison are facts. Explain why the selected action was strong, or say "
    "clearly when the best alternative was stronger. Do not invent operational facts. Return every requested field "
    "as a short plain-language string."
)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def json_object(value: Any) -> dict[str, Any]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise TypeError("Provider JSON result must be an object")
    return parsed


def validate_artifact_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    actions = payload.get("availableActions")
    if not isinstance(actions, list) or len(actions) < 2:
        raise ValueError("availableActions must contain at least two actions")
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get("id"), str):
            raise TypeError("Every available action must have an ID")
    return actions


class ChainCommand:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.db = duckdb.connect(config={"allow_unsigned_extensions": True, "threads": 4})
        self.db.execute(f"LOAD '{EXTENSION}'")
        self.db.execute(
            """CREATE TEMPORARY SECRET chain_command (
                 TYPE dc,
                 SYSTEM_ONE_PROVIDER 'typesafe', SYSTEM_ONE_API_KEY ?, SYSTEM_ONE_MODEL ?,
                 SYSTEM_TWO_PROVIDER 'openai', SYSTEM_TWO_API_KEY ?, SYSTEM_TWO_MODEL ?)""",
            [
                require_env("TYPESAFE_API_KEY"),
                os.environ.get("TYPESAFE_MODEL", "jev-1.13.0"),
                require_env("OPENAI_API_KEY"),
                os.environ.get("OPENAI_MODEL", "gpt-5.6-luna"),
            ],
        )
        self.db.execute("SET dc_system_one_batch_size=100")
        self.db.execute("SET dc_system_one_concurrency=10")
        self.db.execute("SET dc_system_two_batch_size=25")
        self.db.execute("SET dc_system_two_concurrency=5")
        self.db.execute("SET dc_system_one_session_cache_bytes=8388608")
        self.db.execute("SET dc_system_two_session_cache_bytes=8388608")

    def system_one(self, payload: dict[str, Any]) -> dict[str, Any]:
        actions = validate_artifact_payload(payload)
        criteria = {str(action["id"]): f"{action.get('label', '')}: {action.get('summary', '')}" for action in actions}
        with self.lock:
            row = self.db.execute(
                "SELECT system_one_choice(?::JSON, ?, ?::JSON)",
                [json.dumps(payload), SYSTEM_ONE_INSTRUCTIONS, json.dumps(criteria)],
            ).fetchone()
        if row is None:
            raise RuntimeError("System One returned no result")
        value = row[0]
        return {
            "choice": value["choice"],
            "confidence": value["confidence"],
            "probabilities": json_object(value["probabilities"]),
            "model": value["model"],
            "cache_hit": value["cache_hit"],
        }

    def system_two(self, payload: dict[str, Any]) -> dict[str, Any]:
        fields = [
            "situation_summary",
            "why_this_decision",
            "strongest_alternative",
            "tradeoff",
            "what_to_watch_next",
        ]
        with self.lock:
            row = self.db.execute(
                "SELECT system_two_extract(?::JSON, ?, ?::JSON)",
                [json.dumps(payload), SYSTEM_TWO_INSTRUCTIONS, json.dumps(fields)],
            ).fetchone()
        if row is None:
            raise RuntimeError("System Two returned no result")
        value = row[0]
        debrief = json_object(value["value"])
        return {
            "debrief": {field: str(debrief.get(field, "")) for field in fields},
            "model": value["model"],
            "cache_hit": value["cache_hit"],
        }

    def stats(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT system, requests, items, cache_hits, retries FROM dc_stats() ORDER BY system"
            ).fetchall()
        return [
            {"system": row[0], "requests": row[1], "items": row[2], "cache_hits": row[3], "retries": row[4]}
            for row in rows
        ]


class Handler(BaseHTTPRequestHandler):
    app: ChainCommand

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if path == "/api/stats":
            self._json({"stats": self.app.stats()})
            return
        if path not in {"/", "/index.html"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = (HERE / "index.html").read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._payload()
            if path == "/api/system-one":
                self._json(self.app.system_one(payload))
                return
            if path == "/api/system-two":
                self._json(self.app.system_two(payload))
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (duckdb.Error, RuntimeError, KeyError) as exc:
            self._json({"error": f"Provider request failed: {exc}"}, HTTPStatus.BAD_GATEWAY)

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[chain-command] {format_string % args}")

    def _payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise TypeError("Request body must be a JSON object")
        return payload

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    host = "127.0.0.1"
    port = int(os.environ.get("CHAIN_COMMAND_PORT", "8787"))
    Handler.app = ChainCommand()
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"Chain Command is running at {url}")
    print("Press Ctrl-C to stop.")
    if os.environ.get("CHAIN_COMMAND_OPEN_BROWSER", "1") != "0":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
