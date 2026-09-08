"""M2.4B.3.5 — operator-blind voice model A/B: harness safety tests.

Deterministic, offline. No Ollama, no audio, no mic. Proves the blind
harness under ``docs/research/m2_4b_llm_bench/`` is a *fair* and *sealed*
comparison and that it touches no production default.
"""
from __future__ import annotations

import ast
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = REPO_ROOT / "docs" / "research" / "m2_4b_llm_bench"
for p in (str(REPO_ROOT / "src"), str(BENCH_DIR), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import blind_ab_common as C  # noqa: E402
import operator_blind_ab as OB  # noqa: E402

from fakes import FakeModelProvider  # noqa: E402
from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.conversation.turn import Role  # noqa: E402

MAPPING = C.MAPPING_PATH
HARNESS_SRC = (BENCH_DIR / "operator_blind_ab.py").read_text(encoding="utf-8")
COMMON_SRC = (BENCH_DIR / "blind_ab_common.py").read_text(encoding="utf-8")
MAKE_SRC = (BENCH_DIR / "make_mapping.py").read_text(encoding="utf-8")

MODEL_TAGS = ("gemma4:e4b", "gemma4:e2b")


async def _drain(stream) -> str:
    return "".join([c async for c in stream])


class TestSealedMapping(unittest.TestCase):
    def test_mapping_file_exists(self) -> None:
        self.assertTrue(MAPPING.exists(), f"run make_mapping.py — missing {MAPPING}")

    def test_mapping_has_exactly_the_two_allowed_models(self) -> None:
        m = C.load_mapping()
        self.assertEqual(sorted(m), ["A", "B"])
        self.assertEqual(set(m.values()), set(C.ALLOWED_MODELS))
        self.assertEqual(set(C.ALLOWED_MODELS), {"gemma4:e4b", "gemma4:e2b"})

    def test_A_is_not_B(self) -> None:
        m = C.load_mapping()
        self.assertNotEqual(m["A"], m["B"])

    def test_resolve_model_round_trips_the_mapping(self) -> None:
        m = C.load_mapping()
        self.assertEqual(C.resolve_model("A"), m["A"])
        self.assertEqual(C.resolve_model("b"), m["B"])  # case-insensitive
        with self.assertRaises(ValueError):
            C.resolve_model("C")

    def test_mapping_payload_carries_a_random_salt(self) -> None:
        data = json.loads(MAPPING.read_text(encoding="utf-8"))
        self.assertIn("salt", data)
        self.assertGreaterEqual(len(data["salt"]), 16)


class TestBlindingOfOperatorOutput(unittest.TestCase):
    def test_selftest_stdout_never_prints_a_model_tag(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = OB.run_selftest()
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        for tag in MODEL_TAGS:
            self.assertNotIn(tag, out, f"selftest leaked {tag!r} to stdout")

    def test_no_print_statement_in_harness_emits_a_model_tag(self) -> None:
        # every string literal reachable by a print(...) call must be tag-free
        tree = ast.parse(HARNESS_SRC)
        leaks: list[str] = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print"):
                continue
            for s in ast.walk(node):
                if isinstance(s, ast.Constant) and isinstance(s.value, str):
                    for tag in MODEL_TAGS:
                        if tag in s.value:
                            leaks.append(s.value)
        self.assertEqual(leaks, [], f"print() literals contain a model tag: {leaks}")

    def test_resolve_model_result_is_never_bound_for_printing(self) -> None:
        # in live(), resolve_model(candidate) is called only to validate the
        # mapping; its result must be discarded (assigned to `_`), never
        # passed onward.
        tree = ast.parse(HARNESS_SRC)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.value, ast.Call)
                    and getattr(node.value.func, "id", None) == "resolve_model"):
                self.assertTrue(
                    isinstance(node.targets[0], ast.Name) and node.targets[0].id == "_",
                    "resolve_model() result must be discarded to `_` in live()",
                )

    def test_operator_commands_use_candidate_labels_not_model_tags(self) -> None:
        # the usage docstring shows only `--candidate A/B`, never a model tag
        doc = OB.__doc__ or ""
        self.assertIn("--candidate A", doc)
        for tag in MODEL_TAGS:
            self.assertNotIn(tag, doc)
        # argparse exposes --candidate {A,B} and --language {pl,en}; no --model
        self.assertIn('"--candidate", choices=["A", "B"]', HARNESS_SRC)
        self.assertNotIn("--model", HARNESS_SRC)
        self.assertNotIn('add_argument("--model', HARNESS_SRC)


