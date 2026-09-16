"""M3.1 Identity Foundation (ADR-0005): ``NeXaIdentity`` load / render.

10 acceptance criteria: deterministic load, fail-loud on missing field,
fail-loud on wrong schema version, immutability, deterministic rendering,
rendered text contains name/purpose/principles, rendered text excludes
anything credential/env/memory/private-data-shaped, Identity loads and
renders independently of Persona, `bootstrap.build_default_session()`
composes both without breaking persona, and the broader regression suite
(run separately, not in this file).
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.core.identity import (  # noqa: E402
    DEFAULT_IDENTITY_PATH,
    IdentityConfigError,
    NeXaIdentity,
    load_identity,
    render_identity_instruction,
)


def _write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class TestLoadIdentity(unittest.TestCase):
    def test_default_config_loads_deterministically(self) -> None:
        first = load_identity()
        second = load_identity()
        self.assertEqual(first, second)
        self.assertEqual(first.name, "NeXa")
        self.assertTrue(first.purpose)
        self.assertGreater(len(first.principles), 0)

    def test_missing_required_field_fails_loudly(self) -> None:
        with self.assertRaises(IdentityConfigError) as ctx:
            load_identity(_SCRATCH_DIR / "missing_field.json")
        self.assertIn("purpose", str(ctx.exception))

    def test_wrong_schema_version_fails_loudly(self) -> None:
        with self.assertRaises(IdentityConfigError) as ctx:
            load_identity(_SCRATCH_DIR / "bad_schema.json")
        self.assertIn("identity_schema_version", str(ctx.exception))

    def test_missing_file_fails_loudly(self) -> None:
        with self.assertRaises(IdentityConfigError):
            load_identity(_SCRATCH_DIR / "does_not_exist.json")

    def test_empty_principles_fails_loudly(self) -> None:
        with self.assertRaises(IdentityConfigError):
            load_identity(_SCRATCH_DIR / "empty_principles.json")


class TestImmutability(unittest.TestCase):
    def test_identity_is_frozen(self) -> None:
        identity = load_identity()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            identity.name = "SomethingElse"  # type: ignore[misc]

    def test_principles_is_a_tuple(self) -> None:
        identity = load_identity()
        self.assertIsInstance(identity.principles, tuple)


class TestRenderIdentityInstruction(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = NeXaIdentity(
            identity_id="test_id",
            identity_schema_version=1,
            name="NeXa",
            product_name="NeXa IkiGai",
            purpose="Be one personal AI system across devices.",
            principles=("Local-first.", "Privacy-first."),
        )

    def test_rendering_is_deterministic(self) -> None:
        first = render_identity_instruction(self.identity)
        second = render_identity_instruction(self.identity)
        self.assertEqual(first, second)

    def test_rendered_text_contains_name_purpose_and_principles(self) -> None:
        rendered = render_identity_instruction(self.identity)
        self.assertIn(self.identity.name, rendered)
        self.assertIn(self.identity.purpose, rendered)
        for principle in self.identity.principles:
            self.assertIn(principle, rendered)

    def test_rendered_text_excludes_credentials_env_memory_and_private_data(self) -> None:
        os.environ["NEXA_TEST_SECRET_SHOULD_NOT_LEAK"] = "leaked-value-should-not-appear"
        try:
            rendered = render_identity_instruction(self.identity)
        finally:
            del os.environ["NEXA_TEST_SECRET_SHOULD_NOT_LEAK"]
        self.assertNotIn("leaked-value-should-not-appear", rendered)
        for forbidden in ("api_key", "password", "token", "memory", "user_id"):
            self.assertNotIn(forbidden, rendered.lower())


class TestPersonaIndependence(unittest.TestCase):
    def test_identity_loads_and_renders_without_importing_persona_config(self) -> None:
        """`nexa.core.identity` must not import `nexa.config` (PersonaConfig)
        -- Identity and Persona are independent layers (ADR-0005 D2)."""
        import nexa.core.identity.loader as loader_module
        import nexa.core.identity.model as model_module
        import nexa.core.identity.render as render_module

        for module in (loader_module, model_module, render_module):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("nexa.config", source)
            self.assertNotIn("PersonaConfig", source)

    def test_identity_fields_have_no_persona_or_style_concerns(self) -> None:
        identity = load_identity()
        field_names = {f.name for f in dataclasses.fields(identity)}
        self.assertEqual(
            field_names,
            {
                "identity_id",
                "identity_schema_version",
                "name",
                "product_name",
                "purpose",
                "principles",
            },
        )


class TestBootstrapIntegration(unittest.TestCase):
    def test_build_default_session_composes_identity_and_persona(self) -> None:
        from nexa.bootstrap import build_default_session
        from nexa.config import load_persona
        from nexa.core.identity import load_identity, render_identity_instruction

        session = build_default_session()
        identity_text = render_identity_instruction(load_identity())
        persona = load_persona()

        self.assertIn(identity_text, session.system_prompt)
        self.assertIn(persona.system, session.system_prompt)
        # identity precedes persona -- additive composition, not a replacement
        self.assertLess(
            session.system_prompt.index(identity_text),
            session.system_prompt.index(persona.system),
        )


_SCRATCH_DIR = Path(__file__).resolve().parent / "_scratch_identity_configs"


def setUpModule() -> None:
    _SCRATCH_DIR.mkdir(exist_ok=True)
    valid_base = json.loads(DEFAULT_IDENTITY_PATH.read_text(encoding="utf-8"))

    missing_field = dict(valid_base)
    del missing_field["purpose"]
    _write_config(_SCRATCH_DIR / "missing_field.json", missing_field)

    bad_schema = dict(valid_base)
    bad_schema["identity_schema_version"] = 999
    _write_config(_SCRATCH_DIR / "bad_schema.json", bad_schema)

    empty_principles = dict(valid_base)
    empty_principles["principles"] = []
    _write_config(_SCRATCH_DIR / "empty_principles.json", empty_principles)


def tearDownModule() -> None:
    import shutil

    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
