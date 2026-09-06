"""M2.4 `nexa.tts.config` tests — no Piper server/venv/voice model needed.

Covers the exact real-hardware-verified trap from R0010: the real Piper
HTTP server only accepts `POST /synthesize` — `POST /` (bare `base_url`)
returns 405. This is a deterministic regression test for that
configuration, per the explicit task requirement — Pipecat's
`PiperHttpTTSService` must always be constructed with `synthesize_url`,
never `base_url` alone.
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.tts.config import (  # noqa: E402
    EN_VOICE,
    PIPER_TTS_VERSION,
    PL_VOICE,
    PiperHttpConfig,
)


class TestSynthesizeEndpoint(unittest.TestCase):
    def test_synthesize_url_targets_the_real_verified_endpoint(self) -> None:
        config = PiperHttpConfig(host="127.0.0.1", port=5001)
        self.assertEqual(config.synthesize_url, "http://127.0.0.1:5001/synthesize")

    def test_synthesize_url_is_not_the_bare_base_url(self) -> None:
        """R0010: `POST /` (bare base_url) returns 405 on the real Piper
        HTTP server — this must never be what NeXa configures
        PiperHttpTTSService with."""
        config = PiperHttpConfig()
        self.assertNotEqual(config.synthesize_url, config.base_url)
        self.assertNotEqual(config.synthesize_url, config.base_url + "/")
        self.assertTrue(config.synthesize_url.endswith("/synthesize"))

    def test_base_url_has_no_trailing_slash(self) -> None:
        config = PiperHttpConfig(host="127.0.0.1", port=5001)
        self.assertEqual(config.base_url, "http://127.0.0.1:5001")

    def test_info_url_is_distinct_from_synthesize_url(self) -> None:
        config = PiperHttpConfig()
        self.assertNotEqual(config.info_url, config.synthesize_url)
        self.assertTrue(config.info_url.endswith("/info"))


class TestPinnedVersionAndVoices(unittest.TestCase):
    def test_piper_version_is_pinned(self) -> None:
        self.assertEqual(PIPER_TTS_VERSION, "1.8.0")

    def test_product_voices_match_the_prior_local_assistant(self) -> None:
        """M2.4 finalization: the product default voices are the exact
        same ones the prior local assistant used (verified against its own
        read-only reference config) — not R0010 spike's placeholder "low"
        quality voices, which remain available as EN_VOICE_SPIKE/PL_VOICE_SPIKE."""
        self.assertEqual(EN_VOICE, "en_GB-jenny_dioco-medium")
        self.assertEqual(PL_VOICE, "pl_PL-gosia-medium")


class TestPiperHttpConfigIsExplicitAndTyped(unittest.TestCase):
    def test_config_is_frozen(self) -> None:
        config = PiperHttpConfig()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.port = 9999  # type: ignore[misc]

    def test_defaults_resolve_to_concrete_values(self) -> None:
        config = PiperHttpConfig()
        self.assertEqual(config.en_voice, EN_VOICE)
        self.assertEqual(config.pl_voice, PL_VOICE)
        self.assertIsInstance(config.venv_python, Path)
        self.assertIsInstance(config.voices_dir, Path)

    def test_explicit_values_are_used_verbatim(self) -> None:
        venv = Path("/tmp/fake-venv/bin/python3")
        voices = Path("/tmp/fake-voices")
        config = PiperHttpConfig(
            host="0.0.0.0", port=9000, en_voice="x", pl_voice="y",
            venv_python=venv, voices_dir=voices,
        )
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 9000)
        self.assertEqual(config.en_voice, "x")
        self.assertEqual(config.pl_voice, "y")
        self.assertEqual(config.venv_python, venv)
        self.assertEqual(config.voices_dir, voices)


if __name__ == "__main__":
    unittest.main()