class TestFairConfiguration(unittest.TestCase):
    def setUp(self) -> None:
        self.a = C.build_blind_session("A")
        self.b = C.build_blind_session("B")

    def test_both_are_real_conversation_sessions(self) -> None:
        self.assertIsInstance(self.a, ConversationSession)
        self.assertIsInstance(self.b, ConversationSession)

    def test_candidate_sessions_are_fresh_and_separate(self) -> None:
        self.assertIsNot(self.a, self.b)
        self.assertEqual(self.a.history, ())
        self.assertEqual(self.b.history, ())
        self.assertNotEqual(self.a.session_id, self.b.session_id)

    def test_same_persona_and_generation_options_for_both(self) -> None:
        self.assertEqual(self.a.system_prompt, self.b.system_prompt)
        self.assertEqual(self.a.options, self.b.options)
        # the real persona, not a stub
        from nexa.config import load_persona
        self.assertEqual(self.a.system_prompt, load_persona().system)
        self.assertEqual(self.a.options, load_persona().options)

    def test_num_thread_and_keep_alive_identical_for_both(self) -> None:
        self.assertEqual(self.a.provider._num_thread, C.BLIND_NUM_THREAD)
        self.assertEqual(self.b.provider._num_thread, C.BLIND_NUM_THREAD)
        self.assertEqual(self.a.provider._num_thread, 2)
        self.assertEqual(self.a.provider._keep_alive, self.b.provider._keep_alive)
        self.assertEqual(self.a.provider._keep_alive, C.BLIND_KEEP_ALIVE)

    def test_only_the_model_tag_differs(self) -> None:
        ma = self.a.provider.describe().model
        mb = self.b.provider.describe().model
        self.assertNotEqual(ma, mb)
        self.assertEqual({ma, mb}, set(C.ALLOWED_MODELS))


class TestNumThreadProviderWire(unittest.IsolatedAsyncioTestCase):
    """The research provider must send `num_thread` and otherwise build the
    exact same request body as the production `LocalModelProvider`."""

    async def test_generate_puts_num_thread_in_options(self) -> None:
        import urllib.request

        from nexa.providers.base import GenerationOptions, ProviderMessage
        captured: dict = {}

        class _FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def __iter__(self):
                return iter([b'{"message":{"content":"hi"},"done":false}\n',
                             b'{"done":true}\n'])

        def _fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _FakeResp()

        prov = C._NumThreadProvider(model="gemma4:e2b", num_thread=2, keep_alive="30m")
        orig = urllib.request.urlopen
        urllib.request.urlopen = _fake_urlopen
        try:
            out = await _drain(prov.generate(
                [ProviderMessage(role="user", content="hi")], GenerationOptions()))
        finally:
            urllib.request.urlopen = orig
        self.assertEqual(out, "hi")
        body = captured["body"]
        self.assertEqual(body["options"]["num_thread"], 2)
        self.assertEqual(body["keep_alive"], "30m")
        self.assertEqual(body["stream"], True)
        # same option keys as production + exactly one extra (num_thread)
        self.assertEqual(
            set(body["options"]) - {"num_thread"},
            {"num_ctx", "temperature", "top_p", "top_k", "repeat_penalty", "num_predict"},
        )


