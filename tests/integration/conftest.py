"""A real OpenAI-compatible server on loopback.

These tests deliberately open a socket: they exercise the actual httpx call
path, status-code classification and JSON parsing, which a scripted
transport cannot prove. The unit suite's no-network guard does not apply
here — it lives in tests/unit/conftest.py.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

#: One scripted reply: answer text, or (status, body) for a failure.
Reply = str | tuple[int, dict[str, Any]]


class FakeModelServer(ThreadingHTTPServer):
    """Serves scripted `/chat/completions` replies and records every request."""

    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.script: dict[str, list[Reply]] = {}
        self.default: Reply = "high"
        self.requests: list[dict[str, Any]] = []
        self.headers_seen: list[dict[str, str]] = []

    @property
    def base_url(self) -> str:
        """The `/v1` base a provider points at."""
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host!s}:{port}/v1"

    def next_reply(self, model_id: str) -> Reply:
        """Pop this model's next scripted reply, or fall back to the default."""
        queue = self.script.get(model_id)
        return queue.pop(0) if queue else self.default

    def prompts_for(self, model_id: str) -> list[str]:
        """Every prompt one model was sent, in order."""
        return [
            request["messages"][-1]["content"]
            for request in self.requests
            if request.get("model") == model_id
        ]


class _Handler(BaseHTTPRequestHandler):
    server: FakeModelServer

    def do_POST(self) -> None:
        """Answer a chat-completions call from the script."""
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append(payload)
        self.server.headers_seen.append(dict(self.headers))

        reply = self.server.next_reply(str(payload.get("model", "")))
        if isinstance(reply, tuple):
            status, body = reply
        else:
            status, body = 200, _completion(reply)
        self._send(status, body)

    def _send(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        if status == 429:
            self.send_header("Retry-After", "7")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the test output clean."""


def _completion(text: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "cost": 0.0001},
    }


@pytest.fixture
def model_server() -> Iterator[FakeModelServer]:
    """A live OpenAI-compatible endpoint on 127.0.0.1."""
    server = FakeModelServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
