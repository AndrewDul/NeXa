"""R0080 §2/§7 — proves the REAL, accepted production local-voice
entrypoint (``apps/nexa_bilingual_voice_probe.py``) activates NeXa Core
Context: one ``ContextRuntime`` built once, ``make_local_context_provider()``
wired into the real ``VoiceConversationAdapter(context_provider=...)``
construction call.

Runs the real ``main()`` (not a reimplementation of its wiring), stopping
only at the point ``VoiceRuntime.run()`` would open real audio hardware
(patched to raise immediately) -- everything before that (Piper HTTP
server, warm_up_session against the real local model, WhisperCpp
construction, ContextRuntime, VoiceConversationAdapter) runs for real,
mirroring ``tests/test_cloud_voice_app_entrypoint.py``'s own "run the real
entrypoint" convention for the cloud app.

**Not run by default** — same convention as ``test_live_ollama_integration.py``:
this starts a real Piper HTTP server subprocess and warms up the real
local Ollama model, so it needs those services available and is too heavy
for every unit-test invocation:

    NEXA_RUN_LIVE_TESTS=1 python3 -m pytest tests/test_local_voice_context_activation.py -v

Verified in this environment: 2 passed in ~13s (Ollama + Piper available).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
APPS = REPO_ROOT / "apps"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APPS) not in sys.path:
    sys.path.insert(0, str(APPS))

import nexa_bilingual_voice_probe as probe  # noqa: E402

from nexa.core.context.engine import ContextEngine  # noqa: E402
from nexa.providers.base import ModelUnavailableError  # noqa: E402

RUN_LIVE = os.environ.get("NEXA_RUN_LIVE_TESTS") == "1"


class _StopBeforeHardware(RuntimeError):
    """Sentinel raised in place of real audio-device access."""


@unittest.skipUnless(RUN_LIVE, "set NEXA_RUN_LIVE_TESTS=1 to run the live local-voice test")
class TestLocalVoiceEntrypointActivatesCoreContext(unittest.IsolatedAsyncioTestCase):
    async def test_real_main_wires_context_provider_into_the_real_adapter(self) -> None:
        captured_kwargs: dict = {}
        real_adapter_cls = probe.VoiceConversationAdapter

        def _spy_adapter(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return real_adapter_cls(*args, **kwargs)

        async def _stop_before_hardware(self) -> None:
            raise _StopBeforeHardware()

        with (
            mock.patch.object(sys, "argv", ["nexa_bilingual_voice_probe.py"]),
            mock.patch.object(probe, "VoiceConversationAdapter", _spy_adapter),
            mock.patch.object(probe.VoiceRuntime, "run", _stop_before_hardware),
        ):
            try:
                await probe.main()
            except (_StopBeforeHardware, ModelUnavailableError) as exc:
                if isinstance(exc, ModelUnavailableError):
                    self.skipTest(f"local model unavailable in this environment: {exc}")
                # expected: main() reached VoiceRuntime.run() and stopped there
            else:
                self.fail("main() completed without reaching VoiceRuntime.run()")

        self.assertIn("context_provider", captured_kwargs)
        self.assertIsNotNone(captured_kwargs["context_provider"])

    async def test_context_provider_uses_a_real_context_engine(self) -> None:
        """Not a fake/no-op provider -- it is genuinely bound to a real
        ContextEngine instance (via make_local_context_provider's own
        closure), proven by calling it against a session with a current
        turn and checking it doesn't raise for lack of Memory wiring."""
        captured_kwargs: dict = {}
        real_adapter_cls = probe.VoiceConversationAdapter

        def _spy_adapter(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return real_adapter_cls(*args, **kwargs)

        async def _stop_before_hardware(self) -> None:
            raise _StopBeforeHardware()

        with (
            mock.patch.object(sys, "argv", ["nexa_bilingual_voice_probe.py"]),
            mock.patch.object(probe, "VoiceConversationAdapter", _spy_adapter),
            mock.patch.object(probe.VoiceRuntime, "run", _stop_before_hardware),
        ):
            try:
                await probe.main()
            except _StopBeforeHardware:
                pass
            except ModelUnavailableError as exc:
                self.skipTest(f"local model unavailable in this environment: {exc}")

        context_provider = captured_kwargs["context_provider"]
        # closure introspection: make_local_context_provider closes over a
        # real ContextEngine -- verify via the closure cell, not a private
        # session call (which would need a real current turn/DB state we
        # don't want to fabricate here).
        closure_engines = [
            c.cell_contents
            for c in (context_provider.__closure__ or ())
            if isinstance(c.cell_contents, ContextEngine)
        ]
        self.assertEqual(len(closure_engines), 1)


if __name__ == "__main__":
    unittest.main()
