"""M2.2: `Language` is a strict, typed PL/EN enum with no auto-detect
member, and `WhisperCppConfig` is explicit/typed. R0006 measured
auto-detection misclassifying a short Polish utterance as Japanese — auto
must be structurally impossible to select, not just discouraged."""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.stt.config import (  # noqa: E402
    DEFAULT_THREADS,
    DEFAULT_TIMEOUT_S,
    Language,
    WhisperCppConfig,
)


class TestLanguageIsTypedPlEnOnly(unittest.TestCase):
    def test_language_has_exactly_pl_and_en_members(self) -> None:
        self.assertEqual({member.value for member in Language}, {"pl", "en"})

    def test_auto_is_not_a_valid_language_value(self) -> None:
        with self.assertRaises(ValueError):
            Language("auto")

    def test_language_has_no_auto_member(self) -> None:
        self.assertNotIn("AUTO", Language.__members__)


class TestWhisperCppConfigIsExplicitAndTyped(unittest.TestCase):
    def test_config_is_frozen(self) -> None:
        config = WhisperCppConfig()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.threads = 8  # type: ignore[misc]

    def test_defaults_resolve_to_concrete_paths(self) -> None:
        config = WhisperCppConfig()
        self.assertIsInstance(config.binary_path, Path)
        self.assertIsInstance(config.model_path, Path)
        self.assertEqual(config.threads, DEFAULT_THREADS)
        self.assertEqual(config.timeout_s, DEFAULT_TIMEOUT_S)

    def test_explicit_values_are_used_verbatim(self) -> None:
        binary = Path("/tmp/fake-whisper-cli")
        model = Path("/tmp/fake-model.bin")
        config = WhisperCppConfig(binary_path=binary, model_path=model, threads=2, timeout_s=5.0)
        self.assertEqual(config.binary_path, binary)
        self.assertEqual(config.model_path, model)
        self.assertEqual(config.threads, 2)
        self.assertEqual(config.timeout_s, 5.0)


if __name__ == "__main__":
    unittest.main()
