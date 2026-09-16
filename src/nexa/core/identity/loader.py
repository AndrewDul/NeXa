"""Loads ``NeXaIdentity`` from versioned config (ADR-0005 D1, D3).

Fails loudly on anything wrong — a missing field, an unknown schema version,
an empty required value — rather than silently falling back to a default
identity. There is exactly one identity; if its config is broken, NeXa
should not start pretending to be something else.
"""

from __future__ import annotations

import json
from pathlib import Path

from .model import NeXaIdentity

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_IDENTITY_PATH = REPO_ROOT / "configs" / "identity" / "nexa_identity_v1.json"

#: The only schema version this loader accepts. A config file declaring any
#: other value is rejected outright (ADR-0005 D1) — no silent migration.
CURRENT_IDENTITY_SCHEMA_VERSION = 1

_REQUIRED_STRING_FIELDS = ("identity_id", "name", "product_name", "purpose")


class IdentityConfigError(ValueError):
    """Raised when identity config is missing, malformed, or the wrong
    schema version. Never caught to fall back to a default identity."""


def load_identity(path: Path | str = DEFAULT_IDENTITY_PATH) -> NeXaIdentity:
    resolved = Path(path)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise IdentityConfigError(f"identity config not found: {resolved}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IdentityConfigError(f"identity config is not valid JSON: {resolved}") from exc

    if not isinstance(data, dict):
        raise IdentityConfigError(f"identity config must be a JSON object: {resolved}")

    required_fields = (*_REQUIRED_STRING_FIELDS, "identity_schema_version", "principles")
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise IdentityConfigError(
            f"identity config {resolved} is missing field(s): {', '.join(missing)}"
        )

    schema_version = data["identity_schema_version"]
    if schema_version != CURRENT_IDENTITY_SCHEMA_VERSION:
        raise IdentityConfigError(
            f"identity config {resolved} has identity_schema_version={schema_version!r}, "
            f"expected {CURRENT_IDENTITY_SCHEMA_VERSION!r}"
        )

    for field_name in _REQUIRED_STRING_FIELDS:
        value = data[field_name]
        if not isinstance(value, str) or not value.strip():
            raise IdentityConfigError(
                f"identity config {resolved} field {field_name!r} must be a non-empty string"
            )

    principles = data["principles"]
    if not isinstance(principles, list) or not principles:
        raise IdentityConfigError(
            f"identity config {resolved} field 'principles' must be a non-empty list"
        )
    for item in principles:
        if not isinstance(item, str) or not item.strip():
            raise IdentityConfigError(
                f"identity config {resolved} 'principles' entries must all be non-empty strings"
            )

    return NeXaIdentity(
        identity_id=data["identity_id"],
        identity_schema_version=schema_version,
        name=data["name"],
        product_name=data["product_name"],
        purpose=data["purpose"],
        principles=tuple(principles),
    )
