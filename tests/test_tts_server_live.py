"""Opt-in live Piper HTTP server integration test (M2.4).

**Not run by default** — requires the real external Piper venv + voices to
be installed (`python3 scripts/setup_piper_http.py`). No microphone
required. Invoke explicitly:

    NEXA_RUN_LIVE_TTS_TEST=1 ./.venv/bin/python3 -m unittest tests.test_tts_server_live -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.tts import EN_VOICE, PL_VOICE, PiperHttpServer  # noqa: E402

RUN_LIVE_TEST = os.environ.get("NEXA_RUN_LIVE_TTS_TEST") == "1"


@unittest.skipUnless(
    RUN_LIVE_TEST, "set NEXA_RUN_LIVE_TTS_TEST=1 to run against the real external Piper server"
)
class TestPiperHttpServerLive(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = PiperHttpServer()  # raises explicitly if not installed
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_synthesize_english_produces_real_wav_audio(self) -> None:
        audio = await self.server.synthesize("Hello NeXa.", voice=EN_VOICE)
        self.assertGreater(len(audio), 44)  # more than just a WAV header
        self.assertTrue(audio.startswith(b"RIFF"))

    async def test_synthesize_polish_produces_real_wav_audio(self) -> None:
        audio = await self.server.synthesize("Cześć NeXa.", voice=PL_VOICE)
        self.assertGreater(len(audio), 44)
        self.assertTrue(audio.startswith(b"RIFF"))

    async def test_prewarm_loads_both_configured_voices(self) -> None:
        elapsed = await self.server.prewarm()
        self.assertIn(EN_VOICE, elapsed)
        self.assertIn(PL_VOICE, elapsed)


if __name__ == "__main__":
    unittest.main()
