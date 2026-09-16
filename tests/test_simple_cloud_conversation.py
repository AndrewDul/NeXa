"""``CloudRealtimeConversationAdapter`` (ADR-0004 Amendment 2 / R0071) --
the simplified, golden-M2.6A-derived production cloud voice path.

No real audio device, no network: dry construction mirrors the accepted
``dry=True`` convention (``build_gemini_voice_runtime``, the golden
probe's own ``--dry``). The frame-translation tap is exercised through a
REAL Pipecat ``Pipeline``/``PipelineWorker`` with a real ``ConversationRouter``
(the same pattern ``tests/test_realtime_gemini_runtime.py`` already
established for the paused dual-pipeline runtime), never a hand-rolled
simulation of the tap's own logic.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeModelProvider  # noqa: E402
from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.realtime.gemini.simple_conversation import (  # noqa: E402
    _make_event_tap_class,
    _pipecat_imports,
    build_cloud_realtime_conversation_adapter,
)
from nexa.realtime.policy import ConversationPolicy  # noqa: E402
from nexa.realtime.router import ConversationRouter  # noqa: E402
from nexa.realtime.snapshot import build_cloud_context_snapshot  # noqa: E402

try:
    import pipecat  # noqa: F401

    _PIPECAT_AVAILABLE = True
except Exception:  # pragma: no cover - only if pipecat-ai isn't importable
    _PIPECAT_AVAILABLE = False


def _fresh_router() -> ConversationRouter:
    session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
    return ConversationRouter(session, policy=ConversationPolicy.CLOUD_PREFERRED), session


class TestDryConstruction(unittest.TestCase):
    def test_dry_build_constructs_full_object_graph_without_hardware(self) -> None:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        snapshot = build_cloud_context_snapshot(session)
        adapter = build_cloud_realtime_conversation_adapter(
            session=session, api_key="DRY-NO-KEY", snapshot=snapshot, dry=True,
        )
        self.assertIsNone(adapter.hw_worker)
        self.assertIsNone(adapter.hw_runner)
        self.assertIsNotNone(adapter.llm)
        self.assertEqual(adapter.router.policy, ConversationPolicy.CLOUD_PREFERRED)

    def test_dry_build_uses_sulafat_by_default(self) -> None:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        snapshot = build_cloud_context_snapshot(session)
        adapter = build_cloud_realtime_conversation_adapter(
            session=session, api_key="DRY-NO-KEY", snapshot=snapshot, dry=True,
        )
        self.assertEqual(adapter.llm._settings.voice, "Sulafat")  # noqa: SLF001

    def test_dry_build_seeds_history_from_snapshot(self) -> None:
        async def _seed() -> ConversationSession:
            provider = FakeModelProvider([["cześć"]])
            session = ConversationSession(provider=provider, system_prompt="p")
            async for _ in session.send("hej"):
                pass
            return session

        session = asyncio.run(_seed())
        snapshot = build_cloud_context_snapshot(session)
        adapter = build_cloud_realtime_conversation_adapter(
            session=session, api_key="DRY-NO-KEY", snapshot=snapshot, dry=True,
        )
        self.assertIsNotNone(adapter.llm)  # constructed without error with seeded history


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestConversationEventTap(unittest.IsolatedAsyncioTestCase):
    """The one new FrameProcessor this module adds -- exercised through a
    REAL Pipeline/PipelineWorker + REAL ConversationRouter, never simulated
    separately."""

    async def _build(self):
        P = _pipecat_imports()
        router, session = _fresh_router()
        tap_cls = _make_event_tap_class(P)
        tap = tap_cls(router=router)
        pipeline = P["Pipeline"]([tap])
        worker = P["PipelineWorker"](
            pipeline,
            params=P["PipelineParams"](audio_in_sample_rate=16000, audio_out_sample_rate=24000),
            enable_rtvi=False,
            idle_timeout_secs=None,
        )
        runner = P["WorkerRunner"]()
        await runner.add_workers(worker)
        run_task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.2)
        return P, router, session, worker, runner, run_task

    async def _teardown(self, runner, run_task) -> None:
        await runner.end(reason="test done")
        await asyncio.wait_for(run_task, timeout=5.0)

    async def test_normal_turn_opens_transcribes_and_commits(self) -> None:
        """UserStartedSpeakingFrame opens a turn; a final TranscriptionFrame
        + LLMFullResponseEndFrame commit it to canonical history -- exactly
        the golden M2.6A frame vocabulary, end to end through the router."""
        P, router, session, worker, runner, run_task = await self._build()
        try:
            await worker.queue_frames(
                [
                    P["UserStartedSpeakingFrame"](),
                    P["TranscriptionFrame"](
                        text="are there black holes", user_id="", timestamp="", finalized=True
                    ),
                    P["TTSTextFrame"](text="yes, there are", aggregated_by=""),
                    P["LLMFullResponseEndFrame"](),
                ]
            )
            await asyncio.sleep(0.2)
            self.assertEqual(len(session.history), 2)
            self.assertEqual(session.history[0].content, "are there black holes")
            self.assertEqual(session.history[1].content, "yes, there are")
        finally:
            await self._teardown(runner, run_task)

    async def test_interruption_commits_conservative_empty_prefix_and_opens_next_turn(
        self,
    ) -> None:
        """M2.6B.3B's own conservative rule, reused: an interrupted turn's
        assistant text is always empty; a duplicate InterruptionFrame
        (Pipecat's own fan-out) is safe, not just tolerated."""
        P, router, session, worker, runner, run_task = await self._build()
        try:
            await worker.queue_frames(
                [
                    P["UserStartedSpeakingFrame"](),
                    P["TranscriptionFrame"](
                        text="tell me about gravity", user_id="", timestamp="", finalized=True
                    ),
                    P["TTSTextFrame"](text="gravity is a force that", aggregated_by=""),
                ]
            )
            await asyncio.sleep(0.1)
            # the (paused-architecture-equivalent) interruption: two
            # InterruptionFrame sightings, exactly as Pipecat's own
            # broadcast_interruption() fans out.
            await worker.queue_frames([P["InterruptionFrame"](), P["InterruptionFrame"]()])
            await asyncio.sleep(0.1)
            # conservative empty assistant text -> ConversationSession's own
            # documented rule: blank/None assistant_text appends NO
            # assistant turn at all (never an empty one) -- only the user
            # turn lands in canonical history.
            self.assertEqual(len(session.history), 1)
            self.assertEqual(session.history[0].content, "tell me about gravity")
            # the interrupting utterance gets its own fresh turn, not
            # blocked by the just-committed one.
            self.assertFalse(router.has_turn_awaiting_assistant())
        finally:
            await self._teardown(runner, run_task)

    async def test_second_turn_while_first_awaiting_assistant_does_not_abandon_it(
        self,
    ) -> None:
        P, router, session, worker, runner, run_task = await self._build()
        try:
            await worker.queue_frames(
                [
                    P["UserStartedSpeakingFrame"](),
                    P["TranscriptionFrame"](
                        text="first question", user_id="", timestamp="", finalized=True
                    ),
                ]
            )
            await asyncio.sleep(0.1)
            self.assertTrue(router.has_turn_awaiting_assistant())
            first_turn = router._turn.current  # noqa: SLF001
            # a stray VAD start while awaiting the assistant must not
            # abandon the still-open turn (mirrors R0046's own guard).
            await worker.queue_frames([P["UserStartedSpeakingFrame"]()])
            await asyncio.sleep(0.1)
            self.assertIs(router._turn.current, first_turn)  # noqa: SLF001
        finally:
            await self._teardown(runner, run_task)


if __name__ == "__main__":
    unittest.main()
