"""M2.3 `VoiceConversationAdapter` tests — no microphone/whisper model/
Ollama/network required. Uses `FakeModelProvider`/a fake slow `ModelProvider`
at the true external boundary (the model backend), exactly like
`test_conversation_session.py` — never mocking `ConversationSession` or the
adapter itself.

Covers the canonical-path proof (typed + voice reach the identical
`ConversationSession` instance) and the conversation-turn FIFO/concurrency
guarantees required by M2.3's real-hardware-testing-informed spec.
"""

from __future__ import annotations

import asyncio
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
    ModelProvider,
    ModelUnavailableError,
    ProviderDescription,
)
from nexa.stt import Language, TranscriptionResult  # noqa: E402
from nexa.voice_conversation import (  # noqa: E402
    ConversationQueueOverflowError,
    VoiceConversationAdapter,
)

SYSTEM_PROMPT = "Jesteś NeXa — testowa persona."


def _fake_transcription(text: str, language: Language = Language.EN) -> TranscriptionResult:
    return TranscriptionResult(
        text=text, language=language, audio_duration_s=1.0, wall_latency_s=0.1
    )


class _SlowProvider(ModelProvider):
    """A `ModelProvider` whose `generate()` takes real wall-clock time —
    used to prove FIFO/concurrency behavior deterministically without a
    real LLM."""

    def __init__(self, delay_s: float = 0.05) -> None:
        self._delay_s = delay_s
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def describe(self) -> ProviderDescription:
        return ProviderDescription(provider_name="slow", model="slow-model")

    async def generate(self, messages, options, *, cancel_token=None):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        # The last message may be M2.3's trailing language directive
        # (ConversationSession, R0009) rather than the user's own turn.
        user_text = next(m.content for m in reversed(messages) if m.role == "user")
        self.calls.append(user_text)
        try:
            await asyncio.sleep(self._delay_s)
        finally:
            self.in_flight -= 1
        yield f"reply to {user_text}"


async def _settle(adapter: VoiceConversationAdapter) -> None:
    await adapter.shutdown()


