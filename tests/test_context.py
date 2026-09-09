"""Deterministic tests for ``ConversationContext``'s bounding strategy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation.context import (  # noqa: E402
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_TURNS,
    ConversationContext,
)
from nexa.conversation.turn import ConversationTurn, Role  # noqa: E402

SYSTEM_PROMPT = "system"


def _turn(role: Role, content: str) -> ConversationTurn:
    return ConversationTurn(role=role, content=content)


class TestConversationContext(unittest.TestCase):
    def test_empty_history_yields_system_only(self) -> None:
        context = ConversationContext.build(SYSTEM_PROMPT, [])
        self.assertEqual(context.turns, ())
        messages = context.to_provider_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, "system")

    def test_bounded_by_max_turns(self) -> None:
        history = [_turn(Role.USER, f"msg {i}") for i in range(10)]
        context = ConversationContext.build(SYSTEM_PROMPT, history, max_turns=3)
        self.assertEqual(len(context.turns), 3)
        self.assertEqual([t.content for t in context.turns], ["msg 7", "msg 8", "msg 9"])

    def test_bounded_by_max_chars_drops_oldest_first(self) -> None:
        history = [
            _turn(Role.USER, "a" * 100),
            _turn(Role.USER, "b" * 100),
            _turn(Role.USER, "c" * 100),
        ]
        context = ConversationContext.build(
            SYSTEM_PROMPT, history, max_turns=100, max_chars=250
        )
        # Only the 2 most recent fit under the 250-char budget.
        self.assertEqual([t.content[0] for t in context.turns], ["b", "c"])

    def test_most_recent_turn_always_kept_even_if_oversized(self) -> None:
        history = [_turn(Role.USER, "x" * 5000)]
        context = ConversationContext.build(SYSTEM_PROMPT, history, max_chars=100)
        self.assertEqual(len(context.turns), 1)

    def test_turns_are_never_split_and_stay_ordered(self) -> None:
        history = [_turn(Role.USER, f"turn-{i}") for i in range(5)]
        context = ConversationContext.build(SYSTEM_PROMPT, history, max_turns=5)
        self.assertEqual(
            [t.content for t in context.turns],
            [f"turn-{i}" for i in range(5)],
        )

    def test_default_window_holds_a_long_voice_session_without_eviction(self) -> None:
        """M2.5B.1 / R0029: the first turn-eviction permanently collapses
        Ollama's prompt-prefix KV-cache reuse, so a normal (even
        interruption-heavy) session must stay entirely inside the window.
        At the old cap of 20 the collapse hit mid-session (history 22)."""
        # 38 turns (19 user/assistant pairs) — a long session, still < 40.
        history: list[ConversationTurn] = []
        for i in range(19):
            history.append(_turn(Role.USER, f"pytanie numer {i}"))
            history.append(_turn(Role.ASSISTANT, f"odpowiedz numer {i} " * 8))
        ctx = ConversationContext.build(SYSTEM_PROMPT, history)
        # nothing dropped: the oldest turn is still there
        self.assertEqual(len(ctx.turns), len(history))
        self.assertEqual(ctx.turns[0].content, "pytanie numer 0")

    def test_prompt_prefix_is_byte_stable_as_the_session_grows(self) -> None:
        """Turn N+1's wire message list must be turn N's list plus the new
        turn appended — a pure prefix-extension — for as long as the window
        does not evict. This is the property Ollama's KV-cache reuse needs."""
        history: list[ConversationTurn] = []
        prev_messages: list | None = None
        for i in range(DEFAULT_MAX_TURNS // 2):
            history.append(_turn(Role.USER, f"q{i}"))
            history.append(_turn(Role.ASSISTANT, f"a{i}"))
            msgs = [
                (m.role, m.content)
                for m in ConversationContext.build(
                    SYSTEM_PROMPT, list(history)
                ).to_provider_messages()
            ]
            if prev_messages is not None:
                # every message of the previous build is an unchanged prefix
                self.assertEqual(msgs[: len(prev_messages)], prev_messages)
            prev_messages = msgs

    def test_eviction_above_the_cap_still_keeps_the_newest_turns(self) -> None:
        n = DEFAULT_MAX_TURNS + 12
        history = [_turn(Role.USER, f"m{i}") for i in range(n)]
        ctx = ConversationContext.build(SYSTEM_PROMPT, history)
        self.assertLessEqual(len(ctx.turns), n)
        self.assertEqual(ctx.turns[-1].content, f"m{n - 1}")
        # the char budget (20k) is not the binding constraint here
        self.assertLess(sum(len(t.content) for t in ctx.turns), DEFAULT_MAX_CHARS)

    def test_to_provider_messages_prefixes_system_prompt(self) -> None:
        history = [_turn(Role.USER, "hi"), _turn(Role.ASSISTANT, "hello")]
        context = ConversationContext.build(SYSTEM_PROMPT, history)
        messages = context.to_provider_messages()
        # A language directive (M2.3, R0009) follows every user turn — see
        # test_language.py for dedicated detect/inject coverage; this
        # test's own concern is the system-prompt-prefix/turn ordering.
        self.assertEqual(
            [(m.role, m.content) for m in messages],
            [
                ("system", SYSTEM_PROMPT),
                ("user", "hi"),
                ("system", "Respond to this message in English."),
                ("assistant", "hello"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
