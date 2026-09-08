"""M2.4B.3.6 — Production Local Model Serving Freeze: canonical-provider tests.

Deterministic, offline. No real Ollama, no audio. Proves that B.3.6:

* keeps `gemma4:e4b` as the one canonical local conversation model
  (`DEFAULT_LOCAL_MODEL`) — no e2b routing, no per-language model authority,
  no fallback model;
* makes the canonical provider send `num_thread=2` and `keep_alive=30m`
  (the R0021/R0022 serving finding) and *nothing else new* on the wire;
* keeps TEXT and VOICE on the one provider authority
  (`build_default_session`);
* adds a warm-up that is infrastructure-only — it never writes
  `ConversationSession` history, never creates a turn, never introduces a
  second session/provider/persona, and fails visibly if Ollama is down;
* does not change `ResponseMode.TEXT` / `ResponseMode.VOICE` behaviour or
  the PL/EN response-language mechanism.
"""
from __future__ import annotations

import ast
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
for p in (str(SRC), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fakes import FakeModelProvider  # noqa: E402
from nexa import bootstrap, config  # noqa: E402
from nexa.bootstrap import build_default_session, warm_up_session  # noqa: E402
from nexa.conversation.context import ConversationContext  # noqa: E402
from nexa.conversation.response_mode import (  # noqa: E402
    ResponseMode,
    voice_response_directive,
)
from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.providers.base import ModelUnavailableError  # noqa: E402
from nexa.providers.ollama import LocalModelProvider  # noqa: E402

PERSONA_SYS = config.load_persona().system
PERSONA_OPTS = config.load_persona().options


async def _drain(stream) -> str:
    return "".join([c async for c in stream])


# --------------------------------------------------------------------------- #
# a tiny fake Ollama that records the exact request body
# --------------------------------------------------------------------------- #

class _RecordingOllamaHandler(BaseHTTPRequestHandler):
    def log_message(self, *a, **k) -> None:  # silence
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.server.bodies.append(json.loads(self.rfile.read(length) or b"{}"))  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        chunk = json.dumps({"message": {"content": "ok"}, "done": False}) + "\n"
        self.wfile.write(chunk.encode())
        self.wfile.write((json.dumps({"done": True, "eval_count": 1}) + "\n").encode())
        self.wfile.flush()


def _start_recorder() -> HTTPServer:
    srv = HTTPServer(("127.0.0.1", 0), _RecordingOllamaHandler)
    srv.bodies = []  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


PROD_OPTION_KEYS = {
    "num_ctx", "temperature", "top_p", "top_k", "repeat_penalty", "num_predict",
}


class TestCanonicalModelUnchanged(unittest.TestCase):
    def test_default_local_model_is_gemma4_e4b(self) -> None:
        self.assertEqual(config.DEFAULT_LOCAL_MODEL, "gemma4:e4b")

    def test_serving_freeze_defaults(self) -> None:
        self.assertEqual(config.DEFAULT_LOCAL_NUM_THREAD, 2)
        self.assertEqual(config.DEFAULT_LOCAL_KEEP_ALIVE, "30m")

    def test_settings_carry_the_serving_policy(self) -> None:
        s = config.local_provider_settings_from_env()
        self.assertEqual(s.model, "gemma4:e4b")
        self.assertEqual(s.num_thread, 2)
        self.assertEqual(s.keep_alive, "30m")

    def test_no_e2b_or_fallback_model_wired_in_src(self) -> None:
        # No second / fallback / faster model tag, and no per-language model
        # constant, anywhere in the package — comments and docstrings excluded
        # (only real code counts).
        offenders: list[str] = []
        for py in (SRC / "nexa").rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if "gemma4:e2b" in node.value or "gemma4:e3b" in node.value:
                        offenders.append(f"{py.name}: string {node.value!r}")
                if isinstance(node, ast.Name) and (
                    "FALLBACK_MODEL" in node.id
                    or node.id in {"PL_MODEL", "EN_MODEL", "POLISH_MODEL", "ENGLISH_MODEL"}
                ):
                    offenders.append(f"{py.name}: name {node.id}")
        self.assertEqual(offenders, [], f"unexpected model routing: {offenders}")

    def test_only_one_default_local_model_constant(self) -> None:
        model_consts = [
            n for n in dir(config)
            if n.isupper() and "MODEL" in n and "ENDPOINT" not in n
            and isinstance(getattr(config, n), str)
        ]
        self.assertEqual(model_consts, ["DEFAULT_LOCAL_MODEL"])

    def test_cloud_provider_still_fails_closed_no_silent_local_fallback(self) -> None:
        import os
        prev = os.environ.get("NEXA_MODEL_PROVIDER")
        os.environ["NEXA_MODEL_PROVIDER"] = "cloud"
        try:
            with self.assertRaises(NotImplementedError):
                config.local_provider_settings_from_env()
        finally:
            if prev is None:
                os.environ.pop("NEXA_MODEL_PROVIDER", None)
            else:
                os.environ["NEXA_MODEL_PROVIDER"] = prev


class TestCanonicalProviderWire(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.srv = _start_recorder()
        self.port = self.srv.server_address[1]

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()

    def _session(self) -> ConversationSession:
        settings = config.local_provider_settings_from_env()
        provider = LocalModelProvider(
            model=settings.model,
            base_url=f"http://127.0.0.1:{self.port}",
            keep_alive=settings.keep_alive,
            num_thread=settings.num_thread,
        )
        return ConversationSession(
            provider=provider, system_prompt=PERSONA_SYS, options=PERSONA_OPTS
        )

    async def test_build_default_session_provider_carries_the_policy(self) -> None:
        s = build_default_session()
        self.assertEqual(s.provider.describe().model, "gemma4:e4b")
        self.assertEqual(s.provider._num_thread, 2)
        self.assertEqual(s.provider._keep_alive, "30m")

    async def test_wire_body_adds_only_num_thread_plus_keep_alive(self) -> None:
        s = self._session()
        await _drain(s.send("czym jest atom?", response_mode=ResponseMode.VOICE))
        body = self.srv.bodies[-1]
        self.assertEqual(body["model"], "gemma4:e4b")
        self.assertEqual(body["keep_alive"], "30m")
        self.assertTrue(body["stream"])
        self.assertIs(body["think"], False)
        opts = body["options"]
        self.assertEqual(opts["num_thread"], 2)
        # every production option unchanged, exactly one new key (num_thread)
        self.assertEqual(set(opts) - {"num_thread"}, PROD_OPTION_KEYS)
        self.assertEqual(opts["num_ctx"], PERSONA_OPTS.num_ctx)
        self.assertEqual(opts["temperature"], PERSONA_OPTS.temperature)
        self.assertEqual(opts["top_p"], PERSONA_OPTS.top_p)
        self.assertEqual(opts["top_k"], PERSONA_OPTS.top_k)
        self.assertEqual(opts["repeat_penalty"], PERSONA_OPTS.repeat_penalty)
        self.assertEqual(opts["num_predict"], PERSONA_OPTS.num_predict)

    async def test_text_and_voice_use_the_same_provider_authority(self) -> None:
        s = self._session()
        await _drain(s.send("dzień dobry", response_mode=ResponseMode.TEXT))
        await _drain(s.send("i co dalej?", response_mode=ResponseMode.VOICE))
        text_body, voice_body = self.srv.bodies[-2], self.srv.bodies[-1]
        for b in (text_body, voice_body):
            self.assertEqual(b["model"], "gemma4:e4b")
            self.assertEqual(b["keep_alive"], "30m")
            self.assertEqual(b["options"]["num_thread"], 2)
        # the ONLY difference is the transient VOICE directive system message
        text_sys = [m["content"] for m in text_body["messages"] if m["role"] == "system"]
        voice_sys = [m["content"] for m in voice_body["messages"] if m["role"] == "system"]
        self.assertNotIn(voice_response_directive(), text_sys)
        self.assertIn(voice_response_directive(), voice_sys)

    async def test_num_thread_disabled_when_env_is_zero(self) -> None:
        import os
        prev = os.environ.get("NEXA_LOCAL_NUM_THREAD")
        os.environ["NEXA_LOCAL_NUM_THREAD"] = "0"
        try:
            settings = config.local_provider_settings_from_env()
            self.assertIsNone(settings.num_thread)
            provider = LocalModelProvider(
                model=settings.model,
                base_url=f"http://127.0.0.1:{self.port}",
                keep_alive=settings.keep_alive,
                num_thread=settings.num_thread,
            )
            s = ConversationSession(
                provider=provider, system_prompt=PERSONA_SYS, options=PERSONA_OPTS
            )
            await _drain(s.send("hej"))
            self.assertNotIn("num_thread", self.srv.bodies[-1]["options"])
        finally:
            if prev is None:
                os.environ.pop("NEXA_LOCAL_NUM_THREAD", None)
            else:
                os.environ["NEXA_LOCAL_NUM_THREAD"] = prev


class TestTextPathUnchanged(unittest.IsolatedAsyncioTestCase):
    async def test_text_mode_wire_messages_have_no_voice_directive(self) -> None:
        fake = FakeModelProvider([["ok"]])
        s = ConversationSession(provider=fake, system_prompt=PERSONA_SYS, options=PERSONA_OPTS)
        await _drain(s.send("what is an atom?"))  # default = TEXT
        sent = [m.content for m in fake.calls[0]]
        self.assertNotIn(voice_response_directive(), sent)
        # byte-for-byte identical to a direct TEXT context build
        ctx = ConversationContext.build(PERSONA_SYS, s.history[:1])
        expected = [m.content for m in ctx.to_provider_messages(response_mode=ResponseMode.TEXT)]
        self.assertEqual(sent, expected)

    async def test_pl_en_language_directive_still_injected(self) -> None:
        from nexa.conversation.language import language_directive

        fake = FakeModelProvider([["ok"], ["ok"]])
        s = ConversationSession(provider=fake, system_prompt=PERSONA_SYS, options=PERSONA_OPTS)
        await _drain(s.send("Co to jest atom?"))
        await _drain(s.send("What is an atom?"))
        pl_sent = [m.content for m in fake.calls[0]]
        en_sent = [m.content for m in fake.calls[1]]
        self.assertIn(language_directive("pl"), pl_sent)
        self.assertIn(language_directive("en"), en_sent)


class TestWarmUpIsInfrastructureOnly(unittest.IsolatedAsyncioTestCase):
    async def test_warm_up_does_not_write_history_or_create_turns(self) -> None:
        fake = FakeModelProvider([["discarded warm token"]])
        s = ConversationSession(provider=fake, system_prompt=PERSONA_SYS, options=PERSONA_OPTS)
        result = await warm_up_session(s)
        self.assertEqual(s.history, ())
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result.model, fake.describe().model)

    async def test_warm_up_sends_only_the_persona_plus_voice_prefix(self) -> None:
        fake = FakeModelProvider([["x"]])
        s = ConversationSession(provider=fake, system_prompt=PERSONA_SYS, options=PERSONA_OPTS)
        await warm_up_session(s)
        sent = fake.calls[0]
        self.assertEqual([m.role for m in sent], ["system", "system"])
        self.assertEqual(sent[0].content, PERSONA_SYS)
        self.assertEqual(sent[1].content, voice_response_directive())
        # no user text at all
        self.assertFalse(any(m.role == "user" for m in sent))
        # num_predict clamped to 1 for the warm-up call; nothing else changed
        opts = fake.options_by_call[0]
        self.assertEqual(opts.num_predict, 1)
        self.assertEqual(opts.num_ctx, PERSONA_OPTS.num_ctx)
        self.assertEqual(opts.temperature, PERSONA_OPTS.temperature)
        self.assertIs(opts.think, PERSONA_OPTS.think)

    async def test_warm_up_prefix_is_a_prefix_of_the_first_real_voice_turn(self) -> None:
        fake = FakeModelProvider([["x"], ["real"]])
        s = ConversationSession(provider=fake, system_prompt=PERSONA_SYS, options=PERSONA_OPTS)
        await warm_up_session(s)
        await _drain(s.send("Co to jest czarna dziura?", response_mode=ResponseMode.VOICE))
        warm_sent = [m.content for m in fake.calls[0]]
        real_sent = [m.content for m in fake.calls[1]]
        self.assertEqual(real_sent[: len(warm_sent)], warm_sent)

    async def test_warm_up_reuses_the_session_provider_no_second_authority(self) -> None:
        fake = FakeModelProvider([["x"]])
        s = ConversationSession(provider=fake, system_prompt=PERSONA_SYS, options=PERSONA_OPTS)
        await warm_up_session(s)
        self.assertIs(s.provider, fake)  # same provider object, no new one built

    async def test_warm_up_propagates_model_unavailable_no_silent_fallback(self) -> None:
        fake = FakeModelProvider([], fail_on_call=0)
        s = ConversationSession(provider=fake, system_prompt=PERSONA_SYS, options=PERSONA_OPTS)
        with self.assertRaises(ModelUnavailableError):
            await warm_up_session(s)
        self.assertEqual(s.history, ())


class TestNoSecondAuthorityInSource(unittest.TestCase):
    def test_bootstrap_builds_exactly_one_provider_and_one_session(self) -> None:
        tree = ast.parse((SRC / "nexa" / "bootstrap.py").read_text(encoding="utf-8"))
        provider_ctors = 0
        session_ctors = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "LocalModelProvider":
                    provider_ctors += 1
                if node.func.id == "ConversationSession":
                    session_ctors += 1
        self.assertEqual(provider_ctors, 1)
        self.assertEqual(session_ctors, 1)

    def test_warm_up_never_touches_history(self) -> None:
        src = (SRC / "nexa" / "bootstrap.py").read_text(encoding="utf-8")
        # warm-up must not call session.send / append to _history
        self.assertNotIn("_history", src)
        self.assertNotIn(".send(", src)

    def test_warm_up_result_is_frozen_telemetry(self) -> None:
        self.assertTrue(bootstrap.WarmUpResult.__dataclass_params__.frozen)


if __name__ == "__main__":
    unittest.main()
