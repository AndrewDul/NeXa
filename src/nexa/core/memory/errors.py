"""Exceptions raised by ``nexa.core.memory``. Never caught to silently
coerce, truncate, or fabricate data — every one of these means the caller
must fix its input or handle a genuine absence."""

from __future__ import annotations


class MemoryError(RuntimeError):
    """Base class for all ``nexa.core.memory`` errors."""


class MemoryValidationError(MemoryError, ValueError):
    """A write violated a validation rule: malformed ``namespace``/
    ``record_type`` (R0073 §6), a non-JSON-serializable or non-object
    ``payload`` (R0073 §5), an out-of-range ``limit`` (R0073 §7 scale
    rules), or a malformed ``hard_delete`` ``reason_code`` (R0073 final
    review §4)."""


class MemoryNotFoundError(MemoryError):
    """Referenced a ``memory_id`` that does not exist (e.g. ``supersede``
    on an unknown id)."""
