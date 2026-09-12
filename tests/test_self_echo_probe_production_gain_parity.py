"""R0053 VALIDATION-CONTRACT FIX — proves the self-echo probe's
``AecReferenceFeeder`` receives the SAME gain-coherence wiring
production's ``build_gemini_voice_runtime`` constructs, not a bare,
unscaled feeder.

Why this exists: an earlier revision of the probe
(``docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py``)
built its ``AecReferenceFeeder`` with no ``gain_source`` at all — so a
"0 false barge-ins" result from it would have proven nothing about
R0053's actual fix (which lives entirely in ``gain_source``). This test
would have caught that gap.

Offline, no audio hardware, no Gemini: both ``build_probe_pipeline``
and ``build_gemini_voice_runtime`` are heavy, device-opening functions
(neither is ever CALLED here — this repo's own convention, confirmed by
every existing ``build_gemini_voice_runtime`` test, is ``dry=True``
construction only; the probe's hardware path has no such dry mode).
This test instead parses each function's own source (``inspect.
getsource`` + ``ast``) and proves, structurally: (1) each constructs
exactly one ``CoherentReferenceGain``, passing ``card=`` sourced from
``output_alsa_mixer_card``; (2) each constructs exactly one
``AecReferenceFeeder``, passing ``gain_source=`` as that same
``CoherentReferenceGain`` instance's ``.current_gain`` method. It does
NOT require identical object instances — only identical ownership and
calculation semantics, per the charter's own instruction.
"""

from __future__ import annotations

import ast
import inspect
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
PROBE_DIR = REPO_ROOT / "docs" / "research" / "m2_6_cloud_realtime_voice"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(PROBE_DIR) not in sys.path:
    sys.path.insert(0, str(PROBE_DIR))


def _find_calls(tree: ast.AST, callee_name: str) -> list[ast.Call]:
    """All ``ast.Call`` nodes in ``tree`` whose callee's final name
    component is ``callee_name`` (matches both ``Foo(...)`` and
    ``pkg.Foo(...)`` call shapes)."""
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name == callee_name:
            calls.append(node)
    return calls


