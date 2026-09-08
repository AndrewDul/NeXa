"""M2.4B.3.5 — shared plumbing for the operator-blind model A/B.

RESEARCH TOOLING ONLY (deletable with the rest of `docs/research/`). It
does **not** change any production default: `gemma4:e4b` is still the
canonical model (`nexa.config.DEFAULT_LOCAL_MODEL`); this only builds a
throwaway `ConversationSession` bound to whichever model the hidden
mapping assigns to a candidate label.

Fairness: both candidates get the *same* everything — the real
`ConversationSession`, the real persona (`configs/personas/nexa_persona_v1.json`),
the real `GenerationOptions` (num_ctx / temperature / num_predict …),
`ResponseMode.VOICE`, `keep_alive`, and `num_thread` (R0021's serving
finding, applied to BOTH so it is not an unfair advantage). The only
independent variable is the model identity, which is never printed.
"""
from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path

from nexa.config import DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_MODEL_ENDPOINT, load_persona
from nexa.conversation.session import ConversationSession
from nexa.providers.base import CancelToken, GenerationOptions, ProviderMessage
from nexa.providers.ollama import LocalModelProvider, ModelUnavailableError

_HERE = Path(__file__).resolve().parent
MAPPING_PATH = _HERE / "operator_blind_ab_mapping_20260908.txt"
RESULTS_DIR = _HERE / "blind_results"

#: The only two models this comparison is allowed to touch.
ALLOWED_MODELS: frozenset[str] = frozenset({"gemma4:e4b", "gemma4:e2b"})
CANDIDATE_LABELS: tuple[str, ...] = ("A", "B")

#: R0021: `num_thread=2` is the fastest decode config on this 4-core Pi.
#: Applied identically to BOTH candidates so the model is the only variable.
#: NOT shipped to production config here — research harness only.
BLIND_NUM_THREAD = 2
#: Long enough that the model never evicts mid-session (the "always-on
#: NeXa" condition). Identical for both candidates.
BLIND_KEEP_ALIVE = "30m"

_DONE = object()


# --------------------------------------------------------------------------- #
# hidden mapping
# --------------------------------------------------------------------------- #

def load_mapping() -> dict[str, str]:
    """Read the sealed ``{"A": <model>, "B": <model>}`` mapping.

    Raises if the file is missing or malformed. The caller MUST NOT print
    the result to an operator-visible stream.
    """
    if not MAPPING_PATH.exists():
        raise FileNotFoundError(
            f"blind mapping not found at {MAPPING_PATH} — run make_mapping.py first"
        )
    data = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    mapping = data["mapping"] if "mapping" in data else data
    labels = sorted(mapping)
    if labels != list(CANDIDATE_LABELS):
        raise ValueError(f"mapping labels {labels!r} != {list(CANDIDATE_LABELS)!r}")
    models = set(mapping.values())
    if models != set(ALLOWED_MODELS):
        raise ValueError(
            f"mapping models {sorted(models)!r} != {sorted(ALLOWED_MODELS)!r}"
        )
    if mapping["A"] == mapping["B"]:
        raise ValueError("mapping assigns the same model to both candidates")
    return {k: str(v) for k, v in mapping.items()}


def resolve_model(candidate: str) -> str:
    """``"A"``/``"B"`` -> the assigned Ollama model tag (never print it)."""
    c = candidate.strip().upper()
    if c not in CANDIDATE_LABELS:
        raise ValueError(f"candidate must be one of {CANDIDATE_LABELS}, got {candidate!r}")
    return load_mapping()[c]


# --------------------------------------------------------------------------- #
# provider — LocalModelProvider + num_thread (kept identical for A and B)
# --------------------------------------------------------------------------- #

class _NumThreadProvider(LocalModelProvider):
    """``LocalModelProvider`` with one extra Ollama request option:
    ``num_thread``. Mirrors ``LocalModelProvider.generate`` exactly (M2.3,
    R0009 wire discipline) and only adds that one key to ``options``.
    Research harness only — production still uses the stock provider.
    """

    def __init__(self, *, num_thread: int | None = None, **kw) -> None:
        super().__init__(**kw)
        self._num_thread = num_thread

    async def generate(  # noqa: D102 - see LocalModelProvider.generate
        self,
        messages: list[ProviderMessage],
        options: GenerationOptions,
        *,
        cancel_token: CancelToken | None = None,
    ) -> AsyncIterator[str]:
        opts: dict[str, object] = {
            "num_ctx": options.num_ctx,
            "temperature": options.temperature,
            "top_p": options.top_p,
            "top_k": options.top_k,
            "repeat_penalty": options.repeat_penalty,
            "num_predict": options.num_predict,
        }
        if self._num_thread is not None:
            opts["num_thread"] = self._num_thread
        body: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": opts,
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
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    for raw_line in resp:
                        if cancel_token is not None and cancel_token.is_cancelled:
                            return
                        line = raw_line.strip()
                        if not line:
                            continue
                        chunk = json.loads(line)
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            loop.call_soon_threadsafe(queue.put_nowait, content)
                        if chunk.get("done"):
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
                f"ollama provider (model=<blinded>, base_url={self._base_url!r}) "
                f"failed: {errors[0]!r}"
            ) from errors[0]


def build_blind_session(candidate: str) -> ConversationSession:
    """A FRESH `ConversationSession` bound to the candidate's model.

    Same persona + `GenerationOptions` for both candidates; only the model
    tag differs (and is not exposed). Each call returns a new session with
    empty history, so candidate A cannot inherit candidate B's context.
    """
    model = resolve_model(candidate)
    persona = load_persona()
    provider = _NumThreadProvider(
        model=model,
        base_url=DEFAULT_LOCAL_MODEL_ENDPOINT,
        keep_alive=BLIND_KEEP_ALIVE,
        num_thread=BLIND_NUM_THREAD,
    )
    return ConversationSession(
        provider=provider,
        system_prompt=persona.system,
        options=persona.options,
    )


def production_default_model() -> str:
    """The canonical model, for tests that assert it is untouched."""
    return DEFAULT_LOCAL_MODEL
