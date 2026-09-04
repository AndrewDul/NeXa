"""``llama-server`` ``ModelProvider`` (ADR-0002 D3 — second implementation,
the portability path).

Talks to ``llama-server``'s OpenAI-compatible ``/v1/chat/completions``
streaming endpoint (SSE). Same standard-library-only approach as the Ollama
provider: a background thread performs the blocking read and feeds an
``asyncio.Queue``.

Known limitation (documented, not silently papered over): unlike Ollama,
``llama-server`` does not accept a per-request context-window size — its
context length is fixed by the server's own ``-c`` startup flag. Callers must
ensure the running ``llama-server`` was started with a context size that
matches ``GenerationOptions.num_ctx``; this provider cannot enforce it and
does not pretend to.

**Not live-tested in M1.1**: there is no GGUF weight file available outside
Ollama's permission-gated blob store on this machine (a pre-existing, unrelated
blocker — see `docs/CURRENT_STATE.md`), so this adapter is verified at the
interface/request-shaping level only (see `tests/test_llama_server_provider.py`),
not against a real running ``llama-server`` process.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from collections.abc import AsyncIterator

from .base import (
    CancelToken,
    GenerationOptions,
    ModelProvider,
    ModelUnavailableError,
    ProviderDescription,
    ProviderMessage,
)

_DONE = object()


class LlamaServerProvider(ModelProvider):
    """Second ``ModelProvider`` implementation: ``llama-server`` (OpenAI-compatible)."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:8080",
        timeout: float = 600.0,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def describe(self) -> ProviderDescription:
        return ProviderDescription(provider_name="llama-server", model=self._model)

    async def generate(
        self,
        messages: list[ProviderMessage],
        options: GenerationOptions,
        *,
        cancel_token: CancelToken | None = None,
    ) -> AsyncIterator[str]:
        body: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "temperature": options.temperature,
            "top_p": options.top_p,
            "top_k": options.top_k,
            "repeat_penalty": options.repeat_penalty,
            "max_tokens": options.num_predict,
        }

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[object] = asyncio.Queue()
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                req = urllib.request.Request(
                    f"{self._base_url}/v1/chat/completions",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    for raw_line in resp:
                        if cancel_token is not None and cancel_token.is_cancelled:
                            return
                        line = raw_line.strip()
                        if not line or not line.startswith(b"data:"):
                            continue
                        payload = line[len(b"data:") :].strip()
                        if payload == b"[DONE]":
                            return
                        chunk = json.loads(payload)
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        content = choices[0].get("delta", {}).get("content", "")
                        if content:
                            loop.call_soon_threadsafe(queue.put_nowait, content)
                        if choices[0].get("finish_reason"):
                            return
            except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
                errors.append(exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = await queue.get()
            if item is _DONE:
                break
            yield item

        if errors:
            raise ModelUnavailableError(
                f"llama-server provider (model={self._model!r}, "
                f"base_url={self._base_url!r}) failed: {errors[0]!r}"
            ) from errors[0]
