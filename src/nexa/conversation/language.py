"""Deterministic PL/EN response-language detection for ``ConversationSession``.

Real hardware testing (2026-09-06, M2.3, R0009) found `gemma4:e4b`'s own
in-context language-mirroring instruction insufficient by itself: a fresh
English `ConversationSession` (whose entire system prompt is Polish)
answered a fresh English question in Polish anyway — the dominant language
of the surrounding prompt outweighed a purely prose-instructed mirroring
rule, even after that instruction was strengthened and made explicitly
bilingual (still failed the same real-hardware acceptance test). This
module is the fallback the task's own architecture guidance anticipated:
"do not introduce a deterministic classifier unless actual testing proves
the model-level policy is insufficient" — it now has, so this exists.

Two languages only, matching ADR-0003 D5's PL/EN scope. This is NOT a
general-purpose language classifier and must not be extended to more
languages without new evidence — the same discipline ADR-0003 D5 already
applies to the STT language hint (a related but distinct concern: this
module decides the *response* language from already-transcribed/typed
text, never whisper.cpp's input-language hint).
"""

from __future__ import annotations

import re

_POLISH_DIACRITICS = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")

# Deliberately small, high-precision Polish function-word set: words that
# essentially never appear in English text, so their presence is strong PL
# evidence even in short, diacritic-free sentences (e.g. "Co to jest X?").
# Deliberately excludes "to" — it is also a common English preposition
# ("Respond to this message...") and produced a real false-positive PL
# detection on the English language directive itself before this fix.
_POLISH_STOPWORDS = frozenset(
    {
        "co", "jest", "czym", "jak", "czy", "jaka", "jaki", "jakie",
        "nie", "się", "po", "dla", "gdzie", "kiedy", "dlaczego", "moge",
        "moze", "może", "mogę", "teraz", "jeszcze", "bedzie", "będzie",
        "powiedz", "krotko", "krótko", "mi", "czlowiek", "człowiek",
        "dzisiaj", "prosze", "proszę", "odpowiedz", "polsku", "angielsku",
    }
)

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_response_language(text: str) -> str | None:
    """Best-effort PL/EN detection of one message's dominant language.

    Returns ``"pl"``, ``"en"``, or ``None`` only for text with no words at
    all (nothing to detect) — callers must treat ``None`` as "inject no
    language directive", never guess. Any non-empty text with words is
    classified as ``"pl"`` or ``"en"``; this is a deliberate two-language
    binary choice (ADR-0003 D5 scope), not a general classifier that can
    say "unknown".
    """
    if any(ch in _POLISH_DIACRITICS for ch in text):
        return "pl"

    words = {w.lower() for w in _WORD_RE.findall(text)}
    if not words:
        return None
    if words & _POLISH_STOPWORDS:
        return "pl"
    return "en"


def language_directive(language: str) -> str:
    """A short, explicit, per-turn instruction reinforcing the response
    language — injected as a transient wire-level message, never stored in
    `ConversationTurn` history (it is not something anyone said)."""
    if language == "pl":
        return "Odpowiedz na tę wiadomość po polsku."
    return "Respond to this message in English."
