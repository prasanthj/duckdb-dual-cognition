"""Deterministic HTTP/1.1 service; never invokes paid inference."""

import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = Path(os.environ.get("DC_BUILD_DIR", ROOT / "build"))
if not BUILD_DIR.exists() and (ROOT / "build-dc").exists():
    BUILD_DIR = ROOT / "build-dc"
EXTENSION = BUILD_DIR / "extension/dc/dc.duckdb_extension"


class TestHTTPServer(ThreadingHTTPServer):
    # Python 3.11 defaults to five pending connections, below our ten workers.
    # A larger backlog prevents test-server overload during concurrent connects.
    request_queue_size = 128


class Stub:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self.sockets: set[tuple[str, int]] = set()
        self.active = 0
        self.peak = 0
        self.delay = 0.0
        self.status = 200
        self.status_sequence: list[int] = []
        self.retry_after: str | None = None
        self.mode = "normal"
        self.system_two_value: str | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            def handle_one_request(self) -> None:
                try:
                    super().handle_one_request()
                except ConnectionResetError:
                    self.close_connection = True  # Expected after native cancellation.

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                data = json.loads(body)
                with owner.lock:
                    status = owner.status_sequence.pop(0) if owner.status_sequence else owner.status
                    owner.calls.append(
                        {
                            "body": data,
                            "bytes": len(body),
                            "authorization": self.headers.get("Authorization"),
                            "path": self.path,
                            "status": status,
                        }
                    )
                    owner.sockets.add(self.client_address)
                    owner.active += 1
                    owner.peak = max(owner.peak, owner.active)
                processing = True
                try:
                    time.sleep(owner.delay)
                    if self.path.endswith("/v1/responses"):
                        user_text = data["input"][1]["content"][0]["text"]
                        items = json.loads(user_text)["items"]
                        results = []
                        for item in items:
                            evidence = item["evidence"]
                            instruction = item["instruction"]
                            if "schema" in item:
                                schema = item["schema"]
                                fields = schema if isinstance(schema, list) else schema.get("required", list(schema))
                                value = json.dumps({field: f"{field}:{evidence}" for field in fields})
                            else:
                                value = owner.system_two_value or f"{instruction} | {evidence}"
                            results.append({"id": item["id"], "value": value})
                        result = {
                            "model": "gpt-5.6-luna-stub",
                            "output": [{"content": [{"type": "output_text", "text": json.dumps({"results": results})}]}],
                            "usage": {"input_tokens": 12, "output_tokens": 7},
                        }
                        if owner.mode == "missing":
                            result["output"][0]["content"][0]["text"] = json.dumps({"results": results[:-1]})
                        response = json.dumps(result).encode() if owner.mode != "malformed" else b"not-json"
                    else:
                        answers = {}
                        for key, question in reversed(list(data["questions"].items())):
                            evidence = question["instructions"]["evidence"]
                            number = evidence.get("i", 9) if isinstance(evidence, dict) else 9
                            kind = question["type"]
                            if kind == "noul":
                                answer = {"type": kind, "noul": (int(number) % 10) / 10}
                            elif kind == "choice":
                                options = list(question["criteria"])
                                chosen = options[int(number) % len(options)]
                                answer = {
                                    "type": kind,
                                    "choice": chosen,
                                    "confidence": 1.0,
                                    "probabilities": {x: float(x == chosen) for x in options},
                                }
                            else:
                                criteria = question["criteria"]
                                answer = {
                                    "type": kind,
                                    "score": 0.5,
                                    "confidence": 0.5,
                                    "legend": {str(i): item for i, item in enumerate(criteria)},
                                    "probabilities": {str(i): 0.5 if i < 2 else 0.0 for i in range(len(criteria))},
                                }
                            if owner.mode == "distinct_confidence" and kind != "noul":
                                answer["confidence"] = 0.73
                            if owner.mode == "mixed_confidence" and kind != "noul":
                                answer["confidence"] = 0.95 if int(number) % 2 == 0 else 0.55
                            if owner.mode == "large_answers":
                                answer["extra"] = "x" * (1024 * 1024)
                            answers[key] = answer
                        if owner.mode == "missing":
                            answers.pop(next(iter(answers)))
                        if owner.mode == "range":
                            next(iter(answers.values()))["noul"] = 1.5
                        result = {
                            "model": "x" * 1025 if owner.mode == "large_model" else "jev-stub-pinned",
                            "answers": answers,
                            "usage": {"input_tokens": 10, "output_tokens": 5},
                        }
                        response = json.dumps(result).encode() if owner.mode != "malformed" else b"not-json"
                    # Count overlapping request processing, not the server's
                    # response-write epilogue. A client can begin its next
                    # request as soon as it has read this response, before the
                    # handler thread reaches its finally block on Linux.
                    with owner.lock:
                        owner.active -= 1
                    processing = False
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    if owner.retry_after is not None:
                        self.send_header("Retry-After", owner.retry_after)
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    if processing:
                        with owner.lock:
                            owner.active -= 1

        self.server = TestHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1/systemone"

    @property
    def responses_endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1/responses"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.fixture
def stub() -> Iterator[Stub]:
    instance = Stub()
    yield instance
    instance.close()


def connect(stub: Stub) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(config={"allow_unsigned_extensions": True, "threads": 4})
    con.execute(f"LOAD '{EXTENSION}'")
    con.execute(
        """CREATE TEMPORARY SECRET dc_test (
        TYPE dc,
        SYSTEM_ONE_PROVIDER 'typesafe', SYSTEM_ONE_API_KEY 'test-system-one',
        SYSTEM_ONE_ENDPOINT ?, SYSTEM_ONE_MODEL 'jev-stub',
        SYSTEM_TWO_PROVIDER 'openai', SYSTEM_TWO_API_KEY 'test-system-two',
        SYSTEM_TWO_ENDPOINT ?, SYSTEM_TWO_MODEL 'gpt-5.6-luna-stub')""",
        [stub.endpoint, stub.responses_endpoint],
    )
    return con


@pytest.fixture
def db(stub: Stub) -> Iterator[duckdb.DuckDBPyConnection]:
    if not EXTENSION.is_file():
        pytest.fail("Build native extension first: ./build.sh")
    con = connect(stub)
    yield con
    con.close()
