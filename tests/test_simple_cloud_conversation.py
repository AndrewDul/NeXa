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
from nexa.realtime.gemini.core_recall_tool import (  # noqa: E402
    RECALL_TOOL_NAME,
    RECALL_TOOL_USE_INSTRUCTION,
    RecallExecutor,
)
from nexa.realtime.gemini.simple_conversation import (  # noqa: E402
    _make_event_tap_class,
    _make_mic_level_tap_class,
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

    def test_no_recall_executor_is_byte_for_byte_unchanged(self) -> None:
        """R0079: default recall_executor=None must reproduce exactly the
        pre-R0079 construction -- no tools, no appended instruction text."""
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        snapshot = build_cloud_context_snapshot(session)
        adapter = build_cloud_realtime_conversation_adapter(
            session=session, api_key="DRY-NO-KEY", snapshot=snapshot, dry=True,
        )
        self.assertNotIn(RECALL_TOOL_NAME, adapter.llm._functions)  # noqa: SLF001
        self.assertEqual(
            adapter.llm._settings.system_instruction, snapshot.system_instruction  # noqa: SLF001
        )

    def test_recall_executor_registers_tool_and_appends_instruction(self) -> None:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        snapshot = build_cloud_context_snapshot(session)
        executor = RecallExecutor()
        try:
            adapter = build_cloud_realtime_conversation_adapter(
                session=session,
                api_key="DRY-NO-KEY",
                snapshot=snapshot,
                dry=True,
                recall_executor=executor,
            )
            item = adapter.llm._functions[RECALL_TOOL_NAME]  # noqa: SLF001
            self.assertTrue(item.cancel_on_interruption)
            self.assertIn(
                RECALL_TOOL_USE_INSTRUCTION,
                adapter.llm._settings.system_instruction,  # noqa: SLF001
            )
            self.assertIn(
                snapshot.system_instruction,
                adapter.llm._settings.system_instruction,  # noqa: SLF001
            )
        finally:
            executor.close()


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


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestDiagnosticTimeline(unittest.IsolatedAsyncioTestCase):
    """R0081 §4/§5 -- the narrow, purely-additive diagnostic-timeline hook.
    Same real Pipeline/PipelineWorker/ConversationRouter infrastructure as
    ``TestConversationEventTap`` above, with ``on_diagnostic`` wired this
    time."""

    async def _build(self, *, on_diagnostic):
        P = _pipecat_imports()
        router, session = _fresh_router()
        tap_cls = _make_event_tap_class(P)
        tap = tap_cls(router=router, on_diagnostic=on_diagnostic)
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

    async def test_default_none_is_byte_for_byte_unchanged(self) -> None:
        """No on_diagnostic at all -- _diag() must be a safe no-op, never
        raise, never require the callback."""
        P, router, session, worker, runner, run_task = await self._build(on_diagnostic=None)
        try:
            await worker.queue_frames(
                [
                    P["UserStartedSpeakingFrame"](),
                    P["TranscriptionFrame"](
                        text="hello", user_id="", timestamp="", finalized=True
                    ),
                    P["LLMFullResponseEndFrame"](),
                ]
            )
            await asyncio.sleep(0.2)
            self.assertEqual(len(session.history), 1)  # unaffected, no crash
        finally:
            await self._teardown(runner, run_task)

    async def test_full_normal_turn_timeline_labels(self) -> None:
        events: list[str] = []
        P, router, session, worker, runner, run_task = await self._build(
            on_diagnostic=events.append
        )
        try:
            await worker.queue_frames(
                [
                    P["UserStartedSpeakingFrame"](),
                    P["UserStoppedSpeakingFrame"](),
                    P["TranscriptionFrame"](
                        text="are there black holes", user_id="", timestamp="", finalized=True
                    ),
                    P["TTSStartedFrame"](),
                    P["TTSTextFrame"](text="yes, there are", aggregated_by=""),
                    P["TTSStoppedFrame"](),
                    P["LLMFullResponseEndFrame"](),
                ]
            )
            await asyncio.sleep(0.2)
        finally:
            await self._teardown(runner, run_task)

        labels = [e.split(":", 1)[0] for e in events]
        self.assertEqual(
            labels,
            [
                "LOCAL_VAD_START",
                "USER_TURN_START",
                "LOCAL_VAD_STOP",
                "USER_TRANSCRIPT",
                "BOT_AUDIO_STARTED",
                "BOT_AUDIO_STOPPED",
                "USER_TURN_END",
            ],
        )
        self.assertEqual(events[3], "USER_TRANSCRIPT:are there black holes")

    async def test_interruption_timeline_includes_playback_stopped_and_new_turn(self) -> None:
        events: list[str] = []
        P, router, session, worker, runner, run_task = await self._build(
            on_diagnostic=events.append
        )
        try:
            await worker.queue_frames(
                [
                    P["UserStartedSpeakingFrame"](),
                    P["TranscriptionFrame"](
                        text="tell me about gravity", user_id="", timestamp="", finalized=True
                    ),
                    P["InterruptionFrame"](),
                ]
            )
            await asyncio.sleep(0.2)
        finally:
            await self._teardown(runner, run_task)

        # R0081 (corrected): direction-aware label, not a bare
        # "INTERRUPTION_FRAME" string -- see TestInterruptionDirectionTelemetry
        # below for the dedicated, detailed coverage of its exact content.
        self.assertTrue(any(e.startswith("INTERRUPTION_FRAME_") for e in events))
        self.assertIn("PLAYBACK_STOPPED", events)
        # interruption closes the current turn AND opens a fresh one
        self.assertEqual(events.count("USER_TURN_END"), 1)
        self.assertEqual(events.count("USER_TURN_START"), 2)  # initial open + post-interrupt

    async def test_non_final_transcript_never_diagnosed_as_user_transcript(self) -> None:
        """Only a FINAL transcript is treated as 'the real user transcript'
        -- a partial must never be printed/correlated as if it were one."""
        events: list[str] = []
        P, router, session, worker, runner, run_task = await self._build(
            on_diagnostic=events.append
        )
        try:
            await worker.queue_frames(
                [
                    P["TranscriptionFrame"](
                        text="partial words", user_id="", timestamp="", finalized=False
                    ),
                ]
            )
            await asyncio.sleep(0.2)
        finally:
            await self._teardown(runner, run_task)

        self.assertFalse(any(e.startswith("USER_TRANSCRIPT") for e in events))

    async def test_diagnostic_hook_exception_never_breaks_the_pipeline(self) -> None:
        def _exploding(label: str) -> None:
            raise RuntimeError("diagnostic sink failed")

        P, router, session, worker, runner, run_task = await self._build(
            on_diagnostic=_exploding
        )
        try:
            await worker.queue_frames(
                [
                    P["UserStartedSpeakingFrame"](),
                    P["TranscriptionFrame"](
                        text="still works", user_id="", timestamp="", finalized=True
                    ),
                    P["LLMFullResponseEndFrame"](),
                ]
            )
            await asyncio.sleep(0.2)
            self.assertEqual(len(session.history), 1)
            self.assertEqual(session.history[0].content, "still works")
        finally:
            await self._teardown(runner, run_task)


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestInterruptionDirectionTelemetry(unittest.IsolatedAsyncioTestCase):
    """R0081 (continued) -- direction/sibling/vad_active-aware
    InterruptionFrame diagnostics, added after live evidence proved a
    bare 'not every InterruptionFrame is paired with a fresh local VAD
    onset' claim was too strong. Real Pipeline/PipelineWorker/
    ConversationRouter, same discipline as every other tap test."""

    async def _build(self, *, on_diagnostic):
        P = _pipecat_imports()
        router, session = _fresh_router()
        tap_cls = _make_event_tap_class(P)
        tap = tap_cls(router=router, on_diagnostic=on_diagnostic)
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

    async def test_interruption_while_vad_active_is_labeled_true(self) -> None:
        events: list[str] = []
        P, router, session, worker, runner, run_task = await self._build(
            on_diagnostic=events.append
        )
        try:
            await worker.queue_frames(
                [P["UserStartedSpeakingFrame"](), P["InterruptionFrame"]()]
            )
            await asyncio.sleep(0.2)
        finally:
            await self._teardown(runner, run_task)
        interruption_events = [e for e in events if e.startswith("INTERRUPTION_FRAME_")]
        self.assertEqual(len(interruption_events), 1)
        self.assertIn("vad_active=True", interruption_events[0])

    async def test_interruption_with_no_vad_activity_is_labeled_false(self) -> None:
        """R0081's live evidence: a DELAYED InterruptionFrame (consistent
        with GeminiLiveLLMService's own serverContent.interrupted
        acknowledgement, per the installed SDK's own source, not a fresh
        local VAD onset) arrives with no local VAD activity in progress."""
        events: list[str] = []
        P, router, session, worker, runner, run_task = await self._build(
            on_diagnostic=events.append
        )
        try:
            await worker.queue_frames([P["InterruptionFrame"]()])
            await asyncio.sleep(0.2)
        finally:
            await self._teardown(runner, run_task)
        interruption_events = [e for e in events if e.startswith("INTERRUPTION_FRAME_")]
        self.assertEqual(len(interruption_events), 1)
        self.assertIn("vad_active=False", interruption_events[0])

    async def test_direction_is_captured_in_the_label(self) -> None:
        events: list[str] = []
        P, router, session, worker, runner, run_task = await self._build(
            on_diagnostic=events.append
        )
        from pipecat.processors.frame_processor import FrameDirection

        try:
            await worker.queue_frame(P["InterruptionFrame"](), FrameDirection.UPSTREAM)
            await asyncio.sleep(0.2)
        finally:
            await self._teardown(runner, run_task)
        interruption_events = [e for e in events if e.startswith("INTERRUPTION_FRAME_")]
        self.assertEqual(len(interruption_events), 1)
        self.assertTrue(interruption_events[0].startswith("INTERRUPTION_FRAME_UPSTREAM"))

    async def test_sibling_fan_out_pair_is_identifiable(self) -> None:
        """Two InterruptionFrame instances sharing broadcast_sibling_id
        (Pipecat's own real fan-out mechanism, confirmed by installed-
        source read) must be identifiable as siblings from the
        diagnostic labels alone -- proving 'these two sightings are ONE
        physical interruption's fan-out' is directly readable, not
        guessed."""
        events: list[str] = []
        P, router, session, worker, runner, run_task = await self._build(
            on_diagnostic=events.append
        )
        try:
            downstream = P["InterruptionFrame"]()
            upstream = P["InterruptionFrame"]()
            downstream.broadcast_sibling_id = upstream.id
            upstream.broadcast_sibling_id = downstream.id
            await worker.queue_frames([downstream])
            await asyncio.sleep(0.1)
        finally:
            await self._teardown(runner, run_task)
        interruption_events = [e for e in events if e.startswith("INTERRUPTION_FRAME_")]
        self.assertEqual(len(interruption_events), 1)
        self.assertIn(f"id={downstream.id}", interruption_events[0])
        self.assertIn(f"sibling={upstream.id}", interruption_events[0])

    async def test_awaiting_assistant_state_is_captured(self) -> None:
        """R0081 side-finding, worth recording precisely: InterruptionFrame
        is a Pipecat SystemFrame (confirmed by installed-source read,
        frames.py -- `class InterruptionFrame(SystemFrame)`), which Pipecat
        gives priority/out-of-band handling over normally-queued data
        frames -- queuing all three frames in ONE batch (no real
        wall-clock gap) lets the InterruptionFrame jump ahead of the
        still-in-flight TranscriptionFrame, unlike real production timing
        (where a genuine STT/Gemini transcript event and a later VAD
        onset are always separated by real wall-clock time). This test
        forces realistic sequential timing with explicit awaits between
        frames, matching how the real pipeline actually behaves turn by
        turn, rather than relying on same-batch queue order."""
        events: list[str] = []
        P, router, session, worker, runner, run_task = await self._build(
            on_diagnostic=events.append
        )
        try:
            await worker.queue_frames([P["UserStartedSpeakingFrame"]()])
            await asyncio.sleep(0.1)
            await worker.queue_frames(
                [
                    P["TranscriptionFrame"](
                        text="hi", user_id="", timestamp="", finalized=True
                    )
                ]
            )
            await asyncio.sleep(0.1)
            await worker.queue_frames([P["InterruptionFrame"]()])
            await asyncio.sleep(0.1)
        finally:
            await self._teardown(runner, run_task)
        interruption_events = [e for e in events if e.startswith("INTERRUPTION_FRAME_")]
        self.assertEqual(len(interruption_events), 1)
        self.assertIn("awaiting_assistant=True", interruption_events[0])

    async def test_never_leaks_conversation_content(self) -> None:
        """The direction-aware label carries only IDs/booleans/direction
        -- never transcript/Memory/recall content."""
        events: list[str] = []
        P, router, session, worker, runner, run_task = await self._build(
            on_diagnostic=events.append
        )
        try:
            await worker.queue_frames(
                [
                    P["UserStartedSpeakingFrame"](),
                    P["TranscriptionFrame"](
                        text="a very specific secret sentence", user_id="",
                        timestamp="", finalized=True,
                    ),
                    P["InterruptionFrame"](),
                ]
            )
            await asyncio.sleep(0.2)
        finally:
            await self._teardown(runner, run_task)
        interruption_events = [e for e in events if e.startswith("INTERRUPTION_FRAME_")]
        self.assertEqual(len(interruption_events), 1)
        self.assertNotIn("secret sentence", interruption_events[0])


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestMicLevelTap(unittest.IsolatedAsyncioTestCase):
    """R0081 §"ADD SIGNAL-LEVEL DIAGNOSTICS" -- the second, narrow
    diagnostic FrameProcessor, exercised through a REAL
    Pipeline/PipelineWorker, never a hand-rolled simulation."""

    async def _build(self, *, on_diagnostic):
        P = _pipecat_imports()
        tap_cls = _make_mic_level_tap_class(P)
        tap = tap_cls(on_diagnostic=on_diagnostic, diagnostic_interval_s=0.0)
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
        return P, worker, runner, run_task

    async def _teardown(self, runner, run_task) -> None:
        await runner.end(reason="test done")
        await asyncio.wait_for(run_task, timeout=5.0)

    async def test_input_audio_frame_emits_mic_rms(self) -> None:
        events: list[str] = []
        P, worker, runner, run_task = await self._build(on_diagnostic=events.append)
        try:
            await worker.queue_frames(
                [P["InputAudioRawFrame"](
                    audio=b"\x10\x20" * 160, sample_rate=16000, num_channels=1
                )]
            )
            await asyncio.sleep(0.2)
        finally:
            await self._teardown(runner, run_task)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].startswith("MIC_RMS:"))

    async def test_non_input_audio_frames_never_emit(self) -> None:
        events: list[str] = []
        P, worker, runner, run_task = await self._build(on_diagnostic=events.append)
        try:
            await worker.queue_frames(
                [P["TTSTextFrame"](text="hi", aggregated_by="")]
            )
            await asyncio.sleep(0.2)
        finally:
            await self._teardown(runner, run_task)
        self.assertEqual(events, [])

    async def test_diagnostic_exception_never_breaks_the_pipeline(self) -> None:
        def _exploding(label: str) -> None:
            raise RuntimeError("boom")

        P, worker, runner, run_task = await self._build(on_diagnostic=_exploding)
        try:
            await worker.queue_frames(
                [P["InputAudioRawFrame"](
                    audio=b"\x01\x02" * 160, sample_rate=16000, num_channels=1
                )]
            )
            await asyncio.sleep(0.2)  # must not raise / hang
        finally:
            await self._teardown(runner, run_task)


