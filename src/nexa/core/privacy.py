"""Explicit cloud-eligibility classification for context NeXa Core may hand
to a cloud provider (ADR-0004 Amendment 2, ADR-0005, R0073 §1).

This is a NeXa Core privacy/export-policy concept — which local, Core-owned
state may leave the device — not a realtime/audio concept. It lives here so
every Core subsystem (memory, identity, future user model/capabilities/
device state) can classify its own data without depending on the realtime/
cloud-provider layer (ADR-0005's ownership rule: the canonical authority for
a Core concept moves inward toward ``nexa.core``, never outward toward a
provider-facing package). ``nexa.realtime.privacy`` re-exports this module
for backward compatibility — see that module's docstring.

Pure, no I/O, no provider/session dependency.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class CloudEligibility(StrEnum):
    """Per-fact classification for whether a piece of local NeXa Core state
    may reach a cloud provider.

    ``LOCAL_ONLY`` — never leaves the device, under any policy.
    ``CLOUD_SAFE`` — may be included in a cloud session's context
      automatically (no per-fact confirmation needed).
    ``CLOUD_WITH_USER_APPROVAL`` — may only be included once the caller has
      obtained explicit user approval for THIS fact; :func:`filter_cloud_safe`
      excludes it exactly like ``LOCAL_ONLY`` — a caller that has obtained
      approval must pass the fact through as ``CLOUD_SAFE`` itself, so an
      approval decision is never made implicitly inside this module.

    This answers exactly one question -- "may this leave the device?" -- and
    is deliberately not a general data-sensitivity classification (local
    logging, export, future encrypted sync, and UI redaction are separate,
    unbuilt policy axes; R0073 §12).
    """

    LOCAL_ONLY = "local_only"
    CLOUD_SAFE = "cloud_safe"
    CLOUD_WITH_USER_APPROVAL = "cloud_with_user_approval"


def filter_cloud_safe(facts: Iterable[tuple[str, CloudEligibility]]) -> tuple[str, ...]:
    """Keep only the text of facts explicitly tagged ``CLOUD_SAFE``.

    Never called with credentials, raw memory records, or full local files —
    callers are expected to pass short, already-summarised strings. This
    function does not know what a "fact" means beyond its own tag; the
    classification decision belongs to whatever NeXa Core component
    produced the fact, not to this filter.
    """
    return tuple(text for text, eligibility in facts if eligibility is CloudEligibility.CLOUD_SAFE)
