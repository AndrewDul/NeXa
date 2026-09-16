"""Wires the default M1.1 canonical conversation path together.

One place decides how the pieces are assembled from configuration, so
`apps/nexa_chat.py` and the live integration test build the exact same real
path — no parallel test-only wiring.

M2.4B.3.6 productionised the R0021/R0022 serving findings here — still one
provider, one model (`gemma4:e4b`), one session authority:

* the canonical `LocalModelProvider` now carries the serving-freeze policy
  from `nexa.config` (`num_thread=2`, `keep_alive=30m`);
* `warm_up_session()` is an OPTIONAL, infrastructure-only model + persona
  prefix primer — it sends the canonical persona + `ResponseMode.VOICE`
  prefix (empty history, no user text) once so Ollama loads the model and
  fills the KV prefix a real first turn would otherwise pay for cold. It
  never touches `session.history`, never creates a `ConversationTurn`, and
  never uses a second session / provider / persona. If Ollama is
  unavailable it raises `ModelUnavailableError` — visible, no silent
  alternate model.

M3.1 adds NeXa's Identity root (ADR-0005) to `system_prompt`, composed
ahead of the existing persona — additive, not a replacement. Identity
states what NeXa is (stable, immutable); persona stays the conversation
style layer (how NeXa speaks) — see `nexa.core.identity` / ADR-0005 D2.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

from .config import load_persona, local_provider_settings_from_env
from .conversation.response_mode import ResponseMode
from .conversation.session import ConversationSession
from .core.identity import load_identity, render_identity_instruction
from .providers.ollama import LocalModelProvider


def build_default_session() -> ConversationSession:
    identity = load_identity()
    persona = load_persona()
    settings = local_provider_settings_from_env()
    provider = LocalModelProvider(
        model=settings.model,
        base_url=settings.base_url,
        keep_alive=settings.keep_alive,
        num_thread=settings.num_thread,
    )
    system_prompt = f"{render_identity_instruction(identity)}\n\n{persona.system}"
    return ConversationSession(
        provider=provider,
        system_prompt=system_prompt,
        options=persona.options,
    )


@dataclass(frozen=True, slots=True)
class WarmUpResult:
    """What `warm_up_session` observed. Infrastructure telemetry only — it is
    never fed back into the session, history, or memory."""

    model: str
    prefix_message_count: int
    first_token_latency_s: float | None
    duration_s: float
    discarded_chars: int


async def warm_up_session(
    session: ConversationSession,
    *,
    response_mode: ResponseMode = ResponseMode.VOICE,
) -> WarmUpResult:
    """Infrastructure-only model + persona-prefix warm-up for `session`.

    Sends the canonical persona + `response_mode` prefix with EMPTY history
    (so the wire messages are exactly the persona `system` message plus, for
    `ResponseMode.VOICE`, the constant voice directive — no user text) once
    through the session's own provider, with `num_predict=1`, and discards
    the output. Ollama then has the model loaded and the ~500-token
    persona/voice prefix already in its KV cache, so the first real user
    turn extends that cached prefix instead of reprocessing it cold
    (R0021: cold-prefix TTFT ~29–40 s vs warm ~3 s).

    Guarantees:

    * `session.history` is untouched — no `ConversationTurn` is created,
      nothing enters chat history or long-term memory;
    * the same `session.provider`, `session.system_prompt` and
      `session.options` are reused — no second model/provider/persona
      authority;
    * on any provider failure `ModelUnavailableError` propagates unchanged
      (fail closed) — NeXa never silently swaps to another model.
    """
    context = session.build_context()  # empty history -> just the prefix
    messages = context.to_provider_messages(response_mode=response_mode)
    warm_options = replace(session.options, num_predict=1)

    start = time.monotonic()
    first_token_at: float | None = None
    discarded = 0
    async for chunk in session.provider.generate(messages, warm_options):
        if first_token_at is None:
            first_token_at = time.monotonic()
        discarded += len(chunk)
    duration = time.monotonic() - start

    return WarmUpResult(
        model=session.provider.describe().model,
        prefix_message_count=len(messages),
        first_token_latency_s=(
            round(first_token_at - start, 3) if first_token_at is not None else None
        ),
        duration_s=round(duration, 3),
        discarded_chars=discarded,
    )
