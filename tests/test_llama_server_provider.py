"""Deterministic, interface-level tests for ``LlamaServerProvider`` against a
small stdlib-only fake HTTP server.

Not live-tested against a real ``llama-server`` process in M1.1 — see the
module docstring in ``src/nexa/providers/llama_server.py`` for the exact,
pre-existing blocker (no GGUF weight file reachable outside Ollama's
permission-gated blob store on this machine).
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.providers.base import (  # noqa: E402
    GenerationOptions,
    ModelUnavailableError,
    ProviderMessage,
)
from nexa.providers.llama_server import LlamaServerProvider  # noqa: E402


class _FakeLlamaServerHandler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.last_request_body = body  # type: ignore[attr-defined]

        if self.server.mode == "error":  # type: ignore[attr-defined]
            self.send_response(500)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for text in self.server.chunks:  # type: ignore[attr-defined]
            payload = {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()
        final = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def _start_fake_server(*, mode: str = "ok", chunks: list[str] | None = None) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _FakeLlamaServerHandler)
    server.mode = mode  # type: ignore[attr-defined]
    server.chunks = chunks or ["Hello", ", ", "world."]  # type: ignore[attr-defined]
    server.last_request_body = None  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class TestLlamaServerProvider(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._server: HTTPServer | None = None

    def tearDown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    async def test_streams_and_combines_chunks(self) -> None:
        self._server = _start_fake_server(chunks=["Hello", ", ", "world."])
        port = self._server.server_address[1]
        provider = LlamaServerProvider(model="some-gguf", base_url=f"http://127.0.0.1:{port}")

        chunks = [
            c
            async for c in provider.generate(
                [ProviderMessage(role="user", content="hi")], GenerationOptions()
            )
        ]

        self.assertEqual("".join(chunks), "Hello, world.")

    async def test_request_body_is_openai_shaped(self) -> None:
        self._server = _start_fake_server(chunks=["ok"])
        port = self._server.server_address[1]
        provider = LlamaServerProvider(model="some-gguf", base_url=f"http://127.0.0.1:{port}")

        async for _ in provider.generate(
            [ProviderMessage(role="user", content="hi")],
            GenerationOptions(num_predict=64, temperature=0.5),
        ):
            pass

        body = self._server.last_request_body  # type: ignore[attr-defined]
        self.assertEqual(body["model"], "some-gguf")
        self.assertTrue(body["stream"])
        self.assertEqual(body["max_tokens"], 64)
        self.assertEqual(body["temperature"], 0.5)
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
        self.assertNotIn("num_ctx", body)  # documented limitation, not silently faked

    async def test_provider_failure_raises_model_unavailable(self) -> None:
        self._server = _start_fake_server(mode="error")
        port = self._server.server_address[1]
        provider = LlamaServerProvider(model="some-gguf", base_url=f"http://127.0.0.1:{port}")

        with self.assertRaises(ModelUnavailableError):
            async for _ in provider.generate(
                [ProviderMessage(role="user", content="hi")], GenerationOptions()
            ):
                pass


if __name__ == "__main__":
    unittest.main()
