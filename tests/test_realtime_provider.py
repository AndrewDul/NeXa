"""``RealtimeVoiceProvider`` boundary contract (ADR-0004 Decision B).

Proves the abstraction is usable (a minimal fake can implement it) without
pulling in any cloud SDK, and that it is a peer of ``ModelProvider`` — not a
subtype.
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.providers.base import ModelProvider  # noqa: E402
from nexa.realtime.provider import (  # noqa: E402
    AssistantAudioEvent,
    ProviderEvent,
    ProviderReadiness,
    RealtimeProviderCapabilities,
    RealtimeVoiceProvider,
    UserTranscriptionEvent,
)
from nexa.realtime.snapshot import CloudContextSnapshot  # noqa: E402


class _FakeRealtimeVoiceProvider(RealtimeVoiceProvider):
    """Minimal concrete implementation proving the ABC is a coherent,
    implementable contract."""

    def __init__(self) -> None:
        self._readiness = ProviderReadiness.CONNECTING
        self.started_with: CloudContextSnapshot | None = None
        self.stopped_reason: str | None = None
        self.sent_audio: list[bytes] = []
        self.turn_starts = 0
        self.turn_ends = 0
        self.cancels = 0

    def capabilities(self) -> RealtimeProviderCapabilities:
        return RealtimeProviderCapabilities(
            provider_name="fake",
            supports_interruption=True,
            supports_session_resumption=True,
            supports_server_transcription=True,
            supports_function_calling=False,
            input_sample_rate_hz=16_000,
            output_sample_rate_hz=24_000,
            max_session_seconds=900.0,
        )

    @property
    def readiness(self) -> ProviderReadiness:
        return self._readiness

    async def start(self, snapshot: CloudContextSnapshot) -> None:
        self.started_with = snapshot
        self._readiness = ProviderReadiness.READY

    async def stop(self, *, reason: str) -> None:
        self.stopped_reason = reason
        self._readiness = ProviderReadiness.FAILED

    async def user_turn_start(self) -> None:
        self.turn_starts += 1

    async def send_user_audio(self, pcm: bytes) -> None:
        self.sent_audio.append(pcm)

    async def user_turn_end(self) -> None:
        self.turn_ends += 1

    async def cancel(self) -> None:
        self.cancels += 1

    async def events(self) -> AsyncIterator[ProviderEvent]:
        yield UserTranscriptionEvent(text="hej", final=True)
        yield AssistantAudioEvent(pcm=b"\x00\x01")


class TestRealtimeVoiceProviderContract(unittest.IsolatedAsyncioTestCase):
    def test_is_not_a_model_provider_subtype(self) -> None:
        # ADR-0004 Decision B: a peer of ModelProvider, never a subtype.
        self.assertFalse(issubclass(RealtimeVoiceProvider, ModelProvider))
        self.assertFalse(issubclass(ModelProvider, RealtimeVoiceProvider))

    def test_capabilities_answerable_before_start(self) -> None:
        provider = _FakeRealtimeVoiceProvider()
        caps = provider.capabilities()
        self.assertEqual(caps.provider_name, "fake")
        self.assertEqual(caps.input_sample_rate_hz, 16_000)
        self.assertEqual(caps.output_sample_rate_hz, 24_000)

    async def test_lifecycle(self) -> None:
        provider = _FakeRealtimeVoiceProvider()
        self.assertEqual(provider.readiness, ProviderReadiness.CONNECTING)

        snapshot = CloudContextSnapshot(
            system_instruction="role card",
            language_preference=None,
            recent_turns=(),
            policy_name="cloud_preferred",
            active_provider_name="cloud",
        )
        await provider.start(snapshot)
        self.assertEqual(provider.readiness, ProviderReadiness.READY)
        self.assertIs(provider.started_with, snapshot)

        await provider.user_turn_start()
        await provider.send_user_audio(b"\x01\x02")
        await provider.user_turn_end()
        await provider.cancel()
        self.assertEqual(provider.turn_starts, 1)
        self.assertEqual(provider.turn_ends, 1)
        self.assertEqual(provider.cancels, 1)
        self.assertEqual(provider.sent_audio, [b"\x01\x02"])

        events = [event async for event in provider.events()]
        self.assertEqual(len(events), 2)
        self.assertIsInstance(events[0], UserTranscriptionEvent)
        self.assertIsInstance(events[1], AssistantAudioEvent)

        await provider.stop(reason="test done")
        self.assertEqual(provider.stopped_reason, "test done")
        self.assertEqual(provider.readiness, ProviderReadiness.FAILED)

    def test_readiness_enum_covers_lifecycle_contract(self) -> None:
        expected = {
            "connecting",
            "context_init",
            "ready",
            "degraded",
            "reconnecting",
            "failed",
        }
        self.assertEqual({r.value for r in ProviderReadiness}, expected)


if __name__ == "__main__":
    unittest.main()
