"""``ResponseLanguageResolver`` — M2.4B.5 / corrected M2.4B.5B (R0027): the
one authority that decides the language NeXa *replies* in, kept explicitly
separate from the language the user *spoke* (``InputSpeechLanguage``) and
from the language STT *decoded* in (``STTDecodeLanguage``).

Three distinct concepts, never merged:

* **InputSpeechLanguage** — what the user just spoke (from
  ``BilingualSpeechTranscriber`` — not this module).
* **ResponseLanguage** — what NeXa answers in *this* turn.
* **ResponseLanguagePreference** (``ResponsePreference.sticky``) —
  transient per-session state: ``None`` | ``pl`` | ``en``. NOT a
  conversation turn, NOT long-term memory.

Resolution:

1. **Normal turn** (no request this turn):
   * a sticky preference exists → ResponseLanguage = the sticky language,
     regardless of what was spoken;
   * otherwise → mirror ``InputSpeechLanguage``.
2. **One-turn override** ("Answer in English.", "Odpowiedz po polsku.") →
   ResponseLanguage = the requested language for *this* turn only.
   **``ResponsePreference`` is NOT mutated.** The next normal turn resumes
   rule 1.
3. **Sticky command** ("From now on speak English.", "Od teraz mów po
   angielsku.") or a **sticky switch** ("Wracamy do polskiego.", "Let's go
   back to Polish.", "Switch to English.") → **set**
   ``ResponsePreference.sticky`` and use that language for this and future
   turns, until changed.

The request detector is deliberately narrow and deterministic: it fires
only on an imperative *about how NeXa answers*, never on ordinary content
that merely mentions a language ("Tell me about Polish history.", "What is
the English word for …?", "Why is Polish difficult?", "Translate this
English sentence."). The LLM is never the language-routing authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "ResponsePreference",
    "ResponseLanguageDecision",
    "ResponseLanguageResolver",
    "LanguageRequest",
    "detect_language_request",
    "detect_explicit_language_request",
]

# Content-question leads: "how do you say / what is X in <lang>" etc. — a
# translation / vocabulary question, never an instruction to NeXa.
_QUESTION_LEAD = re.compile(
    r"^\s*(how (do|did|does|to|would|can) (you|i|we)?\s*(say|write|spell|pronounce|call)"
    r"|what('?s| is| are| was| does| do)\b"
    r"|why (is|are|was|does|do)\b"
    r"|jak (sie |się )?(mówi|powie|powiedzieć|napisać|pisze|przetłumaczy)"
    r"|co (to )?(znaczy|oznacza)\b"
    r"|jak (jest|będzie|brzmi)\b)",
    re.IGNORECASE,
)

# "translate ..." is a content request about text, not a reply-language
# directive.
_TRANSLATE_LEAD = re.compile(r"^\s*(translate|przetłumacz|przetlumacz)\b", re.IGNORECASE)

# A "sticky" marker anywhere in the directive makes it a session preference.
_STICKY_MARKER = re.compile(
    r"\b(from now on|henceforth|permanently|always|going forward)\b"
    r"|\bod teraz\b|\bod tej pory\b|\bna stałe\b|\bna stale\b|\bzawsze\b",
    re.IGNORECASE,
)

# "switch / go back / return to <lang>" — inherently a mode change → sticky.
_EN_SWITCH = (
    r"\b(switch|change|go)\s+(back\s+)?to english\b",
    r"\b(let'?s\s+)?(go|come)\s+back to english\b",
    r"\bback to english\b",
    r"\b(wracamy|wróćmy|wrocmy|wracajmy|wróć|wroc)\b[^.?!]{0,15}\bdo angielskiego\b",
    r"\b(przełącz|przelacz|przełączmy|przelaczmy)\b[^.?!]{0,15}\bna angielski\b",
)
_PL_SWITCH = (
    r"\b(switch|change|go)\s+(back\s+)?to polish\b",
    r"\b(let'?s\s+)?(go|come)\s+back to polish\b",
    r"\bback to polish\b",
    r"\b(wracamy|wróćmy|wrocmy|wracajmy|wróć|wroc)\b[^.?!]{0,15}\bdo polskiego\b",
    r"\b(przełącz|przelacz|przełączmy|przelaczmy)\b[^.?!]{0,15}\bna polski\b",
)

# Plain imperative "reply in <lang>" (one-turn unless a sticky marker is
# also present, handled in the resolver).
_EN_DIRECTIVE = (
    r"\b(answer|reply|respond|speak|talk|write|say(\s+it)?)\b[^.?!]{0,30}\bin english\b",
    r"\bin english\b[^.?!]{0,15}\b(please|only)\b",
    # "speak English" / "answer English" — verb directly before the bare
    # language name (the question / translate leads above already exclude
    # "the English word", "why is English", "translate ... English").
    r"\b(speak|answer|reply|respond|write|talk)\s+english\b",
    r"\b(odpowiadaj|odpowiedz|mów|mow|mówże|mowze|pisz|napisz|powiedz|gadaj)\b"
    r"[^.?!]{0,30}\b(po angielsku|na angielski)\b",
    r"\b(po angielsku|in english)\b[^.?!]{0,10}\b(proszę|prosze|please)\b",
)
_PL_DIRECTIVE = (
    r"\b(answer|reply|respond|speak|talk|write|say(\s+it)?)\b[^.?!]{0,30}\bin polish\b",
    r"\bin polish\b[^.?!]{0,15}\b(please|only)\b",
    r"\b(speak|answer|reply|respond|write|talk)\s+polish\b",
    r"\b(odpowiadaj|odpowiedz|mów|mow|mówże|mowze|pisz|napisz|powiedz|gadaj)\b"
    r"[^.?!]{0,30}\b(po polsku|na polski)\b",
    r"\b(po polsku|in polish)\b[^.?!]{0,10}\b(proszę|prosze|please)\b",
)

_EN_SWITCH_RE = [re.compile(p, re.IGNORECASE) for p in _EN_SWITCH]
_PL_SWITCH_RE = [re.compile(p, re.IGNORECASE) for p in _PL_SWITCH]
_EN_DIR_RE = [re.compile(p, re.IGNORECASE) for p in _EN_DIRECTIVE]
_PL_DIR_RE = [re.compile(p, re.IGNORECASE) for p in _PL_DIRECTIVE]


@dataclass(frozen=True, slots=True)
class LanguageRequest:
    """A detected instruction about NeXa's reply language."""

    language: str  # "pl" | "en"
    kind: str  # "one_turn" | "sticky"


