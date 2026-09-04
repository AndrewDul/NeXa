"""Deterministic tests for ``ConversationContext``'s bounding strategy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation.context import ConversationContext  # noqa: E402
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

    def test_to_provider_messages_prefixes_system_prompt(self) -> None:
        history = [_turn(Role.USER, "hi"), _turn(Role.ASSISTANT, "hello")]
        context = ConversationContext.build(SYSTEM_PROMPT, history)
        messages = context.to_provider_messages()
        self.assertEqual(
            [(m.role, m.content) for m in messages],
            [("system", SYSTEM_PROMPT), ("user", "hi"), ("assistant", "hello")],
        )


if __name__ == "__main__":
    unittest.main()
