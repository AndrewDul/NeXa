"""M2.4B.4 — bilingual voice-input research corpus: deterministic guards.

Offline, no audio, no Ollama. Proves the research corpus/recorder under
`docs/research/m2_4b_bilingual_stt/` has correct, stable ground-truth
metadata and that it touches no production state (no config mutation, no
ConversationSession, no model/provider/TTS/VoiceRuntime-language change).
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = REPO_ROOT / "docs" / "research" / "m2_4b_bilingual_stt"
for p in (str(REPO_ROOT / "src"), str(RESEARCH_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import corpus as C  # noqa: E402

CORPUS_SRC = (RESEARCH_DIR / "corpus.py").read_text(encoding="utf-8")
CAPTURE_SRC = (RESEARCH_DIR / "capture_corpus.py").read_text(encoding="utf-8")


class TestCorpusShape(unittest.TestCase):
    def test_group_counts(self) -> None:
        counts = Counter(it.group for it in C.CORPUS)
        self.assertEqual(counts, Counter({"pl": 15, "en": 15, "short": 10, "mixed": 10}))
        self.assertEqual(len(C.CORPUS), 50)

    def test_meets_task_minimums(self) -> None:
        counts = Counter(it.group for it in C.CORPUS)
        self.assertGreaterEqual(counts["pl"], 15)
        self.assertGreaterEqual(counts["en"], 15)
        self.assertGreaterEqual(counts["short"], 10)
        self.assertGreaterEqual(counts["mixed"], 10)

    def test_uids_unique_and_well_formed(self) -> None:
        uids = [it.uid for it in C.CORPUS]
        self.assertEqual(len(set(uids)), 50)
        for n, it in enumerate(C.CORPUS, 1):
            self.assertTrue(it.uid.startswith(f"{n:03d}_{it.group}_"), it.uid)
            self.assertTrue(it.uid.isascii())
            self.assertNotIn(" ", it.uid)

    def test_language_labels_valid_and_grouped(self) -> None:
        for it in C.CORPUS:
            self.assertIn(it.expected_language, C.LANGUAGE_LABELS)
            if it.group == "pl":
                self.assertEqual(it.expected_language, "pl")
            elif it.group == "en":
                self.assertEqual(it.expected_language, "en")
            elif it.group == "short":
                self.assertEqual(it.expected_language, "ambiguous")
            elif it.group == "mixed":
                self.assertEqual(it.expected_language, "mixed")

    def test_short_leans_and_mixed_primary_are_constrained(self) -> None:
        for it in C.CORPUS:
            if it.group == "short":
                self.assertIn(it.leans, ("pl", "en", "none"))
                self.assertEqual(it.primary, "")
            elif it.group == "mixed":
                self.assertIn(it.primary, ("pl", "en"))
                self.assertEqual(it.leans, "none")
            else:
                self.assertEqual(it.leans, "none")
                self.assertEqual(it.primary, "")

    def test_every_item_has_nonempty_reference_text(self) -> None:
        for it in C.CORPUS:
            self.assertTrue(it.text.strip())

    def test_mixed_items_actually_contain_both_languages(self) -> None:
        # sanity guard against a mislabelled monolingual line: each
        # code-switch line must contain at least one clearly-English word
        # and at least one clearly-Polish signal (a diacritic or a PL-only
        # word). Tokenised on non-letters; substring, not a real parser.
        import re

        pl_diacritics = set("ąćęłńóśźż")
        pl_words = {
            "wróćmy", "wrócić", "dlaczego", "prościej", "jeszcze", "powiedz",
            "gwiazdach", "polsku", "polskiego", "dobra", "ale", "to", "do",
            "mi", "po",
        }
        en_words = {
            "tell", "me", "more", "okay", "lets", "let", "continue", "what",
            "black", "hole", "is", "can", "you", "explain", "a", "now",
        }
        for it in C.CORPUS:
            if it.group != "mixed":
                continue
            low = it.text.lower()
            tokens = set(re.findall(r"[a-ząćęłńóśźż]+", low))
            has_pl = bool(pl_diacritics & set(low)) or bool(tokens & pl_words)
            has_en = bool(tokens & en_words)
            self.assertTrue(has_pl, f"no Polish signal: {it.text}")
            self.assertTrue(has_en, f"no English signal: {it.text}")


class TestCorpusSerialisation(unittest.TestCase):
    def test_as_records_round_trips_json(self) -> None:
        recs = C.as_records()
        self.assertEqual(len(recs), 50)
        blob = json.dumps(recs, ensure_ascii=False)
        again = json.loads(blob)
        self.assertEqual([r["uid"] for r in again], [it.uid for it in C.CORPUS])
        for r in again:
            self.assertEqual(
                set(r),
                {"uid", "group", "expected_language", "text", "leans", "primary", "acceptable"},
            )

    def test_build_corpus_is_deterministic(self) -> None:
        a = C.build_corpus()
        b = C.build_corpus()
        self.assertEqual([x.uid for x in a], [x.uid for x in b])
        self.assertEqual([x.text for x in a], [x.text for x in b])

    def test_slug_is_ascii_and_stable(self) -> None:
        self.assertEqual(C._slug("Wróćmy do polskiego."), "wrocmy_do_polskiego")
        self.assertEqual(C._slug("What is a black hole?"), "what_is_a_black_hole")
        self.assertTrue(C._slug("Zażółć gęślą jaźń").isascii())


class TestNoProductionImpact(unittest.TestCase):
    def test_importing_corpus_does_not_touch_nexa_config(self) -> None:
        # corpus.py must not import nexa at all
        tree = ast.parse(CORPUS_SRC)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertFalse(
            any(m == "nexa" or m.startswith("nexa.") for m in imported),
            f"corpus.py imports nexa: {sorted(imported)}",
        )

    def test_capture_tool_only_uses_the_readonly_device_helper(self) -> None:
        tree = ast.parse(CAPTURE_SRC)
        from_nexa = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("nexa"):
                from_nexa.add(f"{node.module}:{','.join(a.name for a in node.names)}")
        # the ONLY nexa dependency is the by-name input-device resolver
        self.assertEqual(from_nexa, {"nexa.voice.device:find_device_index"})

    def test_research_tooling_never_constructs_production_authorities(self) -> None:
        for src, name in ((CORPUS_SRC, "corpus.py"), (CAPTURE_SRC, "capture_corpus.py")):
            for forbidden in (
                "ConversationSession(", "build_default_session(", "LocalModelProvider(",
                "warm_up_session(", "VoiceRuntime(", "DEFAULT_LOCAL_MODEL =",
                "DEFAULT_LOCAL_NUM_THREAD =", "DEFAULT_LOCAL_KEEP_ALIVE =",
                "WhisperCppTranscriber(",
            ):
                self.assertNotIn(forbidden, src, f"{name} contains {forbidden!r}")

    def test_research_tooling_lives_only_under_docs_research(self) -> None:
        self.assertTrue((RESEARCH_DIR / "corpus.py").exists())
        self.assertFalse((REPO_ROOT / "src" / "nexa" / "corpus.py").exists())
        self.assertFalse((REPO_ROOT / "apps" / "capture_corpus.py").exists())


if __name__ == "__main__":
    unittest.main()
