"""M2.4B.3.2 — NeXa-owned short-reply speech continuity controller.

Sits between ``NexaSpeechPlanner`` and Pipecat's ``PiperHttpTTSService``:

    AssistantSpeechBridge → NexaSpeechPlanner → NexaSpeechContinuityController
        → PiperHttpTTSService → TtsStatusObserver → LocalAudioOutputTransport

**Scope (R0018): this is intentionally small.** R0018 measured
``realtime_text_ratio ≈ 0.53`` — ``gemma4:e4b`` produces spoken-equivalent
text at ~53 % of the rate ``pl_PL-gosia-medium`` consumes it. A runtime
buffer therefore **cannot** make an arbitrarily long reply continuous. This
controller only:

* releases phrase 0 immediately — never a prebuffer, never worse first-audio
  latency;
* for phrases 1..N, releases immediately when the estimated audio reserve is
  low, and holds an already-complete phrase *briefly* (bounded, gated) only
  while the reserve is healthy, to avoid needless TTS-context churn;
* never waits to grow a larger batch, never inserts silence, never changes
  speech rate, never creates / splits / rewrites text.

It sees only the already-normalised ``AggregatedTextFrame`` phrases from the
planner. ``NexaSpeechPlanner`` remains the sole TTS-text normalisation
authority; ``ConversationSession`` / history are untouched; it constructs no
session / model client / TTS service / audio transport.

**Buffer signal — ESTIMATE, not exact.** The controller cannot see the
transport's own audio queue without invasive changes, so it estimates the
reserve as ``Σ produced-audio-seconds − (now − first-audio-at)``, fed by the
downstream ``TtsStatusObserver``'s real ``TTSAudioRawFrame`` byte counts (the
same quantity R0013's ``BufferEstimate`` validated against real
``BotStoppedSpeaking``). Every field and log names it an estimate; the
offline replay and hardware A/B re-check it in this exact path.
"""

from __future__ import annotations

from asyncio import CancelledError, Task, current_task
from asyncio import sleep as _sleep
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic as _now_s

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# --------------------------------------------------------------------------- #
# Tunables — CANDIDATE values (R0018). Not product truth; constructor-
# overridable; operator A/B in the B.3.2 hardware test picks the target.
# --------------------------------------------------------------------------- #

#: Estimated audio reserve (seconds) at/above which the buffer is "healthy"
#: and a ready phrase 1..N may be held briefly. Candidate A/B: 1.5 / 2.0 / 2.5.
DEFAULT_CONTINUITY_TARGET_S = 2.0
#: Below this the buffer is "near empty" — release the next phrase with the
#: highest urgency (distinguished only for metrics/logging).
DEFAULT_CONTINUITY_NEAR_EMPTY_S = 0.5
#: Absolute ceiling on any single hold. A hold's planned duration is
#: ``min(reserve_estimate − target, this)`` — so a hold is always brief and
#: always ends before the reserve is estimated to drop below ``target``.
#: Kept small deliberately: a hold is only ever a churn-avoidance nicety
#: (R0018 proved a buffer cannot fix the rate deficit), so it must never add
#: perceptible tail latency.
DEFAULT_CONTINUITY_MAX_HOLD_S = 0.4

# 16-bit PCM, one sample per channel per frame (Pipecat local audio + Piper).
_BYTES_PER_SAMPLE = 2


def _audio_bytes_to_seconds(n: int, rate: int | None, channels: int | None) -> float:
    """Mirror of ``nexa.voice_tts.metrics.audio_bytes_to_seconds`` — kept
    inline so this module has no heavy import. Guarded: never raises."""
    if not rate or rate <= 0:
        return 0.0
    ch = channels if channels and channels > 0 else 1
    denom = rate * _BYTES_PER_SAMPLE * ch
    if denom <= 0 or n < 0:
        return 0.0
    return n / denom


class ReleaseReason:
    """Why a phrase was released to the TTS service (metrics/logging only)."""

    FIRST_PHRASE = "FIRST_PHRASE"
    BUFFER_LOW = "BUFFER_LOW"
    BUFFER_NEAR_EMPTY = "BUFFER_NEAR_EMPTY"
    TURN_COMPLETE = "TURN_COMPLETE"
    # a later phrase arrived; flush the held one first, in order
    NEXT_PHRASE = "NEXT_PHRASE"
    # the bounded hold deadline fired (reserve estimated to have reached target)
    HOLD_EXPIRED = "HOLD_EXPIRED"
    # interruption / turn restart
    RESET = "RESET"
    CONTROLLER_DISABLED = "CONTROLLER_DISABLED"

