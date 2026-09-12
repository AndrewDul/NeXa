"""``apps/nexa_cloud_voice_app.py`` — CLI/production-entrypoint contract
tests (PRE-ATTEMPT #3 LAUNCH CONTRACT AUDIT, following R0045/R0046).

R0045 and R0046 both documented an "EXACT NEXT LIVE COMMAND" of
``.venv/bin/python apps/nexa_cloud_voice_app.py --bargein`` — a flag this
app's ``argparse`` has **never** defined, at any point in its history
(confirmed via ``git log --all -p -- apps/nexa_cloud_voice_app.py``: no
``bargein`` string ever appears in this file's own diffs). ``--bargein``
belongs exclusively to ``apps/nexa_bilingual_voice_probe.py`` — the LOCAL
voice probe, an entirely different app built on an entirely different,
genuinely opt-in barge-in mechanism (``HalfDuplexGate``/
``bargein_enabled``/``LocalAudioConfig``). The cloud app's own barge-in
architecture (``BargeInController``, R0045's atomic provider replacement,
R0046's canonical turn lifecycle) is constructed **unconditionally** by
``build_gemini_voice_runtime`` — there has never been a flag to gate it,
so no flag was ever needed, and the documented ``--bargein`` command was
a copy-paste error from the local-voice convention that escaped every
prior checkpoint's own validation because nothing, until now, ever
imported or exercised this app's own ``parse_args()``/``main()``.

These tests close that specific gap: they run the REAL entrypoint (not a
reimplementation of its argument parsing or wiring) and prove (1) the
actually-documented bare command parses and reaches construction, (2)
``--bargein`` is rejected (so if it is ever legitimately added, this test
starts failing and forces the docs to be checked), and (3) the object
graph the entrypoint's own ``--dry`` path constructs includes
``BargeInController`` with no flag required at all. No Gemini connection,
no hardware, no real credential is ever touched.
"""

from __future__ import annotations

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

import nexa_cloud_voice_app as cloud_app  # noqa: E402

from nexa.voice.bargein import BargeInController  # noqa: E402


class TestParseArgsMatchesDocumentedCommands(unittest.TestCase):
    """Deterministic, no-I/O proof that the app's ``argparse`` contract
    matches what the reports document — the exact drift that reached the
    operator undetected."""

    def test_bare_invocation_parses_and_defaults_to_a_live_run(self) -> None:
        """The documented EXACT NEXT LIVE COMMAND
        (``.venv/bin/python apps/nexa_cloud_voice_app.py``, no flags)."""
        with mock.patch.object(sys, "argv", ["nexa_cloud_voice_app.py"]):
            args = cloud_app.parse_args()
        self.assertFalse(args.dry)

    def test_dry_flag_still_parses(self) -> None:
        """The other documented command
        (``apps/nexa_cloud_voice_app.py --dry``) — unaffected regression
        check, not the drift itself."""
        with mock.patch.object(sys, "argv", ["nexa_cloud_voice_app.py", "--dry"]):
            args = cloud_app.parse_args()
        self.assertTrue(args.dry)

    def test_bargein_flag_does_not_exist_on_this_app(self) -> None:
        """R0045/R0046 wrongly documented ``--bargein`` on THIS app. Cloud
        barge-in is unconditional here — there is, and has only ever
        been, no flag to gate it. If this test ever starts failing
        because ``--bargein`` parses, that is a deliberate, documented
        architecture change, never a silent drift — the launch-command
        docs must be updated in the SAME commit that adds such a flag."""
        with mock.patch.object(sys, "argv", ["nexa_cloud_voice_app.py", "--bargein"]):
            with self.assertRaises(SystemExit):
                cloud_app.parse_args()


class TestEntrypointConstructsBargeInArchitectureUnconditionally(
    unittest.IsolatedAsyncioTestCase
):
    """Runs the REAL ``main()`` (not a re-implementation of its wiring)
    under the documented sanity/live commands and proves the object graph
    it builds includes the full R0045/R0046 barge-in architecture with no
    flag required — the "production wiring proof" the audit demanded."""

    async def test_bare_dry_invocation_constructs_bargein_wiring(self) -> None:
        captured: list = []
        real_build = cloud_app.build_gemini_voice_runtime

        def _spy_build(*args, **kwargs):
            runtime = real_build(*args, **kwargs)
            captured.append(runtime)
            return runtime

        with (
            mock.patch.object(sys, "argv", ["nexa_cloud_voice_app.py", "--dry"]),
            mock.patch.object(cloud_app, "build_gemini_voice_runtime", _spy_build),
        ):
            await cloud_app.main()

        self.assertEqual(len(captured), 1)
        runtime = captured[0]
        self.assertIsInstance(runtime.bargein, BargeInController)
        self.assertIs(runtime.provider_handle.current, runtime.provider)

    async def test_bare_live_invocation_reaches_construction_with_no_extra_flags(
        self,
    ) -> None:
        """The documented EXACT NEXT LIVE COMMAND — no flags at all —
        parses and proceeds all the way to the credential load (the FIRST
        thing the live branch does, before any Gemini/hardware call).
        Stopped there deliberately (no real credential in this sandbox,
        and none should ever be needed to prove the launch CONTRACT) —
        this is the argparse + call-path proof, never a live connection."""
        from nexa.realtime.gemini.credentials import GeminiCredentialError

        def _no_credential():
            raise GeminiCredentialError("no credential in test sandbox")

        with (
            mock.patch.object(sys, "argv", ["nexa_cloud_voice_app.py"]),
            mock.patch.object(cloud_app, "load_gemini_credential", _no_credential),
            self.assertRaises(SystemExit),
        ):
            await cloud_app.main()


if __name__ == "__main__":
    unittest.main()
