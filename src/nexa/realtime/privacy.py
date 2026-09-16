"""Deprecated location — ``CloudEligibility``/``filter_cloud_safe`` moved to
``nexa.core.privacy`` (R0073 §1, ADR-0005): cloud eligibility is a NeXa Core
privacy/export-policy concept, not a realtime/audio one, and Core must never
depend on the realtime/provider layer. Re-exported here only so existing
``from nexa.realtime.privacy import ...`` call sites keep working unchanged
— new code should import ``nexa.core.privacy`` directly.
"""

from __future__ import annotations

from nexa.core.privacy import CloudEligibility, filter_cloud_safe

__all__ = ["CloudEligibility", "filter_cloud_safe"]
