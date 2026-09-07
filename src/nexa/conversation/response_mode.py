"""``ResponseMode`` — a transient presentation hint for one model request.

M2.4B.3.3. NeXa still has exactly one canonical ``ConversationSession`` —
one persona, one history, one context, one model authority, one
response-language authority. This is **not** a second brain: it is only a
per-request flag saying whether the reply will be *spoken* or *typed*, so
the provider prompt can carry a short "you are speaking live, keep it
conversational" instruction in voice mode.

Discipline (same as the R0009 language directive, ``conversation.context``):

* the voice instruction is a **fixed constant string in a fixed position**
  (immediately after the persona system prompt), recomputed identically on
  every ``to_provider_messages()`` call, so the wire prompt's prefix stays
  byte-stable and Ollama/llama.cpp prompt-prefix KV-cache reuse is not
  broken;
* it is **never** stored in ``ConversationTurn`` history — it is not
  something anyone said;
* ``ResponseMode.TEXT`` (the default) changes nothing — typed chat's
  provider message sequence is byte-for-byte what it was before B.3.3;
* it does **not** decide the response language — the canonical
  per-turn ``language_directive`` still does that;
* it is a *generation policy*, not output clipping: an explicit request
  for detail / an explanation / steps / a comparison / a list / a long
  answer is honoured, never truncated.
"""

from __future__ import annotations

from enum import StrEnum


class ResponseMode(StrEnum):
    """How the assistant's reply will be presented to the user."""

    TEXT = "text"
    VOICE = "voice"


# One `system` message, bilingual (mirroring the persona's own bilingual
# language rule). Deterministic; never stored in history. Says: speak
# directly, 1–3 sentences for an ordinary question, short first sentence
# (first-audio latency — R0019), no lecture / list by default, BUT honour
# an explicit request for detail / steps / a list / a comparison / more.
_VOICE_RESPONSE_DIRECTIVE = (
    "Rozmawiasz teraz na żywo, głosowo. Odpowiadaj wprost i naturalnie. "
    "Na zwykłe pytanie odpowiadaj zwięźle — zwykle 1–3 zdania. Niech "
    "pierwsze zdanie będzie krótkie i konkretne, żeby mowa mogła szybko "
    "ruszyć. Nie zamieniaj prostego pytania w wykład ani listę i zostaw "
    "użytkownikowi miejsce na kolejne pytanie. Jeśli użytkownik prosi o "
    "szczegóły, wyjaśnienie, kroki, porównanie, listę lub dłuższą "
    "odpowiedź — podaj dokładnie to, o co prosi.\n"
    "You are in a live voice conversation. Answer directly and naturally. "
    "For an ordinary question, be concise — usually 1–3 sentences. Keep the "
    "first sentence short and concrete so speech can begin quickly. Don't "
    "turn a simple question into a lecture or a list, and leave room for "
    "the user's next turn. If the user asks for detail, an explanation, "
    "steps, a comparison, a list, or a longer answer, give exactly what "
    "they ask for."
)


def voice_response_directive() -> str:
    """The transient voice-mode instruction (constant). See module docstring
    for why it is a fixed string in a fixed prompt position."""
    return _VOICE_RESPONSE_DIRECTIVE
