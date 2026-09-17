"""``apps/nexa_cloud_voice_simple.py`` — R0080 production-entrypoint
activation tests. Runs the REAL ``main()`` (not a reimplementation of its
wiring), exactly as ``tests/test_cloud_voice_app_entrypoint.py`` already
established for the sibling (paused) app. No Gemini connection, no
hardware, no real credential is ever touched -- ``--dry`` stops before any
of that, matching the existing, unmodified ``--dry`` contract.
"""

from __future__ import annotations

import os
import sys
import tempfile
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

import nexa_cloud_voice_simple as cloud_simple  # noqa: E402

from nexa.realtime.gemini.core_recall_tool import (  # noqa: E402
    RECALL_TOOL_NAME,
    RecallExecutor,
)
from nexa.realtime.gemini.simple_conversation import GEMINI_MODEL  # noqa: E402


class TestParseArgs(unittest.TestCase):
    def test_core_recall_defaults_to_enabled(self) -> None:
        with mock.patch.object(sys, "argv", ["nexa_cloud_voice_simple.py"]):
            args = cloud_simple.parse_args()
        self.assertTrue(args.core_recall)

    def test_no_core_recall_flag_disables_it(self) -> None:
        with mock.patch.object(
            sys, "argv", ["nexa_cloud_voice_simple.py", "--no-core-recall"]
        ):
            args = cloud_simple.parse_args()
        self.assertFalse(args.core_recall)