def detect_language_request(text: str) -> LanguageRequest | None:
    """Classify an utterance as a one-turn override, a sticky command, or
    (``None``) not a reply-language directive at all."""
    if not text or not text.strip():
        return None
    if _QUESTION_LEAD.search(text) or _TRANSLATE_LEAD.search(text):
        return None

    en_switch = any(r.search(text) for r in _EN_SWITCH_RE)
    pl_switch = any(r.search(text) for r in _PL_SWITCH_RE)
    en_dir = any(r.search(text) for r in _EN_DIR_RE)
    pl_dir = any(r.search(text) for r in _PL_DIR_RE)

    en_any = en_switch or en_dir
    pl_any = pl_switch or pl_dir
    if en_any == pl_any:  # both or neither → not a clean single-language directive
        return None
    language = "en" if en_any else "pl"
    is_switch = en_switch if language == "en" else pl_switch
    sticky = is_switch or bool(_STICKY_MARKER.search(text))
    return LanguageRequest(language=language, kind="sticky" if sticky else "one_turn")


def detect_explicit_language_request(text: str) -> str | None:
    """Back-compat: just the requested language (``pl``/``en``) or ``None``,
    without the one-turn/sticky distinction."""
    req = detect_language_request(text)
    return req.language if req is not None else None


@dataclass(slots=True)
class ResponsePreference:
    """Transient per-session sticky response-language preference
    (``ResponseLanguagePreference``). ``None`` | ``pl`` | ``en``. Not a
    turn, not memory. Mutated in place by the resolver **only** for a
    sticky command / switch — never for a one-turn override."""

    sticky: str | None = None


@dataclass(frozen=True, slots=True)
class ResponseLanguageDecision:
    response_language: str  # "pl" | "en" — this turn
    reason: str
    explicit_request: str | None = None  # the requested language, if any
    request_kind: str | None = None  # None | "one_turn" | "sticky"
    preference_changed: bool = False  # True only when sticky was set/changed
    sticky_after: str | None = None  # ResponsePreference.sticky after resolve()


@dataclass(slots=True)
class ResponseLanguageResolver:
    """One deterministic authority. ``resolve`` mutates ``preference`` in
    place **only** for a sticky command / switch."""

    preference: ResponsePreference = field(default_factory=ResponsePreference)

    def resolve(
        self, transcript: str, *, input_language: str
    ) -> ResponseLanguageDecision:
        req = detect_language_request(transcript)

        if req is not None and req.kind == "sticky":
            changed = self.preference.sticky != req.language
            self.preference.sticky = req.language
            return ResponseLanguageDecision(
                response_language=req.language,
                reason=f"sticky command → ResponseLanguagePreference = {req.language}",
                explicit_request=req.language,
                request_kind="sticky",
                preference_changed=changed,
                sticky_after=req.language,
            )

        if req is not None and req.kind == "one_turn":
            # THIS turn only — ResponsePreference is left untouched.
            return ResponseLanguageDecision(
                response_language=req.language,
                reason=f"one-turn override → reply in {req.language} this turn only "
                f"(sticky preference unchanged: {self.preference.sticky})",
                explicit_request=req.language,
                request_kind="one_turn",
                preference_changed=False,
                sticky_after=self.preference.sticky,
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
