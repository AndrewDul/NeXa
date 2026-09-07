"""``ConversationContext`` — the bounded view of history sent to a model.

Distinct from chat history (unbounded, owned by ``ConversationSession``) and
from long-term memory (not built until M5, AGENTS.md §3.8). This is only the
per-request window a ``ModelProvider`` call actually sees.

Bounding strategy (deterministic, appropriate for M1.1 — not summarization):
keep the most recent turns, most-recent-first selection, bounded by both a
turn count and a total character budget. Turns are never split; the single
most recent turn is always kept even if it alone exceeds the character
budget, so the model always sees at least the latest thing the user said.

Response-language mirroring (M2.3, R0009): ``to_provider_messages()``
inserts a deterministic language directive after **every** user turn, not
just the latest one. Real-hardware testing found that a fresh English
session's own prose instruction (in the Polish-language persona system
prompt) was not enough on its own — a fresh English question got a Polish
reply. Injecting a directive only for the current turn (as a message never
replayed from stored history) initially fixed correctness but permanently
broke Ollama/llama.cpp's prompt-prefix KV-cache reuse the instant it was
used once: every later turn's context, rebuilt from ``ConversationTurn``
history, then diverges from what was actually cached (which included that
one extra message) at the exact point it was inserted — turning every
"warm" turn (normally ~2-6s to first token) into a full ~15-20s reprocess
for the rest of the session. Recomputing the same directive after every
historical user turn, purely from that turn's own (unmodified) stored text,
means the rebuilt prompt is byte-identical across calls — the previous
prompt's exact prefix, plus new content — so caching works normally. This
keeps ``ConversationTurn``/``ConversationSession.history`` completely
unmodified (the real transcript, ADR-0003 D2) — only the wire-level message
list gains the directive, freshly, every time it's built.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..providers.base import ProviderMessage
from .language import detect_response_language, language_directive
from .response_mode import ResponseMode, voice_response_directive
from .turn import ConversationTurn, Role

DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_CHARS = 12_000


@dataclass(frozen=True, slots=True)
class ConversationContext:
    system_prompt: str
    turns: tuple[ConversationTurn, ...]

    @classmethod
    def build(
        cls,
        system_prompt: str,
        history: Sequence[ConversationTurn],
        *,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> ConversationContext:
        selected: list[ConversationTurn] = []
        total_chars = 0
        for turn in reversed(history):
            if len(selected) >= max_turns:
                break
            turn_chars = len(turn.content)
            if selected and total_chars + turn_chars > max_chars:
                break
            selected.append(turn)
            total_chars += turn_chars
        selected.reverse()
        return cls(system_prompt=system_prompt, turns=tuple(selected))

    def to_provider_messages(
        self, *, response_mode: ResponseMode = ResponseMode.TEXT
    ) -> list[ProviderMessage]:
        messages = [ProviderMessage(role="system", content=self.system_prompt)]
        # M2.4B.3.3: a transient, constant, fixed-position voice-mode
        # instruction. ResponseMode.TEXT (default) adds nothing — the typed
        # sequence is unchanged. Never stored in history; does not decide
        # the response language (the per-turn directive below still does).
        if response_mode == ResponseMode.VOICE:
            messages.append(
                ProviderMessage(role="system", content=voice_response_directive())
            )
        for turn in self.turns:
            messages.append(ProviderMessage(role=turn.role.value, content=turn.content))
            if turn.role == Role.USER:
                language = detect_response_language(turn.content)
                if language is not None:
                    messages.append(
                        ProviderMessage(role="system", content=language_directive(language))
                    )
        return messages
