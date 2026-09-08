"""``ResponseLanguageResolver`` — M2.4B.5: the one authority that decides
the language NeXa *replies* in, kept explicitly separate from the language
the user *spoke* (``InputSpeechLanguage``) and from the language STT
*decoded* in (``STTDecodeLanguage``).

Rules (R0024 architecture, operator-accepted):

1. **No explicit request, no sticky preference** → mirror the current
   ``InputSpeechLanguage`` (spoken PL → answer PL, spoken EN → answer EN).
2. **An explicit request in this turn's transcript** ("Answer in
   English.", "Odpowiedz po polsku.", "Od teraz mów po angielsku.",
   "Wracamy do polskiego.") → answer in the requested language **and** set
   it as the sticky session preference (so later turns keep it until
   changed). A one-turn-only override is a possible future refinement; the
   accepted behaviour is sticky.
3. **A sticky preference is set and no new request** → answer in the
   sticky language, regardless of what was spoken.

The request detector is deliberately narrow and deterministic: it fires
only on an imperative *about how NeXa should answer*, never on ordinary
content that merely mentions "Polish"/"English" ("Tell me about Polish
history.", "What's the English word for …?"). The LLM is never the sole
language-routing authority.

``ResponsePreference`` is transient per-session state (``pl`` | ``en`` |
``None``). It is NOT a conversation turn and NOT long-term memory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "ResponsePreference",
    "ResponseLanguageDecision",
    "ResponseLanguageResolver",
    "detect_explicit_language_request",
]

# Content-question leads: if the utterance is asking *how to say / what is
# something in* a language, it is a translation/vocabulary question, not an
# instruction about NeXa's reply language.
_QUESTION_LEAD = re.compile(
    r"^\s*(how (do|did|does|to|would|can) (you|i|we)?\s*(say|write|spell|pronounce|call)"
    r"|what('?s| is| are| was)\b"
    r"|jak (sie |się )?(mówi|powie|powiedzieć|napisać|pisze|przetłumaczy)"
    r"|co (to )?(znaczy|oznacza)\b"
    r"|jak (jest|będzie|brzmi)\b)",
    re.IGNORECASE,
)

# "reply in X" — an imperative directed at the assistant.
_EN_PATTERNS = (
    r"\b(answer|reply|respond|speak|talk|write|say it|switch|go back)\b[^.?!]{0,30}\bin english\b",
    r"\bin english\b[^.?!]{0,15}\b(please|from now on|only)\b",
    r"\bfrom now on\b[^.?!]{0,30}\benglish\b",
    r"\b(switch|change) to english\b",
    r"\b(back|go back) to english\b",
    r"\balways\b[^.?!]{0,25}\bin english\b",
    # Polish phrasings asking for an English reply
    r"\b(odpowiadaj|odpowiedz|mów|mow|mówże|pisz|napisz|powiedz|przełącz|przelacz)\b"
    r"[^.?!]{0,30}\b(po angielsku|na angielski)\b",
    r"\b(od teraz|zawsze|proszę|prosze)\b[^.?!]{0,30}\bpo angielsku\b",
    r"\b(wracamy|wróćmy|wrocmy|wracajmy)\b[^.?!]{0,15}\bdo angielskiego\b",
)
_PL_PATTERNS = (
    r"\b(answer|reply|respond|speak|talk|write|say it|switch|go back)\b[^.?!]{0,30}\bin polish\b",
    r"\bin polish\b[^.?!]{0,15}\b(please|from now on|only)\b",
    r"\bfrom now on\b[^.?!]{0,30}\bpolish\b",
    r"\b(switch|change) to polish\b",
    r"\b(back|go back) to polish\b",
    r"\balways\b[^.?!]{0,25}\bin polish\b",
    r"\b(odpowiadaj|odpowiedz|mów|mow|mówże|pisz|napisz|powiedz|przełącz|przelacz)\b"
    r"[^.?!]{0,30}\b(po polsku|na polski)\b",
    r"\b(od teraz|zawsze|proszę|prosze)\b[^.?!]{0,30}\bpo polsku\b",
    r"\b(wracamy|wróćmy|wrocmy|wracajmy)\b[^.?!]{0,15}\bdo polskiego\b",
)

_EN_RE = [re.compile(p, re.IGNORECASE) for p in _EN_PATTERNS]
_PL_RE = [re.compile(p, re.IGNORECASE) for p in _PL_PATTERNS]


def detect_explicit_language_request(text: str) -> str | None:
    """``"pl"`` / ``"en"`` if the utterance is an instruction to reply in
    that language; ``None`` otherwise (including translation/vocabulary
    questions that merely name a language)."""
    if not text or not text.strip():
        return None
    if _QUESTION_LEAD.search(text):
        return None
    pl_hit = any(r.search(text) for r in _PL_RE)
    en_hit = any(r.search(text) for r in _EN_RE)
    if pl_hit and not en_hit:
        return "pl"
    if en_hit and not pl_hit:
        return "en"
    # both or neither -> not a clean directive
    return None


@dataclass(slots=True)
class ResponsePreference:
    """Transient per-session sticky response-language preference. Not a
    turn, not memory. Mutated in place by the resolver."""

    sticky: str | None = None


@dataclass(frozen=True, slots=True)
class ResponseLanguageDecision:
    response_language: str  # "pl" | "en"
    reason: str
    explicit_request: str | None = None  # the requested language, if any
    preference_changed: bool = False
    sticky_after: str | None = None


@dataclass(slots=True)
class ResponseLanguageResolver:
    """One deterministic authority. ``resolve`` also updates ``preference``
    in place when the turn carries an explicit request."""

    preference: ResponsePreference = field(default_factory=ResponsePreference)

    def resolve(
        self, transcript: str, *, input_language: str
    ) -> ResponseLanguageDecision:
        req = detect_explicit_language_request(transcript)
        if req is not None:
            self.preference.sticky = req
            return ResponseLanguageDecision(
                response_language=req,
                reason=f"explicit request → reply in {req} (sticky preference set)",
                explicit_request=req,
                preference_changed=True,
                sticky_after=req,
            )
        if self.preference.sticky is not None:
            return ResponseLanguageDecision(
                response_language=self.preference.sticky,
                reason=f"sticky session preference: {self.preference.sticky}",
                sticky_after=self.preference.sticky,
            )
        lang = input_language if input_language in ("pl", "en") else "en"
        return ResponseLanguageDecision(
            response_language=lang,
            reason=f"mirror input speech language ({lang})",
            sticky_after=None,
        )