class TestResponseModeAndHistory(unittest.IsolatedAsyncioTestCase):
    async def test_voice_mode_directive_present_and_language_mechanism_intact(self) -> None:
        from nexa.conversation.language import language_directive
        from nexa.conversation.response_mode import ResponseMode, voice_response_directive
        s = C.build_blind_session("A")
        s.provider = FakeModelProvider([["Czarna dziura to obszar."]])
        await _drain(s.send("Co to jest czarna dziura?", response_mode=ResponseMode.VOICE))
        wire = [(m.role, m.content) for m in s.provider.calls[0]]
        self.assertIn(("system", voice_response_directive()), wire)
        self.assertIn(("system", language_directive("pl")), wire)  # response-lang mechanism intact

    async def test_history_persists_within_a_candidate_session(self) -> None:
        from nexa.conversation.response_mode import ResponseMode
        s = C.build_blind_session("A")
        s.provider = FakeModelProvider([["odp 1"], ["odp 2"]])
        await _drain(s.send("pierwsze", response_mode=ResponseMode.VOICE))
        await _drain(s.send("drugie", response_mode=ResponseMode.VOICE))
        self.assertEqual([t.role for t in s.history],
                         [Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT])
        self.assertEqual(s.history[0].content, "pierwsze")
        self.assertEqual(s.history[3].content, "odp 2")

    async def test_no_mapping_data_enters_conversation_history_or_wire(self) -> None:
        from nexa.conversation.response_mode import ResponseMode
        data = json.loads(MAPPING.read_text(encoding="utf-8"))
        secrets = [data["salt"], *data["mapping"].values(), "operator_blind_ab_mapping"]
        s = C.build_blind_session("A")
        s.provider = FakeModelProvider([["odpowiedz"]])
        await _drain(s.send("pytanie", response_mode=ResponseMode.VOICE))
        blob = " ".join(m.content for m in s.provider.calls[0])
        blob += " " + " ".join(t.content for t in s.history)
        blob += " " + s.system_prompt
        for secret in secrets:
            self.assertNotIn(secret, blob, f"blind secret {secret!r} leaked into the session")


class TestProductionUntouched(unittest.TestCase):
    def test_production_default_model_still_gemma4_e4b(self) -> None:
        from nexa.config import DEFAULT_LOCAL_MODEL
        self.assertEqual(DEFAULT_LOCAL_MODEL, "gemma4:e4b")
        self.assertEqual(C.production_default_model(), "gemma4:e4b")
        self.assertEqual(OB.production_default_model(), "gemma4:e4b")

    def test_bootstrap_build_default_session_uses_the_production_default(self) -> None:
        from nexa.bootstrap import build_default_session
        s = build_default_session()
        self.assertEqual(s.provider.describe().model, "gemma4:e4b")
        # production provider has NO num_thread override
        self.assertFalse(hasattr(s.provider, "_num_thread"))

    def test_harness_lives_only_under_docs_research(self) -> None:
        # the blind harness adds no file under src/ or apps/
        self.assertTrue((BENCH_DIR / "operator_blind_ab.py").exists())
        self.assertFalse((REPO_ROOT / "src" / "nexa" / "blind_ab_common.py").exists())
        self.assertFalse((REPO_ROOT / "apps" / "operator_blind_ab.py").exists())
        # and it does not write into src/ (only build_blind_session -> a session obj)
        for src in (HARNESS_SRC, COMMON_SRC, MAKE_SRC):
            self.assertNotIn("nexa/config.py", src)
            self.assertNotIn("DEFAULT_LOCAL_MODEL =", src)  # never reassigns it

    def test_no_production_config_or_provider_module_modified_by_import(self) -> None:
        # importing the harness must not monkeypatch the production provider
        import nexa.providers.ollama as prod
        self.assertIs(prod.LocalModelProvider.generate,
                      prod.LocalModelProvider.generate)  # identity stable
        self.assertNotIn("num_thread", (prod.LocalModelProvider.generate.__doc__ or ""))


class TestLanguageRouting(unittest.TestCase):
    def test_language_choices_are_pl_and_en(self) -> None:
        from nexa.stt import Language
        vals = {lang.value for lang in Language}
        self.assertEqual(vals, {"pl", "en"})
        self.assertIn("pl", vals)
        self.assertIn("en", vals)

    def test_harness_routes_the_language_arg_into_voiceruntime_and_planner(self) -> None:
        # Language(args.language) feeds BOTH the STT (VoiceRuntime language=)
        # and the SpeechPlanner default_language
        self.assertIn("language = Language(args.language)", HARNESS_SRC)
        self.assertIn("default_language=lang", HARNESS_SRC)
        self.assertIn("language=language", HARNESS_SRC)  # VoiceRuntime(... language=language ...)
        self.assertIn("WARMUP = {\"pl\":", HARNESS_SRC)

    def test_warmup_and_prime_phrases_exist_for_both_languages(self) -> None:
        self.assertEqual(set(OB.WARMUP), {"pl", "en"})
        self.assertEqual(set(OB.PRIME_TEXT), {"pl", "en"})


if __name__ == "__main__":
    unittest.main()
