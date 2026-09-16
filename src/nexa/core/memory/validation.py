"""Write-time validation for ``nexa.core.memory`` (R0073 §5, §6). Python-
level, not SQL ``CHECK`` constraints (except payload JSON-shape, which is
also enforced in the schema as a second line of defense) — consistent with
how ``nexa.core.identity``'s loader validates in Python rather than via DB
constraints.
"""

from __future__ import annotations

import json
import re

from .errors import MemoryValidationError

#: dot-hierarchical, lowercase, e.g. "teacher.python", "lifeos.health" (R0073 §6)
NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

#: a single lowercase snake_case token, e.g. "skill_mastery" -- never
#: dot-qualified, never re-prefixed with its own namespace (R0073 §6)
RECORD_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

#: short, machine-oriented hard_delete reason category (R0073 final review §4)
REASON_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

MAX_QUERY_LIMIT = 1000


def validate_namespace(namespace: str) -> None:
    if not NAMESPACE_PATTERN.match(namespace):
        raise MemoryValidationError(
            f"invalid namespace {namespace!r}: must be dot-hierarchical "
            f"lowercase segments, e.g. 'teacher.python' "
            f"(pattern: {NAMESPACE_PATTERN.pattern})"
        )


def validate_record_type(record_type: str, *, namespace: str) -> None:
    if not RECORD_TYPE_PATTERN.match(record_type):
        raise MemoryValidationError(
            f"invalid record_type {record_type!r}: must be a single "
            f"lowercase snake_case token, e.g. 'skill_mastery' "
            f"(pattern: {RECORD_TYPE_PATTERN.pattern})"
        )
    if record_type == namespace or record_type.startswith(namespace + "."):
        raise MemoryValidationError(
            f"record_type {record_type!r} must not repeat its own "
            f"namespace {namespace!r} -- namespace already supplies "
            f"ownership (R0073 §6)"
        )


def validate_payload(payload: dict | None) -> str | None:
    """Returns the canonical JSON serialization (sorted keys, compact
    separators — deterministic across writes of equal content), or
    ``None`` when ``payload`` is ``None``."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise MemoryValidationError(
            f"payload must be a dict or None, got {type(payload).__name__}"
        )
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except TypeError as exc:
        raise MemoryValidationError(f"payload is not JSON-serializable: {exc}") from exc


def validate_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise MemoryValidationError(f"limit must be a positive int, got {limit!r}")
    if limit > MAX_QUERY_LIMIT:
        raise MemoryValidationError(
            f"limit {limit} exceeds MAX_QUERY_LIMIT={MAX_QUERY_LIMIT} -- "
            f"use pagination (cursor) instead of one large query"
        )


def validate_reason_code(reason_code: str) -> None:
    if not REASON_CODE_PATTERN.match(reason_code):
        raise MemoryValidationError(
            f"invalid reason_code {reason_code!r}: must be a short "
            f"lowercase snake_case token, e.g. 'user_requested' "
            f"(pattern: {REASON_CODE_PATTERN.pattern})"
        )