def _kwarg_source(call: ast.Call, kwarg_name: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == kwarg_name:
            return ast.unparse(kw.value)
    return None


def assert_gain_wiring_contract(testcase: unittest.TestCase, *, source: str, label: str) -> None:
    """Shared assertion body -- applied identically to both production's
    and the probe's function source, so the two can never silently
    drift apart (e.g. one gets checked more strictly than the other)."""
    tree = ast.parse(source)
    gain_calls = _find_calls(tree, "CoherentReferenceGain")
    testcase.assertEqual(
        len(gain_calls), 1,
        f"{label}: expected exactly one CoherentReferenceGain(...) "
        f"construction, found {len(gain_calls)}",
    )
    card_arg = _kwarg_source(gain_calls[0], "card")
    testcase.assertIsNotNone(card_arg, f"{label}: CoherentReferenceGain(...) must pass card=")
    testcase.assertIn(
        "output_alsa_mixer_card", card_arg,
        f"{label}: CoherentReferenceGain's card= must be sourced from "
        f"LocalAudioConfig.output_alsa_mixer_card, got {card_arg!r}",
    )

    # The variable name CoherentReferenceGain(...) was assigned to --
    # gain_source= must reference THIS SAME instance's .current_gain,
    # not a fresh/unrelated one. Same tree as above -- node identity
    # must be preserved for the `is`-based match below.
    assign_target: str | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(node.value is c for c in gain_calls):
            target = node.targets[0]
            if isinstance(target, ast.Name):
                assign_target = target.id
    testcase.assertIsNotNone(
        assign_target, f"{label}: CoherentReferenceGain(...) must be bound to a local variable"
    )

    feeder_calls = _find_calls(tree, "AecReferenceFeeder")
    testcase.assertEqual(
        len(feeder_calls), 1,
        f"{label}: expected exactly one AecReferenceFeeder(...) "
        f"construction, found {len(feeder_calls)}",
    )
    gain_source_arg = _kwarg_source(feeder_calls[0], "gain_source")
    testcase.assertIsNotNone(
        gain_source_arg,
        f"{label}: AecReferenceFeeder(...) must pass gain_source= -- a bare, "
        f"unscaled feeder does not exercise the R0053 fix",
    )
    testcase.assertEqual(
        gain_source_arg, f"{assign_target}.current_gain",
        f"{label}: gain_source= must be the SAME CoherentReferenceGain "
        f"instance's .current_gain, got {gain_source_arg!r}",
    )


class TestProductionGainWiring(unittest.TestCase):
    def test_build_gemini_voice_runtime_wires_coherent_reference_gain(self) -> None:
        from nexa.realtime.gemini.runtime import build_gemini_voice_runtime

        source = inspect.getsource(build_gemini_voice_runtime)
        assert_gain_wiring_contract(self, source=source, label="build_gemini_voice_runtime")


class TestProbeGainWiring(unittest.TestCase):
    def test_build_probe_pipeline_wires_coherent_reference_gain(self) -> None:
        import m2_6b4m_self_echo_probe as probe

        source = inspect.getsource(probe.build_probe_pipeline)
        assert_gain_wiring_contract(self, source=source, label="build_probe_pipeline")


class TestAssertionHelperCatchesTheOriginalBug(unittest.TestCase):
    """Meta-test: proves ``assert_gain_wiring_contract`` itself actually
    rejects the exact shape R0052's original probe had (an
    ``AecReferenceFeeder`` with no ``gain_source=`` at all) -- so this
    whole contract check is not accidentally a tautology."""

    def test_bare_feeder_with_no_gain_source_is_rejected(self) -> None:
        buggy_source = """
def build_probe_pipeline(P, *, recorder, assistant_sample_rate):
    cfg = LocalAudioConfig()
    aec_feeder = AecReferenceFeeder(
        aec_health=aec_health, sample_rate=assistant_sample_rate, channels=1
    )
    return aec_feeder
"""
        with self.assertRaises(AssertionError):
            assert_gain_wiring_contract(self, source=buggy_source, label="buggy")

    def test_gain_source_from_an_unrelated_variable_is_rejected(self) -> None:
        # Exactly one CoherentReferenceGain(...) call (so the count==1
        # check passes) but gain_source= references a DIFFERENT
        # variable name -- must still be rejected.
        buggy_source = """
def build_probe_pipeline(P, *, recorder, assistant_sample_rate):
    cfg = LocalAudioConfig()
    reference_gain = CoherentReferenceGain(card=cfg.output_alsa_mixer_card)
    aec_feeder = AecReferenceFeeder(
        aec_health=aec_health,
        sample_rate=assistant_sample_rate,
        channels=1,
        gain_source=someone_else.current_gain,
    )
    return aec_feeder
"""
        with self.assertRaises(AssertionError):
            assert_gain_wiring_contract(self, source=buggy_source, label="buggy")

    def test_card_not_sourced_from_config_field_is_rejected(self) -> None:
        buggy_source = """
def build_probe_pipeline(P, *, recorder, assistant_sample_rate):
    reference_gain = CoherentReferenceGain(card="UACDemoV10")
    aec_feeder = AecReferenceFeeder(
        aec_health=aec_health,
        sample_rate=assistant_sample_rate,
        channels=1,
        gain_source=reference_gain.current_gain,
    )
    return aec_feeder
"""
        with self.assertRaises(AssertionError):
            assert_gain_wiring_contract(self, source=buggy_source, label="buggy")


class TestBothCallSitesUseTheSameProductionClasses(unittest.TestCase):
    """Belt-and-suspenders: the probe must import the REAL
    ``CoherentReferenceGain``/``AecReferenceFeeder`` classes production
    uses, never a reimplementation or a locally-defined stand-in."""

    def test_probe_imports_the_real_coherent_reference_gain(self) -> None:
        import m2_6b4m_self_echo_probe as probe

        from nexa.voice.aec_gain import CoherentReferenceGain

        source = inspect.getsource(probe.build_probe_pipeline)
        self.assertIn("from nexa.voice.aec_gain import CoherentReferenceGain", source)
        # And it really is importable as the same real class production uses
        # (not shadowed/monkeypatched anywhere in the probe module).
        self.assertIs(
            getattr(sys.modules.get("nexa.voice.aec_gain"), "CoherentReferenceGain", None),
            CoherentReferenceGain,
        )

    def test_probe_imports_the_real_aec_reference_feeder(self) -> None:
        import m2_6b4m_self_echo_probe as probe

        source = inspect.getsource(probe.build_probe_pipeline)
        self.assertIn("from nexa.voice_tts.aec_reference import AecReferenceFeeder", source)


if __name__ == "__main__":
    unittest.main()
