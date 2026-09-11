"""``ConversationRouter`` (ADR-0004 Decisions D, I, J, M2.6B.2). Proven
with fake local/cloud provider adapters — no real Gemini/Pipecat here.
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
from nexa.conversation.session import ConversationSession, ExternalExchangeOutcome  # noqa: E402
from nexa.realtime.policy import (  # noqa: E402
    ActiveProvider,
    ConversationPolicy,
    DistributionMode,
    ProviderEligibilityPolicy,
)
from nexa.realtime.provider import ProviderReadiness, RealtimeProviderCapabilities  # noqa: E402
from nexa.realtime.router import ConversationRouter  # noqa: E402


class _FakeCloudProvider:
    """Minimal fake — not a real ``RealtimeVoiceProvider`` subclass, just
    duck-typed to what ``ConversationRouter`` actually calls, so tests stay
    independent of the ABC's full surface."""

    def __init__(self, *, fail_on_start: bool = False) -> None:
        self.started_with = None
        self.stopped_reason = None
        self.fail_on_start = fail_on_start
        self.readiness = ProviderReadiness.CONNECTING

    def capabilities(self) -> RealtimeProviderCapabilities:
        return RealtimeProviderCapabilities(
            provider_name="fake_cloud",
            supports_interruption=True,
            supports_session_resumption=True,
            supports_server_transcription=True,
            supports_function_calling=False,
            input_sample_rate_hz=16_000,
            output_sample_rate_hz=24_000,
        )

    async def start(self, snapshot) -> None:
        if self.fail_on_start:
            raise RuntimeError("simulated connect failure")
        self.started_with = snapshot
        self.readiness = ProviderReadiness.READY

    async def stop(self, *, reason: str) -> None:
        self.stopped_reason = reason
        self.readiness = ProviderReadiness.FAILED


def _session() -> ConversationSession:
    return ConversationSession(provider=FakeModelProvider(), system_prompt="p")


class TestLocalOnlyIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_local_only_never_calls_the_factory(self) -> None:
        factory_calls = []

        def factory():
            factory_calls.append(1)
            return _FakeCloudProvider()

        router = ConversationRouter(
            _session(), policy=ConversationPolicy.LOCAL_ONLY, cloud_provider_factory=factory
        )
        started = await router.start_cloud()
        self.assertFalse(started)
        self.assertEqual(factory_calls, [])  # factory never called
        self.assertEqual(router.active_provider, ActiveProvider.LOCAL)

    async def test_local_only_default_policy(self) -> None:
        router = ConversationRouter(_session())
        self.assertEqual(router.policy, ConversationPolicy.LOCAL_ONLY)


class TestCloudPreferredStart(unittest.IsolatedAsyncioTestCase):
    async def test_starts_cloud_when_eligible(self) -> None:
        cloud = _FakeCloudProvider()
        router = ConversationRouter(
            _session(),
            policy=ConversationPolicy.CLOUD_PREFERRED,
            cloud_provider_factory=lambda: cloud,
        )
        started = await router.start_cloud(language_preference="pl")
        self.assertTrue(started)
        self.assertEqual(router.active_provider, ActiveProvider.CLOUD)
        self.assertIsNotNone(cloud.started_with)
        self.assertEqual(cloud.started_with.language_preference, "pl")

    async def test_distributed_eea_ch_uk_requires_billing_verified(self) -> None:
        cloud = _FakeCloudProvider()
        router = ConversationRouter(
            _session(),
            policy=ConversationPolicy.CLOUD_PREFERRED,
            eligibility=ProviderEligibilityPolicy(
                distribution_mode=DistributionMode.DISTRIBUTED, billing_verified=False
            ),
            target_user_in_eea_ch_uk=True,
            cloud_provider_factory=lambda: cloud,
        )
        started = await router.start_cloud()
        self.assertFalse(started)
        self.assertIsNone(cloud.started_with)
        self.assertEqual(router.active_provider, ActiveProvider.LOCAL)

    async def test_no_connectivity_prevents_start(self) -> None:
        router = ConversationRouter(
            _session(),
            policy=ConversationPolicy.CLOUD_PREFERRED,
            cloud_provider_factory=lambda: _FakeCloudProvider(),
            connectivity_check=lambda: False,
        )
        started = await router.start_cloud()
        self.assertFalse(started)


class TestAutoProvisional(unittest.IsolatedAsyncioTestCase):
    async def test_auto_behaves_like_cloud_preferred_when_eligible(self) -> None:
        cloud = _FakeCloudProvider()
        router = ConversationRouter(
            _session(), policy=ConversationPolicy.AUTO, cloud_provider_factory=lambda: cloud
        )
        started = await router.start_cloud()
        self.assertTrue(started)

    async def test_auto_never_touches_cloud_when_ineligible(self) -> None:
        router = ConversationRouter(
            _session(),
            policy=ConversationPolicy.AUTO,
            eligibility=ProviderEligibilityPolicy(
                distribution_mode=DistributionMode.DISTRIBUTED, billing_verified=False
            ),
            target_user_in_eea_ch_uk=True,
            cloud_provider_factory=lambda: _FakeCloudProvider(),
        )
        started = await router.start_cloud()
        self.assertFalse(started)


