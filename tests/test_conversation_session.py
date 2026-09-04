"""Deterministic tests for the canonical M1.1 turn path (ADR-0002 D1).

Uses ``FakeModelProvider`` only — never talks to Ollama. The live Ollama path
is covered separately in ``test_live_ollama_integration.py``.
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
from nexa.providers.base import GenerationOptions, ModelUnavailableError  # noqa: E402

SYSTEM_PROMPT = "Jesteś NeXa — testowa persona."


async def _drain(stream) -> str:
    return "".join([chunk async for chunk in stream])


class TestConversationSession(unittest.IsolatedAsyncioTestCase):
    async def test_sends_correct_ordered_context(self) -> None:
        provider = FakeModelProvider([["Cześć!"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("Cześć NeXa"))

        sent = provider.calls[0]
        self.assertEqual([m.role for m in sent], ["system", "user"])
        self.assertEqual(sent[0].content, SYSTEM_PROMPT)
        self.assertEqual(sent[1].content, "Cześć NeXa")

    async def test_user_turn_is_stored(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("hello"))

        self.assertEqual(session.history[0].role, Role.USER)
        self.assertEqual(session.history[0].content, "hello")

    async def test_streamed_chunks_are_combined_correctly(self) -> None:
        provider = FakeModelProvider([["I ", "am ", "NeXa."]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        combined = await _drain(session.send("who are you"))

        self.assertEqual(combined, "I am NeXa.")

    async def test_completed_assistant_turn_is_stored(self) -> None:
        provider = FakeModelProvider([["I ", "am ", "NeXa."]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("who are you"))

        self.assertEqual(session.history[1].role, Role.ASSISTANT)
        self.assertEqual(session.history[1].content, "I am NeXa.")

    async def test_context_remains_bounded(self) -> None:
        provider = FakeModelProvider([["ok"]] * 10)
        session = ConversationSession(
            provider=provider, system_prompt=SYSTEM_PROMPT, max_turns=4
        )

        for i in range(5):
            await _drain(session.send(f"message {i}"))

        # 5 user + 5 assistant = 10 turns of real history...
        self.assertEqual(len(session.history), 10)
        # ...but the context built for the model never exceeds max_turns.
        context = session.build_context()
        self.assertLessEqual(len(context.turns), 4)

    async def test_provider_failure_surfaces_explicitly(self) -> None:
        provider = FakeModelProvider(fail_on_call=0)
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        with self.assertRaises(ModelUnavailableError):
            await _drain(session.send("hello"))

    async def test_no_fallback_on_provider_failure(self) -> None:
        provider = FakeModelProvider(fail_on_call=0)
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        with self.assertRaises(ModelUnavailableError):
            await _drain(session.send("hello"))

        # Only the one configured provider was ever called - no second
        # provider/model was silently tried, and no assistant turn exists.
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(session.history), 1)
        self.assertEqual(session.history[0].role, Role.USER)

    async def test_polish_input_flows_through_canonical_path(self) -> None:
        provider = FakeModelProvider([["Cześć! W czym mogę pomóc?"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        reply = await _drain(session.send("Cześć, jak się masz?"))

        self.assertEqual(reply, "Cześć! W czym mogę pomóc?")
        self.assertEqual(session.history[0].content, "Cześć, jak się masz?")
        self.assertEqual(session.history[1].content, reply)

    async def test_english_input_flows_through_canonical_path(self) -> None:
        provider = FakeModelProvider([["Hi! How can I help?"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        reply = await _drain(session.send("Hi, how are you?"))

        self.assertEqual(reply, "Hi! How can I help?")
        self.assertEqual(session.history[0].content, "Hi, how are you?")
        self.assertEqual(session.history[1].content, reply)

    async def test_multi_turn_history_persists_within_session(self) -> None:
        provider = FakeModelProvider([["first reply"], ["second reply"], ["third reply"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("first message"))
        await _drain(session.send("second message"))
        await _drain(session.send("third message"))

        self.assertEqual(len(session.history), 6)
        contents = [t.content for t in session.history]
        self.assertEqual(
            contents,
            [
                "first message",
                "first reply",
                "second message",
                "second reply",
                "third message",
                "third reply",
            ],
        )
        # The third call's context includes the earlier turns, in order.
        third_call_messages = provider.calls[2]
        self.assertEqual(
            [m.content for m in third_call_messages],
            [
                SYSTEM_PROMPT,
                "first message",
                "first reply",
                "second message",
                "second reply",
                "third message",
            ],
        )

    async def test_options_passed_through_unmodified(self) -> None:
        options = GenerationOptions(num_ctx=8192, think=False, num_predict=64)
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(
            provider=provider, system_prompt=SYSTEM_PROMPT, options=options
        )

        await _drain(session.send("hello"))

        self.assertEqual(provider.options_by_call[0], options)


if __name__ == "__main__":
    unittest.main()
