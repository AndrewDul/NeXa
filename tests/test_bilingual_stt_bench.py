"""M2.4B.4 — deterministic tests for the STT-benchmark scoring logic.

Offline, no audio, no whisper.cpp. Covers the pure functions in
`docs/research/m2_4b_bilingual_stt/score_stt.py` (WER, normalisation,
S-class first pass, mixed-survival) and re-asserts the research tooling
touches no production state.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = REPO_ROOT / "docs" / "research" / "m2_4b_bilingual_stt"
for p in (str(REPO_ROOT / "src"), str(RESEARCH_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import score_stt as S  # noqa: E402
from corpus import CORPUS  # noqa: E402

BENCH_SRC = (RESEARCH_DIR / "bench_stt.py").read_text(encoding="utf-8")
SCORE_SRC = (RESEARCH_DIR / "score_stt.py").read_text(encoding="utf-8")
_BY_UID = {it.uid: it for it in CORPUS}


class TestNormalisation(unittest.TestCase):
    def test_punctuation_and_case_are_stripped(self) -> None:
        self.assertEqual(S.norm("Co to jest, czarna dziura."), "co to jest czarna dziura")
        self.assertEqual(S.norm(" What is a BLACK hole? "), "what is a black hole")

    def test_apostrophe_kept_inside_word(self) -> None:
        self.assertEqual(S.norm("I don't know"), "i don't know")

    def test_none_and_empty(self) -> None:
        self.assertEqual(S.norm(None), "")
        self.assertEqual(S.norm("   "), "")


class TestWer(unittest.TestCase):
    def test_identical_is_zero(self) -> None:
        self.assertEqual(S.wer("what is a black hole", "What is a black hole?"), 0.0)

    def test_one_substitution_of_five(self) -> None:
        self.assertAlmostEqual(S.wer("a b c d e", "a b x d e"), 1 / 5)

    def test_deletion_and_insertion(self) -> None:
        self.assertAlmostEqual(S.wer("a b c", "a c"), 1 / 3)
        self.assertAlmostEqual(S.wer("a b c", "a b c d"), 1 / 3)

    def test_empty_hyp_is_full_error(self) -> None:
        self.assertEqual(S.wer("a b c", ""), 1.0)

    def test_best_wer_uses_acceptable_variants(self) -> None:
        # 020_en "...colour of the Sun" has an accepted "color" variant
        it = _BY_UID["020_en_what_is_the_actual_colour_of_the_sun"]
        self.assertIn("color", " ".join(it.acceptable).lower())
        self.assertEqual(S.best_wer(it, "What is the actual color of the Sun?"), 0.0)


class TestSClassFirstPass(unittest.TestCase):
    def test_exact_normalised_is_s0(self) -> None:
        it = _BY_UID["016_en_what_is_a_black_hole"]
        self.assertEqual(S.auto_sclass(it, "What is a black hole?"), "S0")

    def test_empty_transcript_is_s3(self) -> None:
        it = _BY_UID["016_en_what_is_a_black_hole"]
        self.assertEqual(S.auto_sclass(it, ""), "S3")

    def test_non_latin_script_leak_flags_s3(self) -> None:
        it = _BY_UID["031_short_tak"]
        self.assertEqual(S.auto_sclass(it, "タク"), "S3?")

    def test_tiny_lexical_is_s1(self) -> None:
        it = _BY_UID["018_en_what_is_a_star_made_of"]  # "What is a star made of?" (6 w)
        self.assertEqual(S.auto_sclass(it, "What is a star made of"), "S0")  # punct only
        self.assertEqual(S.auto_sclass(it, "What is a star made off"), "S1")  # 1/6 word

    def test_large_distortion_flags_s2_or_worse(self) -> None:
        it = _BY_UID["018_en_what_is_a_star_made_of"]
        self.assertIn(S.auto_sclass(it, "what a star"), ("S2?", "S3?"))


class TestMixedSurvival(unittest.TestCase):
    def test_both_sides_present(self) -> None:
        it = _BY_UID["041_mixed_dobra_tell_me_more"]
        cls, _ = S.mixed_survival(it, "Dobra, tell me more.")
        self.assertEqual(cls, "USABLE?")

    def test_one_side_only(self) -> None:
        it = _BY_UID["041_mixed_dobra_tell_me_more"]
        cls, _ = S.mixed_survival(it, "tell me more")
        self.assertEqual(cls, "PARTIALLY_USABLE?")

    def test_neither_side(self) -> None:
        it = _BY_UID["041_mixed_dobra_tell_me_more"]
        cls, _ = S.mixed_survival(it, "hmmmm")
        self.assertEqual(cls, "BROKEN?")


class TestNoProductionImpact(unittest.TestCase):
    def test_bench_only_reads_stt_paths_and_pins(self) -> None:
        tree = ast.parse(BENCH_SRC)
        from_nexa = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("nexa"):
                from_nexa.add(node.module)
        # only the stt config module (path + pin constants), nothing that
        # constructs a transcriber / session / provider / runtime.
        self.assertEqual(from_nexa, {"nexa.stt.config"})

    def test_research_tooling_constructs_no_production_authorities(self) -> None:
        for src, name in ((BENCH_SRC, "bench_stt.py"), (SCORE_SRC, "score_stt.py")):
            for forbidden in (
                "WhisperCppTranscriber(", "ConversationSession(", "VoiceRuntime(",
                "build_default_session(", "LocalModelProvider(",
                "DEFAULT_LOCAL_MODEL", "PiperHttp",
            ):
                self.assertNotIn(forbidden, src, f"{name} contains {forbidden!r}")

    def test_bench_uses_production_model_and_thread_count(self) -> None:
        # the benchmark must measure the SHIPPING config, not a different one
        self.assertIn("ggml-base-q8_0", BENCH_SRC)
        self.assertIn("DEFAULT_THREADS", BENCH_SRC)
        self.assertNotIn("-bs", BENCH_SRC)  # no beam override -> whisper.cpp default


if __name__ == "__main__":
    unittest.main()
