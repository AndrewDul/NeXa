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

from nexa.conversation.turn import Role  # noqa: E402
from nexa.realtime.provider import (  # noqa: E402
    AssistantAudioEvent,
    AssistantTranscriptionEvent,
    ProviderReadiness,
    ReadinessChangedEvent,
)
from nexa.realtime.snapshot import CloudContextSnapshot, SnapshotTurn  # noqa: E402

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

            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                self.received_frames.append(type(frame).__name__)
                if isinstance(frame, P["LLMRunFrame"]):
                    pass  # LLMRunFrame is consumed upstream by the aggregator;
                    # it never reaches the service directly. Counted via
                    # LLMContextFrame instead (the aggregator's translation).
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
