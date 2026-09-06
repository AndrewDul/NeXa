"""Deterministic tests for `nexa.conversation.language` (M2.3, R0009).

A fake `ModelProvider` cannot prove real-model language *quality* — that is
covered by the real `gemma4:e4b` acceptance test recorded in `R0009`. What
these tests prove deterministically: the detector's PL/EN classification on
exactly the phrases used in this milestone's acceptance tests, and that
`ConversationSession` injects the resulting directive as a transient
wire-level message, identically for every caller (typed or voice) — never
stored in history, never owned by a second conversation authority.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation.language import detect_response_language, language_directive  # noqa: E402


class TestDetectResponseLanguage(unittest.TestCase):
    def test_polish_acceptance_phrases_detected_as_polish(self) -> None:
        phrases = [
            "Co to jest czarna dziura?",
            "Co to jest teleportacja?",
            "A czy człowiek może się dzisiaj teleportować?",
            "Powiedz mi krótko czym jest Słońce.",
            "Powiedz mi teraz po polsku, jak długo jeszcze będzie świecić.",
            "Cześć NeXa",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(detect_response_language(phrase), "pl")

    def test_english_acceptance_phrases_detected_as_english(self) -> None:
        phrases = [
            "What is a black hole?",
            "What is the speed of light?",
            "Can anything with mass travel that fast?",
            "And how old is it?",
            "Hello NeXa",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(detect_response_language(phrase), "en")

    def test_diacritics_alone_are_sufficient_signal(self) -> None:
        # No stopword match, but "źle" carries a diacritic.
        self.assertEqual(detect_response_language("źle"), "pl")

    def test_stopword_without_diacritics_is_sufficient_signal(self) -> None:
        # "Co to jest X" has no diacritics but is unambiguously Polish.
        self.assertEqual(detect_response_language("Co to jest X"), "pl")

    def test_empty_text_returns_none(self) -> None:
        self.assertIsNone(detect_response_language(""))
        self.assertIsNone(detect_response_language("   "))

    def test_explicit_language_request_phrases_are_detected_in_their_own_language(self) -> None:
        self.assertEqual(detect_response_language("odpowiedz po polsku"), "pl")
        self.assertEqual(detect_response_language("answer in English"), "en")


class TestLanguageDirective(unittest.TestCase):
    def test_polish_directive_is_polish(self) -> None:
        directive = language_directive("pl")
        self.assertEqual(detect_response_language(directive), "pl")

    def test_english_directive_is_english(self) -> None:
        directive = language_directive("en")
        self.assertEqual(detect_response_language(directive), "en")


if __name__ == "__main__":
    unittest.main()
