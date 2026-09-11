"""Gemini credential loading (ADR-0004 Decision K, amended by Amendment 1
§1). Uses fake credentials only — the real operator key is never copied
into a test.
"""

from __future__ import annotations

import dataclasses
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.realtime.gemini.credentials import (  # noqa: E402
    ENV_VAR,
    EnvThenFileCredentialSource,
    GeminiCredential,
    GeminiCredentialError,
    load_gemini_credential,
    redact,
)

FAKE_KEY = "FAKE-TEST-KEY-0123456789"


class TestRedaction(unittest.TestCase):
    def test_redact_shows_only_a_prefix_and_length(self) -> None:
        redacted = redact(FAKE_KEY)
        self.assertTrue(redacted.startswith(FAKE_KEY[:4]))
        self.assertNotIn(FAKE_KEY[4:], redacted)
        self.assertIn(str(len(FAKE_KEY)), redacted)

    def test_redact_handles_empty(self) -> None:
        self.assertEqual(redact(""), "<empty>")

    def test_credential_repr_never_leaks_the_value(self) -> None:
        credential = GeminiCredential(_value=FAKE_KEY, source="env")
        rendered = repr(credential)
        self.assertNotIn(FAKE_KEY, rendered)
        self.assertEqual(str(credential), rendered)

    def test_reveal_is_the_only_way_to_get_the_raw_value(self) -> None:
        credential = GeminiCredential(_value=FAKE_KEY, source="env")
        self.assertEqual(credential.reveal(), FAKE_KEY)


class TestEnvBeforeFile(unittest.TestCase):
    def test_env_var_takes_priority_over_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secrets_file = Path(tmp) / "gemini.env"
            secrets_file.write_text(f"{ENV_VAR}=FROM-FILE\n", encoding="utf-8")
            secrets_file.chmod(0o600)
            source = EnvThenFileCredentialSource(secrets_file=secrets_file)
            with mock.patch.dict(os.environ, {ENV_VAR: FAKE_KEY}):
                credential = load_gemini_credential(source)
            self.assertEqual(credential.reveal(), FAKE_KEY)
            self.assertEqual(credential.source, "env")

    def test_falls_back_to_file_when_env_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secrets_file = Path(tmp) / "gemini.env"
            secrets_file.write_text(f"{ENV_VAR}={FAKE_KEY}\n", encoding="utf-8")
            secrets_file.chmod(0o600)
            source = EnvThenFileCredentialSource(secrets_file=secrets_file)
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ.pop(ENV_VAR, None)
                credential = load_gemini_credential(source)
            self.assertEqual(credential.reveal(), FAKE_KEY)
            self.assertEqual(credential.source, "file")

    def test_missing_credential_raises_typed_error_without_the_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secrets_file = Path(tmp) / "missing.env"
            source = EnvThenFileCredentialSource(secrets_file=secrets_file)
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ.pop(ENV_VAR, None)
                with self.assertRaises(GeminiCredentialError):
                    load_gemini_credential(source)

    def test_unsafe_file_permissions_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secrets_file = Path(tmp) / "gemini.env"
            secrets_file.write_text(f"{ENV_VAR}={FAKE_KEY}\n", encoding="utf-8")
            secrets_file.chmod(0o644)  # world-readable — unsafe
            source = EnvThenFileCredentialSource(secrets_file=secrets_file)
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ.pop(ENV_VAR, None)
                with self.assertLogs(
                    "nexa.realtime.gemini.credentials", level="WARNING"
                ) as cm:
                    load_gemini_credential(source)
            self.assertTrue(any("readable/writable" in m for m in cm.output))

    def test_safe_file_permissions_do_not_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secrets_file = Path(tmp) / "gemini.env"
            secrets_file.write_text(f"{ENV_VAR}={FAKE_KEY}\n", encoding="utf-8")
            secrets_file.chmod(0o600)
            mode = stat.S_IMODE(secrets_file.stat().st_mode)
            self.assertEqual(mode, 0o600)
            source = EnvThenFileCredentialSource(secrets_file=secrets_file)
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ.pop(ENV_VAR, None)
                with self.assertRaises(AssertionError):
                    with self.assertLogs(
                        "nexa.realtime.gemini.credentials", level="WARNING"
                    ):
                        load_gemini_credential(source)


class TestNoTierInference(unittest.TestCase):
    def test_credential_has_no_paid_free_or_tier_field(self) -> None:
        field_names = {f.name for f in dataclasses.fields(GeminiCredential)}
        self.assertEqual(field_names, {"_value", "source"})
        for name in field_names:
            self.assertNotIn("paid", name)
            self.assertNotIn("tier", name)
            self.assertNotIn("billing", name)


if __name__ == "__main__":
    unittest.main()
