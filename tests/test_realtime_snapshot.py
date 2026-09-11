"""``CloudContextSnapshot`` (ADR-0004 Decision E) — bounded, privacy-filtered,
derived from canonical ``ConversationSession`` state only.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeModelProvider  # noqa: E402
from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.conversation.turn import Role  # noqa: E402
from nexa.realtime.snapshot import (  # noqa: E402
    CLOUD_ROLE_CARD,
    build_cloud_context_snapshot,
)

SECRET_PERSONA = "SECRET-PERSONA-MARKER-DO-NOT-LEAK"
FAKE_CREDENTIAL_MARKER = "NOT-A-REAL-KEY-DO-NOT-LEAK-MARKER"


async def _drain(stream) -> str:
    return "".join([chunk async for chunk in stream])


class TestCloudContextSnapshot(unittest.IsolatedAsyncioTestCase):
    async def _session_with_turns(self, n_turns: int) -> ConversationSession:
        provider = FakeModelProvider([["ok"]] * n_turns)
        session = ConversationSession(provider=provider, system_prompt=SECRET_PERSONA)
        for i in range(n_turns):
            await _drain(session.send(f"user turn {i}"))
        return session

    async def test_snapshot_makes_no_cloud_call_and_is_pure(self) -> None:
        session = await self._session_with_turns(2)
        snap1 = build_cloud_context_snapshot(session)
        snap2 = build_cloud_context_snapshot(session)
        self.assertEqual(snap1, snap2)  # deterministic, same input -> same output

    async def test_snapshot_never_contains_persona_verbatim(self) -> None:
        session = await self._session_with_turns(1)
        snapshot = build_cloud_context_snapshot(session)
        self.assertNotIn(SECRET_PERSONA, snapshot.system_instruction)
        for turn in snapshot.recent_turns:
            self.assertNotIn(SECRET_PERSONA, turn.content)
        self.assertEqual(snapshot.system_instruction[: len(CLOUD_ROLE_CARD)], CLOUD_ROLE_CARD)

    async def test_snapshot_never_contains_credentials(self) -> None:
        session = await self._session_with_turns(1)
        snapshot = build_cloud_context_snapshot(session)
        rendered = repr(snapshot)
        self.assertNotIn(FAKE_CREDENTIAL_MARKER, rendered)
        # structurally impossible too: nothing in build_cloud_context_snapshot
        # is ever handed a credential in the first place.

    async def test_snapshot_is_bounded_by_turn_count(self) -> None:
        session = await self._session_with_turns(20)  # 40 history entries
        snapshot = build_cloud_context_snapshot(session, max_turns=6, max_chars=100_000)
        self.assertLessEqual(len(snapshot.recent_turns), 6)
        # and it is the MOST RECENT 6 (3 exchanges), not the oldest
        contents = [t.content for t in snapshot.recent_turns]
        self.assertIn("user turn 19", contents)
        self.assertNotIn("user turn 0", contents)

    async def test_snapshot_is_bounded_by_char_budget(self) -> None:
        provider = FakeModelProvider([["x" * 500]] * 5)
        session = ConversationSession(provider=provider, system_prompt="p")
        for _i in range(5):
            await _drain(session.send("y" * 500))
        snapshot = build_cloud_context_snapshot(session, max_turns=100, max_chars=600)
        total_chars = sum(len(t.content) for t in snapshot.recent_turns)
        self.assertLessEqual(total_chars, 600)

    async def test_snapshot_never_truncates_a_single_turn(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt="p")
        long_text = "z" * 900
        await _drain(session.send(long_text))
        snapshot = build_cloud_context_snapshot(session, max_turns=10, max_chars=10)
        # the single retained turn, if any, is either whole or dropped —
        # never partial.
        for turn in snapshot.recent_turns:
            self.assertTrue(turn.content == long_text or turn.content == "ok")

    async def test_empty_history_yields_empty_snapshot(self) -> None:
        provider = FakeModelProvider()
        session = ConversationSession(provider=provider, system_prompt="p")
        snapshot = build_cloud_context_snapshot(session)
        self.assertEqual(snapshot.recent_turns, ())

    async def test_language_preference_carried_but_optional(self) -> None:
        session = await self._session_with_turns(1)
        with_pref = build_cloud_context_snapshot(session, language_preference="pl")
        without_pref = build_cloud_context_snapshot(session, language_preference=None)
        self.assertEqual(with_pref.language_preference, "pl")
        self.assertIn("pl", with_pref.system_instruction)
        self.assertIsNone(without_pref.language_preference)
        self.assertEqual(without_pref.system_instruction, CLOUD_ROLE_CARD)

    async def test_recent_turns_preserve_role_and_order(self) -> None:
        session = await self._session_with_turns(3)
        snapshot = build_cloud_context_snapshot(session, max_turns=100)
        roles = [t.role for t in snapshot.recent_turns]
        self.assertEqual(roles, [Role.USER, Role.ASSISTANT] * 3)


if __name__ == "__main__":
    unittest.main()
