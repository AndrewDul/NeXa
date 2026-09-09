"""M2.5B — ``AecReferenceFeeder``: the production XVF3800 AEC far-end
reference tee.

R0028 / M2.5A proved a hot microphone during NeXa's own reply is only safe
when the reSpeaker XVF3800 is fed its AEC far-end reference — the identical
TTS PCM, also sent to ``plug:respeaker`` (its USB *playback* endpoint,
which NeXa otherwise leaves silent). Operator M2.5A.2: with the reference
fed, QUIET_AEC false-VAD 0 and near-field voice still detected 3/3.

This processor is the *smallest robust* production tee:

* placed in the output stages **immediately after** the Piper TTS service,
  so it sees every ``TTSAudioRawFrame`` the audible path plays;
* **one persistent** ``aplay`` reading raw PCM on stdin — never a process
  per sentence/segment (R0028 constraint);
* a small bounded queue + writer task: an AEC reference tolerates a dropped
  chunk far better than back-pressuring the pipeline, so on overflow a
  chunk is dropped and counted, never blocked;
* health is reported to :class:`~nexa.voice.aec.AecReferenceHealth` — the
  single source of truth the ``BargeInController`` and the mic gate consult.
  If the feed cannot start or dies, ``barge_in_safe`` goes False and the
  mic falls back to R0026 whole-response suppression — loud, never silent.

Every frame is forwarded downstream unchanged. When the pipeline is built
without barge-in this processor is simply not inserted.

The ALSA sink is injectable (``sink_factory``) so the frame-handling and
health logic is deterministically testable with no audio device.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from nexa.voice.aec import AecReferenceHealth

#: XVF3800 USB playback endpoint = its AEC far-end reference input.
AEC_REFERENCE_PCM = "plug:respeaker"
#: Max queued PCM chunks before the oldest is dropped (a few hundred ms).
DEFAULT_MAX_QUEUED_CHUNKS = 24


class _PcmSink:
    """A raw-PCM sink. The default implementation is one persistent
    ``aplay`` on the AEC-reference device; tests substitute a fake."""

    def __init__(self, *, sample_rate: int, channels: int, device: str) -> None:
        self._proc = subprocess.Popen(
            ["aplay", "-q", "-t", "raw", "-f", "S16_LE",
             "-r", str(sample_rate), "-c", str(channels), "-D", device, "-"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None and self._proc.stdin is not None

    def write(self, pcm: bytes) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(pcm)
        self._proc.stdin.flush()

    def close(self) -> None:
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except OSError:
            pass
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()


class AecReferenceFeeder(FrameProcessor):
    def __init__(
        self,
        *,
        aec_health: AecReferenceHealth,
        sample_rate: int,
        channels: int = 1,
        device: str = AEC_REFERENCE_PCM,
        max_queued_chunks: int = DEFAULT_MAX_QUEUED_CHUNKS,
        sink_factory: Callable[[], _PcmSink] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._health = aec_health
        self._sample_rate = sample_rate
        self._channels = channels
        self._device = device
        self._sink_factory = sink_factory or (
            lambda: _PcmSink(sample_rate=sample_rate, channels=channels, device=device)
        )
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=max_queued_chunks
        )
        self._sink: _PcmSink | None = None
        self._writer_task: asyncio.Task | None = None
        # telemetry
        self.frames_mirrored = 0
        self.bytes_mirrored = 0
        self.chunks_dropped = 0
        self.respawns = 0

    # -- lifecycle ----------------------------------------------------- #
    async def setup(self, setup) -> None:
        await super().setup(setup)
        self.begin()

    async def cleanup(self) -> None:
        await self.end()
        await super().cleanup()

    def begin(self) -> None:
        """Start the reference feed. Called from ``setup`` in production;
        callable directly in tests (no Pipecat task manager needed)."""
        self._start_sink(initial=True)
        self._writer_task = self.create_task(self._run_writer())

    async def end(self) -> None:
        if self._writer_task is not None:
            await self._queue.put(None)  # stop sentinel
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
            self._writer_task = None
        if self._sink is not None:
            self._sink.close()
            self._sink = None
        self._health.mark_stopped()

    def _start_sink(self, *, initial: bool) -> None:
        try:
            self._sink = self._sink_factory()
        except Exception:
            logger.exception(
                "nexa.voice_tts: could not start the XVF3800 AEC reference feed "
                f"({self._device}) — barge-in will stay in the R0026 safe mode"
            )
            self._sink = None
            self._health.mark_failed()
            return
        if not initial:
            self.respawns += 1
        if self._sink.alive:
            self._health.mark_started()
            logger.info(
                f"nexa.voice_tts: XVF3800 AEC reference feed active on {self._device}"
            )
        else:
            self._health.mark_failed()

    # -- frame path -------------------------------------------------- #
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame) and frame.audio:
            self._enqueue(frame.audio)
        elif isinstance(frame, (EndFrame, CancelFrame)):
            pass  # cleanup() handles teardown
        await self.push_frame(frame, direction)

    def _enqueue(self, pcm: bytes) -> None:
        try:
            self._queue.put_nowait(pcm)
        except asyncio.QueueFull:
            # drop the oldest, keep the newest (freshest reference matters)
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(pcm)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
            self.chunks_dropped += 1

    async def _run_writer(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            pcm = await self._queue.get()
            if pcm is None:
                return
            if self._sink is None or not self._sink.alive:
                self._health.mark_failed()
                self._start_sink(initial=False)
                if self._sink is None or not self._sink.alive:
                    continue  # stay in safe mode; drop this chunk
            try:
                await loop.run_in_executor(None, self._sink.write, pcm)
                self.frames_mirrored += 1
                self.bytes_mirrored += len(pcm)
            except (BrokenPipeError, OSError, ValueError):
                logger.warning(
                    "nexa.voice_tts: AEC reference write failed — feed dropped"
                )
                self._health.mark_failed()
                if self._sink is not None:
                    self._sink.close()
                self._sink = None
