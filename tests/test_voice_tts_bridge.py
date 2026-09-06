"""M2.4 `AssistantSpeechBridge`/`TtsStatusObserver`/`voice_for_language`
tests — no microphone/Piper server/voice model/Ollama/network required.

Uses real Pipecat frame types and a fake downstream sink `FrameProcessor`
(no real `PiperHttpTTSService`), matching the same pattern
`test_voice_utterance_capture.py`/`test_voice_conversation_adapter.py`
already established for driving a real processor class through real
Pipecat frames without a running pipeline/TaskManager.
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

from pipecat.frames.frames import (  # noqa: E402
    ErrorFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from nexa.tts.errors import UnsupportedTtsLanguageError  # noqa: E402
from nexa.voice_tts import (  # noqa: E402
    AssistantSpeechBridge,
    TtsStatusObserver,
    voice_for_language,
)

EN_VOICE = "en_US-amy-low"
PL_VOICE = "pl_PL-mls_6892-low"


class TestVoiceForLanguage(unittest.TestCase):
    def test_polish_language_selects_polish_voice(self) -> None:
        self.assertEqual(voice_for_language("pl", en_voice=EN_VOICE, pl_voice=PL_VOICE), PL_VOICE)

    def test_english_language_selects_english_voice(self) -> None:
        self.assertEqual(voice_for_language("en", en_voice=EN_VOICE, pl_voice=PL_VOICE), EN_VOICE)

    def test_none_falls_back_to_english_voice(self) -> None:
        self.assertEqual(voice_for_language(None, en_voice=EN_VOICE, pl_voice=PL_VOICE), EN_VOICE)

    def test_unsupported_language_raises_explicitly(self) -> None:
        with self.assertRaises(UnsupportedTtsLanguageError):
            voice_for_language("de", en_voice=EN_VOICE, pl_voice=PL_VOICE)


class TestAssistantSpeechBridge(unittest.IsolatedAsyncioTestCase):
    def _bridge_with_captured_pushes(self):
        bridge = AssistantSpeechBridge(en_voice=EN_VOICE, pl_voice=PL_VOICE)
        pushed: list[Frame] = []

        async def fake_push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        bridge.push_frame = fake_push_frame
        pending: list[asyncio.Future] = []
        bridge.create_task = lambda coro, name=None, context=None: pending.append(
            asyncio.ensure_future(coro)
        )
        bridge._worker_task = asyncio.ensure_future(bridge._run())
        return bridge, pushed

    async def test_user_transcript_english_emits_start_frame_with_english_voice(self) -> None:
        bridge, pushed = self._bridge_with_captured_pushes()
        bridge.on_user_transcript("What is a black hole?")
        await asyncio.sleep(0.05)

        settings_frames = [f for f in pushed if isinstance(f, TTSUpdateSettingsFrame)]
        start_frames = [f for f in pushed if isinstance(f, LLMFullResponseStartFrame)]
        self.assertEqual(len(start_frames), 1)
        self.assertEqual(len(settings_frames), 1)
        self.assertEqual(settings_frames[0].delta.voice, EN_VOICE)

    async def test_user_transcript_polish_emits_start_frame_with_polish_voice(self) -> None:
        bridge, pushed = self._bridge_with_captured_pushes()
        bridge.on_user_transcript("Co to jest czarna dziura?")
        await asyncio.sleep(0.05)

        settings_frames = [f for f in pushed if isinstance(f, TTSUpdateSettingsFrame)]
        self.assertEqual(len(settings_frames), 1)
        self.assertEqual(settings_frames[0].delta.voice, PL_VOICE)

    async def test_voice_settings_frame_not_repeated_for_same_language(self) -> None:
        bridge, pushed = self._bridge_with_captured_pushes()
        bridge.on_user_transcript("What is a black hole?")
        await asyncio.sleep(0.02)
        bridge.on_assistant_complete("A black hole is dense.")
        await asyncio.sleep(0.02)
        bridge.on_user_transcript("What is the speed of light?")
        await asyncio.sleep(0.05)

        settings_frames = [f for f in pushed if isinstance(f, TTSUpdateSettingsFrame)]
        self.assertEqual(len(settings_frames), 1, "same language must not re-send a voice update")

    async def test_language_switch_emits_a_new_settings_frame(self) -> None:
        bridge, pushed = self._bridge_with_captured_pushes()
        bridge.on_user_transcript("What is a black hole?")
        await asyncio.sleep(0.02)
        bridge.on_assistant_complete("A black hole is dense.")
        await asyncio.sleep(0.02)
        bridge.on_user_transcript("Co to jest czarna dziura?")
        await asyncio.sleep(0.05)

        settings_frames = [f for f in pushed if isinstance(f, TTSUpdateSettingsFrame)]
        self.assertEqual([f.delta.voice for f in settings_frames], [EN_VOICE, PL_VOICE])

    async def test_token_becomes_llm_text_frame_in_order(self) -> None:
        bridge, pushed = self._bridge_with_captured_pushes()
        bridge.on_user_transcript("Hello")
        for tok in ["A ", "black ", "hole."]:
            bridge.on_assistant_token(tok)
        await asyncio.sleep(0.05)

        text_frames = [f for f in pushed if isinstance(f, LLMTextFrame)]
        self.assertEqual([f.text for f in text_frames], ["A ", "black ", "hole."])

    async def test_one_token_yields_exactly_one_llm_text_frame(self) -> None:
        bridge, pushed = self._bridge_with_captured_pushes()
        bridge.on_assistant_token("hello")
        await asyncio.sleep(0.05)
        text_frames = [f for f in pushed if isinstance(f, LLMTextFrame)]
        self.assertEqual(len(text_frames), 1)

    async def test_completion_emits_end_frame(self) -> None:
        bridge, pushed = self._bridge_with_captured_pushes()
        bridge.on_user_transcript("Hello")
        bridge.on_assistant_token("hi")
        bridge.on_assistant_complete("hi")
        await asyncio.sleep(0.05)

        end_frames = [f for f in pushed if isinstance(f, LLMFullResponseEndFrame)]
        self.assertEqual(len(end_frames), 1)

    async def test_conversation_error_still_emits_end_frame_no_text(self) -> None:
        bridge, pushed = self._bridge_with_captured_pushes()
        bridge.on_user_transcript("Hello")
        bridge.on_conversation_error(RuntimeError("boom"))
        await asyncio.sleep(0.05)

        end_frames = [f for f in pushed if isinstance(f, LLMFullResponseEndFrame)]
        text_frames = [f for f in pushed if isinstance(f, LLMTextFrame)]
        self.assertEqual(len(end_frames), 1)
        self.assertEqual(text_frames, [])

    async def test_frame_order_is_start_then_tokens_then_end(self) -> None:
        bridge, pushed = self._bridge_with_captured_pushes()
        bridge.on_user_transcript("Hello")
        bridge.on_assistant_token("Hi ")
        bridge.on_assistant_token("there.")
        bridge.on_assistant_complete("Hi there.")
        await asyncio.sleep(0.05)

        kinds = [type(f).__name__ for f in pushed]
        # TTSUpdateSettingsFrame precedes LLMFullResponseStartFrame; both
        # tokens follow it in order; LLMFullResponseEndFrame is last.
        self.assertEqual(
            kinds,
            [
                "TTSUpdateSettingsFrame",
                "LLMFullResponseStartFrame",
                "LLMTextFrame",
                "LLMTextFrame",
                "LLMFullResponseEndFrame",
            ],
        )


class TestTtsStatusObserver(unittest.IsolatedAsyncioTestCase):
    def _observer_with_capture(self):
        events: list[str] = []
        observer = TtsStatusObserver(
            on_tts_started=lambda: events.append("started"),
            on_tts_first_audio=lambda: events.append("first_audio"),
            on_tts_stopped=lambda: events.append("stopped"),
            on_tts_error=lambda e: events.append(f"error:{e}"),
        )
        return observer, events

    async def test_started_then_audio_then_stopped_reports_in_order(self) -> None:
        observer, events = self._observer_with_capture()
        await observer.process_frame(TTSStartedFrame(context_id="c1"), FrameDirection.DOWNSTREAM)
        await observer.process_frame(
            TTSAudioRawFrame(b"\x00\x00", 16000, 1, context_id="c1"), FrameDirection.DOWNSTREAM
        )
        await observer.process_frame(TTSStoppedFrame(context_id="c1"), FrameDirection.DOWNSTREAM)

        self.assertEqual(events, ["started", "first_audio", "stopped"])

    async def test_first_audio_reported_only_once_per_response(self) -> None:
        observer, events = self._observer_with_capture()
        await observer.process_frame(TTSStartedFrame(context_id="c1"), FrameDirection.DOWNSTREAM)
        for _ in range(3):
            await observer.process_frame(
                TTSAudioRawFrame(b"\x00\x00", 16000, 1, context_id="c1"), FrameDirection.DOWNSTREAM
            )
        self.assertEqual(events.count("first_audio"), 1)

    async def test_error_frame_reports_error(self) -> None:
        observer, events = self._observer_with_capture()
        frame = ErrorFrame(error="synthesis failed")
        await observer.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(events, ["error:synthesis failed"])

    async def test_frames_are_forwarded_unchanged(self) -> None:
        observer, _ = self._observer_with_capture()
        forwarded: list[Frame] = []

        async def fake_push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            forwarded.append(frame)

        observer.push_frame = fake_push_frame
        frame = TTSStartedFrame(context_id="c1")
        await observer.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(forwarded, [frame])


class TestFinalUnpunctuatedFragmentFlush(unittest.IsolatedAsyncioTestCase):
    """R0010 verified Pipecat's `TTSService` flushes any remaining
    unterminated text from its `SimpleTextAggregator` on
    `LLMFullResponseEndFrame` — the exact frame
    `AssistantSpeechBridge.on_assistant_complete()` emits
    (`test_completion_emits_end_frame` above). This test proves that
    flush behavior directly against the real (trusted, from R0010),
    network-free aggregator class Pipecat's `TTSService` wraps, so a
    model response ending without terminal punctuation is never silently
    dropped."""

    async def test_unpunctuated_fragment_is_returned_by_flush(self) -> None:
        from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator

        aggregator = SimpleTextAggregator()
        async for _ in aggregator.aggregate("This is the final fragment"):
            pass  # no sentence-ending punctuation, so nothing yields yet

        result = await aggregator.flush()
        self.assertIsNotNone(result)
        self.assertEqual(result.text, "This is the final fragment")


if __name__ == "__main__":
    unittest.main()