#: ``decide_release`` returns this (with ``planned_hold_s > 0``) to mean
#: "hold, don't release yet". The *actual* release reason is then one of
#: ``HOLD_EXPIRED`` / ``NEXT_PHRASE`` / ``TURN_COMPLETE`` / ``RESET``.
PLAN_HOLD = "HOLD_PLANNED"


@dataclass
class ControllerRelease:
    """One phrase's trip through the controller — passed to ``on_release``
    for the B.1 metrics. All times are ``_now_s()`` seconds."""

    phrase_index: int
    text_len: int
    received_at: float
    released_at: float
    reserve_at_receive_s: float | None    # ESTIMATE; None before any audio
    reserve_at_release_s: float | None    # ESTIMATE
    reason: str

    @property
    def hold_s(self) -> float:
        return max(0.0, self.released_at - self.received_at)


def decide_release(
    reserve_estimate_s: float | None,
    *,
    is_first: bool,
    target_s: float,
    near_empty_s: float,
    max_hold_s: float,
) -> tuple[str, float]:
    """Pure policy. Returns ``(reason, planned_hold_s)``.

    ``planned_hold_s == 0`` → release now. ``> 0`` → hold that long (or until
    a later phrase / turn end, whichever comes first). Phrase 0 is always
    immediate. A phrase is held only when the estimate is available *and* at
    or above ``target_s``; the hold is ``min(reserve − target, max_hold_s)``
    so it always ends before the reserve is estimated to reach ``target_s``.
    """
    if is_first:
        return ReleaseReason.FIRST_PHRASE, 0.0
    if reserve_estimate_s is None:
        return ReleaseReason.BUFFER_LOW, 0.0
    if reserve_estimate_s < near_empty_s:
        return ReleaseReason.BUFFER_NEAR_EMPTY, 0.0
    if reserve_estimate_s < target_s:
        return ReleaseReason.BUFFER_LOW, 0.0
    hold = min(reserve_estimate_s - target_s, max_hold_s)
    if hold <= 0.0:
        return ReleaseReason.BUFFER_LOW, 0.0
    return PLAN_HOLD, hold


