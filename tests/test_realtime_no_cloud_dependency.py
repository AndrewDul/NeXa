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

    def test_local_only_router_never_imports_the_gemini_service_module(self) -> None:
        """``GeminiLiveProvider`` (``nexa.realtime.gemini.service``, M2.6B.2)
        is constructed only via an injected factory — ``ConversationRouter``
        itself has no import of it, so a ``LOCAL_ONLY`` policy path
        (which never even calls the factory) can never reach it."""
        result = _run(
            "import sys\n"
            "from nexa.realtime.policy import ConversationPolicy, conversation_policy_from_env\n"
            "from nexa.realtime.router import ConversationRouter\n"
            "from nexa.conversation.session import ConversationSession\n"
            "from nexa.providers.base import ModelProvider, ProviderDescription\n"
            "\n"
            "class _NoopProvider(ModelProvider):\n"
            "    def describe(self):\n"
            "        return ProviderDescription(provider_name='noop', model='noop')\n"
            "    async def generate(self, messages, options, *, cancel_token=None):\n"
            "        return\n"
            "        yield\n"
            "\n"
            "assert conversation_policy_from_env() == ConversationPolicy.LOCAL_ONLY\n"
            "session = ConversationSession(provider=_NoopProvider(), system_prompt='p')\n"
            "router = ConversationRouter(session)  # no cloud_provider_factory given\n"
            "assert 'nexa.realtime.gemini' not in sys.modules\n"
            "assert 'nexa.realtime.gemini.service' not in sys.modules\n"
            "assert 'google.genai' not in sys.modules\n"
            "print('OK')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)

    def test_importing_gemini_service_module_alone_does_not_import_google_genai(self) -> None:
        """``nexa.realtime.gemini.service`` defers every Pipecat/Gemini
        import to inside ``GeminiLiveProvider.start()`` — importing the
        module itself must not require the optional ``cloud-gemini``
        extra."""
        result = _run(
            "import sys\n"
            "import nexa.realtime.gemini.service\n"
            "assert 'google.genai' not in sys.modules, sys.modules.keys()\n"
            "assert 'pipecat' not in sys.modules, sys.modules.keys()\n"
            "print('OK')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)

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
