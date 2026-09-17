"""M2.5B.2 — ``ProviderWindow`` policy + rendering (pure, no model)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation.provider_window import ProviderWindow  # noqa: E402
from nexa.conversation.response_mode import ResponseMode  # noqa: E402
from nexa.conversation.turn import ConversationTurn, Role  # noqa: E402

SYS = "system-persona"


def _hist(n_exchanges: int) -> list[ConversationTurn]:
    h: list[ConversationTurn] = []
    for i in range(n_exchanges):
        h.append(ConversationTurn(role=Role.USER, content=f"pytanie {i}"))
        h.append(ConversationTurn(role=Role.ASSISTANT, content=f"odpowiedz {i}"))
    return h


class TestPolicy(unittest.TestCase):
    def test_rejects_bad_bounds(self) -> None:
        ProviderWindow(keep_entries=0, soft_entries=20, hard_entries=30)  # keep=0 is valid
        with self.assertRaises(ValueError):
            ProviderWindow(keep_entries=-2, soft_entries=20, hard_entries=30)
        with self.assertRaises(ValueError):
            ProviderWindow(keep_entries=20, soft_entries=10, hard_entries=30)  # keep >= soft
        with self.assertRaises(ValueError):
            ProviderWindow(keep_entries=8, soft_entries=30, hard_entries=20)  # hard < soft
        with self.assertRaises(ValueError):
            ProviderWindow(keep_entries=7, soft_entries=20, hard_entries=30)  # odd keep

    def test_keep_zero_reset_shows_only_the_current_turn(self) -> None:
        w = ProviderWindow(keep_entries=0, soft_entries=20, hard_entries=24)
        hist_len = 25  # odd — a new USER turn was just appended
        self.assertTrue(w.needs_sync_rollover(hist_len))
        nb = w.cutover_sync(hist_len)
        self.assertEqual(nb, 24)                  # base == index of the new USER turn
        self.assertEqual(w.window_entries(hist_len), 1)   # only the current turn is in view
        self.assertEqual(w.rollovers_sync, 1)
        # next turn: window regrows by append
        self.assertEqual(w.window_entries(27), 3)

    def test_below_soft_no_maintenance(self) -> None:
        w = ProviderWindow(keep_entries=8, soft_entries=40, hard_entries=60)
        self.assertFalse(w.needs_prewarm(38))
        self.assertFalse(w.needs_sync_rollover(38))
        self.assertEqual(w.base, 0)

    def test_prewarm_arms_at_soft_and_targets_the_small_window(self) -> None:
        w = ProviderWindow(keep_entries=8, soft_entries=40, hard_entries=60)
        self.assertTrue(w.needs_prewarm(40))
        self.assertEqual(w.next_base(40), 32)          # keep last 8 entries
        self.assertEqual(w.window_entries(40) - w.keep_entries,
                         w.next_base(40))              # dropped entries
        self.assertFalse(w.needs_sync_rollover(40))

    def test_background_cutover_keeps_last_keep_entries(self) -> None:
        w = ProviderWindow(keep_entries=8, soft_entries=40, hard_entries=60)
        n = 44
        w.mark_prewarmed(w.next_base(n))
        self.assertTrue(w.prewarm_ready(n))
        self.assertFalse(w.needs_prewarm(n))
        self.assertEqual(w.cutover_background(n), n - 8)
        self.assertEqual(w.base, n - 8)
        self.assertIsNone(w.prewarmed_base)
        self.assertEqual(w.rollovers_background, 1)
        # after cutover the window is exactly keep_entries again
        self.assertEqual(w.window_entries(n), 8)

    def test_sync_rollover_only_at_hard_without_prewarm(self) -> None:
        w = ProviderWindow(keep_entries=8, soft_entries=40, hard_entries=50)
        self.assertFalse(w.needs_sync_rollover(49))
        self.assertTrue(w.needs_sync_rollover(50))
        w.mark_prewarmed(w.next_base(50))             # a ready pre-warm suppresses it
        self.assertFalse(w.needs_sync_rollover(50))

    def test_sync_cutover_advances_and_counts(self) -> None:
        w = ProviderWindow(keep_entries=8, soft_entries=40, hard_entries=50)
        self.assertEqual(w.cutover_sync(50), 42)
        self.assertEqual(w.base, 42)
        self.assertEqual(w.rollovers_sync, 1)
        self.assertEqual(w.window_entries(50), 8)

    def test_base_stays_on_turn_boundary_with_odd_history(self) -> None:
        w = ProviderWindow(keep_entries=8, soft_entries=40, hard_entries=60)
        self.assertEqual(w.next_base(45) % 2, 0)      # odd history_len (user turn just added)

    def test_repeated_rollovers_keep_advancing_and_window_stays_bounded(self) -> None:
        w = ProviderWindow(keep_entries=8, soft_entries=20, hard_entries=28)
        for _ in range(5):
            n = w.base + 24            # window grew to 24 > soft 20
            self.assertTrue(w.needs_prewarm(n))
            w.mark_prewarmed(w.next_base(n))
            w.cutover_background(n)
            self.assertEqual(w.window_entries(n), 8)   # bounded every time
        self.assertEqual(w.rollovers_background, 5)
        self.assertEqual(w.rollovers_sync, 0)
        self.assertGreater(w.base, 60)                 # base really advanced

    def test_prewarm_not_rearmed_once_ready(self) -> None:
        w = ProviderWindow(keep_entries=8, soft_entries=20, hard_entries=30)
        n = 24
        self.assertTrue(w.needs_prewarm(n))
        w.mark_prewarmed(w.next_base(n))
        self.assertFalse(w.needs_prewarm(n))           # idempotent — no repeated pre-warm


class TestRendering(unittest.TestCase):
    def test_renders_only_the_window_slice(self) -> None:
        w = ProviderWindow(_base=6)
        hist = _hist(8)  # 16 entries
        msgs = w.render(SYS, hist)
        self.assertEqual((msgs[0].role, msgs[0].content), ("system", SYS))
        contents = [m.content for m in msgs]
        self.assertIn("pytanie 3", contents)      # entry 6 == exchange 3 USER
        self.assertNotIn("pytanie 2", contents)   # entry 4 — outside the window
        self.assertNotIn("odpowiedz 2", contents)

    def test_prefix_stable_as_history_grows_at_fixed_base(self) -> None:
        w = ProviderWindow(_base=4)
        prev: list | None = None
        for n in range(4, 14):
            msgs = [(m.role, m.content) for m in w.render(SYS, _hist(n))]
            if prev is not None:
                self.assertEqual(msgs[: len(prev)], prev)  # pure prefix-extension
            prev = msgs

    def test_byte_identical_to_context_renderer_for_the_same_slice(self) -> None:
        from nexa.conversation.context import ConversationContext

        hist = _hist(3)
        for mode in (ResponseMode.TEXT, ResponseMode.VOICE):
            got = [(m.role, m.content)
                   for m in ProviderWindow(_base=0).render(SYS, hist, response_mode=mode)]
            want = [(m.role, m.content)
                    for m in ConversationContext.build(SYS, hist).to_provider_messages(
                        response_mode=mode)]
            self.assertEqual(got, want)

    def test_interrupted_wire_suffix_applied_in_window(self) -> None:
        hist = _hist(2)
        hist.append(ConversationTurn(role=Role.USER, content="krócej"))
        hist.append(ConversationTurn(role=Role.ASSISTANT, content="Zaczy", interrupted=True))
        msgs = ProviderWindow(_base=0).render(SYS, hist)
        self.assertTrue(any(m.content == "Zaczy […]" for m in msgs))

    def test_render_rejects_misaligned_languages(self) -> None:
        with self.assertRaises(ValueError):
            ProviderWindow().render(SYS, _hist(3), ["pl"])  # 1 != 6

    def test_render_at_explicit_base_for_prewarm(self) -> None:
        w = ProviderWindow(_base=0)
        hist = _hist(20)  # 40 entries
        pre = w.render(SYS, hist, base=32)          # the small post-roll window
        self.assertEqual(sum(1 for m in pre if m.role != "system"), 8)  # keep 8 entries


class TestContextAddendum(unittest.TestCase):
    """R0079 (R0078 Revision 2 §14) — local voice Context Engine parity:
    ``context_addendum`` is a trailing, non-persisted system message,
    appended at most once, never affecting prefix stability."""

    def test_default_none_is_byte_for_byte_unchanged(self) -> None:
        hist = _hist(2)
        without = ProviderWindow(_base=0).render(SYS, hist)
        with_none = ProviderWindow(_base=0).render(SYS, hist, context_addendum=None)
        self.assertEqual(
            [(m.role, m.content) for m in without],
            [(m.role, m.content) for m in with_none],
        )

    def test_addendum_appended_as_one_trailing_system_message(self) -> None:
        hist = _hist(1)
        msgs = ProviderWindow(_base=0).render(SYS, hist, context_addendum="Relevant: X")
        self.assertEqual(msgs[-1].role, "system")
        self.assertEqual(msgs[-1].content, "Relevant: X")
        # exactly one occurrence -- never duplicated
        self.assertEqual(sum(1 for m in msgs if m.content == "Relevant: X"), 1)

    def test_addendum_present_exactly_once_alongside_language_directive(self) -> None:
        hist = _hist(1)
        msgs = ProviderWindow(_base=0).render(SYS, hist, context_addendum="Relevant: X")
        system_messages = [m for m in msgs if m.role == "system"]
        # persona + (no voice directive, TEXT mode) + language directive + addendum
        addendum_count = sum(1 for m in system_messages if m.content == "Relevant: X")
        self.assertEqual(addendum_count, 1)

    def test_addendum_never_appears_before_the_current_turn(self) -> None:
        hist = _hist(2)
        msgs = ProviderWindow(_base=0).render(SYS, hist, context_addendum="Relevant: X")
        addendum_index = next(i for i, m in enumerate(msgs) if m.content == "Relevant: X")
        last_user_index = max(i for i, m in enumerate(msgs) if m.role == "user")
        self.assertGreater(addendum_index, last_user_index)

    def test_addendum_does_not_change_base_or_rollover_counters(self) -> None:
        """Prefix-stability regression: rendering with an addendum must not
        itself trigger or affect base/rollover bookkeeping -- render() is
        pure, only send()'s own reset logic (unchanged, R0079 doesn't
        touch it) ever advances base."""
        w = ProviderWindow(keep_entries=0, soft_entries=6, hard_entries=8)
        hist = _hist(2)
        before = (w.base, w.rollovers_background, w.rollovers_sync)
        w.render(SYS, hist, context_addendum="Relevant: X")
        after = (w.base, w.rollovers_background, w.rollovers_sync)
        self.assertEqual(before, after)

    def test_addendum_not_added_to_history_or_window_slice(self) -> None:
        hist = _hist(1)
        msgs = ProviderWindow(_base=0).render(SYS, hist, context_addendum="Relevant: X")
        # history itself is untouched -- only the rendered message LIST gained
        # one trailing entry; the window's own turn count is unchanged.
        non_system = [m for m in msgs if m.role != "system"]
        self.assertEqual(len(non_system), len(hist))


if __name__ == "__main__":
    unittest.main()
