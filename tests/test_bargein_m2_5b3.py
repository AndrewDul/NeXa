"""M2.5B.3 — interruption-capture lifecycle: no 12 s cap / 15 s timeout for
a normal live interruption, no DROP_BUSY of a segment of the confirmed
interruption.

Root cause (R0029 §M2.5B.3): the settle window was armed ONLY on a VAD
``INTERRUPT_SEGMENT_ENDED``, and ``broadcast_interruption()`` flushes the
``BargeInController``'s own frame queue — so on a lagged Pi loop a segment-END
frame can be dropped, the settle never arms, the capture never finalises,
and the 12 s controller cap + 15 s adapter timeout fire; a trailing segment
is then busy-dropped.

Fix: the settle is armed at *confirm* and driven by a ``_last_vad_activity``
timestamp updated on EVERY VAD frame (incl. ``UserSpeakingFrame``), so a
lost segment-END is harmless; ``_interrupt_open_segments`` no longer gates
finalisation; a trailing interruption segment is never DROP_BUSY'd.

Offline, deterministic. Built via ``build_bargein_stack`` (the probe path).
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.voice.interruption import InterruptionState  # noqa: E402

# reuse the m2_5b1 fixture (adapter + real controller via build_bargein_stack)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bargein_m2_5b1 import _Fixture, _tr  # noqa: E402


class _Lifecycle(_Fixture):
    """Adds VAD-frame-level driving on top of the m2_5b1 fixture."""

    def _sm(self):
        return self.stack.controller._sm  # noqa: SLF001

    async def _confirm(self, *, settle=0.15, capture_timeout=3.0):
        """Confirm an interruption exactly as the real controller does:
        candidate -> hold -> _do_confirm (which now enters capture + arms the
        settle BEFORE broadcast_interruption)."""
        self._build(settle=settle, capture_timeout=capture_timeout)
        ctl = self.stack.controller
        rid = ctl.notify_response_dispatched()
        self.adapter._active_response_id = rid  # noqa: SLF001
        self._turn_task = asyncio.ensure_future(
            self.adapter._run_turn_inner(_tr("Opowiedz o czarnych dziurach"))  # noqa: SLF001
        )
        await asyncio.sleep(0.03)
        ctl._sm.speech_started(now=0.0)  # noqa: SLF001  RESPONDING -> CANDIDATE
        self.assertEqual(ctl._sm.poll(now=1.0).value, "interrupt_confirmed")  # noqa: SLF001
        await ctl._do_confirm("sustained_vad")   # arms settle + deadline, enters capture
        await self._turn_task
        return ctl

    def _tick_speaking(self, ctl, t: float) -> None:
        """Simulate a ``UserSpeakingFrame`` at loop-time ``t`` while INTERRUPTING."""
        ctl._time_source = lambda: t  # noqa: SLF001
        ctl._note_vad_activity()  # noqa: SLF001

    def _seg_end(self, ctl, t: float) -> None:
        ctl._time_source = lambda: t  # noqa: SLF001
        ctl._handle_speech_stopped()  # noqa: SLF001

    def _seg_start(self, ctl, t: float) -> None:
        ctl._time_source = lambda: t  # noqa: SLF001
        ctl._handle_speech_started()  # noqa: SLF001


class TestNoSpuriousCapOrTimeout(_Lifecycle):
    async def test_A_single_segment_settles_promptly_deadline_never_fires(self) -> None:
        ctl = await self._confirm(settle=0.15)
        # user speaks the interruption for a bit (UserSpeakingFrame ticks),
        # then stops — but its VAD segment-END frame is DELIVERED normally
        self._tick_speaking(ctl, 1.0)
        self._tick_speaking(ctl, 1.2)
        self._seg_end(ctl, 1.4)                 # segment-END delivered
        self.adapter.handle_transcription(_tr("Czekaj, powiedz krócej."))  # STT result
        ctl._time_source = lambda: 1.7          # noqa: SLF001 clock past last-activity + settle
        # settle fires ~0.15s after last activity; let it
        await asyncio.sleep(0.4)
        self.assertEqual(self.adapter.coalesced_interrupt_turns, 1)
        self.assertFalse(self.adapter.capturing_interrupt)
        self.assertEqual(self.stack.controller.state_machine.state,
                         InterruptionState.RESPONDING)  # replacement dispatched
        # the 12s hard-cap task must have been cancelled, not fired
        dl = self.stack.controller._capture_deadline_task  # noqa: SLF001
        self.assertTrue(dl is None or dl.cancelled() or dl.done())
        self.assertEqual(self.stack.controller.telemetry.last_interrupt_reason,
                         "sustained_vad")

    async def test_B_only_segment_end_LOST_still_settles_no_cap_no_timeout(self) -> None:
        """THE live bug, minimal form: a single-segment interruption whose
        ONLY VAD segment-END frame is flushed by broadcast_interruption().
        With the old code (settle armed only on INTERRUPT_SEGMENT_ENDED) the
        settle would never fire -> 12s cap + 15s timeout. It must now settle
        from the UserSpeakingFrame ticks + the confirm-time arm."""
        ctl = await self._confirm(settle=0.2, capture_timeout=1.2)
        # user speaks the whole interruption; its segment-END is NEVER
        # delivered (only UserSpeakingFrame ticks, then silence)
        for t in (0.5, 0.9, 1.3, 1.7):
            self._tick_speaking(ctl, t)
        # its audio still reaches STT eventually (buffer flushed by a frame)
        self.adapter.handle_transcription(_tr("Czekaj, mów po polsku."))
        ctl._time_source = lambda: 2.1  # noqa: SLF001  clock past last tick + settle
        with self.assertNoLogs("nexa.voice_conversation.adapter", level="WARNING"):
            await asyncio.sleep(0.6)      # < the 1.2s adapter capture timeout
        self.assertEqual(self.adapter.coalesced_interrupt_turns, 1)
        self.assertFalse(self.adapter.capturing_interrupt)
        # 12s controller hard-cap task was cancelled, never fired
        dl = self.stack.controller._capture_deadline_task  # noqa: SLF001
        self.assertTrue(dl is None or dl.done() or dl.cancelled())
        # the coalesced turn carries the captured text
        user_turns = [t for t in self.sess.history if t.role.value == "user"]
        self.assertEqual(user_turns[-1].content, "Czekaj, mów po polsku.")

    async def test_B2_lost_first_segment_then_more_speech_coalesces_and_no_drop(self) -> None:
        ctl = await self._confirm(settle=0.2, capture_timeout=2.5)
        # segment-1 END LOST; user keeps talking; a LATER segment delivered
        for t in (0.5, 0.9, 1.3, 1.7, 2.1):
            self._tick_speaking(ctl, t)
        self._seg_start(ctl, 2.4)
        for t in (2.6, 2.9):
            self._tick_speaking(ctl, t)
        self._seg_end(ctl, 3.1)
        self.adapter.handle_transcription(_tr("bo nie uruchamiasz angielskiego modelu."))
        ctl._time_source = lambda: 3.4  # noqa: SLF001
        await asyncio.sleep(0.5)
        self.assertEqual(self.adapter.coalesced_interrupt_turns, 1)
        self.assertFalse(self.adapter.capturing_interrupt)
        # a late STT result for the LOST first segment now arrives — discarded
        # quietly (expected artefact), NOT DROP_BUSY'd
        self.adapter.handle_transcription(_tr("Słuchaj, źle to robimy,"))
        self.assertEqual(self.dropped, [])
        self.assertEqual(self.adapter.dropped_busy_turns, 0)

    async def test_C_replacement_response_begins_capture_state_cleared(self) -> None:
        ctl = await self._confirm(settle=0.12)
        self._tick_speaking(ctl, 1.0)
        self._seg_end(ctl, 1.2)
        self.adapter.handle_transcription(_tr("Krócej."))
        ctl._time_source = lambda: 1.5  # noqa: SLF001
        await asyncio.sleep(0.35)
        # replacement turn is in flight / done -> ALL capture state cleared
        self.assertFalse(self.adapter.capturing_interrupt)
        self.assertFalse(self.stack.gate.capturing_interrupt)
        self.assertEqual(self._sm().capture_open_segments, 0)
        self.assertEqual(self._sm().capture_segments_ended, 0)
        self.assertEqual(self.adapter.conversation_in_flight
                         + self.adapter.conversation_queue_depth, 0)

    async def test_D_new_speech_after_replacement_is_a_fresh_barge_in(self) -> None:
        ctl = await self._confirm(settle=0.12)
        self._tick_speaking(ctl, 1.0)
        self._seg_end(ctl, 1.2)
        self.adapter.handle_transcription(_tr("Krócej."))
        ctl._time_source = lambda: 1.5  # noqa: SLF001
        await asyncio.sleep(0.35)
        self.assertEqual(self.adapter.coalesced_interrupt_turns, 1)
        # replacement reply now streaming; a genuinely NEW utterance's STT
        # result arrives well past the late-result grace -> normal DROP_BUSY
        self.adapter._last_capture_finalized_at = None  # noqa: SLF001 - simulate grace elapsed
        self.adapter._late_interrupt_results = 0  # noqa: SLF001
        # ensure a turn is in flight
        if not self.adapter.turn_in_flight:
            self.adapter._turn_in_flight = True  # noqa: SLF001
        self.adapter.handle_transcription(_tr("Zupełnie nowe pytanie w trakcie."))
        self.assertEqual(len(self.dropped), 1)

    async def test_E_delayed_STT_completion_waits_then_finalises_once(self) -> None:
        ctl = await self._confirm(settle=0.12, capture_timeout=3.0)
        self._tick_speaking(ctl, 1.0)
        self._seg_end(ctl, 1.2)
        ctl._time_source = lambda: 1.5  # noqa: SLF001
        await asyncio.sleep(0.3)                              # settle fires, pending still 1
        self.assertTrue(self.adapter.capturing_interrupt)     # NOT finalised yet
        self.assertEqual(self.adapter.coalesced_interrupt_turns, 0)
        self.adapter.handle_transcription(_tr("Powiedz krócej."))   # STT arrives -> pending 0
        await asyncio.sleep(0.1)
        self.assertEqual(self.adapter.coalesced_interrupt_turns, 1)  # finalised exactly once
        self.assertFalse(self.adapter.capturing_interrupt)

    async def test_F_missing_vad_and_stt_hard_timeout_releases_state(self) -> None:
        await self._confirm(settle=5.0, capture_timeout=0.3)  # settle can't fire first
        # nothing delivered at all; keep the controller clock frozen so the
        # settle loop keeps waiting -> only the adapter hard timeout can act
        await asyncio.sleep(0.6)
        self.assertFalse(self.adapter.capturing_interrupt)          # released
        self.assertEqual(self._sm().state, InterruptionState.IDLE)  # not latched
        # no crash, no coalesced turn (nothing was captured)
        self.assertEqual(self.adapter.coalesced_interrupt_turns, 0)

    async def test_G_six_second_segment_of_the_interruption_is_not_dropped(self) -> None:
        ctl = await self._confirm(settle=0.15, capture_timeout=2.0)
        # a long (~6s) second segment of the SAME confirmed interruption
        self._seg_start(ctl, 0.5)
        for t in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0):
            self._tick_speaking(ctl, t)
        self._seg_end(ctl, 6.5)
        # its (long) transcript
        self.adapter.handle_transcription(
            _tr("bo trzeba najpierw uruchomić angielski model a potem dopiero pytać")
        )
        ctl._time_source = lambda: 6.8  # noqa: SLF001
        await asyncio.sleep(0.4)
        self.assertEqual(self.adapter.coalesced_interrupt_turns, 1)
        turns = [t for t in self.sess.history]
        self.assertTrue(any("angielski model" in t.content for t in turns))
        self.assertEqual(self.dropped, [])
        self.assertEqual(self.adapter.dropped_busy_turns, 0)

    async def test_H_repeated_interruptions_no_stale_deadline_across_N_and_N1(self) -> None:
        for cycle in range(4):
            ctl = await self._confirm(settle=0.12, capture_timeout=2.0)
            self._tick_speaking(ctl, 1.0 + cycle)
            self._seg_end(ctl, 1.2 + cycle)
            self.adapter.handle_transcription(_tr(f"krócej numer {cycle}"))
            _t = 1.5 + cycle
            ctl._time_source = lambda _t=_t: _t  # noqa: SLF001
            await asyncio.sleep(0.3)
            self.assertEqual(self.adapter.coalesced_interrupt_turns, 1)
            self.assertFalse(self.adapter.capturing_interrupt)
            dl = self.stack.controller._capture_deadline_task  # noqa: SLF001
            self.assertTrue(dl is None or dl.done() or dl.cancelled())
            # tear down this cycle's adapter/tasks before the next
            for t in getattr(self, "_ctl_tasks", []):
                if not t.done():
                    t.cancel()
            await self.adapter.shutdown()


if __name__ == "__main__":
    unittest.main()