class TestCanonicalPathSameSession(unittest.IsolatedAsyncioTestCase):
    """Proves structurally and behaviorally that voice input reaches the
    exact same `ConversationSession` typed chat uses — not a second one."""

    async def test_typed_and_voice_turns_enter_the_same_session_in_order(self) -> None:
        provider = FakeModelProvider([["typed reply"], ["voice reply"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

        # 1. A typed turn through the existing canonical API.
        typed_chunks = [chunk async for chunk in session.send("typed message")]
        self.assertEqual("".join(typed_chunks), "typed reply")

        # 2. A simulated TranscriptionResult through the M2.3 adapter, into
        #    the SAME session object.
        adapter = VoiceConversationAdapter(session)
        adapter.start()
        adapter.handle_transcription(_fake_transcription("voice message"))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        # 4. History/context ordering proves both modalities entered the
        #    same session, in submission order.
        contents = [t.content for t in session.history]
        self.assertEqual(
            contents,
            ["typed message", "typed reply", "voice message", "voice reply"],
        )
        self.assertEqual(session.history[2].role, Role.USER)

    async def test_adapter_holds_no_second_history_object(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(session)

        # The adapter must reference the same session, not wrap/replace it.
        self.assertIs(adapter._session, session)
        self.assertFalse(hasattr(adapter, "history"))
        self.assertFalse(hasattr(adapter, "_history"))

    async def test_voice_and_typed_turns_get_the_identical_language_policy(self) -> None:
        """M2.3/R0009: response-language mirroring is `ConversationSession`
        policy, not a voice-specific behavior. A fresh session's first
        Polish voice transcript must produce the exact same trailing
        language directive a fresh session's first typed Polish turn
        would — because both go through the identical `session.send()`
        call, not a second, voice-only code path. Two independent fresh
        sessions are used (each session only injects a directive on its
        own first turn or an actual language switch, R0009) so both are
        genuinely "first turn" and both are directive-eligible."""
        typed_provider = FakeModelProvider([["typed pl reply"]])
        typed_session = ConversationSession(provider=typed_provider, system_prompt=SYSTEM_PROMPT)
        [c async for c in typed_session.send("Co to jest teleportacja?")]
        typed_directive = typed_provider.calls[0][-1]

        voice_provider = FakeModelProvider([["voice pl reply"]])
        voice_session = ConversationSession(provider=voice_provider, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(voice_session)
        adapter.start()
        adapter.handle_transcription(_fake_transcription("Co to jest grawitacja?", Language.PL))
        await asyncio.sleep(0.05)
        await _settle(adapter)
        voice_directive = voice_provider.calls[0][-1]

        self.assertEqual(typed_directive.role, voice_directive.role)
        self.assertEqual(typed_directive.content, voice_directive.content)


class TestTranscriptToUserTurn(unittest.IsolatedAsyncioTestCase):
    async def test_transcription_text_reaches_conversation_session(self) -> None:
        provider = FakeModelProvider([["Cześć!"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(session)
        adapter.start()

        adapter.handle_transcription(_fake_transcription("Cześć NeXa", Language.PL))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        self.assertEqual(session.history[0].role, Role.USER)
        self.assertEqual(session.history[0].content, "Cześć NeXa")

    async def test_one_transcript_yields_exactly_one_user_turn(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(session)
        adapter.start()

        adapter.handle_transcription(_fake_transcription("one utterance"))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        user_turns = [t for t in session.history if t.role == Role.USER]
        self.assertEqual(len(user_turns), 1)

    async def test_whitespace_only_transcript_is_rejected_explicitly(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(session)
        adapter.start()

        adapter.handle_transcription(_fake_transcription("   \n\t  "))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        self.assertEqual(session.history, ())
        self.assertEqual(provider.calls, [])

    async def test_empty_string_transcript_is_rejected_explicitly(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(session)
        adapter.start()

        adapter.handle_transcription(_fake_transcription(""))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        self.assertEqual(session.history, ())


class TestSttFailureNeverBecomesATurn(unittest.IsolatedAsyncioTestCase):
    async def test_stt_error_produces_no_conversation_turn(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(session)
        adapter.start()

        adapter.handle_transcription_error(RuntimeError("whisper-cli exploded"))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        self.assertEqual(session.history, ())
        self.assertEqual(provider.calls, [])


class TestConversationErrorPropagation(unittest.IsolatedAsyncioTestCase):
    async def test_provider_failure_reaches_caller_and_no_fabricated_reply(self) -> None:
        provider = FakeModelProvider(fail_on_call=0)
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        errors: list[Exception] = []
        completes: list[str] = []
        adapter = VoiceConversationAdapter(
            session, on_conversation_error=errors.append, on_assistant_complete=completes.append
        )
        adapter.start()

        adapter.handle_transcription(_fake_transcription("hello"))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ModelUnavailableError)
        self.assertEqual(completes, [])
        # The user's turn stays in history; no assistant turn was fabricated.
        self.assertEqual(len(session.history), 1)
        self.assertEqual(session.history[0].role, Role.USER)


class TestStreaming(unittest.IsolatedAsyncioTestCase):
    async def test_assistant_tokens_stream_as_the_provider_yields_them(self) -> None:
        provider = FakeModelProvider([["I ", "am ", "NeXa."]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        tokens: list[str] = []
        completes: list[str] = []
        adapter = VoiceConversationAdapter(
            session, on_assistant_token=tokens.append, on_assistant_complete=completes.append
        )
        adapter.start()

        adapter.handle_transcription(_fake_transcription("who are you"))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        self.assertEqual(tokens, ["I ", "am ", "NeXa."])
        self.assertEqual(completes, ["I am NeXa."])

    async def test_on_user_transcript_fires_before_generation(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        events: list[str] = []
        adapter = VoiceConversationAdapter(
            session,
            on_user_transcript=lambda t: events.append(f"user:{t}"),
            on_assistant_token=lambda t: events.append(f"token:{t}"),
        )
        adapter.start()

        adapter.handle_transcription(_fake_transcription("hi"))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        self.assertEqual(events, ["user:hi", "token:ok"])


class TestConversationFifoAndConcurrency(unittest.IsolatedAsyncioTestCase):
    async def test_two_quick_transcriptions_are_processed_fifo(self) -> None:
        provider = _SlowProvider(delay_s=0.05)
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        completes: list[str] = []
        adapter = VoiceConversationAdapter(session, on_assistant_complete=completes.append)
        adapter.start()

        adapter.handle_transcription(_fake_transcription("first"))
        adapter.handle_transcription(_fake_transcription("second"))
        await asyncio.sleep(0.5)
        await _settle(adapter)

        self.assertEqual(provider.calls, ["first", "second"])
        self.assertEqual(completes, ["reply to first", "reply to second"])

    async def test_max_conversation_turn_concurrency_is_one(self) -> None:
        provider = _SlowProvider(delay_s=0.05)
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(session)
        adapter.start()

        adapter.handle_transcription(_fake_transcription("a"))
        adapter.handle_transcription(_fake_transcription("b"))
        adapter.handle_transcription(_fake_transcription("c"))
        await asyncio.sleep(0.5)
        await _settle(adapter)

        self.assertEqual(provider.max_in_flight, 1)
        self.assertEqual(adapter.max_observed_conversation_concurrency, 1)

    async def test_second_transcription_can_arrive_while_first_turn_is_active(self) -> None:
        provider = _SlowProvider(delay_s=0.2)
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(session)
        adapter.start()

        adapter.handle_transcription(_fake_transcription("first"))
        await asyncio.sleep(0.02)  # first turn is definitely still running
        self.assertEqual(provider.in_flight, 1)

        t0 = asyncio.get_running_loop().time()
        adapter.handle_transcription(_fake_transcription("second"))  # must not block
        elapsed = asyncio.get_running_loop().time() - t0
        self.assertLess(elapsed, 0.05, "submitting a second turn must not block on the first")

        await asyncio.sleep(1.0)
        await _settle(adapter)
        self.assertEqual(provider.calls, ["first", "second"])

    async def test_assistant_streams_do_not_interleave(self) -> None:
        provider = _SlowProvider(delay_s=0.05)
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        stream_events: list[tuple[str, str]] = []

        adapter = VoiceConversationAdapter(
            session,
            on_user_transcript=lambda t: stream_events.append(("user", t)),
            on_assistant_token=lambda t: stream_events.append(("token", t)),
            on_assistant_complete=lambda t: stream_events.append(("complete", t)),
        )
        adapter.start()

        adapter.handle_transcription(_fake_transcription("first"))
        adapter.handle_transcription(_fake_transcription("second"))
        await asyncio.sleep(0.5)
        await _settle(adapter)

        # A "complete" for a turn must appear before the next turn's "user".
        complete_indices = [i for i, e in enumerate(stream_events) if e[0] == "complete"]
        user_indices = [i for i, e in enumerate(stream_events) if e[0] == "user"]
        self.assertEqual(len(complete_indices), 2)
        self.assertLess(complete_indices[0], user_indices[1])

    async def test_no_dropped_or_duplicated_turns_under_rapid_submission(self) -> None:
        provider = _SlowProvider(delay_s=0.02)
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(session, max_queue_size=10)
        adapter.start()

        for i in range(5):
            adapter.handle_transcription(_fake_transcription(f"msg{i}"))
        await asyncio.sleep(1.0)
        await _settle(adapter)

        self.assertEqual(provider.calls, [f"msg{i}" for i in range(5)])

    async def test_bounded_overflow_raises_explicit_error_not_silent_drop(self) -> None:
        provider = _SlowProvider(delay_s=0.1)
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        errors: list[Exception] = []
        adapter = VoiceConversationAdapter(
            session, on_conversation_error=errors.append, max_queue_size=1
        )
        adapter.start()

        adapter.handle_transcription(_fake_transcription("a"))  # picked up immediately
        await asyncio.sleep(0.01)
        adapter.handle_transcription(_fake_transcription("b"))  # fills the one slot
        adapter.handle_transcription(_fake_transcription("c"))  # overflow

        await asyncio.sleep(0.05)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ConversationQueueOverflowError)
        await _settle(adapter)


class TestShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_without_start_is_a_safe_no_op(self) -> None:
        provider = FakeModelProvider([["ok"]])
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(session)
        await adapter.shutdown()  # must not raise

    async def test_shutdown_finishes_a_queued_turn_before_exiting(self) -> None:
        provider = _SlowProvider(delay_s=0.05)
        session = ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)
        completes: list[str] = []
        adapter = VoiceConversationAdapter(session, on_assistant_complete=completes.append)
        adapter.start()

        adapter.handle_transcription(_fake_transcription("last words"))
        await adapter.shutdown()

        self.assertEqual(completes, ["reply to last words"])
        self.assertIsNone(adapter._queue._worker_task)


if __name__ == "__main__":
    unittest.main()
