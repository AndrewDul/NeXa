"""Live integration test for the real M1.1 canonical path against Ollama.

**Not run by default.** It loads the ~10 GB `gemma4:e4b` baseline, which is
too heavy to run on every unit-test invocation. Invoke explicitly:

    NEXA_RUN_LIVE_TESTS=1 python3 -m unittest tests.test_live_ollama_integration -v

Requires: the local Ollama service running with `gemma4:e4b` pulled.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.bootstrap import build_default_session  # noqa: E402
from nexa.config import DEFAULT_LOCAL_MODEL  # noqa: E402
from nexa.conversation.turn import Role  # noqa: E402

RUN_LIVE = os.environ.get("NEXA_RUN_LIVE_TESTS") == "1"


@unittest.skipUnless(RUN_LIVE, "set NEXA_RUN_LIVE_TESTS=1 to run the live Ollama test")
class TestLiveOllamaIntegration(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        # Explicit unload — no model stays resident after this test on its own.
        subprocess.run(
            ["ollama", "stop", DEFAULT_LOCAL_MODEL], capture_output=True, timeout=30
        )

    async def test_polish_and_english_turns_through_the_real_path(self) -> None:
        session = build_default_session()
        self.assertEqual(session.provider.describe().model, DEFAULT_LOCAL_MODEL)

        pl_reply = "".join([chunk async for chunk in session.send("Cześć, jak się masz?")])
        self.assertTrue(pl_reply.strip(), "expected a non-empty Polish reply")

        en_reply = "".join([chunk async for chunk in session.send("Briefly, what is your name?")])
        self.assertTrue(en_reply.strip(), "expected a non-empty English reply")

        # History accumulated across both turns, in order, within one session.
        self.assertEqual(len(session.history), 4)
        self.assertEqual(session.history[0].role, Role.USER)
        self.assertEqual(session.history[1].role, Role.ASSISTANT)
        self.assertEqual(session.history[1].content, pl_reply)
        self.assertEqual(session.history[2].role, Role.USER)
        self.assertEqual(session.history[3].role, Role.ASSISTANT)
        self.assertEqual(session.history[3].content, en_reply)

        print(f"\n[live] PL reply: {pl_reply!r}")
        print(f"[live] EN reply: {en_reply!r}")


if __name__ == "__main__":
    unittest.main()
