"""Deterministic tests for ``LocalModelProvider`` (Ollama) against a small
stdlib-only fake HTTP server — no real Ollama required.

The real Ollama path is covered separately (and only on request) in
``test_live_ollama_integration.py``.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.providers.base import (  # noqa: E402
    CancelToken,
    GenerationOptions,
    ModelUnavailableError,
    ProviderMessage,
)
from nexa.providers.ollama import LocalModelProvider  # noqa: E402


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    # Set per-test via server instance attributes.
    def log_message(self, *args, **kwargs) -> None:  # silence test output
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.last_request_body = body  # type: ignore[attr-defined]

        mode = self.server.mode  # type: ignore[attr-defined]
        if mode == "error":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"internal error")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        chunks = self.server.chunks  # type: ignore[attr-defined]
        delay = self.server.chunk_delay  # type: ignore[attr-defined]
        for text in chunks:
            line = json.dumps({"message": {"content": text}, "done": False}) + "\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()
            if delay:
                time.sleep(delay)
        self.wfile.write((json.dumps({"done": True, "eval_count": len(chunks)}) + "\n").encode())
        self.wfile.flush()


class _FakeOllamaServer(HTTPServer):
    def handle_error(self, request, client_address) -> None:
        # A cancelled test client closes the connection mid-stream; the
        # resulting BrokenPipeError in the server thread is expected, not a
        # real failure. Anything else still prints, for debuggability.
        if sys.exc_info()[0] is not BrokenPipeError:
            super().handle_error(request, client_address)


def _start_fake_server(
    *, mode: str = "ok", chunks: list[str] | None = None, chunk_delay: float = 0.0
) -> HTTPServer:
    server = _FakeOllamaServer(("127.0.0.1", 0), _FakeOllamaHandler)
    server.mode = mode  # type: ignore[attr-defined]
    server.chunks = chunks or ["Cześć", ", ", "jak się masz?"]  # type: ignore[attr-defined]
    server.chunk_delay = chunk_delay  # type: ignore[attr-defined]
    server.last_request_body = None  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class TestLocalModelProviderOllama(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._server: HTTPServer | None = None

    def tearDown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    async def test_streams_and_combines_chunks(self) -> None:
        self._server = _start_fake_server(chunks=["Cześć", ", ", "jak się masz?"])
        port = self._server.server_address[1]
        provider = LocalModelProvider(model="gemma4:e4b", base_url=f"http://127.0.0.1:{port}")

        chunks = [
            c
            async for c in provider.generate(
                [ProviderMessage(role="user", content="hej")],
                GenerationOptions(num_ctx=8192, think=False),
            )
        ]

        self.assertEqual("".join(chunks), "Cześć, jak się masz?")

    async def test_request_body_shape(self) -> None:
        self._server = _start_fake_server(chunks=["ok"])
        port = self._server.server_address[1]
        provider = LocalModelProvider(
            model="gemma4:e4b", base_url=f"http://127.0.0.1:{port}", keep_alive="5m"
        )

        request_messages = [
            ProviderMessage(role="system", content="sys"),
            ProviderMessage(role="user", content="hi"),
        ]
        async for _ in provider.generate(
            request_messages,
            GenerationOptions(num_ctx=8192, think=False, num_predict=64),
        ):
            pass

        body = self._server.last_request_body  # type: ignore[attr-defined]
        self.assertEqual(body["model"], "gemma4:e4b")
        self.assertTrue(body["stream"])
        self.assertEqual(body["keep_alive"], "5m")
        self.assertIs(body["think"], False)
        self.assertEqual(body["options"]["num_ctx"], 8192)
        self.assertEqual(body["options"]["num_predict"], 64)
        self.assertEqual(
            body["messages"],
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        )

    async def test_provider_failure_raises_model_unavailable(self) -> None:
        self._server = _start_fake_server(mode="error")
        port = self._server.server_address[1]
        provider = LocalModelProvider(model="gemma4:e4b", base_url=f"http://127.0.0.1:{port}")

        with self.assertRaises(ModelUnavailableError):
            async for _ in provider.generate(
                [ProviderMessage(role="user", content="hi")], GenerationOptions()
            ):
                pass

    async def test_unreachable_server_raises_model_unavailable(self) -> None:
        # Nothing listening on this port.
        provider = LocalModelProvider(model="gemma4:e4b", base_url="http://127.0.0.1:1")

        with self.assertRaises(ModelUnavailableError):
            async for _ in provider.generate(
                [ProviderMessage(role="user", content="hi")], GenerationOptions()
            ):
                pass

    async def test_cancel_token_stops_iteration_early(self) -> None:
        self._server = _start_fake_server(chunks=["a", "b", "c", "d", "e"], chunk_delay=0.05)
        port = self._server.server_address[1]
        provider = LocalModelProvider(model="gemma4:e4b", base_url=f"http://127.0.0.1:{port}")
        cancel_token = CancelToken()

        seen = []
        async for chunk in provider.generate(
            [ProviderMessage(role="user", content="hi")],
            GenerationOptions(),
            cancel_token=cancel_token,
        ):
            seen.append(chunk)
            if len(seen) == 2:
                cancel_token.cancel()

        self.assertLess(len(seen), 5)


if __name__ == "__main__":
    unittest.main()
