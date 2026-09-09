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


@dataclass
class BargeInTelemetry:
    """Live counters for the probe + shutdown summary. Never persists audio."""

    response_started: int = 0
    response_finished: int = 0
    candidate_started: int = 0
    candidate_rejected: int = 0
    interrupt_confirmed: int = 0
    ignored_speech_starts: int = 0
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
        confirm_hold_secs: float = DEFAULT_CONFIRM_HOLD_SECS,
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
        self._confirm_task: asyncio.Task | None = None
        self._confirming = False  # re-entrancy guard for _do_confirm
        self.telemetry = BargeInTelemetry()

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
        """Called when a reply finished — normal completion OR the tail of an
        interruption teardown."""
        ev = self._sm.notify_response_finished()
        self._cancel_confirm_task()
        if ev == InterruptionEvent.RESPONSE_FINISHED:
            self.telemetry.response_finished += 1
        self._sync_telemetry()

    # -- Pipecat entry point ----------------------------------------- #
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._sm.reset()
            self._sync_telemetry()
        elif isinstance(frame, (EndFrame, CancelFrame, ErrorFrame)):
            self._cancel_confirm_task()
            self._sm.reset()
            self._sync_telemetry()
        elif isinstance(frame, InterruptionFrame):
            pass  # our own broadcast echoing back — nothing to do
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._handle_speech_started()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._handle_speech_stopped()

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
        self._sync_telemetry()

    def _handle_speech_stopped(self) -> None:
        ev = self._sm.speech_stopped(self._now())
        if ev == InterruptionEvent.CANDIDATE_REJECTED:
            self._cancel_confirm_task()
            self.telemetry.candidate_rejected += 1
            if self._on_candidate_rejected is not None:
                self._on_candidate_rejected()
        self._sync_telemetry()

    def _schedule_confirm(self) -> None:
        self._cancel_confirm_task()
        self._confirm_task = self.create_task(self._confirm_after_hold())

    def _cancel_confirm_task(self) -> None:
        if self._confirm_task is not None and not self._confirm_task.done():
            self._confirm_task.cancel()
        self._confirm_task = None

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
            invalidated = self.telemetry.active_response_id
            self.telemetry.interrupt_confirmed += 1
            self.telemetry.last_interrupt_reason = reason
            self._sync_telemetry()
            logger.info(
                f"nexa.voice.bargein: INTERRUPT CONFIRMED (reason={reason}, "
                f"invalidated response_id={invalidated})"
            )
            # Pipecat: cancel + recreate the output audio task, drop queued PCM.
            await self.broadcast_interruption()
            # NeXa: cancel the LLM turn, commit the spoken prefix, capture the
            # interrupting utterance. Synchronous + fast by contract.
            try:
                self._on_confirmed(
                    InterruptContext(invalidated_response_id=invalidated, reason=reason)
                )
            except Exception:
                logger.exception("nexa.voice.bargein: on_confirmed hook raised")
        finally:
            self._confirming = False

    def _sync_telemetry(self) -> None:
        t = self.telemetry
        t.state = self._sm.state.value
        t.active_response_id = self._sm.active_response_id
        t.ignored_speech_starts = self._sm.ignored_speech_starts
        t.aec_reference_active = self._aec.active
        t.aec_reference_failure_count = self._aec.failure_count
