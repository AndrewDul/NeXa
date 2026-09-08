"""``ConversationSession`` — the canonical authority for one text conversation.

The one path: accept user text → append user turn → build bounded context →
call the ``ModelProvider`` → stream assistant output → append the completed
assistant turn. No parallel "final answer" path, no MAS, no silent fallback
provider/model (AGENTS.md §3.2/§3.3, ADR-0002 D1).

On provider failure the exception propagates to the caller unchanged (fail
closed) and no assistant turn is recorded — the user's turn stays in history,
but nothing false is appended in its place.

Response-language mirroring (M2.3, R0009) is implemented in
``ConversationContext.to_provider_messages()``, not here — see that
module's docstring. ``session.history`` (the real transcript, ADR-0003 D2)
is completely unaffected: it stores exactly what the user said and what the
model replied, nothing else.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from ..providers.base import CancelToken, GenerationOptions, ModelProvider
from .context import DEFAULT_MAX_CHARS, DEFAULT_MAX_TURNS, ConversationContext
from .response_mode import ResponseMode
from .streaming import StreamingResponse
from .turn import ConversationTurn, Role


@dataclass
class ConversationSession:
    provider: ModelProvider
    system_prompt: str
    options: GenerationOptions = field(default_factory=GenerationOptions)
    max_turns: int = DEFAULT_MAX_TURNS
    max_chars: int = DEFAULT_MAX_CHARS
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _history: list[ConversationTurn] = field(default_factory=list, init=False, repr=False)
    # M2.4B.5: per-history-entry resolved response language, index-aligned
    # with ``_history`` (``None`` for assistant entries and for user turns
    # that used no explicit request / sticky preference — those fall back
    # to R0009 text detection). Transient wire-level metadata: NOT part of
    # ``history`` (the real transcript), NOT long-term memory.
    _response_languages: list[str | None] = field(
        default_factory=list, init=False, repr=False
    )

    @property
    def history(self) -> tuple[ConversationTurn, ...]:
        return tuple(self._history)

    def build_context(self) -> ConversationContext:
        return ConversationContext.build(
            self.system_prompt,
            self._history,
            max_turns=self.max_turns,
            max_chars=self.max_chars,
            response_languages=self._response_languages,
        )

    async def send(
        self,
        user_text: str,
        *,
        cancel_token: CancelToken | None = None,
        response_mode: ResponseMode = ResponseMode.TEXT,
        response_language: str | None = None,
    ) -> AsyncIterator[str]:
        """``response_mode`` (M2.4B.3.3) is a transient presentation hint —
        ``VOICE`` adds one constant "speak conversationally" system message
        to the wire prompt.

        ``response_language`` (M2.4B.5) is the language NeXa should reply in
        for *this* turn, as resolved by the voice
        ``ResponseLanguageResolver`` (explicit user request / sticky
        session preference). ``None`` (the default, and always for typed
        chat) keeps the pre-B.5 behaviour: the response language is the
        R0009 per-turn detection of the user's own text. Either way it is
        wire-level only — never stored in history.

        ``TEXT`` + no ``response_language`` is byte-for-byte the pre-B.3.3
        path."""
        self._history.append(ConversationTurn(role=Role.USER, content=user_text))
        self._response_languages.append(response_language)

        context = self.build_context()
        messages = context.to_provider_messages(response_mode=response_mode)

        raw_stream = self.provider.generate(messages, self.options, cancel_token=cancel_token)
        response = StreamingResponse(raw_stream)
        async for chunk in response:
            yield chunk

        self._history.append(ConversationTurn(role=Role.ASSISTANT, content=response.text))
        self._response_languages.append(None)
