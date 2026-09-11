"""``ConversationPolicy`` / ``ActiveProvider`` / ``ProviderEligibilityPolicy``
(ADR-0004 Decision D; Decision K as amended by Amendment 1 §1).
"""

from __future__ import annotations

import dataclasses
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.realtime.policy import (  # noqa: E402
    DEFAULT_CONVERSATION_POLICY,
    ActiveProvider,
    ConversationPolicy,
    DistributionMode,
    ProviderEligibilityPolicy,
    conversation_policy_from_env,
    provider_eligibility_policy_from_env,
)


class TestConversationPolicyDefault(unittest.TestCase):
    def test_local_only_is_default(self) -> None:
        self.assertEqual(DEFAULT_CONVERSATION_POLICY, ConversationPolicy.LOCAL_ONLY)
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NEXA_CONVERSATION_POLICY", None)
            self.assertEqual(conversation_policy_from_env(), ConversationPolicy.LOCAL_ONLY)

    def test_explicit_values_recognised(self) -> None:
        for value, expected in (
            ("local_only", ConversationPolicy.LOCAL_ONLY),
            ("cloud_preferred", ConversationPolicy.CLOUD_PREFERRED),
            ("auto", ConversationPolicy.AUTO),
        ):
            with mock.patch.dict(os.environ, {"NEXA_CONVERSATION_POLICY": value}):
                self.assertEqual(conversation_policy_from_env(), expected)

    def test_invalid_policy_fails_closed(self) -> None:
        with mock.patch.dict(os.environ, {"NEXA_CONVERSATION_POLICY": "cloud_only"}):
            with self.assertRaises(ValueError):
                conversation_policy_from_env()

    def test_active_provider_values(self) -> None:
        self.assertEqual({p.value for p in ActiveProvider}, {"local", "cloud"})
        # There must never be a CLOUD_ONLY runtime state (local is always
        # the floor — ADR-0004 Decision D).
        self.assertNotIn("cloud_only", {p.value for p in ActiveProvider})


class TestProviderEligibilityPolicy(unittest.TestCase):
    def test_defaults_are_development_and_unverified(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            policy = provider_eligibility_policy_from_env()
        self.assertEqual(policy.distribution_mode, DistributionMode.DEVELOPMENT)
        self.assertFalse(policy.billing_verified)

    def test_development_never_blocked_by_billing(self) -> None:
        policy = ProviderEligibilityPolicy(
            distribution_mode=DistributionMode.DEVELOPMENT, billing_verified=False
        )
        self.assertTrue(policy.cloud_allowed_for(target_user_in_eea_ch_uk=True))
        self.assertTrue(policy.cloud_allowed_for(target_user_in_eea_ch_uk=False))

    def test_distributed_to_eea_ch_uk_requires_billing_verified(self) -> None:
        unverified = ProviderEligibilityPolicy(
            distribution_mode=DistributionMode.DISTRIBUTED, billing_verified=False
        )
        verified = ProviderEligibilityPolicy(
            distribution_mode=DistributionMode.DISTRIBUTED, billing_verified=True
        )
        self.assertFalse(unverified.cloud_allowed_for(target_user_in_eea_ch_uk=True))
        self.assertTrue(verified.cloud_allowed_for(target_user_in_eea_ch_uk=True))

    def test_distributed_outside_eea_ch_uk_not_blocked(self) -> None:
        policy = ProviderEligibilityPolicy(
            distribution_mode=DistributionMode.DISTRIBUTED, billing_verified=False
        )
        self.assertTrue(policy.cloud_allowed_for(target_user_in_eea_ch_uk=False))

    def test_env_loader_reads_distribution_mode_and_billing(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"NEXA_DISTRIBUTION_MODE": "distributed", "NEXA_BILLING_VERIFIED": "true"},
        ):
            policy = provider_eligibility_policy_from_env()
        self.assertEqual(policy.distribution_mode, DistributionMode.DISTRIBUTED)
        self.assertTrue(policy.billing_verified)

    def test_invalid_distribution_mode_fails_closed(self) -> None:
        with mock.patch.dict(os.environ, {"NEXA_DISTRIBUTION_MODE": "public"}):
            with self.assertRaises(ValueError):
                provider_eligibility_policy_from_env()

    def test_invalid_billing_verified_fails_closed(self) -> None:
        with mock.patch.dict(os.environ, {"NEXA_BILLING_VERIFIED": "maybe"}):
            with self.assertRaises(ValueError):
                provider_eligibility_policy_from_env()

    def test_eligibility_policy_never_inspects_api_key_content(self) -> None:
        """Amendment 1 §1 — eligibility must not be, or derive from, a
        property of the API key. Structural check: the dataclass has no
        key/credential-shaped field, and its only inputs are
        ``distribution_mode`` / ``billing_verified``."""
        field_names = {f.name for f in dataclasses.fields(ProviderEligibilityPolicy)}
        self.assertEqual(field_names, {"distribution_mode", "billing_verified"})
        for name in field_names:
            self.assertNotIn("key", name)
            self.assertNotIn("credential", name)
            self.assertNotIn("paid", name)
            self.assertNotIn("tier", name)


if __name__ == "__main__":
    unittest.main()
