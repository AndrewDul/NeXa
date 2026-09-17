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
from nexa.providers.base import (  # noqa: E402
    GenerationOptions,
    ModelUnavailableError,
    ProviderMessage,
)

SYSTEM_PROMPT = "Jesteś NeXa — testowa persona."


async def _drain(stream) -> str:
    return "".join([chunk async for chunk in stream])


class TestConversationSession(unittest.IsolatedAsyncioTestCase):
    async def test_sends_correct_ordered_context(self) -> None:
        provider = FakeModelProvider([["Cześć!"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("Cześć NeXa"))

        sent = provider.calls[0]
        # A trailing system-role language directive (M2.3, R0009) is
        # appended after the user turn — see test_language.py for
        # dedicated detection/injection coverage; this test's own concern
        # is the persona/history ordering, which stays system-then-user.
        self.assertEqual([m.role for m in sent], ["system", "user", "system"])
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
        # A language directive (R0009) follows every user turn, recomputed
        # identically each time from that turn's own unmodified stored
        # text — see test_language.py and TestResponseLanguageMirroring
        # below for dedicated coverage of the directive itself.
        en_directive = "Respond to this message in English."
        third_call_messages = provider.calls[2]
        self.assertEqual(
            [m.content for m in third_call_messages],
            [
                SYSTEM_PROMPT,
                "first message",
                en_directive,
                "first reply",
                "second message",
                en_directive,
                "second reply",
                "third message",
                en_directive,
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


class TestResponseLanguageMirroring(unittest.IsolatedAsyncioTestCase):
    """M2.3/R0009: real-hardware testing found the persona's own prose
    instruction insufficient (a fresh English session answered in Polish),
    so `ConversationSession` now injects a deterministic per-turn language
    directive. A fake provider cannot prove real-model language *quality*
    — that is the real `gemma4:e4b` acceptance test in `R0009` — but it can
    prove the canonical policy is applied, identically, on every call."""

    async def _sent_messages(self, user_text: str) -> list:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        await _drain(session.send(user_text))
        return provider.calls[0]

    async def test_polish_turn_gets_a_polish_directive(self) -> None:
        sent = await self._sent_messages("Co to jest czarna dziura?")
        self.assertEqual(sent[-1].role, "system")
        self.assertEqual(sent[-1].content, "Odpowiedz na tę wiadomość po polsku.")

    async def test_english_turn_gets_an_english_directive(self) -> None:
        sent = await self._sent_messages("What is a black hole?")
        self.assertEqual(sent[-1].role, "system")
        self.assertEqual(sent[-1].content, "Respond to this message in English.")

    async def test_pl_to_en_switch_within_one_session(self) -> None:
        provider = FakeModelProvider([["pl reply"], ["en reply"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("Powiedz mi krótko czym jest Słońce."))
        await _drain(session.send("And how old is it?"))

        self.assertEqual(provider.calls[0][-1].content, "Odpowiedz na tę wiadomość po polsku.")
        self.assertEqual(provider.calls[1][-1].content, "Respond to this message in English.")
        # The context switch does not erase history — the EN turn's context
        # still includes the PL turn, proving cross-language continuity.
        self.assertIn("Powiedz mi krótko czym jest Słońce.", [m.content for m in provider.calls[1]])

    async def test_each_turns_prompt_prefix_exactly_matches_the_previous_turn(self) -> None:
        """R0009: real-hardware testing found that injecting the directive
        as a message never replayed from stored history (e.g. only for the
        "current" turn) permanently broke Ollama/llama.cpp's prompt-prefix
        KV-cache reuse the instant it was used once — every later turn's
        rebuilt context then diverges from what was actually cached,
        turning every "warm" turn (~2-6s) into a full ~15-20s reprocess for
        the rest of the session. The fix recomputes the same directive
        after *every* historical user turn, purely from that turn's own
        stored text — so turn N+1's prompt must start with byte-for-byte
        the same messages turn N's prompt ended with (plus turn N's own
        reply and the new turn), which is exactly what real prompt-prefix
        caching needs to hit."""
        provider = FakeModelProvider([["r1"], ["r2"], ["r3"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("Hello, how are you?"))
        await _drain(session.send("What is 2 plus 2?"))
        await _drain(session.send("And what is 3 plus 3?"))

        call0, call1, call2 = provider.calls
        expected_call1_prefix = [*call0, ProviderMessage(role="assistant", content="r1")]
        expected_call2_prefix = [*call1, ProviderMessage(role="assistant", content="r2")]
        self.assertEqual(call1[: len(expected_call1_prefix)], expected_call1_prefix)
        self.assertEqual(call2[: len(expected_call2_prefix)], expected_call2_prefix)

    async def test_en_to_pl_switch_within_one_session(self) -> None:
        provider = FakeModelProvider([["en reply"], ["pl reply"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("What is the speed of light?"))
        await _drain(session.send("Powiedz mi to samo po polsku."))

        self.assertEqual(provider.calls[0][-1].content, "Respond to this message in English.")
        self.assertEqual(provider.calls[1][-1].content, "Odpowiedz na tę wiadomość po polsku.")

    async def test_explicit_language_request_overrides_prior_session_language(self) -> None:
        """An explicit request phrased in the target language (the
        acceptance test's own pattern: "odpowiedz po polsku" / "answer in
        English") naturally overrides mirroring because the request message
        *itself* is in that language — no separate override mechanism is
        needed for the phrasing this milestone's acceptance test uses."""
        provider = FakeModelProvider([["en reply"], ["pl reply"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("Let's talk in English for now."))
        await _drain(session.send("Odpowiedz po polsku od teraz."))

        self.assertEqual(provider.calls[1][-1].content, "Odpowiedz na tę wiadomość po polsku.")

    async def test_directive_is_never_stored_in_history(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("Co to jest czarna dziura?"))

        for turn in session.history:
            self.assertNotIn("Odpowiedz na tę wiadomość", turn.content)
            self.assertNotIn("Respond to this message", turn.content)


class TestContextProviderHook(unittest.IsolatedAsyncioTestCase):
    """M3.3 (R0077) — the ``context_provider`` hook on ``send()``, tested
    in isolation from any real Context Engine (see
    ``tests/test_conversation_context_projection.py`` for the full
    integration)."""

    async def test_default_is_byte_identical_to_pre_r0077(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("hello"))

        self.assertEqual(provider.calls[0][0].content, SYSTEM_PROMPT)

    async def test_context_provider_receives_session_after_turn_appended(self) -> None:
        observed: list[str] = []

        def _provider(session: ConversationSession) -> str | None:
            observed.append(session.history[-1].content)
            return None

        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("hello", context_provider=_provider))

        self.assertEqual(observed, ["hello"])

    async def test_context_provider_addendum_appended_to_system_prompt(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("hello", context_provider=lambda s: "extra context block"))

        self.assertIn(SYSTEM_PROMPT, provider.calls[0][0].content)
        self.assertIn("extra context block", provider.calls[0][0].content)

    async def test_context_provider_none_return_leaves_prompt_unchanged(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("hello", context_provider=lambda s: None))

        self.assertEqual(provider.calls[0][0].content, SYSTEM_PROMPT)

    async def test_context_provider_exception_does_not_crash_or_corrupt_history(self) -> None:
        def _broken(session: ConversationSession) -> str | None:
            raise RuntimeError("boom")

        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        result = await _drain(session.send("hello", context_provider=_broken))

        self.assertEqual(result, "ok")
        self.assertEqual(len(session.history), 2)
        self.assertEqual(provider.calls[0][0].content, SYSTEM_PROMPT)

    async def test_extra_context_never_stored_in_history(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        await _drain(session.send("hello", context_provider=lambda s: "SECRET-ADDENDUM"))

        for turn in session.history:
            self.assertNotIn("SECRET-ADDENDUM", turn.content)
        self.assertNotIn("SECRET-ADDENDUM", session.system_prompt)


if __name__ == "__main__":
    unittest.main()
