"""``CoreStore`` — the shared persistence contract future MUTABLE NeXa Core
state (memory, user model, relationship, preferences, goals, learning,
capability/permission/device state) will be built against (ADR-0005 D3).

This module defines the interface only. There is no implementation yet —
``SQLiteCoreStore`` (or whatever concrete store is chosen) is added when the
first real mutable subsystem needs it (planned: M3.2 Memory Foundation),
against this contract, not a new one invented per subsystem.

Immutable, versioned product config (like ``NeXaIdentity``) does NOT go
through ``CoreStore`` — it stays in tracked JSON under ``configs/``, per the
existing ``PersonaConfig`` precedent.

``CoreStore.get``/``put``/``delete`` is a LOW-LEVEL PERSISTENCE SEAM, not a
subsystem-facing API. A subsystem like Memory (M3.2) must define its own
typed repository/service layer on top of a ``CoreStore`` implementation
(e.g. a typed ``MemoryRecord`` + a ``MemoryRepository`` that internally
calls ``CoreStore``) — higher-level semantics (memory types, provenance,
supersession, retrieval-by-subject, etc.) must never depend directly on
this generic ``(collection: str, key: str) -> dict`` shape.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CoreStore(Protocol):
    """Minimal key-value-per-collection contract for future mutable Core
    subsystems. ``collection`` namespaces callers (e.g. ``"memory"``,
    ``"user_model"``) so unrelated subsystems never collide on keys."""

    def get(self, collection: str, key: str) -> dict | None:
        """Return the stored value for ``key`` in ``collection``, or
        ``None`` if absent."""
        ...

    def put(self, collection: str, key: str, value: dict) -> None:
        """Store (replacing) ``value`` for ``key`` in ``collection``."""
        ...

    def delete(self, collection: str, key: str) -> None:
        """Remove ``key`` from ``collection``, if present."""
        ...
