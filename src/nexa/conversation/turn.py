"""``ConversationTurn`` — one user or assistant turn (ADR-0002 D1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    role: Role
    content: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: M2.5B — set True only on an ASSISTANT turn that was cut short by a
    #: barge-in: ``content`` then holds the prefix NeXa actually spoke, not
    #: the full generated reply. Additive + defaulted: every pre-M2.5B
    #: construction site and the whole typed-chat path are byte-for-byte
    #: unchanged. ``content`` stays clean transcript text — the "cut off"
    #: signal to the model is added deterministically at wire-build time
    #: (see ``ConversationContext.to_provider_messages`` /
    #: ``INTERRUPTED_WIRE_SUFFIX``), never stored here.
    interrupted: bool = False
