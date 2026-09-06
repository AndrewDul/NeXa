"""Captures exactly one utterance's raw PCM audio per LISTENING -> ... ->
END_OF_TURN cycle, including the audio immediately preceding confirmed
speech-start (M2.2 pre-roll requirement).

Why pre-roll is required at all: with `start_secs=0.2`
(`nexa.voice.runtime.DEFAULT_VAD_PARAMS`), Silero only confirms
`USER_SPEAKING` after ~0.2s of accumulated speech frames — audio arriving
before that confirmation is real speech, not noise, and would otherwise be
lost, truncating the first word/consonant. `UtteranceBuffer` is pure Python
with no Pipecat dependency so it can be unit tested deterministically without
audio hardware or a running pipeline.

PRE_ROLL_MS justification (both computed, not guessed):
  - Theoretical minimum: Silero confirms speech after
    `start_secs / (512/16000)` = 0.2 / 0.032 = 6.25 -> 6 frames of 512
    samples @ 16kHz = 6 * 32ms = 192ms of audio must already have arrived
    before `VADUserStartedSpeakingFrame` fires.
  - Empirical measurement: running the real `SileroVADAnalyzer` against 3
    real R0006 speech fixtures with manually-verified true onset (RMS
    profile inspection) measured actual confirmation delay at 352ms, 352ms,
    and 288ms — noticeably larger than the theoretical minimum alone.
  - Chosen value covers the empirical worst case with a safety margin
    against jitter, rounded to a clean number.
"""

from __future__ import annotations

from collections import deque

PRE_ROLL_MS = 500


class UtteranceBuffer:
    """Pure buffer: ring pre-roll while not capturing, linear buffer of
    everything (pre-roll + subsequent audio) while capturing. No Pipecat
    dependency, no knowledge of VAD frames — driven purely by
    `append_audio`/`mark_speech_started`/`mark_speech_stopped` calls."""

    def __init__(
        self,
        *,
        sample_rate: int,
        pre_roll_ms: int = PRE_ROLL_MS,
        bytes_per_sample: int = 2,
        channels: int = 1,
    ) -> None:
        self._pre_roll_bytes = int(sample_rate * (pre_roll_ms / 1000) * bytes_per_sample * channels)
        self._ring: deque[bytes] = deque()
        self._ring_bytes = 0
        self._capturing = False
        self._utterance_chunks: list[bytes] = []

    @property
    def is_capturing(self) -> bool:
        return self._capturing

    def append_audio(self, chunk: bytes) -> None:
        """Feed one chunk of raw PCM audio. Routed to the linear utterance
        buffer while capturing, or the pre-roll ring otherwise."""
        if not chunk:
            return
        if self._capturing:
            self._utterance_chunks.append(chunk)
            return
        self._ring.append(chunk)
        self._ring_bytes += len(chunk)
        while self._ring_bytes > self._pre_roll_bytes and len(self._ring) > 1:
            dropped = self._ring.popleft()
            self._ring_bytes -= len(dropped)

    def mark_speech_started(self) -> None:
        """Called on confirmed `VADUserStartedSpeakingFrame`. Seeds the
        utterance with whatever pre-roll audio is currently held, then
        switches to linear capture. Idempotent against duplicate events."""
        if self._capturing:
            return
        self._capturing = True
        self._utterance_chunks = list(self._ring)
        self._ring.clear()
        self._ring_bytes = 0

    def mark_speech_stopped(self) -> bytes:
        """Called on confirmed `VADUserStoppedSpeakingFrame` (END_OF_TURN).
        Returns the complete utterance (pre-roll + captured speech) and
        resets to LISTENING state. Idempotent: returns `b""` if not
        currently capturing, so a duplicate/spurious event cannot leak a
        previous turn's audio into the next."""
        if not self._capturing:
            return b""
        audio = b"".join(self._utterance_chunks)
        self._utterance_chunks = []
        self._capturing = False
        return audio

    def reset(self) -> None:
        """Hard reset — used on error/shutdown so no audio can leak across
        an ERROR -> LISTENING transition."""
        self._ring.clear()
        self._ring_bytes = 0
        self._utterance_chunks = []
        self._capturing = False