class TestFailureFallback(unittest.IsolatedAsyncioTestCase):
    async def test_start_failure_produces_a_notice_and_stays_local(self) -> None:
        cloud = _FakeCloudProvider(fail_on_start=True)
        router = ConversationRouter(
            _session(),
            policy=ConversationPolicy.CLOUD_PREFERRED,
            cloud_provider_factory=lambda: cloud,
        )
        started = await router.start_cloud()
        self.assertFalse(started)
        self.assertEqual(router.active_provider, ActiveProvider.LOCAL)
        self.assertEqual(len(router.notices), 1)
        self.assertEqual(router.notices[0].to, ActiveProvider.LOCAL)

    async def test_session_lost_commits_partial_turn_and_falls_back(self) -> None:
        cloud = _FakeCloudProvider()
        router = ConversationRouter(
            _session(),
            policy=ConversationPolicy.CLOUD_PREFERRED,
            cloud_provider_factory=lambda: cloud,
        )
        await router.start_cloud()
        router.begin_cloud_turn()
        router.on_user_transcription("hej", final=True)
        outcome = router.handle_cloud_session_lost(reason="connection error")
        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_USER_ONLY)
        self.assertEqual(router.active_provider, ActiveProvider.LOCAL)
        self.assertEqual(len(router.notices), 1)

    async def test_no_automatic_flapping_back_to_cloud(self) -> None:
        cloud = _FakeCloudProvider(fail_on_start=True)
        router = ConversationRouter(
            _session(),
            policy=ConversationPolicy.CLOUD_PREFERRED,
            cloud_provider_factory=lambda: cloud,
        )
        await router.start_cloud()
        self.assertEqual(router.active_provider, ActiveProvider.LOCAL)
        # nothing re-attempts cloud on its own; only an explicit call does.
        self.assertEqual(len(router.notices), 1)


class TestCanonicalCloudTurnWritePath(unittest.IsolatedAsyncioTestCase):
    async def test_normal_turn_commits_exactly_once(self) -> None:
        session = _session()
        router = ConversationRouter(
            session,
            policy=ConversationPolicy.CLOUD_PREFERRED,
            cloud_provider_factory=lambda: _FakeCloudProvider(),
        )
        await router.start_cloud()
        router.begin_cloud_turn()
        router.on_user_transcription("hej", final=True)
        router.on_assistant_transcription("hi", final=True)
        outcome = router.commit_cloud_turn(response_language="pl")
        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_EXCHANGE)
        self.assertEqual(len(session.history), 2)
        # a second commit_cloud_turn without a new turn commits nothing more
        self.assertIsNone(router.commit_cloud_turn())
        self.assertEqual(len(session.history), 2)

    async def test_interruption_commits_spoken_prefix_only(self) -> None:
        session = _session()
        router = ConversationRouter(
            session,
            policy=ConversationPolicy.CLOUD_PREFERRED,
            cloud_provider_factory=lambda: _FakeCloudProvider(),
        )
        await router.start_cloud()
        router.begin_cloud_turn()
        router.on_user_transcription("tell me a story", final=True)
        router.on_assistant_transcription("Once upon a ti", final=False)
        router.on_interruption()
        router.set_spoken_prefix("Once upon a ti")
        outcome = router.commit_cloud_turn()
        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_EXCHANGE)
        self.assertTrue(session.history[-1].interrupted)
        self.assertEqual(session.history[-1].content, "Once upon a ti")

    async def test_two_consecutive_turns_stay_index_aligned(self) -> None:
        session = _session()
        router = ConversationRouter(
            session,
            policy=ConversationPolicy.CLOUD_PREFERRED,
            cloud_provider_factory=lambda: _FakeCloudProvider(),
        )
        await router.start_cloud()
        for i in range(2):
            router.begin_cloud_turn()
            router.on_user_transcription(f"user {i}", final=True)
            router.on_assistant_transcription(f"assistant {i}", final=True)
            router.commit_cloud_turn()
        self.assertEqual(len(session.history), 4)
        self.assertEqual(len(session.history), len(session._response_languages))  # noqa: SLF001


class TestFreshSnapshotAfterResumptionFailure(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_snapshot_reflects_current_canonical_state(self) -> None:
        session = _session()
        router = ConversationRouter(
            session,
            policy=ConversationPolicy.CLOUD_PREFERRED,
            cloud_provider_factory=lambda: _FakeCloudProvider(),
        )
        session.record_external_exchange("earlier turn", "earlier reply")
        snapshot = await router.request_fresh_snapshot_after_resumption_failure()
        contents = [t.content for t in snapshot.recent_turns]
        self.assertIn("earlier turn", contents)
        self.assertIn("earlier reply", contents)


class TestSetPolicy(unittest.TestCase):
    def test_set_policy_updates_and_only_this_router_acts_on_it(self) -> None:
        router = ConversationRouter(_session())
        self.assertEqual(router.policy, ConversationPolicy.LOCAL_ONLY)
        router.set_policy(ConversationPolicy.CLOUD_PREFERRED)
        self.assertEqual(router.policy, ConversationPolicy.CLOUD_PREFERRED)


if __name__ == "__main__":
    unittest.main()
