from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/chain-command"


def load_server() -> ModuleType:
    spec = importlib.util.spec_from_file_location("chain_command_server", EXAMPLE / "server.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_artifact_payload_requires_a_bounded_action_set() -> None:
    module = load_server()
    valid = {"availableActions": [{"id": "reroute"}, {"id": "hold"}]}
    assert module.validate_artifact_payload(valid) == valid["availableActions"]

    for invalid in ({}, {"availableActions": [{"id": "only-one"}]}, {"availableActions": [{"label": "missing"}, {"id": "ok"}]}):
        try:
            module.validate_artifact_payload(invalid)
        except (ValueError, TypeError):
            pass
        else:
            raise AssertionError("invalid bounded action set was accepted")


def test_launcher_stops_before_setup_when_provider_keys_are_missing() -> None:
    environment = os.environ.copy()
    environment.pop("TYPESAFE_API_KEY", None)
    environment.pop("OPENAI_API_KEY", None)
    missing_both = subprocess.run(
        [str(EXAMPLE / "run.sh")], cwd=ROOT, env=environment, capture_output=True, text=True, check=False
    )
    assert missing_both.returncode == 1
    assert missing_both.stderr == "TYPESAFE_API_KEY is required.\n"

    environment["TYPESAFE_API_KEY"] = "test-only"
    missing_openai = subprocess.run(
        [str(EXAMPLE / "run.sh")], cwd=ROOT, env=environment, capture_output=True, text=True, check=False
    )
    assert missing_openai.returncode == 1
    assert missing_openai.stderr == "OPENAI_API_KEY is required.\n"


def test_http_contract_serves_the_artifact_and_both_cognition_routes() -> None:
    module = load_server()

    class FakeApp:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict[str, Any]]] = []

        def system_one(self, payload: dict[str, Any]) -> dict[str, Any]:
            self.requests.append(("system_one", payload))
            return {
                "choice": "reroute",
                "confidence": 0.84,
                "probabilities": {"reroute": 0.84, "hold": 0.16},
                "model": "jev-test",
                "cache_hit": False,
            }

        def system_two(self, payload: dict[str, Any]) -> dict[str, Any]:
            self.requests.append(("system_two", payload))
            return {
                "debrief": {
                    "situation_summary": "The closure threatens the order.",
                    "why_this_decision": "Rerouting preserves service.",
                    "strongest_alternative": "Air freight protects time at a higher cost.",
                    "tradeoff": "Four days of delay.",
                    "what_to_watch_next": "Customer inventory coverage.",
                },
                "model": "system-two-test",
                "cache_hit": False,
            }

        def stats(self) -> list[dict[str, Any]]:
            return []

    app = FakeApp()
    module.Handler.app = app
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        artifact = response.read().decode()
        assert response.status == 200
        assert "Chain Command" in artifact
        assert "window.CHAIN_COMMAND_JEV_ENDPOINT = '/api/system-one'" in artifact
        assert "fetch('/api/system-two'" in artifact

        decision = {
            "incident": {"title": "Yantian closure"},
            "availableActions": [{"id": "reroute"}, {"id": "hold"}],
        }
        connection.request(
            "POST",
            "/api/system-one",
            json.dumps(decision),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["choice"] == "reroute"

        outcome = {
            "selectedAction": {"id": "reroute"},
            "deterministicOutcome": {"points": 88, "maximumPoints": 92},
            "strongestAlternative": {"label": "Air freight", "wasSelected": False},
        }
        connection.request(
            "POST",
            "/api/system-two",
            json.dumps(outcome),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        body = json.loads(response.read())
        assert body["debrief"]["strongest_alternative"].startswith("Air freight")
        assert app.requests == [("system_one", decision), ("system_two", outcome)]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_contract_rejects_malformed_or_unbounded_decisions() -> None:
    module = load_server()

    class ValidatingApp:
        def system_one(self, payload: dict[str, Any]) -> dict[str, Any]:
            module.validate_artifact_payload(payload)
            return {}

        def system_two(self, payload: dict[str, Any]) -> dict[str, Any]:
            return payload

        def stats(self) -> list[dict[str, Any]]:
            return []

    module.Handler.app = ValidatingApp()
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        for body in ("not-json", json.dumps({"availableActions": [{"id": "only-one"}]})):
            connection.request("POST", "/api/system-one", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 400
            assert "error" in json.loads(response.read())
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