class TestCoherentReferenceGainWiring(unittest.TestCase):
    """R0081 §13/§B -- the opt-in AecReferenceFeeder gain-coherence fix.
    The real hardware-device-opening path (dry=False) cannot run in this
    environment, so this checks the actual SOURCE, the same
    proven-sufficient discipline R0053's own
    ``test_self_echo_probe_production_gain_parity.py`` established for
    the sibling ``runtime.py`` wiring."""

    def _source(self) -> str:
        return (
            SRC / "nexa" / "realtime" / "gemini" / "simple_conversation.py"
        ).read_text(encoding="utf-8")

    def test_default_is_false_preserving_r0071_behavior(self) -> None:
        self.assertIn("coherent_reference_gain: bool = False", self._source())

    def test_coherent_reference_gain_constructed_with_configured_card(self) -> None:
        source = self._source()
        self.assertIn("CoherentReferenceGain(card=cfg.output_alsa_mixer_card)", source)

    def test_gain_source_bound_to_current_gain_when_enabled(self) -> None:
        source = self._source()
        self.assertIn("gain_source = reference_gain.current_gain", source)
        self.assertIn("gain_source=gain_source", source)

    def test_gain_source_defaults_to_none_in_the_feeder_construction(self) -> None:
        """Not just the function param -- the value actually threaded
        into AecReferenceFeeder() must default to None too."""
        self.assertIn("gain_source = None", self._source())


