"""M2.5B — ``BargeInController``: the one NeXa-owned interruption processor.

Placed **immediately after ``VADProcessor``** (so it sees
``VADUserStartedSpeakingFrame`` / ``VADUserStoppedSpeakingFrame`` first)
and **before** utterance capture, so it decides whether a busy-period
utterance is the one admitted interruption before any STT / conversation
work is enqueued.

It owns: the :class:`~nexa.voice.interruption.InterruptionStateMachine`
(single-candidate invariant, monotonic ``response_id``), the confirm-hold
timer, and interruption telemetry. It reads
:class:`~nexa.voice.aec.AecReferenceHealth` — an interruption is admitted
**only** while the XVF3800 AEC far-end reference is confirmed running
(R0028 / M2.5A.1). On confirmation it calls Pipecat's
``broadcast_interruption()`` (media-queue + output-audio cancellation is
Pipecat's job) and one injected ``on_confirmed`` hook (which cancels the
LLM turn and captures the interrupting utterance — the adapter's job).

Pipecat owns ``InterruptionFrame`` propagation and audio cancellation.
NeXa keeps ``ConversationSession`` / history / model / language authority.
No ``LLMResponseAggregator`` is introduced.

When the pipeline is built with ``bargein_enabled=False`` (the default,
R0026 behaviour) this processor is simply **not inserted** — the M2.1/M2.4
pipeline is byte-for-byte unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    InterruptionFrame,
    StartFrame,
    UserSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .aec import AecReferenceHealth
from .interruption import (
    DEFAULT_CONFIRM_HOLD_SECS,
    InterruptionEvent,
    InterruptionState,
    InterruptionStateMachine,
)

#: M2.5B.1 — after a confirmed barge-in, how long the interruption-capture
#: phase waits with **no VAD activity of any kind** (segment start, segment
#: end, or a ``UserSpeakingFrame``) before it declares the interruption
#: utterance settled. Long enough to bridge a mid-sentence pause
#: ("Czekaj." <pause> "tylko jak powstaje."), which VAD's ``stop_secs=1.0``
#: has already turned into two segments. In the common single-segment case
#: this overlaps the segment's STT decode entirely and adds ~0 latency (the
#: adapter still waits for STT).
#:
#: M2.5B.3 — the settle timer is armed at *confirm* time and driven by a
#: ``_last_vad_activity`` timestamp updated on every VAD frame, not by a
#: segment-END alone. A single lost/flushed ``VADUserStoppedSpeakingFrame``
#: (which ``broadcast_interruption()`` can drop from a lagged frame queue on
#: the Pi) therefore no longer prevents the phase from ever settling — the
#: root cause of the live 12 s-cap / 15 s-timeout hits (R0029 §M2.5B.3).
DEFAULT_INTERRUPT_SETTLE_SECS = 1.2
#: Hard cap on the capture phase — a safety guard that must never fire in
#: ordinary use.
DEFAULT_MAX_CAPTURE_SECS = 12.0


@dataclass
class BargeInTelemetry:
    """Live counters for the probe + shutdown summary. Never persists audio."""

    response_started: int = 0
    response_finished: int = 0
    candidate_started: int = 0
    candidate_rejected: int = 0
    interrupt_confirmed: int = 0
    ignored_speech_starts: int = 0
    interrupt_segments: int = 0        # extra VAD segments coalesced into interruptions
    interrupt_segments_ended: int = 0
    unsafe_speech_ignored: int = 0  # speech during a reply while AEC ref was down
    active_response_id: int | None = None
    state: str = InterruptionState.IDLE.value
    aec_reference_active: bool = False
    aec_reference_failure_count: int = 0
    last_interrupt_reason: str | None = None

    def snapshot(self) -> dict:
        return {**self.__dict__}


@dataclass
class InterruptContext:
    """Handed to the ``on_confirmed`` hook."""

    invalidated_response_id: int | None
    reason: str


class BargeInController(FrameProcessor):
    def __init__(
        self,
        *,
        aec_health: AecReferenceHealth,
        on_confirmed: Callable[[InterruptContext], None],
        on_candidate: Callable[[int | None], None] | None = None,
        on_candidate_rejected: Callable[[], None] | None = None,
        on_interrupt_segment_started: Callable[[], None] | None = None,
        on_interrupt_segment_ended: Callable[[], None] | None = None,
        on_interrupt_capture_settled: Callable[[], None] | None = None,
        confirm_hold_secs: float = DEFAULT_CONFIRM_HOLD_SECS,
        settle_secs: float = DEFAULT_INTERRUPT_SETTLE_SECS,
        max_capture_secs: float = DEFAULT_MAX_CAPTURE_SECS,
        time_source: Callable[[], float] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._sm = InterruptionStateMachine(confirm_hold_secs=confirm_hold_secs)
        self._aec = aec_health
        self._time_source = time_source
        self._on_confirmed = on_confirmed
        self._on_candidate = on_candidate
        self._on_candidate_rejected = on_candidate_rejected
        self._on_seg_started = on_interrupt_segment_started
        self._on_seg_ended = on_interrupt_segment_ended
        self._on_capture_settled = on_interrupt_capture_settled
        self._settle_secs = settle_secs
        self._max_capture_secs = max_capture_secs
        self._confirm_task: asyncio.Task | None = None
        self._settle_task: asyncio.Task | None = None
        self._capture_deadline_task: asyncio.Task | None = None
        self._confirming = False  # re-entrancy guard for _do_confirm
        # M2.5B.3 — interruption-capture lifecycle bookkeeping. ``_capture_id``
        # makes one interruption traceable end-to-end in the logs;
        # ``_last_vad_activity`` drives the settle timer (see the module
        # constant docstring).
        self._capture_id = 0
        self._last_vad_activity = 0.0
        self.telemetry = BargeInTelemetry()

    # -- M2.5B.3 lifecycle trace --------------------------------------- #
    def _trace(self, event: str, **fields: object) -> None:
        """One concise, deterministic line per interruption-capture lifecycle
        event, keyed by ``capture_id`` so a single interruption is traceable.
        Driven by the same calls the deterministic tests drive."""
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        logger.info(
            f"nexa.voice.bargein: capture[{self._capture_id}] {event}"
            + (f" {extra}" if extra else "")
        )

    def _note_vad_activity(self) -> None:
        """M2.5B.3 — any VAD frame during INTERRUPTING pushes the settle
        deadline out. The settle timer is a single long-lived task; it
        re-checks ``_last_vad_activity`` rather than being cancelled/rearmed
        per frame."""
        self._last_vad_activity = self._now()
        if self._sm.state == InterruptionState.INTERRUPTING and (
            self._settle_task is None or self._settle_task.done()
        ):
            self._arm_settle()

    def set_capture_hooks(
        self,
        on_segment_started: Callable[[], None] | None,
        on_segment_ended: Callable[[], None] | None,
        on_capture_settled: Callable[[], None] | None,
    ) -> None:
        """M2.5B.1 — late-bind the interruption-capture callbacks (the
        adapter that owns them is built after this controller)."""
        self._on_seg_started = on_segment_started
        self._on_seg_ended = on_segment_ended
        self._on_capture_settled = on_capture_settled

    # -- state the runtime / bridge drive ------------------------------- #
    @property
    def state_machine(self) -> InterruptionStateMachine:
        return self._sm

    @property
    def active_response_id(self) -> int | None:
        return self._sm.active_response_id

    def is_current_response(self, response_id: int | None) -> bool:
        return self._sm.is_current_response(response_id)

    def notify_response_dispatched(self) -> int | None:
        """Called by ``AssistantSpeechBridge`` when a reply starts generating.
        Returns the freshly allocated ``response_id`` for the caller to stamp
        onto its outgoing work."""
        self._sm.notify_response_dispatched()
        self.telemetry.response_started += 1
        self._sync_telemetry()
        return self._sm.active_response_id

    def notify_response_finished(self) -> None:
        """A reply finished normally. **No-op while ``INTERRUPTING``** (the
        interruption-capture phase is exited only by
        ``notify_interruption_complete``)."""
        ev = self._sm.notify_response_finished()
        if ev == InterruptionEvent.RESPONSE_FINISHED:
            self._cancel_confirm_task()
            self.telemetry.response_finished += 1
        self._sync_telemetry()

    def notify_interruption_complete(self) -> None:
        """M2.5B.1 — the adapter has coalesced + submitted the one canonical
        interruption turn. End the capture phase and cancel every capture
        timer (settle + hard cap)."""
        self._cancel_settle_tasks()
        ev = self._sm.notify_interruption_complete()
        if ev == InterruptionEvent.RESPONSE_FINISHED:
            self.telemetry.response_finished += 1
            self._trace("interruption_complete deadline_cancelled")
        self._sync_telemetry()

    # -- Pipecat entry point ----------------------------------------- #
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._sm.reset()
            self._sync_telemetry()
        elif isinstance(frame, (EndFrame, CancelFrame, ErrorFrame)):
            self._cancel_confirm_task()
            self._cancel_settle_tasks()
            self._sm.reset()
            self._sync_telemetry()
        elif isinstance(frame, InterruptionFrame):
            pass  # our own broadcast echoing back — nothing to do
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._handle_speech_started()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._handle_speech_stopped()
        elif isinstance(frame, UserSpeakingFrame):
            # M2.5B.3 — a "still speaking" tick. During INTERRUPTING it keeps
            # the settle deadline pushed out even if the segment-END frame is
            # later lost/flushed, so the phase always settles ~settle_secs
            # after the user actually stops.
            if self._sm.state == InterruptionState.INTERRUPTING:
                self._note_vad_activity()

        # time-driven confirmation, checked on every frame too (belt for the
        # scheduled wake-up)
        if self._sm.state == InterruptionState.INTERRUPT_CANDIDATE:
            if self._sm.poll(self._now()) == InterruptionEvent.INTERRUPT_CONFIRMED:
                await self._do_confirm("sustained_vad")

        await self.push_frame(frame, direction)

    # -- internals ------------------------------------------------- #
    def _now(self) -> float:
        if self._time_source is not None:
            return self._time_source()
        try:
            return asyncio.get_running_loop().time()
        except RuntimeError:  # not on a loop
            return 0.0

    def _handle_speech_started(self) -> None:
        if not self._sm.response_in_flight:
            return
        if not self._aec.barge_in_safe:
            # R0028 / M2.5A.1: a hot mic during a reply is unsafe without the
            # AEC reference. Do NOT admit an interruption; the mic gate falls
            # back to R0026 whole-response suppression. Loud, not silent.
            self.telemetry.unsafe_speech_ignored += 1
            logger.warning(
                "nexa.voice.bargein: speech during a reply IGNORED — XVF3800 AEC "
                "far-end reference is not active (unsafe hot mic). "
                f"aec_reference_failure_count={self._aec.failure_count}"
            )
            self._sync_telemetry()
            return
        ev = self._sm.speech_started(self._now())
        if ev == InterruptionEvent.CANDIDATE_STARTED:
            self.telemetry.candidate_started += 1
            if self._on_candidate is not None:
                self._on_candidate(self._sm.active_response_id)
            self._schedule_confirm()
        elif ev == InterruptionEvent.IGNORED_SPEECH:
            self.telemetry.ignored_speech_starts += 1
        elif ev == InterruptionEvent.INTERRUPT_SEGMENT_STARTED:
            # more of the same interruption utterance — the settle deadline
            # is pushed out by the fresh VAD activity.
            self.telemetry.interrupt_segments += 1
            self._note_vad_activity()
            self._trace("segment_started", open=self._sm.capture_open_segments)
            if self._on_seg_started is not None:
                self._on_seg_started()
        self._sync_telemetry()

    def _handle_speech_stopped(self) -> None:
        ev = self._sm.speech_stopped(self._now())
        if ev == InterruptionEvent.CANDIDATE_REJECTED:
            self._cancel_confirm_task()
            self.telemetry.candidate_rejected += 1
            if self._on_candidate_rejected is not None:
                self._on_candidate_rejected()
        elif ev == InterruptionEvent.INTERRUPT_SEGMENT_ENDED:
            # a segment of the interruption finished — an STT result is owed
            # for it. The settle deadline is driven by _last_vad_activity, so
            # this is not the *only* thing that keeps the phase alive.
            self._note_vad_activity()
            self._trace(
                "segment_ended", ended=self._sm.capture_segments_ended
            )
            if self._on_seg_ended is not None:
                self._on_seg_ended()
        self._sync_telemetry()

    def _schedule_confirm(self) -> None:
        self._cancel_confirm_task()
        self._confirm_task = self.create_task(self._confirm_after_hold())

    def _cancel_confirm_task(self) -> None:
        if self._confirm_task is not None and not self._confirm_task.done():
            self._confirm_task.cancel()
        self._confirm_task = None

    # -- M2.5B.1 interruption-capture settle phase --------------------- #
    def _arm_settle(self) -> None:
        self._cancel_settle_task()
        self._settle_task = self.create_task(self._settle_after())

    def _cancel_settle_task(self) -> None:
        if self._settle_task is not None and not self._settle_task.done():
            self._settle_task.cancel()
        self._settle_task = None

    def _cancel_settle_tasks(self) -> None:
        self._cancel_settle_task()
        if self._capture_deadline_task is not None and not self._capture_deadline_task.done():
            self._capture_deadline_task.cancel()
        self._capture_deadline_task = None

    async def _settle_after(self) -> None:
        """M2.5B.3 — one long-lived task: fire ``settle_secs`` after the
        *last* VAD activity, re-checking rather than being cancelled/rearmed
        per frame. Robust to a lost segment-END."""
        try:
            while True:
                remaining = self._settle_secs - (self._now() - self._last_vad_activity)
                if remaining <= 0:
                    break
                await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            return
        self._settle_task = None
        self._trace("settle_fired")
        self._notify_capture_settled()

    async def _capture_deadline(self) -> None:
        try:
            await asyncio.sleep(self._max_capture_secs)
        except asyncio.CancelledError:
            return
        logger.warning(
            f"nexa.voice.bargein: capture[{self._capture_id}] hit the "
            f"{self._max_capture_secs}s hard cap — forcing settle (this should "
            "not happen in ordinary use; see R0029 §M2.5B.3)"
        )
        self._notify_capture_settled()

    def _notify_capture_settled(self) -> None:
        if self._sm.state != InterruptionState.INTERRUPTING:
            return
        self._sync_telemetry()
        if self._on_capture_settled is not None:
            self._on_capture_settled()

    async def _confirm_after_hold(self) -> None:
        try:
            await asyncio.sleep(self._sm.confirm_hold_secs)
        except asyncio.CancelledError:
            return
        if self._sm.poll(self._now()) == InterruptionEvent.INTERRUPT_CONFIRMED:
            await self._do_confirm("sustained_vad")

    async def _do_confirm(self, reason: str) -> None:
        if self._confirming:
            return
        self._confirming = True
        try:
            invalidated = self._sm.last_invalidated_response_id
            self._capture_id += 1
            self.telemetry.interrupt_confirmed += 1
            self.telemetry.last_interrupt_reason = reason
            self._sync_telemetry()
            self._trace(
                "interrupt_confirmed", reason=reason, invalidated_response_id=invalidated
            )
            logger.info(
                f"nexa.voice.bargein: INTERRUPT CONFIRMED (capture[{self._capture_id}], "
                f"reason={reason}, invalidated response_id={invalidated})"
            )
            # M2.5B.3 — enter the capture phase FIRST, before any ``await``:
            # NeXa cancels the LLM turn and the adapter enters
            # ``_capturing_interrupt`` synchronously, then the settle timer +
            # hard cap are armed. Only then do we yield to
            # ``broadcast_interruption()``. Previously the ``await`` sat
            # between the state-machine entering INTERRUPTING and the adapter
            # entering capture, so any VAD segment callback in that gap was a
            # silent no-op (R0029 §M2.5B.3 root cause).
            try:
                self._on_confirmed(
                    InterruptContext(invalidated_response_id=invalidated, reason=reason)
                )
            except Exception:
                logger.exception("nexa.voice.bargein: on_confirmed hook raised")
            self._cancel_settle_tasks()
            self._last_vad_activity = self._now()
            self._arm_settle()
            self._trace("settle_armed", settle_secs=self._settle_secs)
            self._capture_deadline_task = self.create_task(self._capture_deadline())
            self._trace("deadline_armed", cap_secs=self._max_capture_secs)
            # Pipecat: cancel + recreate the output audio task, drop queued
            # PCM. (This also clears this processor's own frame queue — hence
            # the capture state above is set BEFORE it.)
            await self.broadcast_interruption()
        finally:
            self._confirming = False

    def _sync_telemetry(self) -> None:
        t = self.telemetry
        t.state = self._sm.state.value
        t.active_response_id = self._sm.active_response_id
        t.ignored_speech_starts = self._sm.ignored_speech_starts
        t.interrupt_segments_ended = self._sm.capture_segments_ended
        t.aec_reference_active = self._aec.active
        t.aec_reference_failure_count = self._aec.failure_count