class NexaSpeechContinuityController(FrameProcessor):
    """See module docstring. Small, bounded, estimate-driven; a no-op
    pass-through when ``enabled=False`` or when no audio signal is wired."""

    def __init__(
        self,
        *,
        target_reserve_s: float = DEFAULT_CONTINUITY_TARGET_S,
        near_empty_s: float = DEFAULT_CONTINUITY_NEAR_EMPTY_S,
        max_hold_s: float = DEFAULT_CONTINUITY_MAX_HOLD_S,
        enabled: bool = True,
        on_release: Callable[[ControllerRelease], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._target_s = float(target_reserve_s)
        self._near_empty_s = float(near_empty_s)
        self._max_hold_s = float(max_hold_s)
        self._enabled = enabled
        self._on_release = on_release
        self._reset_turn()

    # -- per-turn state ------------------------------------------------------ #

    def _reset_turn(self) -> None:
        self._phrase_index = 0
        self._produced_audio_s = 0.0
        self._first_audio_at: float | None = None
        self._held: AggregatedTextFrame | None = None
        self._held_received_at: float | None = None
        self._held_reserve_at_receive: float | None = None
        self._hold_task: Task | None = None

    # -- audio signal (wired from the downstream TtsStatusObserver) -------- #

    def note_tts_audio(
        self, nbytes: int, sample_rate: int | None, num_channels: int | None
    ) -> None:
        """Real ``TTSAudioRawFrame`` byte count observed downstream. Pure
        counter update — safe to call from any context. The reserve is an
        ESTIMATE: produced audio seconds minus wall time since first audio."""
        secs = _audio_bytes_to_seconds(nbytes, sample_rate, num_channels)
        if secs <= 0:
            return
        if self._first_audio_at is None:
            self._first_audio_at = _now_s()
        self._produced_audio_s += secs

    def reserve_estimate_s(self, now: float | None = None) -> float | None:
        """ESTIMATE of audio-seconds queued ahead of the playhead. ``None``
        until the first audio frame is seen."""
        if self._first_audio_at is None:
            return None
        return self._produced_audio_s - ((now or _now_s()) - self._first_audio_at)

    # -- release path ---------------------------------------------------- #

    async def _emit(self, frame: AggregatedTextFrame, *, phrase_index: int,
                    received_at: float, reserve_at_receive: float | None, reason: str) -> None:
        released_at = _now_s()
        await self.push_frame(frame)  # text is NEVER touched
        if self._on_release is not None:
            try:
                self._on_release(ControllerRelease(
                    phrase_index=phrase_index,
                    text_len=len(getattr(frame, "text", "") or ""),
                    received_at=received_at,
                    released_at=released_at,
                    reserve_at_receive_s=reserve_at_receive,
                    reserve_at_release_s=self.reserve_estimate_s(released_at),
                    reason=reason,
                ))
            except Exception:  # pragma: no cover - a metrics sink must never break speech
                logger.exception("nexa.voice_tts continuity: on_release callback failed")

    async def _release_held(self, reason: str) -> None:
        if self._held is None:
            return
        frame, received_at, res_recv = (
            self._held, self._held_received_at, self._held_reserve_at_receive,
        )
        self._held = None
        self._held_received_at = None
        self._held_reserve_at_receive = None
        task, self._hold_task = self._hold_task, None
        # Never ask the task manager to cancel the task we are *running
        # inside* — that happens when the bounded hold expires naturally and
        # ``_hold_then_release`` calls this method. Pipecat's task manager
        # logs "ignoring attempt to cancel the running task" for that and
        # does nothing; the guard makes the intent explicit and keeps the
        # log clean. (``_hold_then_release`` also clears ``_hold_task``
        # before calling here, so ``task`` is normally already ``None`` on
        # that path — this is belt-and-braces.)
        if task is not None and not task.done() and task is not current_task():
            try:
                await self.cancel_task(task)
            except Exception:  # pragma: no cover
                pass
        await self._emit(frame, phrase_index=self._phrase_index - 1,
                         received_at=received_at or _now_s(),
                         reserve_at_receive=res_recv, reason=reason)

    async def _discard_held(self) -> None:
        """M2.5B — drop the held phrase WITHOUT speaking it. Used only on an
        ``InterruptionFrame``: the reply is being killed, so a phrase still
        in the brief continuity hold is stale and must never reach TTS."""
        if self._held is None and self._hold_task is None:
            return
        task, self._hold_task = self._hold_task, None
        self._held = None
        self._held_received_at = None
        self._held_reserve_at_receive = None
        if task is not None and not task.done() and task is not current_task():
            try:
                await self.cancel_task(task)
            except Exception:  # pragma: no cover
                pass

    async def _hold_then_release(self, delay: float) -> None:
        # The single, bounded, gated wait in this module. NOT pacing: it is a
        # release deadline computed as "when the estimated reserve reaches
        # target" (capped at max_hold_s), it inserts no silence, and it is
        # cancelled the moment a later phrase / turn-end releases the phrase.
        try:
            await _sleep(delay)
        except CancelledError:  # pragma: no cover
            return
        # We *are* ``self._hold_task``. Drop the reference before releasing
        # so ``_release_held`` does not try to cancel the running task
        # (Pipecat's task manager logs a warning and ignores that).
        self._hold_task = None
        await self._release_held(ReleaseReason.HOLD_EXPIRED)

    # -- Pipecat entry point ------------------------------------------- #

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            await self._release_held(ReleaseReason.RESET)
            self._reset_turn()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, AggregatedTextFrame):
            # never reorder / stack: a newly-arrived phrase flushes the held one
            if self._held is not None:
                await self._release_held(ReleaseReason.NEXT_PHRASE)
            idx = self._phrase_index
            self._phrase_index += 1
            now = _now_s()
            reserve = self.reserve_estimate_s(now)

            if not self._enabled:
                await self._emit(frame, phrase_index=idx, received_at=now,
                                 reserve_at_receive=reserve,
                                 reason=ReleaseReason.CONTROLLER_DISABLED)
                return

            reason, hold_s = decide_release(
                reserve, is_first=(idx == 0), target_s=self._target_s,
                near_empty_s=self._near_empty_s, max_hold_s=self._max_hold_s,
            )
            if hold_s <= 0.0:
                await self._emit(frame, phrase_index=idx, received_at=now,
                                 reserve_at_receive=reserve, reason=reason)
                return
            # brief, bounded hold — NOT pacing: it ends exactly when the
            # estimated reserve would reach `target`, or sooner (next phrase /
            # turn end), and never inserts silence.
            self._held = frame
            self._held_received_at = now
            self._held_reserve_at_receive = reserve
            self._hold_task = self.create_task(self._hold_then_release(hold_s))
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            await self._release_held(ReleaseReason.TURN_COMPLETE)
            await self.push_frame(frame, direction)
            self._reset_turn()
            return

        if isinstance(frame, InterruptionFrame):
            # M2.5B — barge-in: DISCARD the held phrase (never speak stale
            # text after an interrupt), then reset and forward.
            await self._discard_held()
            self._reset_turn()
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
