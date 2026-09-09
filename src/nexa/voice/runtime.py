"""M2.1 voice runtime: local mic -> Pipecat local audio transport -> Silero
VAD -> NeXa ``VoiceState`` transitions -> local audio-output plumbing.

ADR-0003 D1-D3, D10. Pipecat is infrastructure only: it owns audio
capture/playback and VAD frame production. It does **not** become a second
conversation authority — there is no ``ConversationSession`` call, no LLM,
no persona, no history anywhere in this module. That starts at M2.2/M2.3.

Current Pipecat (1.8.1) API note: ``PipelineTask``/``PipelineRunner`` are
deprecated since 1.3.0. This module uses the current, non-deprecated
``PipelineWorker`` (``pipecat.pipeline.worker``) +
``WorkerRunner`` (``pipecat.workers.runner``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import pyaudio
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    InputAudioRawFrame,
    StartFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, FrameProcessorSetup
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.workers.runner import WorkerRunner

from nexa.stt import (
    Language,
    SerialTranscriptionQueue,
    SpeechTranscriber,
    SttQueueOverflowError,
    TranscriptionResult,
    UtteranceBuffer,
)

from .config import LocalAudioConfig
from .device import find_device_index
from .gate import HalfDuplexGate
from .state import VoiceEvent, VoiceStateMachine

# NeXa-chosen VAD default — evidence-based, not the library default.
#
# `VERIFIED FACT` (2026-09-05, real hardware, this Pi, reSpeaker XVF3800):
# Pipecat/Silero's own library default (`stop_secs=0.2`) split one natural
# Polish sentence into three separate USER_SPEAKING/END_OF_TURN cycles. The
# two internal pauses measured (via this module's own event timestamps) at
# ~1.66 s and ~1.82 s — both far longer than 0.2 s, and also longer than the
# operator's intended ~0.5 s/~0.8 s targets (manual pause timing overshot).
# This is evidence that 0.2 s is too short, but it is NOT evidence of where
# the correct threshold is — a 1.7-1.8 s gap is not representative of an
# ordinary short conversational pause, and is not described as one here.
#
# `VERIFIED FACT` (2026-09-05, deterministic offline calibration — not live
# hardware, not human-timed): the real `SileroVADAnalyzer` (same class, same
# `VADParams`/frame-count confirmation logic the live pipeline uses) was fed
# real recorded Polish speech with precisely inserted real-silence gaps (the
# actual reSpeaker room-noise floor, not digital zeros) of exactly
# 0.4/0.6/0.8/1.0/1.2 s, swept against `stop_secs` in {0.2, 0.6, 0.8, 1.0}.
# Result: a gap is confirmed as a stop only once it reaches `stop_secs`
# (minus ~1 frame, ~32 ms, from frame-count rounding) — `stop_secs=0.8`
# correctly held 0.4 s and 0.6 s gaps as one utterance but **split at
# exactly the 0.8 s gap itself** (a real "~0.8 s" pause can easily land at
# or above 0.8 s). `stop_secs=1.0` is the smallest tested value that held
# all three of the operator's calibration targets (0.4/0.6/0.8 s) as one
# utterance, splitting only at 1.0 s+. Measured stop latency scales with
# `stop_secs` (~0.16 s at 0.2, ~0.96 s at 1.0) — the real cost of the
# larger value is slower genuine end-of-turn detection, not a free change.
# See the M2.1 report's "REAL HARDWARE TEST" section for the full matrix.
#
# `start_secs` is left at the library default (0.2 s) — no evidence yet
# requires changing it.
DEFAULT_VAD_PARAMS = VADParams(stop_secs=1.0)

#: M2.4B.5A: the reason logged when an utterance captured while NeXa is
#: still answering a previous turn is dropped instead of transcribed. The
#: strict pre-M2.5 half-duplex rule — no barge-in, no "interruption".
DROP_BUSY_RESPONSE_IN_FLIGHT = "DROP_BUSY_RESPONSE_IN_FLIGHT"


@dataclass(frozen=True, slots=True)
class DroppedUtterance:
    """Telemetry for an utterance dropped at capture (never enqueued for
    STT). Not a conversation turn; ``ConversationSession`` history is
    untouched."""

    reason: str
    at: float  # time.monotonic()
    at_wall: str  # ISO-8601 UTC, human-readable
    audio_ms: int
    stt_queue_depth: int
    dropped_count_this_session: int


def apply_frame_to_state_machine(frame: Frame, machine: VoiceStateMachine) -> None:
    """Map one Pipecat frame to a ``VoiceStateMachine`` call, if relevant.

    Pure — no Pipecat processor lifecycle, no I/O — so it is directly unit
    testable with real ``Frame`` instances and a real ``VoiceStateMachine``,
    without needing a running pipeline.
    """
    if isinstance(frame, StartFrame):
        machine.start_listening()
    elif isinstance(frame, VADUserStartedSpeakingFrame):
        machine.speech_started()
    elif isinstance(frame, VADUserStoppedSpeakingFrame):
        machine.speech_stopped()
    elif isinstance(frame, ErrorFrame):
        machine.error(frame.error)
    elif isinstance(frame, (EndFrame, CancelFrame)):
        machine.shutdown()


class _VoiceStateFrameProcessor(FrameProcessor):
    """Translates Pipecat VAD/lifecycle frames into NeXa VoiceState
    transitions. Forwards every frame downstream unchanged — it observes,
    it does not own or terminate the pipeline."""

    def __init__(self, state_machine: VoiceStateMachine, **kwargs) -> None:
        super().__init__(**kwargs)
        self._machine = state_machine

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        apply_frame_to_state_machine(frame, self._machine)
        await self.push_frame(frame, direction)


class _UtteranceCaptureFrameProcessor(FrameProcessor):
    """M2.2: captures one utterance's raw PCM per LISTENING -> END_OF_TURN
    cycle (via ``UtteranceBuffer``, which owns the pre-roll) and submits it
    to a ``SerialTranscriptionQueue`` on end-of-turn.

    Placed immediately after ``VADProcessor`` in the pipeline:
    ``VADProcessor.process_frame`` (see
    ``pipecat/processors/audio/vad_processor.py``) pushes each
    ``InputAudioRawFrame`` downstream *before* running VAD analysis that
    might broadcast a ``VADUserStartedSpeakingFrame``/
    ``VADUserStoppedSpeakingFrame`` for it — so this processor always sees
    an audio chunk before any VAD event that chunk triggers, by construction
    (verified from the installed Pipecat 1.8.1 source, not assumed).

    Audio/VAD capture is never blocked by STT: ``queue.submit()`` only
    enqueues and returns immediately, so a new utterance can be captured
    while whisper.cpp is still transcribing the previous one. But real
    hardware testing (2026-09-05/06) showed the earlier per-turn
    ``self.create_task(...)`` pattern could run two whisper.cpp subprocesses
    concurrently if a short utterance followed quickly — real CPU
    contention risk per R0006. ``SerialTranscriptionQueue`` fixes this by
    serializing STT *execution* only, one utterance at a time, FIFO.
    """

    def __init__(
        self,
        *,
        sample_rate: int,
        transcriber: SpeechTranscriber,
        language: Language,
        on_transcription: Callable[[TranscriptionResult], None] | None,
        on_transcription_error: Callable[[Exception], None] | None,
        half_duplex_gate: HalfDuplexGate | None = None,
        on_utterance_dropped: Callable[[DroppedUtterance], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._sample_rate = sample_rate
        self._buffer = UtteranceBuffer(sample_rate=sample_rate)
        self._language = language
        self._on_transcription_error = on_transcription_error
        # M2.4B.5A: an utterance captured while a previous response is still
        # in flight is DROPPED here — it never reaches the STT queue,
        # `ConversationSession`, or history. Not barge-in (M2.5).
        self._gate = half_duplex_gate
        self._on_utterance_dropped = on_utterance_dropped
        self._dropped_busy = 0
        self._queue = SerialTranscriptionQueue(
            transcriber,
            on_result=on_transcription,
            on_error=self._handle_stt_error,
        )

    @property
    def max_observed_stt_concurrency(self) -> int:
        return self._queue.max_observed_concurrency

    @property
    def stt_queue_depth(self) -> int:
        return self._queue.queue_size

    @property
    def dropped_busy_utterances(self) -> int:
        """How many captured utterances this session were dropped because a
        response was in flight (R0026 / the strict pre-M2.5 half-duplex
        rule)."""
        return self._dropped_busy

    def _handle_stt_error(self, exc: Exception) -> None:
        logger.error(f"nexa.stt: transcription failed: {exc}")
        if self._on_transcription_error is not None:
            self._on_transcription_error(exc)

    def _drop_busy(self, audio: bytes) -> None:
        from datetime import UTC, datetime

        self._dropped_busy += 1
        audio_ms = int(1000 * len(audio) / (self._sample_rate * 2))
        record = DroppedUtterance(
            reason=DROP_BUSY_RESPONSE_IN_FLIGHT,
            at=time.monotonic(),
            at_wall=datetime.now(UTC).isoformat(timespec="milliseconds"),
            audio_ms=audio_ms,
            stt_queue_depth=self._queue.queue_size,
            dropped_count_this_session=self._dropped_busy,
        )
        logger.info(
            f"nexa.voice: {DROP_BUSY_RESPONSE_IN_FLIGHT} — dropped {audio_ms}ms utterance "
            f"captured while a response is in flight (session total {self._dropped_busy}); "
            f"stt_queue_depth={record.stt_queue_depth}"
        )
        if self._on_utterance_dropped is not None:
            self._on_utterance_dropped(record)

    async def setup(self, setup: FrameProcessorSetup) -> None:
        await super().setup(setup)
        self._queue.start(self.create_task)

    async def cleanup(self) -> None:
        await self._queue.shutdown()
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            self._buffer.append_audio(frame.audio)
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._buffer.mark_speech_started()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            audio = self._buffer.mark_speech_stopped()
            if audio:
                # M2.4B.5A: strict pre-M2.5 half-duplex — if NeXa is still
                # answering the previous turn, DROP this utterance
                # explicitly (with telemetry). It is never transcribed,
                # never queued for later, never a conversation turn. This
                # is the belt for the edge where an utterance ends just as
                # a response dispatches; the mic gate normally withholds
                # the audio before it ever reaches here.
                # M2.5B: ``should_drop_busy_utterance`` lets exactly the one
                # confirmed interrupting utterance through (one-shot admit).
                if self._gate is not None and self._gate.should_drop_busy_utterance():
                    self._drop_busy(audio)
                else:
                    try:
                        self._queue.submit(audio, self._language)
                    except SttQueueOverflowError as exc:
                        self._handle_stt_error(exc)
        elif isinstance(frame, (ErrorFrame, EndFrame, CancelFrame)):
            self._buffer.reset()

        await self.push_frame(frame, direction)


class _MicGateFrameProcessor(FrameProcessor):
    """M2.4 half-duplex safety: while NeXa's own TTS audio is playing, drop
    inbound microphone audio so it can never reach VAD -> utterance capture
    -> whisper.cpp -> ``ConversationSession`` and create a self-conversation
    loop (real-hardware failure: the reSpeaker heard NeXa and re-transcribed
    her own answer as new user turns).

    Placed **immediately after** ``transport.input()``, before
    ``VADProcessor`` — the gate happens before any expensive STT work, and
    before VAD can even emit a ``VADUserStartedSpeakingFrame`` for the echo
    (Silero needs ~0.2s of confirmed speech; the gate closes the instant the
    first ``BotStartedSpeakingFrame`` reaches here travelling upstream from
    the output transport, comfortably ahead of any acoustic echo).

    Suppression is driven entirely by :class:`~nexa.voice.gate.HalfDuplexGate`
    — real Pipecat playback frames + response-lifecycle notifications from
    the assistant-speech bridge, never a timer or a fake ``VoiceState``.
    Every non-audio frame (control, lifecycle, the playback frames
    themselves) is forwarded unchanged in both directions; only
    ``InputAudioRawFrame`` is withheld, and only while the gate is closed.

    This is temporary M2.4 behaviour — no barge-in, no interruption of TTS/
    LLM/session. M2.5 replaces it with true full-duplex handling.
    """

    def __init__(self, gate: HalfDuplexGate, **kwargs) -> None:
        super().__init__(**kwargs)
        self._gate = gate
        self._suppressed_frames = 0

    @property
    def suppressed_frame_count(self) -> int:
        """How many microphone audio frames have been withheld this session
        (diagnostics only)."""
        return self._suppressed_frames

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Let the gate see playback-lifecycle frames (they travel upstream
        # from the output transport, so they pass through here) and hard
        # stops.
        self._gate.observe_frame(frame)

        if isinstance(frame, InputAudioRawFrame) and self._gate.mic_suppressed:
            self._suppressed_frames += 1
            return  # withhold — NeXa is speaking; this is (or may be) her echo

        await self.push_frame(frame, direction)


class VoiceRuntime:
    """Owns the Pipecat pipeline: local audio in/out + Silero VAD (M2.1),
    plus optional local whisper.cpp STT (M2.2).

    No LLM, no ConversationSession — those live in `nexa.conversation` /
    `nexa.voice_conversation`. No **barge-in**: that is M2.5 (ADR-0003).
    ``state_machine`` is the one thing calling code should observe for
    voice-activity state; it is real NeXa-owned state, independent of
    Pipecat's own frame/lifecycle model. STT is entirely optional: omitting
    ``transcriber`` reproduces exact M2.1 pipeline behavior.

    ``extra_output_stages`` (M2.4) lets a caller insert additional, already-
    built Pipecat ``FrameProcessor``s (e.g. a TTS bridge + TTS service)
    just before the output transport, without ``nexa.voice`` ever needing
    to import anything TTS- or conversation-shaped itself — it only ever
    sees the generic Pipecat ``FrameProcessor`` type, exactly like
    ``transcriber`` is accepted as the generic ``SpeechTranscriber``
    protocol rather than a concrete whisper.cpp import.

    ``half_duplex_gate`` (M2.4, temporary until M2.5) — a
    :class:`~nexa.voice.gate.HalfDuplexGate`. When given, a
    ``_MicGateFrameProcessor`` is inserted right after ``transport.input()``
    that withholds microphone audio while NeXa's own TTS is playing, so the
    reSpeaker can't feed NeXa's voice back into STT. This is NOT barge-in:
    nothing is cancelled, the user simply cannot interrupt while NeXa
    speaks. Omitting it leaves the M2.1/M2.2 pipeline byte-for-byte
    unchanged.
    """

    def __init__(
        self,
        config: LocalAudioConfig | None = None,
        *,
        vad_params: VADParams | None = None,
        on_event: Callable[[VoiceEvent], None] | None = None,
        transcriber: SpeechTranscriber | None = None,
        language: Language | None = None,
        on_transcription: Callable[[TranscriptionResult], None] | None = None,
        on_transcription_error: Callable[[Exception], None] | None = None,
        on_utterance_dropped: Callable[[DroppedUtterance], None] | None = None,
        extra_output_stages: list[FrameProcessor] | None = None,
        half_duplex_gate: HalfDuplexGate | None = None,
        bargein_controller: FrameProcessor | None = None,
    ) -> None:
        self.config = config or LocalAudioConfig()
        self.vad_params = vad_params or DEFAULT_VAD_PARAMS
        self.state_machine = VoiceStateMachine(on_event=on_event)
        self._runner: WorkerRunner | None = None
        if transcriber is not None and language is None:
            raise ValueError(
                "language is required when a transcriber is provided — "
                "auto-detect is never used (ADR-0003 D5)"
            )
        self._transcriber = transcriber
        self._language = language
        self._on_transcription = on_transcription
        self._on_transcription_error = on_transcription_error
        self._on_utterance_dropped = on_utterance_dropped
        self._capture_processor: _UtteranceCaptureFrameProcessor | None = None
        self._extra_output_stages = extra_output_stages or []
        # M2.4 half-duplex safety gate (optional). When present, a
        # _MicGateFrameProcessor is inserted right after transport.input() to
        # withhold mic audio while NeXa's TTS is playing. Omitting it leaves
        # the exact M2.1/M2.2 pipeline unchanged.
        self._half_duplex_gate = half_duplex_gate
        self._mic_gate_processor: _MicGateFrameProcessor | None = None
        # M2.5B — optional NeXa BargeInController, inserted right after
        # VADProcessor (sees VAD frames first) and before utterance capture
        # (so a confirmed interruption is decided before any STT/conversation
        # work). Omitting it (the default) leaves the M2.1/M2.4 pipeline
        # byte-for-byte unchanged.
        self._bargein_controller = bargein_controller

    @property
    def max_observed_stt_concurrency(self) -> int | None:
        """Peak number of simultaneous whisper.cpp transcriptions observed
        this session. Must never exceed 1. ``None`` if no transcriber was
        configured, or the pipeline hasn't been built yet."""
        if self._capture_processor is None:
            return None
        return self._capture_processor.max_observed_stt_concurrency

    @property
    def suppressed_mic_frames(self) -> int | None:
        """How many microphone audio frames the M2.4 half-duplex gate has
        withheld this session (because NeXa was speaking). ``None`` if no
        gate was configured, or the pipeline hasn't been built yet."""
        if self._mic_gate_processor is None:
            return None
        return self._mic_gate_processor.suppressed_frame_count

    @property
    def stt_queue_depth(self) -> int | None:
        """Current `SerialTranscriptionQueue` depth (pending utterances).
        ``None`` before the pipeline is built / with no transcriber."""
        if self._capture_processor is None:
            return None
        return self._capture_processor.stt_queue_depth

    @property
    def dropped_busy_utterances(self) -> int | None:
        """Utterances dropped at capture this session because a response was
        in flight (R0026). ``None`` if no transcriber was configured."""
        if self._capture_processor is None:
            return None
        return self._capture_processor.dropped_busy_utterances

    def _build_pipeline(self) -> Pipeline:
        pa = pyaudio.PyAudio()
        input_index = find_device_index(pa, self.config.input_device_name, require_input=True)
        output_index = find_device_index(pa, self.config.output_device_name, require_output=True)
        logger.info(
            f"nexa.voice: input device index={input_index}, output device index={output_index} "
            f"(sample_rate={self.config.sample_rate}, channels={self.config.channels})"
        )

        transport = LocalAudioTransport(
            LocalAudioTransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=self.config.sample_rate,
                audio_out_sample_rate=self.config.sample_rate,
                audio_in_channels=self.config.channels,
                audio_out_channels=self.config.channels,
                input_device_index=input_index,
                output_device_index=output_index,
            )
        )

        vad_analyzer = SileroVADAnalyzer(
            sample_rate=self.config.sample_rate, params=self.vad_params
        )
        vad_processor = VADProcessor(vad_analyzer=vad_analyzer)
        state_processor = _VoiceStateFrameProcessor(self.state_machine)

        stages: list[FrameProcessor] = [transport.input()]
        if self._half_duplex_gate is not None:
            self._mic_gate_processor = _MicGateFrameProcessor(self._half_duplex_gate)
            stages.append(self._mic_gate_processor)
        stages.append(vad_processor)
        if self._bargein_controller is not None:
            stages.append(self._bargein_controller)
        if self._transcriber is not None:
            self._capture_processor = _UtteranceCaptureFrameProcessor(
                sample_rate=self.config.sample_rate,
                transcriber=self._transcriber,
                language=self._language,
                on_transcription=self._on_transcription,
                on_transcription_error=self._on_transcription_error,
                half_duplex_gate=self._half_duplex_gate,
                on_utterance_dropped=self._on_utterance_dropped,
            )
            stages.append(self._capture_processor)
        stages.append(state_processor)
        stages.extend(self._extra_output_stages)
        stages.append(transport.output())

        return Pipeline(stages)

    async def run(self) -> None:
        """Run the voice pipeline until stopped (Ctrl+C / SIGINT by default).

        Blocks for the lifetime of the session — the caller observes
        ``self.state_machine`` (e.g. via its ``on_event`` callback) for
        semantic voice events.
        """
        pipeline = self._build_pipeline()
        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=self.config.sample_rate,
                audio_out_sample_rate=self.config.sample_rate,
            ),
            enable_rtvi=False,
            # A long-running listening session with natural silence gaps
            # should not be auto-cancelled; M2.1 has no bot speech to reset
            # the idle timer, so disable it rather than pick an arbitrary
            # number without evidence.
            idle_timeout_secs=None,
        )
        self._runner = WorkerRunner()
        await self._runner.add_workers(worker)
        await self._runner.run()
