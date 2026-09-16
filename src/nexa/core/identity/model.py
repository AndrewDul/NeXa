"""``NeXaIdentity`` — the small, stable, immutable root of NeXa Core (ADR-0005 D1).

This holds only facts that answer "what/who is NeXa, invariantly, across
every user, device, and conversation." It explicitly does NOT hold user
preferences, relationship state, memories, projects/goals, learned facts,
communication-style adaptation, device state, or session/temporary
mood/context — those belong to separate, future NeXa Core subsystems, never
to fields added here (ADR-0005 D1, D5 — no scope creep onto Identity).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NeXaIdentity:
    identity_id: str
    identity_schema_version: int
    name: str
    product_name: str
    purpose: str
    principles: tuple[str, ...]
