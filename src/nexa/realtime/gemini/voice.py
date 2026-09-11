"""Provider-agnostic cloud voice preference -> Gemini voice name mapping
(ADR-0004 Decision G).

Voice is a NeXa user preference, not provider identity: NeXa stores an
abstract preference and maps it to whichever concrete voice name the
active provider understands — this is deliberately **not**
``GeminiIdentity.voice = "Sulafat"``. ``Sulafat`` is the current
OPERATOR-CONFIRMED Gemini voice (R0031, 2026-09-10: "I like this voice, we
keep it.") for the ``warm_female`` preference — this module owns exactly
that one mapping, nothing more.

This module deliberately imports nothing from ``google.genai`` — it stays
importable even when the optional ``cloud-gemini`` dependency extra
(ADR-0004 Decision L) is not installed.
"""

from __future__ import annotations

#: NeXa's default abstract voice preference.
DEFAULT_VOICE_PREFERENCE = "warm_female"

#: preference -> Gemini prebuilt voice name. ``warm_female`` -> ``Sulafat``
#: is OPERATOR-CONFIRMED (R0031). The others are recorded future
#: alternatives from R0031 (NOT tested/selected) — kept only as a
#: documented option, never auto-selected.
_PREFERENCE_TO_GEMINI_VOICE: dict[str, str] = {
    DEFAULT_VOICE_PREFERENCE: "Sulafat",
    "gentle": "Vindemiatrix",
    "soft": "Achernar",
    "breezy": "Aoede",
}


class UnknownVoicePreferenceError(ValueError):
    """Raised for a preference this mapping does not recognise — never
    silently falls back to a different voice."""


def gemini_voice_for_preference(preference: str = DEFAULT_VOICE_PREFERENCE) -> str:
    """Map a NeXa voice preference to the Gemini prebuilt voice name."""
    try:
        return _PREFERENCE_TO_GEMINI_VOICE[preference]
    except KeyError as exc:
        raise UnknownVoicePreferenceError(
            f"{preference!r} is not a recognised NeXa voice preference "
            f"({', '.join(_PREFERENCE_TO_GEMINI_VOICE)})"
        ) from exc