class _IsolatedDataDir(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data_dir = os.environ.get("NEXA_DATA_DIR")
        os.environ["NEXA_DATA_DIR"] = self._tmp.name

    async def asyncTearDown(self) -> None:
        if self._old_data_dir is None:
            os.environ.pop("NEXA_DATA_DIR", None)
        else:
            os.environ["NEXA_DATA_DIR"] = self._old_data_dir
        self._tmp.cleanup()


class TestDryRunActivatesCoreRecallByDefault(_IsolatedDataDir):
    """R0080 §3/§7: the real entrypoint, by default, creates ONE
    RecallExecutor, starts it BEFORE building the adapter, passes it into
    ``build_cloud_realtime_conversation_adapter``, and the tool ends up
    registered on the real (offline) ``GeminiLiveLLMService``."""

    async def test_bare_dry_invocation_registers_recall_tool(self) -> None:
        captured: list[RecallExecutor] = []
        real_ctor = cloud_simple.RecallExecutor

        def _spy_ctor():
            ex = real_ctor()
            captured.append(ex)
            return ex

        with (
            mock.patch.object(sys, "argv", ["nexa_cloud_voice_simple.py", "--dry"]),
            mock.patch.object(cloud_simple, "RecallExecutor", _spy_ctor),
        ):
            await cloud_simple.main()

        # Exactly one RecallExecutor for the whole run -- never one per
        # tool call. main()'s own dry-mode cleanup calls executor.close(),
        # which resets `started` to False -- so by the time main() returns
        # this executor has already gone through its full, real
        # start()->close() lifecycle (proven by the "CORE_RECALL executor
        # ready: True" diagnostic line main() prints before closing it --
        # see the other tests in this class for the direct assertion that
        # registration actually happened while it was live).
        self.assertEqual(len(captured), 1)

    async def test_executor_started_before_adapter_is_built(self) -> None:
        """R0080 §3's exact required lifecycle order: RecallExecutor
        created -> awaited start() -> passed into the builder."""
        events: list[str] = []
        real_start = RecallExecutor.start
        real_build = cloud_simple.build_cloud_realtime_conversation_adapter

        async def _spy_start(self) -> None:
            await real_start(self)
            events.append("started")

        def _spy_build(*args, **kwargs):
            events.append("built")
            return real_build(*args, **kwargs)

        with (
            mock.patch.object(sys, "argv", ["nexa_cloud_voice_simple.py", "--dry"]),
            mock.patch.object(RecallExecutor, "start", _spy_start),
            mock.patch.object(
                cloud_simple, "build_cloud_realtime_conversation_adapter", _spy_build
            ),
        ):
            await cloud_simple.main()

        self.assertEqual(events, ["started", "built"])

    async def test_recall_tool_actually_registered_on_real_llm(self) -> None:
        real_build = cloud_simple.build_cloud_realtime_conversation_adapter
        captured_adapters: list = []

        def _spy_build(*args, **kwargs):
            adapter = real_build(*args, **kwargs)
            captured_adapters.append(adapter)
            return adapter

        with (
            mock.patch.object(sys, "argv", ["nexa_cloud_voice_simple.py", "--dry"]),
            mock.patch.object(
                cloud_simple, "build_cloud_realtime_conversation_adapter", _spy_build
            ),
        ):
            await cloud_simple.main()

        self.assertEqual(len(captured_adapters), 1)
        llm = captured_adapters[0].llm
        self.assertIn(RECALL_TOOL_NAME, llm._functions)  # noqa: SLF001
        self.assertTrue(llm._functions[RECALL_TOOL_NAME].cancel_on_interruption)  # noqa: SLF001

    async def test_gemini_model_is_explicitly_pinned(self) -> None:
        real_build = cloud_simple.build_cloud_realtime_conversation_adapter
        captured_adapters: list = []

        def _spy_build(*args, **kwargs):
            adapter = real_build(*args, **kwargs)
            captured_adapters.append(adapter)
            return adapter

        with (
            mock.patch.object(sys, "argv", ["nexa_cloud_voice_simple.py", "--dry"]),
            mock.patch.object(
                cloud_simple, "build_cloud_realtime_conversation_adapter", _spy_build
            ),
        ):
            await cloud_simple.main()

        llm = captured_adapters[0].llm
        self.assertEqual(llm._settings.model, GEMINI_MODEL)  # noqa: SLF001


class TestRollbackFlagDisablesCoreRecall(_IsolatedDataDir):
    """R0080 §4/§7: --no-core-recall reproduces byte-for-byte pre-R0079
    construction -- no RecallExecutor created, no tool registered."""

    async def test_no_recall_executor_constructed(self) -> None:
        constructed: list = []
        real_ctor = cloud_simple.RecallExecutor

        def _spy_ctor():
            ex = real_ctor()
            constructed.append(ex)
            return ex

        with (
            mock.patch.object(
                sys, "argv", ["nexa_cloud_voice_simple.py", "--dry", "--no-core-recall"]
            ),
            mock.patch.object(cloud_simple, "RecallExecutor", _spy_ctor),
        ):
            await cloud_simple.main()

        self.assertEqual(constructed, [])

    async def test_no_tool_registered_when_disabled(self) -> None:
        real_build = cloud_simple.build_cloud_realtime_conversation_adapter
        captured_adapters: list = []

        def _spy_build(*args, **kwargs):
            adapter = real_build(*args, **kwargs)
            captured_adapters.append(adapter)
            return adapter

        with (
            mock.patch.object(
                sys, "argv", ["nexa_cloud_voice_simple.py", "--dry", "--no-core-recall"]
            ),
            mock.patch.object(
                cloud_simple, "build_cloud_realtime_conversation_adapter", _spy_build
            ),
        ):
            await cloud_simple.main()

        llm = captured_adapters[0].llm
        self.assertNotIn(RECALL_TOOL_NAME, llm._functions)  # noqa: SLF001
        # byte-for-byte pre-R0079: system_instruction carries no tool-use text
        self.assertNotIn("recall_context", llm._settings.system_instruction)  # noqa: SLF001


class TestExecutorClosedOnEarlyExit(_IsolatedDataDir):
    """R0080 §3: the executor is closed even when the live (non-dry) path
    exits early at credential load -- no leaked worker thread/connection."""

    async def test_executor_closed_when_credential_missing(self) -> None:
        from nexa.realtime.gemini.credentials import GeminiCredentialError

        closed: list[bool] = []
        real_close = RecallExecutor.close

        def _spy_close(self) -> None:
            closed.append(True)
            real_close(self)

        def _no_credential():
            raise GeminiCredentialError("no credential in test sandbox")

        with (
            mock.patch.object(sys, "argv", ["nexa_cloud_voice_simple.py"]),
            mock.patch.object(cloud_simple, "load_gemini_credential", _no_credential),
            mock.patch.object(RecallExecutor, "close", _spy_close),
            self.assertRaises(SystemExit),
        ):
            await cloud_simple.main()

        self.assertEqual(closed, [True])


def _non_comment_non_docstring_code(path: Path) -> str:
    """Strips ``#``-comment lines and the module docstring, leaving only
    executable code -- so a code-level check doesn't false-positive on an
    explanatory comment/docstring mentioning a string as prose (exactly
    the kind of legitimate reference R0080's own pinning comment makes
    when explaining what this ISN'T)."""
    import ast

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstring = ast.get_docstring(tree) or ""
    code = source.replace(docstring, "", 1) if docstring else source
    return "\n".join(
        line for line in code.splitlines() if not line.strip().startswith("#")
    )


class TestPausedModelStringUnrelated(unittest.TestCase):
    """R0080 §6: the paused M2.6B dual-pipeline's own
    ``gemini-3.1-flash-live-preview`` string must never be USED (as
    opposed to discussed in an explanatory comment/docstring, which R0080's
    own pinning comment legitimately does) anywhere reachable from the
    accepted simple cloud path -- a source-level check, not just
    documentation, so a future accidental import is caught immediately."""

    def test_simple_conversation_never_uses_the_paused_model_string(self) -> None:
        code = _non_comment_non_docstring_code(
            SRC / "nexa" / "realtime" / "gemini" / "simple_conversation.py"
        )
        self.assertNotIn("gemini-3.1-flash-live-preview", code)

    def test_core_recall_tool_never_uses_the_paused_model_string(self) -> None:
        code = _non_comment_non_docstring_code(
            SRC / "nexa" / "realtime" / "gemini" / "core_recall_tool.py"
        )
        self.assertNotIn("gemini-3.1-flash-live-preview", code)

    def test_app_never_uses_the_paused_model_string(self) -> None:
        code = _non_comment_non_docstring_code(APPS / "nexa_cloud_voice_simple.py")
        self.assertNotIn("gemini-3.1-flash-live-preview", code)

    def test_app_never_imports_the_paused_runtime_module(self) -> None:
        source = (APPS / "nexa_cloud_voice_simple.py").read_text(encoding="utf-8")
        self.assertNotIn("nexa.realtime.gemini.runtime", source)
        self.assertNotIn("nexa.realtime.gemini.service", source)


class TestInterruptionPrintDebounce(unittest.TestCase):
    """R0081 §14: Pipecat's own broadcast_interruption() fans out an
    upstream + downstream InterruptionFrame for ONE confirmed interruption
    -- this dedupes the resulting duplicate terminal print, without
    touching router/conversation state at all (that idempotency is proven
    separately: CloudTurnAccumulator.on_interruption() is a plain,
    already-idempotent flag set)."""

    def test_two_rapid_interruption_events_print_once(self) -> None:
        from nexa.realtime.provider import ProviderInterruptionEvent

        printer = cloud_simple._make_event_printer()
        with mock.patch("builtins.print") as mock_print:
            printer(ProviderInterruptionEvent(source="native_pipecat"))
            printer(ProviderInterruptionEvent(source="native_pipecat"))
        interrupted_calls = [
            c for c in mock_print.call_args_list if "interrupted" in str(c)
        ]
        self.assertEqual(len(interrupted_calls), 1)

    def test_two_well_separated_interruptions_both_print(self) -> None:
        from nexa.realtime.provider import ProviderInterruptionEvent

        printer = cloud_simple._make_event_printer()
        with mock.patch("builtins.print") as mock_print:
            printer(ProviderInterruptionEvent(source="native_pipecat"))
        import time as _time

        _time.sleep(cloud_simple._INTERRUPTION_PRINT_DEBOUNCE_S + 0.05)
        with mock.patch("builtins.print") as mock_print2:
            printer(ProviderInterruptionEvent(source="native_pipecat"))
        interrupted_1 = [c for c in mock_print.call_args_list if "interrupted" in str(c)]
        interrupted_2 = [c for c in mock_print2.call_args_list if "interrupted" in str(c)]
        self.assertEqual(len(interrupted_1), 1)
        self.assertEqual(len(interrupted_2), 1)


if __name__ == "__main__":
    unittest.main()
