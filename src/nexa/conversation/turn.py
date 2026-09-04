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
