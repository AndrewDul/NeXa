"""Gemini credential loading (ADR-0004 Decision K, amended by Amendment 1
§1).

Env-var-first, then an XDG secret-file fallback. No paid/unpaid flag is
ever derived from the key string — this loader has zero opinion on billing
tier; that is ``nexa.realtime.policy.ProviderEligibilityPolicy``'s job.
Never prints, logs, or ``repr()``'s the secret value.

This module deliberately imports nothing from ``google.genai`` — it is pure
NeXa credential-handling and stays importable even when the optional
``cloud-gemini`` dependency extra (ADR-0004 Decision L) is not installed.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_VAR = "NEXA_GEMINI_API_KEY"
DEFAULT_SECRETS_FILE = Path.home() / ".config" / "nexa" / "secrets" / "gemini.env"


class GeminiCredentialError(RuntimeError):
    """Raised when no usable Gemini credential is found. Never carries a
    secret value — it fires precisely because none was found."""


def redact(value: str) -> str:
    """First few characters + length only — never the full value."""
    if not value:
        return "<empty>"
    head = value[:4]
    return f"{head}…(len={len(value)})"


@dataclass(frozen=True, slots=True)
class GeminiCredential:
    """Holds the loaded key. ``repr()``/``str()`` never reveal it.

    Deliberately has **no** paid/unpaid/tier field (Amendment 1 §1) — a key
    is just a key; deployment eligibility lives in
    ``nexa.realtime.policy.ProviderEligibilityPolicy``.
    """

    _value: str
    #: ``"env"`` or ``"file"`` — never the value itself.
    source: str

    def __repr__(self) -> str:
        return f"GeminiCredential(source={self.source!r}, value={redact(self._value)})"

    __str__ = __repr__

    def reveal(self) -> str:
        """The only way to get the raw value out — an explicit call, never
        an implicit ``str()`` / ``repr()`` / log line."""
        return self._value


class CredentialSource:
    """Seam for where a credential comes from.

    The default implementation is env-var-then-file; a future OS keychain /
    secret-service backend is an added implementation of this same
    interface, not a rewrite (ADR-0004 Decision K).
    """

    def load(self) -> GeminiCredential | None:
        raise NotImplementedError


class EnvThenFileCredentialSource(CredentialSource):
    """Env var first; XDG secret-file fallback (ADR-0004 Decision K)."""

    def __init__(self, *, secrets_file: Path = DEFAULT_SECRETS_FILE) -> None:
        self._secrets_file = secrets_file

    def load(self) -> GeminiCredential | None:
        env_value = os.environ.get(ENV_VAR)
        if env_value:
            return GeminiCredential(_value=env_value, source="env")
        return self._load_from_file()

    def _load_from_file(self) -> GeminiCredential | None:
        path = self._secrets_file
        if not path.is_file():
            return None
        _warn_if_unsafe_permissions(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != ENV_VAR:
                continue
            value = value.strip().strip('"').strip("'")
            if value:
                return GeminiCredential(_value=value, source="file")
        return None


def _warn_if_unsafe_permissions(path: Path) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        logger.warning(
            "nexa.realtime.gemini: secrets file %s is readable/writable by "
            "group or other (mode %o) — expected 600",
            path,
            mode,
        )


def load_gemini_credential(source: CredentialSource | None = None) -> GeminiCredential:
    """Load the Gemini credential or raise ``GeminiCredentialError``.

    Never returns, logs, or prints the raw value implicitly.
    """
    src = source or EnvThenFileCredentialSource()
    credential = src.load()
    if credential is None:
        raise GeminiCredentialError(
            f"No Gemini credential found: set {ENV_VAR} or provide a "
            f"secrets file at {DEFAULT_SECRETS_FILE}"
        )
    return credential
