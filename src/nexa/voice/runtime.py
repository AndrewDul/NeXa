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

from collections.abc import Callable

import pyaudio
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StartFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.workers.runner import WorkerRunner

from .config import LocalAudioConfig
from .device import find_device_index
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


class VoiceRuntime:
    """Owns the M2.1 Pipecat pipeline: local audio in/out + Silero VAD.

    No STT, no LLM, no TTS, no ConversationSession — those are M2.2+
    (ADR-0003). ``state_machine`` is the one thing calling code should
    observe; it is real NeXa-owned state, independent of Pipecat's own
    frame/lifecycle model.
    """

    def __init__(
        self,
        config: LocalAudioConfig | None = None,
        *,
        vad_params: VADParams | None = None,
        on_event: Callable[[VoiceEvent], None] | None = None,
    ) -> None:
        self.config = config or LocalAudioConfig()
        self.vad_params = vad_params or DEFAULT_VAD_PARAMS
        self.state_machine = VoiceStateMachine(on_event=on_event)
        self._runner: WorkerRunner | None = None

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

        return Pipeline(
            [
                transport.input(),
                vad_processor,
                state_processor,
                transport.output(),
            ]
        )

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
