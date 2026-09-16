"""NeXa Core — the local, provider-independent state NeXa owns (ADR-0004
Amendment 2, ADR-0005).

Each concern (identity, and later memory, user model, personality,
relationship, capabilities, permissions, devices, learning) is its own
narrow subsystem with its own owner module — there is no aggregating
``NeXaCore`` god-object (ADR-0005 D5). Import the subsystem you need
directly, e.g. ``from nexa.core.identity import load_identity``.
"""

from __future__ import annotations
