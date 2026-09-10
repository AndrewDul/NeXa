"""M2.5B.2 — ``ConversationSession`` + ``ProviderWindow`` integration.

Canonical history stays complete; only the provider-facing message list is
bounded and prefix-stable. Uses ``FakeModelProvider`` — no model.
"""
from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/fakes

from fakes import FakeModelProvider  # noqa: E402
from nexa.conversation.provider_window import ProviderWindow  # noqa: E402
from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.conversation.turn import Role  # noqa: E402
from nexa.providers.base import CancelToken  # noqa: E402

SYS = "Jesteś NeXa — testowa persona."


async def _drain(stream) -> str:
    return "".join([c async for c in stream])


def _win(**kw) -> ProviderWindow:
    return ProviderWindow(keep_entries=4, soft_entries=8, hard_entries=12, **kw)


class TestWindowedSend(unittest.IsolatedAsyncioTestCase):
    async def test_none_window_is_the_legacy_path(self) -> None:
        p = FakeModelProvider([["ok"]] * 3)
        s = ConversationSession(provider=p, system_prompt=SYS)  # provider_window=None
        for _ in range(3):
            await _drain(s.send("czy to działa"))
        # legacy ConversationContext path: every turn present, no window logic
        self.assertEqual(len(s.history), 6)
        self.assertIn("czy to działa", [m.content for m in p.calls[-1]])

    async def test_windowed_send_renders_only_history_from_base(self) -> None:
        p = FakeModelProvider([["ok"]] * 20)
        s = ConversationSession(provider=p, system_prompt=SYS, provider_window=_win())
        for i in range(6):
            await _drain(s.send(f"pytanie {i}"))
        # canonical history complete
        self.assertEqual(len(s.history), 12)
        self.assertEqual([t.content for t in s.history if t.role == Role.USER],
                         [f"pytanie {i}" for i in range(6)])
        # base still 0 (window 12 >= soft 8, but no pre-warm ran) -> everything shown
        self.assertEqual(s.provider_window.base, 0)

    async def test_prewarm_then_next_send_cuts_over_and_bounds_the_window(self) -> None:
        p = FakeModelProvider([["ok"]] * 40)
        w = _win()
        s = ConversationSession(provider=p, system_prompt=SYS, provider_window=w)
        for i in range(5):                      # 10 entries > soft 8
            await _drain(s.send(f"q{i}"))
        self.assertTrue(w.needs_prewarm(len(s.history)))
        tok = CancelToken()
        status = await s.prewarm_provider_context(cancel_token=tok)
        self.assertEqual(status, "prewarmed")
        # the pre-warm call sent ONLY the small window (keep_entries=4) with num_predict=1
        pre_call = p.calls[-1]
        self.assertEqual(p.options_by_call[-1].num_predict, 1)
        self.assertEqual(sum(1 for m in pre_call if m.role != "system"), 4)
        self.assertEqual(w.prewarmed_base, len(s.history) - 4)  # keep last 4 of 10
        # next real turn cuts over
        n_before = len(p.calls)
        await _drain(s.send("q5"))
        self.assertEqual(w.base, 6)             # keep the last 4 entries before q5
        self.assertEqual(w.rollovers_background, 1)
        real_call = p.calls[-1]
        # window now = keep_entries(4) + the new user turn (+lang dir)
        non_sys = [m for m in real_call if m.role != "system"]
        self.assertEqual(non_sys[-1].content, "q5")
        self.assertLessEqual(len(non_sys), 6)
        self.assertGreater(len(p.calls), n_before)
        # canonical history STILL complete
        self.assertEqual(len(s.history), 12)

    async def test_hard_limit_forces_a_logged_sync_rollover(self) -> None:
        p = FakeModelProvider([["ok"]] * 40)
        w = _win()
        s = ConversationSession(provider=p, system_prompt=SYS, provider_window=w)
        with self.assertLogs("nexa.conversation.session", level="WARNING") as cm:
            for i in range(7):                  # 14 entries >= hard 12
                await _drain(s.send(f"q{i}"))
        self.assertTrue(any("SYNC rollover" in m for m in cm.output))
        self.assertEqual(w.rollovers_sync, 1)
        self.assertEqual(w.rollovers_background, 0)
        # window bounded: keep_entries + at most one live exchange since the roll
        self.assertLessEqual(w.window_entries(len(s.history)), w.keep_entries + 2)
        self.assertEqual(len(s.history), 14)    # nothing lost

    async def test_prefix_is_a_pure_extension_between_rollovers(self) -> None:
        p = FakeModelProvider([["ok"]] * 40)
        w = ProviderWindow(keep_entries=4, soft_entries=20, hard_entries=30)
        s = ConversationSession(provider=p, system_prompt=SYS, provider_window=w)
        prev: list | None = None
        for i in range(8):                      # stays under soft -> base fixed at 0
            await _drain(s.send(f"pytanie numer {i}"))
            msgs = [(m.role, m.content) for m in p.calls[-1]]
            if prev is not None:
                # last turn's prompt is this turn's prompt minus the tail
                self.assertEqual(msgs[: len(prev) - 1], prev[:-1])
            prev = msgs
        self.assertEqual(w.base, 0)
        self.assertEqual(w.rollovers_sync, 0)

    async def test_response_language_slots_stay_aligned_across_rollover(self) -> None:
        p = FakeModelProvider([["ok"]] * 40)
        w = _win()
        s = ConversationSession(provider=p, system_prompt=SYS, provider_window=w)
        for i in range(5):
            await _drain(s.send(f"q{i}", response_language="pl" if i % 2 else "en"))
        tok = CancelToken()
        await s.prewarm_provider_context(cancel_token=tok)
        await _drain(s.send("q5", response_language="pl"))
        # index alignment invariant of the session
        self.assertEqual(len(s._history), len(s._response_languages))  # noqa: SLF001

    async def test_interrupted_rollback_still_works_with_a_window(self) -> None:
        from nexa.conversation.session import InterruptedTurnOutcome

        p = FakeModelProvider([["ok"]] * 20)
        s = ConversationSession(provider=p, system_prompt=SYS, provider_window=_win())
        await _drain(s.send("pierwsze"))
        # a think-window interrupt: user turn appended by send(), then rolled back
        s._history.append(  # noqa: SLF001 — simulate send() having appended
            __import__("nexa.conversation.turn", fromlist=["ConversationTurn"])
            .ConversationTurn(role=Role.USER, content="drugie")
        )
        s._response_languages.append(None)  # noqa: SLF001
        outcome = s.commit_interrupted_turn("")
        self.assertEqual(outcome, InterruptedTurnOutcome.ROLLED_BACK_USER_TURN)
        self.assertEqual(len(s._history), len(s._response_languages))  # noqa: SLF001
        self.assertEqual([t.content for t in s.history], ["pierwsze", "ok"])

    async def test_prewarm_is_a_noop_below_soft(self) -> None:
        p = FakeModelProvider([["ok"]] * 10)
        s = ConversationSession(provider=p, system_prompt=SYS, provider_window=_win())
        await _drain(s.send("q0"))
        calls_before = len(p.calls)
        status = await s.prewarm_provider_context(cancel_token=CancelToken())
        self.assertEqual(status, "not_needed")
        self.assertEqual(len(p.calls), calls_before)  # no request issued

    async def test_prewarm_cancelled_is_retried_next_time(self) -> None:
        class _CancelDuringPrewarm(FakeModelProvider):
            async def generate(self, messages, options, *, cancel_token=None):
                if cancel_token is not None and options.num_predict == 1:
                    cancel_token.cancel()          # simulate an interrupted pre-warm
                    return
                async for c in super().generate(
                    messages, options, cancel_token=cancel_token
                ):
                    yield c

        p = _CancelDuringPrewarm([["ok"]] * 20)
        w = _win()
        s = ConversationSession(provider=p, system_prompt=SYS, provider_window=w)
        for i in range(5):
            await _drain(s.send(f"q{i}"))
        status = await s.prewarm_provider_context(cancel_token=CancelToken())
        self.assertEqual(status, "cancelled")
        self.assertIsNone(w.prewarmed_base)          # not marked ready
        self.assertTrue(w.needs_prewarm(len(s.history)))  # will retry

    async def test_no_window_path_unaffected_by_new_code(self) -> None:
        logging.getLogger("nexa.conversation.session").setLevel(logging.CRITICAL)
        p = FakeModelProvider([["a"], ["b"]])
        s = ConversationSession(provider=p, system_prompt=SYS)
        await _drain(s.send("one"))
        await _drain(s.send("two"))
        # identical to pre-M2.5B.2: full ConversationContext render
        self.assertEqual([m.content for m in p.calls[0] if m.role == "user"], ["one"])
        self.assertEqual([m.content for m in p.calls[1] if m.role == "user"],
                         ["one", "two"])


if __name__ == "__main__":
    unittest.main()
