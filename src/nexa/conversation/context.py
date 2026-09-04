"""``ConversationContext`` — the bounded view of history sent to a model.

Distinct from chat history (unbounded, owned by ``ConversationSession``) and
from long-term memory (not built until M5, AGENTS.md §3.8). This is only the
per-request window a ``ModelProvider`` call actually sees.

Bounding strategy (deterministic, appropriate for M1.1 — not summarization):
keep the most recent turns, most-recent-first selection, bounded by both a
turn count and a total character budget. Turns are never split; the single
most recent turn is always kept even if it alone exceeds the character
budget, so the model always sees at least the latest thing the user said.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..providers.base import ProviderMessage
from .turn import ConversationTurn

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

    def to_provider_messages(self) -> list[ProviderMessage]:
        messages = [ProviderMessage(role="system", content=self.system_prompt)]
        messages.extend(ProviderMessage(role=t.role.value, content=t.content) for t in self.turns)
        return messages
