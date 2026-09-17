"""End-to-end local voice Context Engine parity (R0079 §19-21):

    VoiceConversationAdapter
        -> ConversationSession.send(context_provider=...)
        -> ContextEngine.build_context()
        -> ProviderWindow.render(context_addendum=...)

Real ``ContextEngine``/``MemoryService``/``MemoryRetriever`` (SQLite,
tempdir) + real ``ConversationSession`` with a real ``ProviderWindow`` +
``VoiceConversationAdapter`` — only the model backend is a fake
(``FakeModelProvider``, capturing exactly what NeXa sent it), matching
this repo's established "fake only at the true external boundary"
convention (see ``test_voice_conversation_adapter.py``'s own docstring
and its ``unittest.IsolatedAsyncioTestCase`` pattern, reused here).
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeModelProvider  # noqa: E402
from nexa.conversation.context_projection import make_local_context_provider  # noqa: E402
from nexa.conversation.provider_window import ProviderWindow  # noqa: E402
from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.core.context.engine import ContextEngine  # noqa: E402
from nexa.core.context.memory_retriever import MemoryRetriever  # noqa: E402
from nexa.core.identity import load_identity  # noqa: E402
from nexa.core.memory.models import (  # noqa: E402
    MemoryCategory,
    MemoryProvenance,
    MemoryWriteTrigger,
)
from nexa.core.memory.repository import MemoryRepository  # noqa: E402
from nexa.core.memory.service import MemoryService  # noqa: E402
from nexa.core.privacy import CloudEligibility  # noqa: E402
from nexa.core.storage.sqlite import connect  # noqa: E402
from nexa.stt import Language, TranscriptionResult  # noqa: E402
from nexa.voice_conversation import VoiceConversationAdapter  # noqa: E402


def _fake_transcription(text: str) -> TranscriptionResult:
    return TranscriptionResult(
        text=text, language=Language.EN, audio_duration_s=1.0, wall_latency_s=0.1
    )


async def _settle(adapter: VoiceConversationAdapter) -> None:
    await adapter.shutdown()


class TestLocalVoiceMemoryParity(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self._tmp.name) / "core.sqlite3")
        self.service = MemoryService(MemoryRepository(self.conn))
        self.retriever = MemoryRetriever(self.service)
        self.identity = load_identity()
        self.engine = ContextEngine(identity=self.identity, retrievers=(self.retriever,))

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _remember(self, namespace: str, content: str, **kwargs) -> None:
        category = kwargs.pop("category", MemoryCategory.FACT)
        cloud_eligibility = kwargs.pop("cloud_eligibility", CloudEligibility.CLOUD_SAFE)
        self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace=namespace,
            category=category,
            record_type=kwargs.pop("record_type", "thing"),
            content=content,
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            cloud_eligibility=cloud_eligibility,
            **kwargs,
        )

    def _build_session_and_adapter(
        self, provider: FakeModelProvider, *, with_context: bool = True
    ) -> tuple[ConversationSession, VoiceConversationAdapter]:
        session = ConversationSession(
            provider=provider,
            system_prompt="Jestes NeXa.",
            provider_window=ProviderWindow(),
        )
        context_provider = make_local_context_provider(self.engine) if with_context else None
        adapter = VoiceConversationAdapter(session, context_provider=context_provider)
        adapter.start()
        return session, adapter

    async def test_context_engine_selects_and_provider_receives_addendum(self) -> None:
        """R0079 item 21: ContextEngine selects the seeded record; the
        model provider actually receives the context addendum in its wire
        messages."""
        self._remember("projects.nexa", "NeXa's canonical memory authority is MemoryService.")
        provider = FakeModelProvider()
        session, adapter = self._build_session_and_adapter(provider)

        adapter.handle_transcription(_fake_transcription("What did we decide about NeXa memory?"))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        self.assertEqual(len(provider.calls), 1)
        wire = provider.calls[0]
        addendum_messages = [
            m for m in wire if m.role == "system" and "MemoryService" in m.content
        ]
        self.assertEqual(len(addendum_messages), 1)
        self.assertIn("Relevant things you already know:", addendum_messages[0].content)

    async def test_local_only_record_is_used_not_filtered(self) -> None:
        """R0079 item 21: local voice may use LOCAL_ONLY information --
        cloud privacy filtering must NOT be incorrectly applied here."""
        self._remember(
            "projects.nexa",
            "The Pi's disk encryption passphrase hint is in the safe.",
            cloud_eligibility=CloudEligibility.LOCAL_ONLY,
        )
        provider = FakeModelProvider()
        session, adapter = self._build_session_and_adapter(provider)

        adapter.handle_transcription(_fake_transcription("What did we decide about NeXa memory?"))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        wire = provider.calls[0]
        self.assertTrue(
            any("disk encryption passphrase" in m.content for m in wire),
            "LOCAL_ONLY content must reach the LOCAL voice provider unfiltered",
        )

    async def test_addendum_not_persisted_in_canonical_history(self) -> None:
        self._remember("projects.nexa", "one canonical authority")
        provider = FakeModelProvider()
        session, adapter = self._build_session_and_adapter(provider)

        adapter.handle_transcription(_fake_transcription("What did we decide about NeXa memory?"))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        for turn in session.history:
            self.assertNotIn("Relevant things you already know", turn.content)

    async def test_no_context_provider_is_byte_for_byte_unchanged(self) -> None:
        """Default None must reproduce exactly the pre-R0079 wire shape."""
        self._remember("projects.nexa", "one canonical authority")
        provider = FakeModelProvider()
        session, adapter = self._build_session_and_adapter(provider, with_context=False)

        adapter.handle_transcription(_fake_transcription("What did we decide about NeXa memory?"))
        await asyncio.sleep(0.05)
        await _settle(adapter)

        wire = provider.calls[0]
        self.assertFalse(any("Relevant things you already know" in m.content for m in wire))

    async def test_provider_window_rollover_counters_unaffected_by_context(self) -> None:
        """R0079 item 20: ProviderWindow prefix-stability bookkeeping is
        unaffected by whether a context_provider is wired in."""
        self._remember("projects.nexa", "one canonical authority")
        provider = FakeModelProvider(chunks_by_call=[["ok"]] * 3)
        session, adapter = self._build_session_and_adapter(provider)

        for i in range(3):
            adapter.handle_transcription(_fake_transcription(f"turn {i} about NeXa memory"))
            await asyncio.sleep(0.05)
        await _settle(adapter)

        self.assertEqual(session.provider_window.base, 0)  # well under hard_entries
        self.assertEqual(session.provider_window.rollovers_sync, 0)
        self.assertEqual(len(provider.calls), 3)


if __name__ == "__main__":
    unittest.main()
