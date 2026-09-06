"""M2.4's cross-boundary integration layer: two small Pipecat
``FrameProcessor``s and a pure voice-selection function.

``AssistantSpeechBridge`` translates `VoiceConversationAdapter`'s existing
streaming callbacks (`on_user_transcript`/`on_assistant_token`/
`on_assistant_complete`/`on_conversation_error`) into the exact Pipecat
1.8.1 frame vocabulary `PiperHttpTTSService` and its native sentence
aggregation already consume — verified from installed source in R0010, not
guessed: ``LLMFullResponseStartFrame`` / ``LLMTextFrame`` /
``LLMFullResponseEndFrame``. It sits **before** the TTS service in the
pipeline.

``TtsStatusObserver`` watches the TTS service's own output frames
(``TTSStartedFrame``/``TTSAudioRawFrame``/``TTSStoppedFrame``/
``ErrorFrame``) to report real status — never fabricated. Frames a TTS
service produces flow *downstream* from it, so this observer must sit
**after** the TTS service, not before (verified empirically while building
this module — placing it upstream saw zero TTS events, since those frames
never reach a processor positioned before the service that creates them).

Voice selection reuses `nexa.conversation.language.detect_response_language`
— the exact same canonical function `ConversationContext.to_provider_messages()`
already calls to build the LLM's own response-language directive — never a
second PL/EN classifier, and never a policy `VoiceConversationAdapter` (M2.3,
unchanged by this module) owns itself.

Neither processor owns history/context/persona/model/provider — they only
translate already-generated text into frames and observe already-produced
audio-status frames.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from loguru import logger
from pipecat.frames.frames import (
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
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.piper.tts import PiperHttpTTSService

from nexa.conversation.language import detect_response_language
from nexa.tts.errors import UnsupportedTtsLanguageError
from nexa.voice import HalfDuplexGate

_SHUTDOWN = object()  # sentinel, distinct from any real queued frame


def voice_for_language(language: str | None, *, en_voice: str, pl_voice: str) -> str:
    """Map a `nexa.conversation.language.detect_response_language()` result
    to a Piper voice identifier. `None` (no signal, e.g. empty text) falls
    back to the English voice — the same practical default the rest of
    M2.3 already treats as "nothing to act on"."""
    if language == "pl":
        return pl_voice
    if language in ("en", None):
        return en_voice
    raise UnsupportedTtsLanguageError(f"no Piper voice mapped for language {language!r}")


class AssistantSpeechBridge(FrameProcessor):
    """Pushes LLM-response frames into the pipeline in strict FIFO order —
    an internal queue + single worker, the same pattern
    `SerialTranscriptionQueue`/`SerialConversationQueue` already use, so
    per-token callbacks (which fire from outside this processor's own
    async context) can never push frames out of order. Place immediately
    before the `PiperHttpTTSService` stage.

    ``gate`` (M2.4, optional) — a `nexa.voice.HalfDuplexGate`. When given,
    this bridge is the one place that tells the gate a reply's *generation*
    lifecycle boundaries (`on_user_transcript` -> dispatched,
    `on_assistant_complete`/`on_conversation_error` -> finished), which the
    gate combines with real Pipecat playback frames to keep the microphone
    closed across the gaps *between* sentence chunks of one answer. The
    bridge never reads the gate or changes its own behaviour based on it."""

    def __init__(
        self,
        *,
        en_voice: str,
        pl_voice: str,
        gate: HalfDuplexGate | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._en_voice = en_voice
        self._pl_voice = pl_voice
        self._gate = gate
        self._current_voice: str | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    async def setup(self, setup) -> None:
        await super().setup(setup)
        self._worker_task = self.create_task(self._run())

    async def cleanup(self) -> None:
        if self._worker_task is not None:
            await self._queue.put(_SHUTDOWN)
            await self._worker_task
            self._worker_task = None
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _SHUTDOWN:
                return
            try:
                await self.push_frame(item)
            except Exception:
                # A last-resort guard, mirroring nexa.stt/nexa.voice_conversation's
                # own queues — a single bad frame must never silently kill the
                # worker loop for the rest of the session.
                logger.exception("nexa.voice_tts: unexpected error pushing a frame")

    def on_user_transcript(self, text: str) -> None:
        """Pass directly as `VoiceConversationAdapter(on_user_transcript=...)`."""
        if self._gate is not None:
            self._gate.notify_response_dispatched()
        language = detect_response_language(text)
        voice = voice_for_language(language, en_voice=self._en_voice, pl_voice=self._pl_voice)
        if voice != self._current_voice:
            self._queue.put_nowait(
                TTSUpdateSettingsFrame(delta=PiperHttpTTSService.Settings(voice=voice))
            )
            self._current_voice = voice
        self._queue.put_nowait(LLMFullResponseStartFrame())

    def on_assistant_token(self, token: str) -> None:
        """Pass directly as `VoiceConversationAdapter(on_assistant_token=...)`."""
        self._queue.put_nowait(LLMTextFrame(token))

    def on_assistant_complete(self, _text: str) -> None:
        """Pass directly as `VoiceConversationAdapter(on_assistant_complete=...)`.
        Triggers Pipecat's own final-fragment flush (verified in R0010) —
        any sentence-less trailing text still gets synthesized."""
        if self._gate is not None:
            self._gate.notify_response_finished()
        self._queue.put_nowait(LLMFullResponseEndFrame())

    def on_conversation_error(self, _exc: Exception) -> None:
        """Pass directly as `VoiceConversationAdapter(on_conversation_error=...)`.
        No new text to speak, but still ends the response so Pipecat's text
        aggregator resets cleanly instead of leaking a partial sentence into
        the next turn."""
        if self._gate is not None:
            self._gate.notify_response_finished()
        self._queue.put_nowait(LLMFullResponseEndFrame())


class TtsStatusObserver(FrameProcessor):
    """Observes the TTS service's own real output frames and reports real
    status via callbacks — never a fabricated one. Forwards every frame
    downstream unchanged. Place immediately *after* the `PiperHttpTTSService`
    stage (TTS output frames flow downstream from where they're produced,
    never back upstream through a processor placed before it)."""

    def __init__(
        self,
        *,
        on_tts_started: Callable[[], None] | None = None,
        on_tts_first_audio: Callable[[], None] | None = None,
        on_tts_stopped: Callable[[], None] | None = None,
        on_tts_error: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._on_tts_started = on_tts_started
        self._on_tts_first_audio = on_tts_first_audio
        self._on_tts_stopped = on_tts_stopped
        self._on_tts_error = on_tts_error
        self._awaiting_first_audio = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSStartedFrame):
            self._awaiting_first_audio = True
            if self._on_tts_started is not None:
                self._on_tts_started()
        elif isinstance(frame, TTSAudioRawFrame):
            if self._awaiting_first_audio and self._on_tts_first_audio is not None:
                self._on_tts_first_audio()
            self._awaiting_first_audio = False
        elif isinstance(frame, TTSStoppedFrame):
            if self._on_tts_stopped is not None:
                self._on_tts_stopped()
        elif isinstance(frame, ErrorFrame):
            if self._on_tts_error is not None:
                self._on_tts_error(frame.error)
        await self.push_frame(frame, direction)
