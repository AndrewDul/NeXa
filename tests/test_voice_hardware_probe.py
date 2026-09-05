"""Opt-in hardware integration test for the M2.1 voice runtime.

**Not run by default** — it opens the real reSpeaker XVF3800 microphone and
speaker. Invoke explicitly:

    NEXA_RUN_VOICE_HARDWARE_TEST=1 python3 -m unittest tests.test_voice_hardware_probe -v

This proves the real pipeline (Pipecat local audio transport + Silero VAD)
starts, reaches LISTENING, and shuts down cleanly on real hardware. It does
**not** require the operator to speak — that is the separate, human-in-the-
loop multi-utterance acceptance test (`apps/nexa_voice_probe.py`, run
manually and recorded in the M2.1 report), which cannot be automated.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.voice import LocalAudioConfig, VoiceRuntime  # noqa: E402
from nexa.voice.state import VoiceState  # noqa: E402

RUN_HARDWARE_TEST = os.environ.get("NEXA_RUN_VOICE_HARDWARE_TEST") == "1"


@unittest.skipUnless(
    RUN_HARDWARE_TEST, "set NEXA_RUN_VOICE_HARDWARE_TEST=1 to run against real audio hardware"
)
class TestVoiceHardwareProbe(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_starts_listens_and_shuts_down_cleanly(self) -> None:
        runtime = VoiceRuntime(LocalAudioConfig())
        task = asyncio.create_task(runtime.run())

        for _ in range(50):
            if runtime.state_machine.state == VoiceState.LISTENING:
                break
            await asyncio.sleep(0.1)
        self.assertEqual(
            runtime.state_machine.state,
            VoiceState.LISTENING,
            "runtime did not reach LISTENING within 5s of real device open",
        )

        await asyncio.sleep(2)  # let it run briefly against real hardware

        assert runtime._runner is not None
        await runtime._runner.cancel(reason="hardware probe test complete")
        await asyncio.wait_for(task, timeout=5)

        self.assertEqual(runtime.state_machine.state, VoiceState.IDLE)


if __name__ == "__main__":
    unittest.main()
