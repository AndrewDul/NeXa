"""Ollama-backed ``ModelProvider`` (ADR-0002 D3 — first ``LocalModelProvider``).

Talks to Ollama's native streaming ``/api/chat`` endpoint. Uses only the
standard library (no HTTP client dependency): the blocking streaming read runs
on a background thread and feeds an ``asyncio.Queue`` so the rest of NeXa's
conversation layer stays async without adding a runtime dependency.
"""

from __future__ import annotations

import asyncio
import json
import select
import threading
import time
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
#: M2.5B.1 — how often the streaming worker re-checks
#: ``cancel_token.is_cancelled`` while Ollama is mid-token and the socket
#: has no data. R0029 measured ``cancel() → worker stopped`` at ~2.0 s with
#: a plain blocking ``readline`` (the worker was stuck between lines); that
#: ~2 s overlapped the interrupting utterance's whisper.cpp decode and
#: inflated it ~34 %. ``select()`` on the socket fd (kept in blocking mode —
#: a per-read ``settimeout`` corrupts ``http.client``'s chunked reader) lets
#: the check run ~4×/s so cancellation lands in well under a second.
_CANCEL_POLL_SECS = 0.25


class LocalModelProvider(ModelProvider):
    """First ``LocalModelProvider`` implementation: Ollama.

    ``model`` and ``base_url`` are configuration, not identity — this class
    knows nothing about "NeXa"; it only knows how to drive one Ollama model.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        keep_alive: str | None = "5m",
        num_thread: int | None = None,
        timeout: float = 600.0,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._keep_alive = keep_alive
        # ``num_thread``: an Ollama decode-thread count sent as a request
        # option. ``None`` = let Ollama choose (the pre-M2.4B.3.6 behaviour).
        # NeXa's canonical value comes from ``nexa.config`` via
        # ``build_default_session`` (R0021/R0022 serving finding). This class
        # stays a generic Ollama driver — it does not know the number's
        # provenance.
        self._num_thread = num_thread
        self._timeout = timeout
        #: M2.5B.1 — the Ollama server's own timing counters from the last
        #: completed (``done``) response: ``prompt_eval_count`` /
        #: ``prompt_eval_duration`` / ``eval_count`` / ``eval_duration`` /
        #: ``load_duration`` / ``total_duration`` (ns). ``None`` until the
        #: first full response; not updated for a response cut short by
        #: cancellation. Read by the latency ledger — measurement only, no
        #: control flow depends on it.
        self.last_metrics: dict[str, int] | None = None

    def describe(self) -> ProviderDescription:
        return ProviderDescription(provider_name="ollama", model=self._model)

    async def generate(
        self,
        messages: list[ProviderMessage],
        options: GenerationOptions,
        *,
        cancel_token: CancelToken | None = None,
    ) -> AsyncIterator[str]:
        request_options: dict[str, object] = {
            "num_ctx": options.num_ctx,
            "temperature": options.temperature,
            "top_p": options.top_p,
            "top_k": options.top_k,
            "repeat_penalty": options.repeat_penalty,
            "num_predict": options.num_predict,
        }
        if self._num_thread is not None:
            request_options["num_thread"] = self._num_thread
        body: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": request_options,
        }
        if self._keep_alive is not None:
            body["keep_alive"] = self._keep_alive
        if options.think is not None:
            body["think"] = options.think

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[object] = asyncio.Queue()
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                req = urllib.request.Request(
                    f"{self._base_url}/api/chat",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                deadline = time.monotonic() + self._timeout
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    try:
                        fd = resp.fileno()
                    except (AttributeError, OSError):  # pragma: no cover
                        fd = None
                    while True:
                        if cancel_token is not None and cancel_token.is_cancelled:
                            cancel_token.mark_cancel_observed()
                            return
                        if time.monotonic() > deadline:
                            errors.append(
                                TimeoutError(f"ollama stream exceeded {self._timeout}s")
                            )
                            break
                        # Wait for data with a short poll so the cancel check
                        # above runs ~4x/s; the socket stays blocking so
                        # http.client's chunked reader is not corrupted.
                        if fd is not None and not select.select(
                            [fd], [], [], _CANCEL_POLL_SECS
                        )[0]:
                            continue
                        raw_line = resp.readline()
                        if not raw_line:
                            break  # EOF — stream ended without an explicit done
                        line = raw_line.strip()
                        if not line:
                            continue
                        chunk = json.loads(line)
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            loop.call_soon_threadsafe(queue.put_nowait, content)
                        if chunk.get("done"):
                            # M2.5B.1 — capture the server's own timing counters
                            self.last_metrics = {
                                k: chunk[k] for k in (
                                    "total_duration", "load_duration",
                                    "prompt_eval_count", "prompt_eval_duration",
                                    "eval_count", "eval_duration",
                                ) if isinstance(chunk.get(k), int)
                            }
                            return
            except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
                errors.append(exc)
            finally:
                if cancel_token is not None:
                    cancel_token.mark_worker_stopped()
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = await queue.get()
            if item is _DONE:
                break
            yield item

        if errors:
            raise ModelUnavailableError(
                f"ollama provider (model={self._model!r}, base_url={self._base_url!r}) "
                f"failed: {errors[0]!r}"
            ) from errors[0]
