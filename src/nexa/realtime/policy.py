"""``ConversationPolicy`` / ``ActiveProvider`` / ``ProviderEligibilityPolicy``
(ADR-0004 Decision D; Decision K as amended by Amendment 1 §1).

Persisted, NeXa-owned policy is kept separate from the runtime
``ActiveProvider`` — a natural-language "switch to cloud" intent may be
*recognised* by a model, but only NeXa's own ``ConversationRouter``
(M2.6B.2+) ever executes a switch; nothing in this module does that itself.

Deployment eligibility (Amendment 1 §1) is a separate policy input,
``ProviderEligibilityPolicy`` — **not** a property of the API key. A Gemini
API key carries no paid/free flag; billing/tier belongs to the
project/account and is verified out-of-band (``billing_verified``), never
inferred from the key string or from ``distribution_mode`` itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class ConversationPolicy(StrEnum):
    """User/NeXa-owned intent for which provider family may be used
    (ADR-0004 Decision D). ``LOCAL_ONLY`` is the default and is airtight:
    the cloud code path is never entered."""

    LOCAL_ONLY = "local_only"
    CLOUD_PREFERRED = "cloud_preferred"
    #: Provisional in M2.6B — no privacy/task/cost classifier exists yet
    #: (ADR-0004 Decision D). Not the default.
    AUTO = "auto"


class ActiveProvider(StrEnum):
    """Runtime state: which provider is currently producing turns."""

    LOCAL = "local"
    CLOUD = "cloud"


class DistributionMode(StrEnum):
    """Amendment 1 §1 — does **not** determine the Gemini billing tier; it
    only says whether this NeXa instance is private/operator-only or is
    being made available to other users."""

    DEVELOPMENT = "development"
    DISTRIBUTED = "distributed"


@dataclass(frozen=True, slots=True)
class ProviderEligibilityPolicy:
    """Deployment-policy input for cloud eligibility (ADR-0004 Decision K,
    Amendment 1 §1) — never derived from the API key.

    ``DEVELOPMENT`` mode is not blocked solely because billing is disabled
    (the Gemini API Free Tier is available in some regions, including the
    UK, but this type deliberately does not encode or assume any tier). A
    ``DISTRIBUTED`` release to a user in the EEA, Switzerland, or the UK
    requires ``billing_verified`` — set out-of-band from the project's
    actual billing / paid-service status, never guessed or inferred.
    """

    distribution_mode: DistributionMode = DistributionMode.DEVELOPMENT
    billing_verified: bool = False

    def cloud_allowed_for(self, *, target_user_in_eea_ch_uk: bool) -> bool:
        """Whether cloud may be used given this eligibility policy and
        whether the target user is in the EEA / Switzerland / UK
        (Amendment 1 §1 — the Gemini API terms' distribution requirement)."""
        if self.distribution_mode is DistributionMode.DEVELOPMENT:
            return True
        if target_user_in_eea_ch_uk:
            return self.billing_verified
        return True


DEFAULT_CONVERSATION_POLICY = ConversationPolicy.LOCAL_ONLY

_POLICY_ENV_VAR = "NEXA_CONVERSATION_POLICY"
_DISTRIBUTION_MODE_ENV_VAR = "NEXA_DISTRIBUTION_MODE"
_BILLING_VERIFIED_ENV_VAR = "NEXA_BILLING_VERIFIED"

_TRUE_VALUES = {"true", "1", "yes"}
_FALSE_VALUES = {"false", "0", "no"}


def conversation_policy_from_env() -> ConversationPolicy:
    """``NEXA_CONVERSATION_POLICY`` — env-var-only, fail-closed (mirrors
    ``nexa.config.local_provider_settings_from_env``'s ``NEXA_MODEL_PROVIDER``
    pattern: an unrecognised value raises rather than silently defaulting).
    Unset -> ``LOCAL_ONLY`` (ADR-0004 Decision D default)."""
    raw = os.environ.get(_POLICY_ENV_VAR)
    if raw is None:
        return DEFAULT_CONVERSATION_POLICY
    try:
        return ConversationPolicy(raw)
    except ValueError as exc:
        raise ValueError(
            f"{_POLICY_ENV_VAR}={raw!r} is not a recognised ConversationPolicy "
            f"({', '.join(p.value for p in ConversationPolicy)})"
        ) from exc


def provider_eligibility_policy_from_env() -> ProviderEligibilityPolicy:
    """``NEXA_DISTRIBUTION_MODE`` (default ``development``) +
    ``NEXA_BILLING_VERIFIED`` (default ``false``) — additive, fail-closed.
    Deliberately has no notion of an API key at all."""
    raw_mode = os.environ.get(_DISTRIBUTION_MODE_ENV_VAR, DistributionMode.DEVELOPMENT.value)
    try:
        mode = DistributionMode(raw_mode)
    except ValueError as exc:
        raise ValueError(
            f"{_DISTRIBUTION_MODE_ENV_VAR}={raw_mode!r} is not a recognised "
            f"DistributionMode ({', '.join(m.value for m in DistributionMode)})"
        ) from exc

    raw_billing = os.environ.get(_BILLING_VERIFIED_ENV_VAR, "false").strip().lower()
    if raw_billing in _TRUE_VALUES:
        billing_verified = True
    elif raw_billing in _FALSE_VALUES:
        billing_verified = False
    else:
        raise ValueError(
            f"{_BILLING_VERIFIED_ENV_VAR}={raw_billing!r} must be one of "
            f"{sorted(_TRUE_VALUES | _FALSE_VALUES)}"
        )
    return ProviderEligibilityPolicy(distribution_mode=mode, billing_verified=billing_verified)
