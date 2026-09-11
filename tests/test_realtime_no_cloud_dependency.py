"""Import-isolation gates for the M2.6B provider-agnostic foundation
(ADR-0004 Decision L / M2.6B acceptance gates 1 and 15).

Run in a genuinely fresh subprocess interpreter (not just checked against
this test process's already-imported ``sys.modules``) so the result is
real evidence, not an artifact of import order elsewhere in the suite.
``google-genai`` IS installed in this dev/research venv (R0030/R0031), so
this test only proves it is not *imported* — not that it is absent.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(script: str) -> subprocess.CompletedProcess:
    # -I (isolated mode): ignores PYTHONPATH/site-user dirs entirely, so
    # ``nexa`` is only found via its editable install in this venv's
    # site-packages — the strongest available guarantee this isn't an
    # artifact of import order or a stray sys.path entry.
    return subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


class TestNoCloudDependencyOnImport(unittest.TestCase):
    def test_importing_realtime_boundary_does_not_import_google_genai(self) -> None:
        result = _run(
            "import sys\n"
            "import nexa.realtime\n"
            "assert 'google.genai' not in sys.modules, sys.modules.keys()\n"
            "assert 'google' not in sys.modules, sys.modules.keys()\n"
            "print('OK')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)

    def test_importing_gemini_credentials_and_voice_does_not_import_google_genai(self) -> None:
        result = _run(
            "import sys\n"
            "from nexa.realtime.gemini import credentials, voice\n"
            "assert 'google.genai' not in sys.modules, sys.modules.keys()\n"
            "print(voice.gemini_voice_for_preference())\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Sulafat", result.stdout)

    def test_local_only_path_never_needs_the_gemini_service_module(self) -> None:
        """``GeminiLiveProvider`` (``nexa.realtime.gemini.service``) is
        M2.6B.2 — it does not exist yet, so a ``LOCAL_ONLY`` install cannot
        possibly import it. This test also pins that boundary: once M2.6B.2
        lands, a LOCAL_ONLY policy path must still never import it."""
        result = _run(
            "import importlib\n"
            "import nexa.realtime\n"
            "from nexa.realtime.policy import ConversationPolicy, conversation_policy_from_env\n"
            "assert conversation_policy_from_env() == ConversationPolicy.LOCAL_ONLY\n"
            "try:\n"
            "    importlib.import_module('nexa.realtime.gemini.service')\n"
            "except ModuleNotFoundError:\n"
            "    print('NOT_YET_IMPLEMENTED')\n"
            "else:\n"
            "    raise AssertionError('nexa.realtime.gemini.service should not exist in M2.6B.1')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NOT_YET_IMPLEMENTED", result.stdout)

    def test_bare_nexa_import_does_not_import_realtime(self) -> None:
        result = _run(
            "import sys\n"
            "import nexa\n"
            "assert 'nexa.realtime' not in sys.modules\n"
            "print('OK')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
