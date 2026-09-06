"""Opt-in live whisper.cpp integration test (M2.2).

**Not run by default** — requires the real pinned whisper.cpp binary + model
to be installed (`python3 scripts/setup_whisper_cpp.py`). No microphone
required; uses the existing R0006 PL/EN fixture recordings. Invoke
explicitly:

    NEXA_RUN_LIVE_STT_TEST=1 ./.venv/bin/python3 -m unittest tests.test_stt_transcriber_live -v
"""

from __future__ import annotations

import os
import sys
import unittest
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.stt.config import Language  # noqa: E402
from nexa.stt.transcriber import WhisperCppTranscriber  # noqa: E402

RUN_LIVE_TEST = os.environ.get("NEXA_RUN_LIVE_STT_TEST") == "1"

FIXTURES_DIR = REPO_ROOT / "docs" / "research" / "m2_voice_spikes" / "asr_test_samples"


def _read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


@unittest.skipUnless(
    RUN_LIVE_TEST, "set NEXA_RUN_LIVE_STT_TEST=1 to run against the real whisper.cpp binary + model"
)
class TestWhisperCppTranscriberLive(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.transcriber = WhisperCppTranscriber()  # raises explicitly if not installed

    async def test_english_fixture_transcribes_recognizably(self) -> None:
        audio = _read_pcm(FIXTURES_DIR / "en_what_is_the_speed_of_light.wav")
        result = await self.transcriber.transcribe(audio, language=Language.EN)
        self.assertIn("speed", result.text.lower())
        self.assertIn("light", result.text.lower())
        self.assertGreater(result.wall_latency_s, 0.0)

    async def test_polish_fixture_transcribes_recognizably(self) -> None:
        audio = _read_pcm(FIXTURES_DIR / "pl_co_to_jest_teleportacja.wav")
        result = await self.transcriber.transcribe(audio, language=Language.PL)
        self.assertIn("teleportacj", result.text.lower())


if __name__ == "__main__":
    unittest.main()
