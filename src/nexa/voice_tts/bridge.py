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
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
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
        on_interruption: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._en_voice = en_voice
        self._pl_voice = pl_voice
        self._gate = gate
        # M2.5B — invoked when an ``InterruptionFrame`` reaches this bridge
        # (the ``BargeInController`` broadcast one). Wired to
        # ``BargeInController.notify_response_finished`` so the interruption
        # state machine + half-duplex gate close out the killed reply.
        self._on_interruption = on_interruption
        self._current_voice: str | None = None
        self._interrupted_frames_dropped = 0
        # M2.4B.5: set True by ``select_voice`` (driven by the voice
        # adapter's ``ResponseLanguageResolver``); when True, ``on_user_transcript``
        # does NOT re-derive the voice from the transcript text. Reset at
        # the end of each response.
        self._explicit_voice_this_turn = False
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

    @property
    def interrupted_frames_dropped(self) -> int:
        """Queued LLM frames discarded this session by ``InterruptionFrame``
        handling (M2.5B) — stale text that must never reach TTS/history."""
        return self._interrupted_frames_dropped

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            # M2.5B — the BargeInController confirmed a barge-in. Drain every
            # queued LLM frame from the killed reply so no stale
            # LLMTextFrame / trailing LLMFullResponseEndFrame can leak into
            # the next response; reset per-turn voice state; then close the
            # generation lifecycle (gate + interruption state machine) via
            # the on_interruption hook. Forward the frame so the planner /
            # continuity / TTS downstream also clear.
            self._drain_queue()
            self._explicit_voice_this_turn = False
            if self._on_interruption is not None:
                self._on_interruption()
        await self.push_frame(frame, direction)

    def _drain_queue(self) -> None:
        drained = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is _SHUTDOWN:
                # never discard the shutdown sentinel — put it back
                self._queue.put_nowait(_SHUTDOWN)
                break
            drained += 1
        self._interrupted_frames_dropped += drained
        if drained:
            logger.info(
                f"nexa.voice_tts: InterruptionFrame — drained {drained} queued "
                f"LLM frame(s) from the interrupted reply"
            )

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

    def select_voice(self, response_language: str | None) -> None:
        """M2.4B.5: choose the Piper voice from the **ResponseLanguage** the
        voice adapter's ``ResponseLanguageResolver`` resolved — *not* from
        the transcript text and *not* from the STT decode language. Call
        before ``on_user_transcript`` for the turn. Idempotent."""
        self._explicit_voice_this_turn = True
        self._apply_voice(
            voice_for_language(
                response_language, en_voice=self._en_voice, pl_voice=self._pl_voice
            )
        )

    def _apply_voice(self, voice: str) -> None:
        if voice != self._current_voice:
            self._queue.put_nowait(
                TTSUpdateSettingsFrame(delta=PiperHttpTTSService.Settings(voice=voice))
            )
            self._current_voice = voice

    def on_user_transcript(self, text: str) -> None:
        """Pass directly as `VoiceConversationAdapter(on_user_transcript=...)`."""
        if self._gate is not None:
            self._gate.notify_response_dispatched()
        if not self._explicit_voice_this_turn:
            # pre-B.5 fallback: no ResponseLanguageResolver is driving voice
            # selection, so derive it from the transcript text (R0009).
            language = detect_response_language(text)
            self._apply_voice(
                voice_for_language(
                    language, en_voice=self._en_voice, pl_voice=self._pl_voice
                )
            )
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
        self._explicit_voice_this_turn = False
        self._queue.put_nowait(LLMFullResponseEndFrame())

    def on_conversation_error(self, _exc: Exception) -> None:
        """Pass directly as `VoiceConversationAdapter(on_conversation_error=...)`.
        No new text to speak, but still ends the response so Pipecat's text
        aggregator resets cleanly instead of leaking a partial sentence into
        the next turn."""
        if self._gate is not None:
            self._gate.notify_response_finished()
        self._explicit_voice_this_turn = False
        self._queue.put_nowait(LLMFullResponseEndFrame())


class TtsStatusObserver(FrameProcessor):
    """Observes the TTS service's own real output frames and reports real
    status via callbacks — never a fabricated one. Forwards every frame
    downstream unchanged. Place immediately *after* the `PiperHttpTTSService`
    stage (TTS output frames flow downstream from where they're produced,
    never back upstream through a processor placed before it).

    The `on_tts_audio` / `on_tts_text` / `on_tts_response_end` callbacks
    (M2.4B.1, all optional, default `None`) are pure measurement hooks for
    the gap profiler: `on_tts_audio` receives each `TTSAudioRawFrame`'s byte
    count + format for in-memory buffer accounting; `on_tts_text` receives
    each synthesized sentence's text (`TTSTextFrame`); `on_tts_response_end`
    fires on the downstream `LLMFullResponseEndFrame`, which Pipecat emits
    only *after* a turn's entire audio context has drained — the definitive
    "this turn's TTS is finished" signal. None of them change what is
    forwarded; omitting them (the M2.4 default) is byte-for-byte the M2.4
    observer."""

    def __init__(
        self,
        *,
        on_tts_started: Callable[[], None] | None = None,
        on_tts_first_audio: Callable[[], None] | None = None,
        on_tts_stopped: Callable[[], None] | None = None,
        on_tts_error: Callable[[str], None] | None = None,
        on_tts_audio: Callable[[int, int, int], None] | None = None,
        on_tts_text: Callable[[str], None] | None = None,
        on_tts_response_end: Callable[[], None] | None = None,
        on_bot_started_speaking: Callable[[], None] | None = None,
        on_bot_stopped_speaking: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._on_tts_started = on_tts_started
        self._on_tts_first_audio = on_tts_first_audio
        self._on_tts_stopped = on_tts_stopped
        self._on_tts_error = on_tts_error
        self._on_tts_audio = on_tts_audio
        self._on_tts_text = on_tts_text
        self._on_tts_response_end = on_tts_response_end
        self._on_bot_started_speaking = on_bot_started_speaking
        self._on_bot_stopped_speaking = on_bot_stopped_speaking
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
            if self._on_tts_audio is not None:
                self._on_tts_audio(
                    len(frame.audio), frame.sample_rate, frame.num_channels
                )
        elif isinstance(frame, TTSTextFrame):
            if self._on_tts_text is not None:
                self._on_tts_text(frame.text)
        elif isinstance(frame, TTSStoppedFrame):
            if self._on_tts_stopped is not None:
                self._on_tts_stopped()
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._on_tts_response_end is not None:
                self._on_tts_response_end()
        elif isinstance(frame, BotStartedSpeakingFrame):
            if self._on_bot_started_speaking is not None:
                self._on_bot_started_speaking()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            if self._on_bot_stopped_speaking is not None:
                self._on_bot_stopped_speaking()
        elif isinstance(frame, ErrorFrame):
            if self._on_tts_error is not None:
                self._on_tts_error(frame.error)
        await self.push_frame(frame, direction)