class TestDiagnosticAudioLevelsGating(unittest.TestCase):
    """R0081 -- audio-level diagnostics (mic tap, reference RMS) require
    BOTH a real on_diagnostic sink AND diagnostic_audio_levels=True;
    neither alone is enough. Source-checked for the same reason as
    TestCoherentReferenceGainWiring above (dry=False cannot run here)."""

    def _source(self) -> str:
        return (
            SRC / "nexa" / "realtime" / "gemini" / "simple_conversation.py"
        ).read_text(encoding="utf-8")

    def test_default_is_false(self) -> None:
        self.assertIn("diagnostic_audio_levels: bool = False", self._source())

    def test_gating_requires_both_conditions(self) -> None:
        self.assertIn(
            "emit_audio_levels = on_diagnostic is not None and diagnostic_audio_levels",
            self._source(),
        )

    def test_mic_tap_only_constructed_when_emit_audio_levels(self) -> None:
        source = self._source()
        self.assertIn("if emit_audio_levels:", source)
        self.assertIn("mic_tap_cls = _make_mic_level_tap_class(P)", source)

    def test_aec_feeder_diagnostic_gated_the_same_way(self) -> None:
        self.assertIn(
            "on_diagnostic=on_diagnostic if emit_audio_levels else None", self._source()
        )


if __name__ == "__main__":
    unittest.main()
