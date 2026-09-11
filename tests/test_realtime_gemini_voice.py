"""Provider-agnostic cloud voice preference mapping (ADR-0004 Decision G)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.realtime.gemini.voice import (  # noqa: E402
    DEFAULT_VOICE_PREFERENCE,
    UnknownVoicePreferenceError,
    gemini_voice_for_preference,
)


class TestVoicePreferenceMapping(unittest.TestCase):
    def test_default_preference_maps_to_sulafat(self) -> None:
        self.assertEqual(DEFAULT_VOICE_PREFERENCE, "warm_female")
        self.assertEqual(gemini_voice_for_preference(), "Sulafat")
        self.assertEqual(gemini_voice_for_preference("warm_female"), "Sulafat")

    def test_recorded_alternatives_are_mapped_but_not_default(self) -> None:
        self.assertEqual(gemini_voice_for_preference("gentle"), "Vindemiatrix")
        self.assertEqual(gemini_voice_for_preference("soft"), "Achernar")
        self.assertEqual(gemini_voice_for_preference("breezy"), "Aoede")

    def test_unknown_preference_raises_rather_than_silently_defaulting(self) -> None:
        with self.assertRaises(UnknownVoicePreferenceError):
            gemini_voice_for_preference("nonexistent")

    def test_mapping_is_configurable_not_hard_coded_identity(self) -> None:
        # Voice is a preference -> provider-voice mapping, not
        # `GeminiIdentity.voice = "Sulafat"` (ADR-0004 Decision G): the
        # function takes the preference as an argument rather than
        # returning a constant.
        import inspect

        sig = inspect.signature(gemini_voice_for_preference)
        self.assertIn("preference", sig.parameters)


if __name__ == "__main__":
    unittest.main()
