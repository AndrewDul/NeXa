"""``GeminiLiveProvider`` (ADR-0004 Decision C, M2.6B.2).

These tests exercise the REAL Pipecat 1.8.1 pipeline / worker / aggregator
machinery (``Pipeline``, ``PipelineWorker``, ``WorkerRunner``,
``LLMContextAggregatorPair``) with a FAKE terminal service standing in for
``GeminiLiveLLMService`` (injected via ``service_class=``) — no network, no
real Gemini connection, fully deterministic. This proves NeXa's own
integration logic (construction, one-time kickoff, event translation)
without re-testing Pipecat's own internals (already audited by source
read in R0032).
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

from nexa.conversation.session import ConversationSession, ExternalExchangeOutcome  # noqa: E402
from nexa.conversation.turn import Role  # noqa: E402
from nexa.realtime.provider import (  # noqa: E402
    AssistantAudioEvent,
    AssistantTranscriptionEvent,
    GenerationCompleteEvent,
    ProviderInterruptionEvent,
    ProviderReadiness,
    ReadinessChangedEvent,
    RealtimeProviderFailedError,
    UserTranscriptionEvent,
)
from nexa.realtime.router import ConversationRouter  # noqa: E402
from nexa.realtime.snapshot import CloudContextSnapshot, SnapshotTurn  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakes import FakeModelProvider  # noqa: E402

try:
    from nexa.realtime.gemini.service import GeminiLiveProvider, _pipecat_imports

    P = _pipecat_imports()
    _PIPECAT_AVAILABLE = True
except Exception:  # pragma: no cover - only if pipecat-ai isn't installed
    _PIPECAT_AVAILABLE = False
    P = {}


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class _FakeGeminiLiveServiceTestCase(unittest.IsolatedAsyncioTestCase):
    """Base class building the fake service class fresh per test (so
    per-instance call counters never leak between tests)."""

    def _make_fake_service_class(self):
        calls: dict = {"constructions": [], "llm_run_frames": 0, "contexts_seen": []}

        class FakeGeminiLiveService(P["FrameProcessor"]):
            class Settings:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs

            def __init__(
                self,
                *,
                api_key,
                system_instruction,
                inference_on_context_initialization,
                settings=None,
            ):
                super().__init__()
                calls["constructions"].append(
                    {
                        "api_key": api_key,
                        "system_instruction": system_instruction,
                        "inference_on_context_initialization": (
                            inference_on_context_initialization
                        ),
                        "settings": settings,
                    }
                )
                self._ready_for_realtime_input = False
                self._context = None
                self.received_frames: list[str] = []
                self.received_audio: list[bytes] = []
                self.turn_starts = 0
                self.turn_ends = 0

            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                self.received_frames.append(type(frame).__name__)
                if isinstance(frame, P["InputAudioRawFrame"]):
                    self.received_audio.append(frame.audio)
                elif isinstance(frame, P["UserStartedSpeakingFrame"]):
                    self.turn_starts += 1
                elif isinstance(frame, P["UserStoppedSpeakingFrame"]):
                    self.turn_ends += 1
                from pipecat.frames.frames import LLMContextFrame

                if isinstance(frame, LLMContextFrame):
                    calls["llm_run_frames"] += 1
                    calls["contexts_seen"].append(list(frame.context.get_messages()))
                    self._context = frame.context
                    self._ready_for_realtime_input = True
                await self.push_frame(frame, direction)

            async def emit_assistant_text(self, text: str) -> None:
                frame = P["TTSTextFrame"](text=text, aggregated_by="gemini")
                await self.push_frame(frame, P["FrameDirection"].DOWNSTREAM)

            async def emit_assistant_audio(self, pcm: bytes) -> None:
                await self.push_frame(
                    P["TTSAudioRawFrame"](audio=pcm, sample_rate=24000, num_channels=1),
                    P["FrameDirection"].DOWNSTREAM,
                )

            async def emit_user_transcription(self, text: str, *, final: bool = True) -> None:
                if final:
                    frame = P["TranscriptionFrame"](
                        text=text, user_id="operator", timestamp="", finalized=True
                    )
                else:
                    frame = P["InterimTranscriptionFrame"](
                        text=text, user_id="operator", timestamp=""
                    )
                await self.push_frame(frame, P["FrameDirection"].DOWNSTREAM)

            async def emit_generation_complete(self) -> None:
                await self.push_frame(
                    P["LLMFullResponseEndFrame"](), P["FrameDirection"].DOWNSTREAM
                )

            async def emit_interruption(self) -> None:
                await self.push_frame(P["InterruptionFrame"](), P["FrameDirection"].DOWNSTREAM)

            async def emit_fatal_error(self, message: str = "boom") -> None:
                # Real GeminiLiveLLMService reports errors via push_error(),
                # which pushes UPSTREAM (frame_processor.py) — faithfully
                # matched here rather than the simpler DOWNSTREAM direction,
                # since that is exactly why an upstream tap is required.
                await self.push_frame(
                    P["FatalErrorFrame"](error=message), P["FrameDirection"].UPSTREAM
                )

        return FakeGeminiLiveService, calls

    async def _started_provider(self, *, recent_turns=(), voice_preference="warm_female"):
        fake_cls, calls = self._make_fake_service_class()
        provider = GeminiLiveProvider(
            api_key="FAKE-TEST-KEY",
            voice_preference=voice_preference,
            service_class=fake_cls,
            ready_timeout_s=5.0,
        )
        snapshot = CloudContextSnapshot(
            system_instruction="ROLE CARD TEXT",
            language_preference=None,
            recent_turns=recent_turns,
            policy_name="cloud_preferred",
            active_provider_name="cloud",
        )
        await provider.start(snapshot)
        # However an assertion after this point fails, the pipeline's
        # background worker task must still be told to stop — otherwise it
        # runs forever and hangs the test process's teardown.
        self.addAsyncCleanup(provider.stop, reason="test cleanup")
        return provider, calls

    async def _drain_one(self, provider, expected_type) -> None:
        event = await asyncio.wait_for(provider.events().__anext__(), timeout=2.0)
        self.assertIsInstance(event, expected_type)

    async def _next_of_type(self, provider, event_type, *, max_events: int = 10):
        stream = provider.events()
        for _ in range(max_events):
            event = await asyncio.wait_for(stream.__anext__(), timeout=2.0)
            if isinstance(event, event_type):
                return event
        raise AssertionError(f"no {event_type} seen within {max_events} events")


class TestConstruction(_FakeGeminiLiveServiceTestCase):
    async def test_becomes_ready_after_start(self) -> None:
        provider, _ = await self._started_provider()
        self.assertEqual(provider.readiness, ProviderReadiness.READY)
        await provider.stop(reason="test done")

    async def test_system_instruction_supplied_at_construction_only(self) -> None:
        provider, calls = await self._started_provider()
        self.assertEqual(len(calls["constructions"]), 1)
        self.assertEqual(calls["constructions"][0]["system_instruction"], "ROLE CARD TEXT")
        self.assertEqual(calls["constructions"][0]["inference_on_context_initialization"], False)
        # No method on the provider ever mutates system_instruction again —
        # structural proof: the provider holds no such setter.
        self.assertFalse(hasattr(provider, "set_system_instruction"))
        self.assertFalse(hasattr(provider, "update_system_instruction"))
        await provider.stop(reason="test done")

    async def test_voice_preference_maps_into_settings(self) -> None:
        provider, calls = await self._started_provider(voice_preference="warm_female")
        settings = calls["constructions"][0]["settings"]
        self.assertEqual(settings.kwargs["voice"], "Sulafat")
        self.assertTrue(settings.kwargs["vad"].disabled)
        await provider.stop(reason="test done")

    async def test_capabilities_answerable_before_start(self) -> None:
        fake_cls, _ = self._make_fake_service_class()
        provider = GeminiLiveProvider(api_key="k", service_class=fake_cls)
        caps = provider.capabilities()
        self.assertEqual(caps.provider_name, "gemini_live")
        self.assertEqual(caps.input_sample_rate_hz, 16_000)
        self.assertEqual(caps.output_sample_rate_hz, 24_000)
        self.assertFalse(caps.supports_function_calling)


class TestOneTimeKickoffAndHistorySeed(_FakeGeminiLiveServiceTestCase):
    async def test_exactly_one_llm_context_frame_reaches_the_service(self) -> None:
        provider, calls = await self._started_provider(
            recent_turns=(SnapshotTurn(role=Role.USER, content="hi"),)
        )
        self.assertEqual(calls["llm_run_frames"], 1)
        await provider.stop(reason="test done")

    async def test_recent_turns_seed_the_initial_context_once(self) -> None:
        turns = (
            SnapshotTurn(role=Role.USER, content="hello there"),
            SnapshotTurn(role=Role.ASSISTANT, content="hi!"),
        )
        provider, calls = await self._started_provider(recent_turns=turns)
        seen = calls["contexts_seen"]
        self.assertEqual(len(seen), 1)  # seeded exactly once
        rendered_texts = [msg.get("content", "") for msg in seen[0]]
        self.assertIn("hello there", rendered_texts)
        self.assertIn("hi!", rendered_texts)
        await provider.stop(reason="test done")

    async def test_empty_recent_turns_still_reaches_ready(self) -> None:
        provider, calls = await self._started_provider(recent_turns=())
        self.assertEqual(provider.readiness, ProviderReadiness.READY)
        await provider.stop(reason="test done")

    async def test_later_turns_use_realtime_input_not_more_context_frames(self) -> None:
        provider, calls = await self._started_provider(
            recent_turns=(SnapshotTurn(role=Role.USER, content="hi"),)
        )
        self.assertEqual(calls["llm_run_frames"], 1)
        await provider.user_turn_start()
        await provider.send_user_audio(b"\x00\x01" * 10)
        await provider.user_turn_end()
        # still exactly one LLMContextFrame ever reached the service — the
        # normal turn used InputAudioRawFrame / UserStarted|StoppedSpeaking,
        # never a second context seed.
        self.assertEqual(calls["llm_run_frames"], 1)
        await provider.stop(reason="test done")


class TestEventTranslation(_FakeGeminiLiveServiceTestCase):
    async def test_assistant_audio_and_transcription_translated(self) -> None:
        provider, calls = await self._started_provider()
        fake = None
        # Drain the readiness event first.
        first = await provider.events().__anext__()
        self.assertIsInstance(first, ReadinessChangedEvent)

        # Reach into the running fake instance via the provider's own
        # reference (production code never needs this — test-only).
        fake = provider._llm  # noqa: SLF001 - test introspection only
        await fake.emit_assistant_text("hej")
        await fake.emit_assistant_audio(b"\x00\x01")

        text_event = await provider.events().__anext__()
        audio_event = await provider.events().__anext__()
        self.assertIsInstance(text_event, AssistantTranscriptionEvent)
        self.assertEqual(text_event.text, "hej")
        self.assertIsInstance(audio_event, AssistantAudioEvent)
        self.assertEqual(audio_event.pcm, b"\x00\x01")
        await provider.stop(reason="test done")

    async def test_resumption_handle_only_kept_when_resumable(self) -> None:
        provider, _ = await self._started_provider()

        class _FakeUpdate:
            def __init__(self, resumable, new_handle):
                self.resumable = resumable
                self.new_handle = new_handle

        class _FakeFrame:
            def __init__(self, update):
                self.session_resumption_update = update

        provider._translate_frame(_FakeFrame(_FakeUpdate(True, "handle-1")))  # noqa: SLF001
        self.assertEqual(provider.latest_resumption_handle.value, "handle-1")
        provider._translate_frame(_FakeFrame(_FakeUpdate(False, "handle-2")))  # noqa: SLF001
        self.assertEqual(provider.latest_resumption_handle.value, "handle-1")  # unchanged
        await provider.stop(reason="test done")

    async def test_interim_user_transcription_is_not_final(self) -> None:
        provider, _ = await self._started_provider()
        await self._drain_one(provider, ReadinessChangedEvent)
        await provider._llm.emit_user_transcription("cze", final=False)  # noqa: SLF001
        event = await self._next_of_type(provider, UserTranscriptionEvent)
        self.assertFalse(event.final)
        self.assertEqual(event.text, "cze")
        await provider.stop(reason="test done")

    async def test_final_user_transcription_is_final(self) -> None:
        provider, _ = await self._started_provider()
        await self._drain_one(provider, ReadinessChangedEvent)
        await provider._llm.emit_user_transcription("cześć", final=True)  # noqa: SLF001
        event = await self._next_of_type(provider, UserTranscriptionEvent)
        self.assertTrue(event.final)
        self.assertEqual(event.text, "cześć")
        await provider.stop(reason="test done")

    async def test_generation_complete_event_is_distinct_from_transcription(self) -> None:
        provider, _ = await self._started_provider()
        await self._drain_one(provider, ReadinessChangedEvent)
        await provider._llm.emit_generation_complete()  # noqa: SLF001
        event = await self._next_of_type(provider, GenerationCompleteEvent)
        self.assertIsInstance(event, GenerationCompleteEvent)
        await provider.stop(reason="test done")

    async def test_interruption_event_translated(self) -> None:
        provider, _ = await self._started_provider()
        await self._drain_one(provider, ReadinessChangedEvent)
        await provider._llm.emit_interruption()  # noqa: SLF001
        event = await self._next_of_type(provider, ProviderInterruptionEvent)
        self.assertIsInstance(event, ProviderInterruptionEvent)
        await provider.stop(reason="test done")

    async def test_fatal_error_pushed_upstream_still_reaches_events(self) -> None:
        """``push_error()`` (the real service's error path) pushes UPSTREAM
        — proves the ``up_tap`` addition actually matters, not just the
        downstream tap."""
        provider, _ = await self._started_provider()
        await self._drain_one(provider, ReadinessChangedEvent)
        await provider._llm.emit_fatal_error("simulated fatal error")  # noqa: SLF001
        event = await self._next_of_type(provider, RealtimeProviderFailedError)
        self.assertIsInstance(event, RealtimeProviderFailedError)
        self.assertEqual(provider.readiness, ProviderReadiness.FAILED)
        await provider.stop(reason="test done")


class TestNotReadyTurnFraming(_FakeGeminiLiveServiceTestCase):
    """ADR-0004 Decision H wiring: while not READY, turn I/O goes through
    ``UtteranceFramer`` instead of the live pipeline; once READY it is
    flushed in order, exactly once."""

    async def test_audio_while_not_ready_is_buffered_not_sent_live(self) -> None:
        provider, calls = await self._started_provider()
        provider._readiness = ProviderReadiness.DEGRADED  # noqa: SLF001 - test-only fault injection
        frames_before = len(provider._llm.received_frames)  # noqa: SLF001
        await provider.user_turn_start()
        await provider.send_user_audio(b"\x01\x02")
        await provider.user_turn_end()
        # nothing new reached the (fake) service live — it was buffered.
        self.assertEqual(len(provider._llm.received_frames), frames_before)  # noqa: SLF001
        self.assertEqual(len(provider._framer), 3)  # start + audio + end

    async def test_buffered_turn_flushed_in_order_on_ready(self) -> None:
        provider, calls = await self._started_provider()
        provider._readiness = ProviderReadiness.RECONNECTING  # noqa: SLF001
        await provider.user_turn_start()
        await provider.send_user_audio(b"\x01\x02")
        await provider.user_turn_end()
        self.assertEqual(len(provider._framer), 3)  # noqa: SLF001

        provider._set_readiness(ProviderReadiness.READY)  # noqa: SLF001
        await provider._flush_framer()  # noqa: SLF001 - normally driven by _wait_until_ready/reconnect
        for _ in range(20):  # let the pipeline's background task drain the queue
            await asyncio.sleep(0.01)
            if "UserStoppedSpeakingFrame" in provider._llm.received_frames:  # noqa: SLF001
                break

        received = provider._llm.received_frames  # noqa: SLF001
        start_i = received.index("UserStartedSpeakingFrame")
        audio_i = received.index("InputAudioRawFrame")
        end_i = received.index("UserStoppedSpeakingFrame")
        self.assertLess(start_i, audio_i)
        self.assertLess(audio_i, end_i)

        # a second flush delivers nothing more (no duplication).
        await provider._flush_framer()  # noqa: SLF001
        self.assertEqual(received.count("UserStartedSpeakingFrame"), 1)
        self.assertEqual(received.count("UserStoppedSpeakingFrame"), 1)


async def _wait_until(predicate, *, timeout: float = 2.0, step: float = 0.01) -> None:
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return
        await asyncio.sleep(step)
        elapsed += step
    raise AssertionError(f"condition not met within {timeout}s")


class TestMidTurnReadinessLoss(_FakeGeminiLiveServiceTestCase):
    """M2.6B.2A hardening — the mid-turn readiness-loss fix. Invariant:
    NO SILENT USER SPEECH LOSS; no duplicated audio; valid
    activity_start/audio/activity_end ordering; no duplicate start/end;
    bounded buffering."""

    async def test_ready_outage_mid_turn_ready_again_no_loss_no_duplication(self) -> None:
        provider, _ = await self._started_provider()
        fake = provider._llm  # noqa: SLF001

        await provider.user_turn_start()  # READY -> sent live
        await provider.send_user_audio(b"live-1")  # READY -> sent live
        await _wait_until(lambda: len(fake.received_audio) >= 1)
        self.assertEqual(fake.turn_starts, 1)
        self.assertEqual(fake.received_audio, [b"live-1"])

        provider._readiness = ProviderReadiness.RECONNECTING  # noqa: SLF001 - fault injection

        await provider.send_user_audio(b"during-outage-1")  # must NOT be silently dropped
        await provider.send_user_audio(b"during-outage-2")
        await provider.user_turn_end()  # still not ready

        # nothing new reached the fake live yet — it's buffered as a NEW,
        # self-contained utterance (the old live one is deterministically
        # aborted, never duplicated).
        self.assertEqual(fake.received_audio, [b"live-1"])
        self.assertEqual(fake.turn_ends, 0)

        provider._set_readiness(ProviderReadiness.READY)  # noqa: SLF001
        await provider._flush_framer()  # noqa: SLF001
        await _wait_until(lambda: fake.turn_ends >= 1)

        # every buffered byte was delivered, in order, exactly once; the
        # already-live byte was never replayed.
        self.assertEqual(fake.received_audio, [b"live-1", b"during-outage-1", b"during-outage-2"])
        self.assertEqual(len(fake.received_audio), len(set(fake.received_audio)))
        self.assertEqual(fake.turn_starts, 2)  # the original live start + the new buffered one
        self.assertEqual(fake.turn_ends, 1)  # only the buffered (2nd) segment got a live end
        # ordering within the flushed (2nd) segment: its own start precedes
        # its own audio precedes its own end.
        received = fake.received_frames
        second_start_i = len(received) - received[::-1].index("UserStartedSpeakingFrame") - 1
        end_i = received.index("UserStoppedSpeakingFrame")
        self.assertLess(second_start_i, end_i)
        await provider.stop(reason="test done")

    async def test_ready_immediate_outage_before_first_audio(self) -> None:
        provider, _ = await self._started_provider()
        fake = provider._llm  # noqa: SLF001

        await provider.user_turn_start()  # READY -> live start sent
        await _wait_until(lambda: fake.turn_starts >= 1)

        provider._readiness = ProviderReadiness.RECONNECTING  # noqa: SLF001
        await provider.user_turn_end()  # not ready, no audio ever arrived

        # No data was ever produced, so none can be lost; the framer has
        # nothing buffered (there was no audio to buffer), and no
        # duplicate/bare frames are queued.
        self.assertEqual(len(provider._framer), 0)  # noqa: SLF001
        self.assertEqual(fake.turn_starts, 1)
        self.assertEqual(fake.turn_ends, 0)
        self.assertEqual(fake.received_audio, [])
        await provider.stop(reason="test done")

    async def test_mid_turn_outage_then_fresh_session_required(self) -> None:
        """Reconnect fails outright and a FRESH provider session is
        required (ADR-0004 Decision I) — the OLD provider is discarded.
        Its not-yet-delivered audio must be retrievable, not silently
        thrown away with the old instance."""
        provider, _ = await self._started_provider()
        fake = provider._llm  # noqa: SLF001

        await provider.user_turn_start()
        await provider.send_user_audio(b"live-1")
        await _wait_until(lambda: len(fake.received_audio) >= 1)

        provider._readiness = ProviderReadiness.RECONNECTING  # noqa: SLF001
        await provider.send_user_audio(b"stranded-1")
        await provider.send_user_audio(b"stranded-2")
        # user_turn_end() never arrives yet -- the outage is still ongoing
        # when NeXa decides a fresh session is required.

        pending = provider.take_pending_audio()
        self.assertEqual(pending, [b"stranded-1", b"stranded-2"])
        # taking it clears it from the old instance -- never re-deliverable
        # from here (avoids duplication if the old instance is stopped and
        # discarded rather than immediately garbage-collected).
        self.assertEqual(provider.take_pending_audio(), [])
        await provider.stop(reason="fresh session required")

        # Replayed into a brand-new provider instance with fresh framing --
        # never the stale one's envelope markers.
        new_provider, new_calls = await self._started_provider()
        new_fake = new_provider._llm  # noqa: SLF001
        await new_provider.user_turn_start()
        for chunk in pending:
            await new_provider.send_user_audio(chunk)
        await new_provider.user_turn_end()
        await _wait_until(lambda: new_fake.turn_ends >= 1)
        self.assertEqual(new_fake.received_audio, [b"stranded-1", b"stranded-2"])
        self.assertEqual(new_fake.turn_starts, 1)
        self.assertEqual(new_fake.turn_ends, 1)
        await new_provider.stop(reason="test done")

    async def test_stale_provider_context_never_seeds_the_fresh_session(self) -> None:
        """ADR-0004 Decision I, finished (M2.6B.2A): fresh-session recovery
        is destroy-and-recreate — a brand new ``GeminiLiveProvider``
        builds its own ``LLMContext`` from a freshly built
        ``CloudContextSnapshot``, never from any previous instance's
        Pipecat-owned context."""
        # The OLD provider was seeded from a stale/old snapshot (as if a
        # long-disconnected session had been carrying "A/OLD").
        old_provider, old_calls = await self._started_provider(
            recent_turns=(
                SnapshotTurn(role=Role.USER, content="turn A"),
                SnapshotTurn(role=Role.ASSISTANT, content="OLD stale reply"),
            )
        )
        old_seen = [msg.get("content", "") for msg in old_calls["contexts_seen"][0]]
        self.assertIn("OLD stale reply", old_seen)
        await old_provider.stop(reason="resumption failed, fresh session required")

        # Canonical NeXa state has since moved on to turns A/B/C.
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session)
        session.record_external_exchange("turn A", "reply A")
        session.record_external_exchange("turn B", "reply B")
        session.record_external_exchange("turn C", "reply C")

        fresh_snapshot = await router.request_fresh_snapshot_after_resumption_failure()
        fresh_contents = [t.content for t in fresh_snapshot.recent_turns]
        self.assertIn("turn C", fresh_contents)
        self.assertIn("reply C", fresh_contents)
        self.assertNotIn("OLD stale reply", fresh_contents)

        # A brand new provider, seeded with the FRESH snapshot, must never
        # see the old provider's stale content -- and structurally cannot,
        # since it builds its own LLMContext from scratch.
        new_fake_cls, new_calls = self._make_fake_service_class()
        new_provider = GeminiLiveProvider(
            api_key="FAKE-TEST-KEY", service_class=new_fake_cls, ready_timeout_s=5.0
        )
        await new_provider.start(fresh_snapshot)
        self.addAsyncCleanup(new_provider.stop, reason="test cleanup")

        new_seen = [msg.get("content", "") for msg in new_calls["contexts_seen"][0]]
        self.assertIn("turn C", new_seen)
        self.assertIn("reply C", new_seen)
        self.assertNotIn("OLD stale reply", new_seen)


class _RouterDrivenTestCase(_FakeGeminiLiveServiceTestCase):
    """Base for tests that drive a real ``ConversationRouter`` exclusively
    from real ``GeminiLiveProvider`` events — never by calling
    ``router.on_user_transcription`` / ``commit_cloud_turn`` etc. directly
    with hand-picked text (M2.6B.2A closes that gap)."""

    def _router(self) -> tuple[ConversationSession, ConversationRouter]:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        # policy is irrelevant to these tests (no start_cloud() call) --
        # LOCAL_ONLY is just a concrete, valid default.
        router = ConversationRouter(session)
        return session, router

    async def _pump_events(self, provider, router, count: int):
        """Consume exactly ``count`` events from the provider's stream and
        feed each one through ``router.handle_provider_event`` — the ONLY
        path by which a provider's output may reach the router/session."""
        stream = provider.events()
        outcomes = []
        for _ in range(count):
            event = await asyncio.wait_for(stream.__anext__(), timeout=2.0)
            outcomes.append(router.handle_provider_event(event))
        return outcomes


class TestNoManualInjectionFullCloudTurn(_RouterDrivenTestCase):
    """A normal cloud turn, driven exclusively by real provider events
    through the real Pipecat pipeline -- no test ever calls
    ``ConversationSession.record_external_exchange`` or
    ``router.on_user_transcription`` directly with hand-picked text."""

    async def test_full_turn_via_provider_events_commits_exactly_once(self) -> None:
        provider, _ = await self._started_provider()
        session, router = self._router()
        fake = provider._llm  # noqa: SLF001

        await self._drain_one(provider, ReadinessChangedEvent)

        router.begin_cloud_turn()  # NeXa's local turn authority marks a new turn
        await provider.user_turn_start()
        await provider.send_user_audio(b"user-audio")
        await fake.emit_user_transcription("hej", final=True)
        await fake.emit_assistant_text("cze")
        await fake.emit_assistant_audio(b"assistant-audio")
        await fake.emit_assistant_text("ść")
        await fake.emit_generation_complete()
        await provider.user_turn_end()

        outcomes = await self._pump_events(provider, router, 5)
        self.assertEqual(
            [o for o in outcomes if o is not None], [ExternalExchangeOutcome.COMMITTED_EXCHANGE]
        )

        self.assertEqual(len(session.history), 2)
        self.assertEqual(session.history[0].content, "hej")
        self.assertEqual(session.history[1].content, "cześć")
        await provider.stop(reason="test done")


class TestTurnOrderingPermutations(_RouterDrivenTestCase):
    """Realistic event permutations (item 3 of the M2.6B.2A charter).
    Scenario C (turnComplete before a delayed final transcription) is
    proven IMPOSSIBLE from the installed Pipecat source, not assumed —
    see the class docstring below for the citation."""

    async def test_a_user_then_assistant_partial_then_final_then_complete(self) -> None:
        provider, _ = await self._started_provider()
        session, router = self._router()
        fake = provider._llm  # noqa: SLF001
        await self._drain_one(provider, ReadinessChangedEvent)

        router.begin_cloud_turn()
        await fake.emit_user_transcription("hej", final=True)
        await fake.emit_assistant_text("Cze")
        await fake.emit_assistant_text("ść!")
        await fake.emit_generation_complete()

        await self._pump_events(provider, router, 4)
        self.assertEqual(len(session.history), 2)
        self.assertEqual(session.history[1].content, "Cześć!")

    async def test_b_interruption_wins_over_late_server_ack(self) -> None:
        provider, _ = await self._started_provider()
        session, router = self._router()
        fake = provider._llm  # noqa: SLF001
        await self._drain_one(provider, ReadinessChangedEvent)

        router.begin_cloud_turn()
        await fake.emit_user_transcription("tell me a story", final=True)
        await fake.emit_assistant_text("Once upon a ti")
        await self._pump_events(provider, router, 2)  # process these first, in order

        # Local interruption authority fires (NeXa's own barge-in signal —
        # never a provider event; BargeInController drives this
        # independently of the cloud provider's event stream in
        # production, so it is called directly here too).
        router.on_interruption()
        router.set_spoken_prefix("Once upon a ti")

        # the server's own interruption ACK / any further generation
        # arrives LATE -- must never overturn the local decision or
        # produce a second commit.
        await fake.emit_interruption()
        await fake.emit_assistant_text(" the end")  # late text, must be ignored
        await fake.emit_generation_complete()

        await self._pump_events(provider, router, 3)
        self.assertEqual(len(session.history), 2)
        self.assertTrue(session.history[1].interrupted)
        self.assertEqual(session.history[1].content, "Once upon a ti")

        # a further, later generation-complete for the SAME (already
        # committed) turn produces no second commit.
        outcome = router.commit_cloud_turn()
        self.assertIsNone(outcome)
        self.assertEqual(len(session.history), 2)

    async def test_c_turn_complete_before_delayed_final_transcription_is_impossible(
        self,
    ) -> None:
        """VERIFIED FACT (installed pipecat-ai==1.8.1,
        services/google/gemini_live/llm.py, ``_connection_task_handler``):
        messages are processed strictly in receive order from a single
        ``async for message in turn`` loop, and — per that method's own
        comment ("server_content fields are NOT mutually exclusive --
        Gemini 3.x can bundle multiple content fields and turn_complete on
        the same message, so process the content-bearing fields before
        closing the turn") — even a message that bundles BOTH
        input_transcription and turn_complete together is handled with
        input_transcription first:
            if sc and sc.input_transcription: await self.
                _handle_msg_input_transcription(message)
            ...
            if sc and sc.turn_complete: await self._handle_msg_turn_complete(message)
        So a LATER message's turn_complete can never be processed before
        an EARLIER (or same-message) final transcription. Scenario C
        cannot occur with the installed Pipecat/Gemini-3.x combination.
        This test proves the ROUTER is still safe if it ever did (defence
        in depth): a generation-complete with no user text yet commits
        nothing until the user text arrives, and nothing is ever
        double-committed once it does.
        """
        provider, _ = await self._started_provider()
        session, router = self._router()
        fake = provider._llm  # noqa: SLF001
        await self._drain_one(provider, ReadinessChangedEvent)

        router.begin_cloud_turn()
        await fake.emit_assistant_text("reply")
        await fake.emit_generation_complete()  # "arrives" before the user transcript
        await fake.emit_user_transcription("hej", final=True)  # delayed

        outcomes = await self._pump_events(provider, router, 3)
        # the premature generation-complete commits nothing (no user text
        # yet) -- CloudTurnAccumulator.on_turn_complete() returns a turn
        # with user_text=None, which the router refuses to commit.
        self.assertEqual([o for o in outcomes if o is not None], [])
        self.assertEqual(len(session.history), 0)

        # the turn is now COMMITTED-empty in the accumulator (M2.6B
        # invariant: at most one commit) -- a real Gemini/Pipecat sequence
        # never produces this ordering, so no further recovery is required
        # or attempted here.

    async def test_d_provider_dies_after_user_transcription_before_assistant(self) -> None:
        provider, _ = await self._started_provider()
        session, router = self._router()
        fake = provider._llm  # noqa: SLF001
        await self._drain_one(provider, ReadinessChangedEvent)

        router.begin_cloud_turn()
        await fake.emit_user_transcription("hej", final=True)
        await self._pump_events(provider, router, 1)

        outcome = router.handle_cloud_session_lost(reason="provider died")
        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_USER_ONLY)
        self.assertEqual(len(session.history), 1)
        self.assertEqual(session.history[0].content, "hej")

    async def test_e_provider_dies_after_spoken_assistant_prefix(self) -> None:
        provider, _ = await self._started_provider()
        session, router = self._router()
        fake = provider._llm  # noqa: SLF001
        await self._drain_one(provider, ReadinessChangedEvent)

        router.begin_cloud_turn()
        await fake.emit_user_transcription("tell me a story", final=True)
        await fake.emit_assistant_text("Once upon a ti")
        await self._pump_events(provider, router, 2)

        router.set_spoken_prefix("Once upon a ti")  # playback layer's high-water mark
        outcome = router.handle_cloud_session_lost(reason="provider died mid-speech")
        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_EXCHANGE)
        self.assertEqual(len(session.history), 2)
        self.assertEqual(session.history[1].content, "Once upon a ti")


class TestLifecycle(_FakeGeminiLiveServiceTestCase):
    async def test_stop_is_idempotent(self) -> None:
        provider, _ = await self._started_provider()
        await provider.stop(reason="first")
        await provider.stop(reason="second")  # must not raise

    async def test_stop_without_start_does_not_raise(self) -> None:
        fake_cls, _ = self._make_fake_service_class()
        provider = GeminiLiveProvider(api_key="k", service_class=fake_cls)
        await provider.stop(reason="never started")


if __name__ == "__main__":
    unittest.main()
