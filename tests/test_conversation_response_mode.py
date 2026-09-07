"""M2.4B.3.3 — ``ResponseMode`` (voice vs text presentation) tests.

Deterministic, offline. Uses ``FakeModelProvider`` — never Ollama, never
TTS, never a mic. Proves the voice-mode instruction is a transient,
fixed-position wire-level hint on the SAME ``ConversationSession``; that
typed chat is byte-for-byte unchanged; that nothing extra lands in
history; and that the response-language directive still wins.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
for p in (SRC, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fakes import FakeModelProvider  # noqa: E402
from nexa.conversation import (  # noqa: E402
    ConversationContext,
    ConversationSession,
    ResponseMode,
    Role,
    voice_response_directive,
)
from nexa.conversation.language import language_directive  # noqa: E402
from nexa.conversation.turn import ConversationTurn  # noqa: E402

SYSTEM_PROMPT = "Jesteś NeXa — testowa persona."
CONV_PKG = SRC / "nexa" / "conversation"
VOICE_CONV_PKG = SRC / "nexa" / "voice_conversation"


async def _drain(stream) -> str:
    return "".join([c async for c in stream])


def _wire(msgs) -> list[tuple[str, str]]:
    return [(m.role, m.content) for m in msgs]


class TestToProviderMessages(unittest.TestCase):
    def _ctx(self) -> ConversationContext:
        return ConversationContext.build(
            SYSTEM_PROMPT,
            [ConversationTurn(Role.USER, "Co to jest czarna dziura?")],
        )

    def test_2_text_mode_is_unchanged_from_pre_b33(self) -> None:
        ctx = self._ctx()
        default = ctx.to_provider_messages()
        explicit_text = ctx.to_provider_messages(response_mode=ResponseMode.TEXT)
        self.assertEqual(_wire(default), _wire(explicit_text))
        # exactly: persona, user, language-directive — no voice message
        self.assertEqual(_wire(default), [
            ("system", SYSTEM_PROMPT),
            ("user", "Co to jest czarna dziura?"),
            ("system", language_directive("pl")),
        ])
        self.assertNotIn(voice_response_directive(), [m.content for m in default])

    def test_1_voice_mode_injects_the_voice_directive_once_after_persona(self) -> None:
        msgs = self._ctx().to_provider_messages(response_mode=ResponseMode.VOICE)
        self.assertEqual(_wire(msgs), [
            ("system", SYSTEM_PROMPT),
            ("system", voice_response_directive()),  # fixed position: right after persona
            ("user", "Co to jest czarna dziura?"),
            ("system", language_directive("pl")),
        ])
        self.assertEqual(
            [m.content for m in msgs].count(voice_response_directive()), 1
        )

    def test_voice_directive_is_a_constant_fixed_position_string(self) -> None:
        # KV-cache discipline (R0009): identical string, identical index,
        # every build — so the wire prompt prefix stays byte-stable.
        hist = [
            ConversationTurn(Role.USER, "pierwsze"),
            ConversationTurn(Role.ASSISTANT, "odp"),
            ConversationTurn(Role.USER, "drugie"),
        ]
        a = ConversationContext.build(SYSTEM_PROMPT, hist).to_provider_messages(
            response_mode=ResponseMode.VOICE)
        b = ConversationContext.build(SYSTEM_PROMPT, hist).to_provider_messages(
            response_mode=ResponseMode.VOICE)
        self.assertEqual(_wire(a), _wire(b))
        self.assertEqual(a[1].role, "system")
        self.assertEqual(a[1].content, voice_response_directive())

    def test_6_language_directive_still_follows_every_user_turn_in_voice_mode(self) -> None:
        hist = [
            ConversationTurn(Role.USER, "Cześć"),
            ConversationTurn(Role.ASSISTANT, "Hej"),
            ConversationTurn(Role.USER, "How does a star work?"),
        ]
        msgs = ConversationContext.build(SYSTEM_PROMPT, hist).to_provider_messages(
            response_mode=ResponseMode.VOICE)
        w = _wire(msgs)
        # voice directive appears once (after persona); language directives
        # appear once per user turn and are PL then EN — canonical, unchanged.
        self.assertEqual([c for _, c in w].count(voice_response_directive()), 1)
        self.assertIn(("system", language_directive("pl")), w)
        self.assertIn(("system", language_directive("en")), w)
        # the voice directive itself does not name a response language
        vd = voice_response_directive()
        self.assertNotIn("Odpowiedz na tę wiadomość po polsku", vd)
        self.assertNotIn("Respond to this message in English", vd)


class TestSessionResponseMode(unittest.IsolatedAsyncioTestCase):
    def _session(self, chunks=None) -> tuple[ConversationSession, FakeModelProvider]:
        prov = FakeModelProvider(chunks or [["To obszar o silnej grawitacji."]])
        return ConversationSession(provider=prov, system_prompt=SYSTEM_PROMPT), prov

    async def test_3_same_session_serves_both_modes(self) -> None:
        session, prov = self._session([["odp tekst"], ["odp głos"]])
        await _drain(session.send("Pytanie tekstowe"))                    # default TEXT
        await _drain(session.send("Pytanie głosowe", response_mode=ResponseMode.VOICE))
        self.assertEqual(len(prov.calls), 2)
        # call 0 (text): no voice directive
        self.assertNotIn(voice_response_directive(), [m.content for m in prov.calls[0]])
        # call 1 (voice): voice directive present, right after persona
        self.assertEqual(prov.calls[1][1].content, voice_response_directive())
        # one session, one history: 4 real turns, nothing else
        self.assertEqual([t.role for t in session.history],
                         [Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT])

    async def test_4_voice_directive_is_never_stored_in_history(self) -> None:
        session, _ = self._session()
        await _drain(session.send("Co to jest gwiazda?", response_mode=ResponseMode.VOICE))
        for turn in session.history:
            self.assertNotIn(voice_response_directive(), turn.content)
            self.assertNotEqual(turn.content, voice_response_directive())
        self.assertEqual(session.history[0].content, "Co to jest gwiazda?")  # verbatim user
        self.assertEqual(session.history[0].role, Role.USER)

    async def test_5_assistant_canonical_response_stored_unchanged(self) -> None:
        session, _ = self._session([["Gwiazda ", "to ", "kula ", "gazu."]])
        reply = await _drain(session.send("Co to?", response_mode=ResponseMode.VOICE))
        self.assertEqual(reply, "Gwiazda to kula gazu.")
        self.assertEqual(session.history[1].role, Role.ASSISTANT)
        self.assertEqual(session.history[1].content, "Gwiazda to kula gazu.")  # exact model output

    async def test_7_polish_voice_request_works(self) -> None:
        session, prov = self._session()
        await _drain(session.send("Z czego składa się gwiazda?", response_mode=ResponseMode.VOICE))
        w = _wire(prov.calls[0])
        self.assertEqual(w[1], ("system", voice_response_directive()))
        # PL response language still canonical
        self.assertIn(("system", language_directive("pl")), w)

    async def test_8_english_voice_request_works(self) -> None:
        session, prov = self._session()
        await _drain(session.send("What is a star made of?", response_mode=ResponseMode.VOICE))
        w = _wire(prov.calls[0])
        self.assertEqual(w[1], ("system", voice_response_directive()))
        self.assertIn(("system", language_directive("en")), w)

    async def test_options_and_persona_unchanged_by_mode(self) -> None:
        session, prov = self._session()
        await _drain(session.send("x", response_mode=ResponseMode.VOICE))
        self.assertEqual(prov.calls[0][0].content, SYSTEM_PROMPT)  # persona still first, verbatim
        self.assertEqual(prov.options_by_call[0], session.options)


class TestVoiceDirectiveContent(unittest.TestCase):
    """The instruction is a *generation policy* — it must express concise
    default AND an explicit-detail override, PL and EN, without clipping."""

    def test_9_expresses_concise_default(self) -> None:
        d = voice_response_directive()
        self.assertIn("1–3 zdania", d)
        self.assertIn("1–3 sentences", d)
        self.assertIn("pierwsze zdanie", d.lower())
        self.assertIn("first sentence", d.lower())

    def test_10_11_12_expresses_detail_override_not_a_hard_clip(self) -> None:
        d = voice_response_directive().lower()
        # detail / explanation / steps / comparison / list / longer -> honour it
        for kw in ("szczegół", "wyjaśnien", "krok", "porównani", "listę", "dłuższą"):
            self.assertIn(kw, d)
        for kw in ("detail", "explanation", "step", "comparison", "list", "longer"):
            self.assertIn(kw, d)
        # it is not an output cap: no token/char limit language
        for bad in ("maksymalnie", "nie więcej niż", "at most", "no more than",
                    "truncat", "obetnij"):
            self.assertNotIn(bad, d)

    def test_language_neutral_wrt_response_language(self) -> None:
        # it must not itself force a reply language (the per-turn directive does)
        d = voice_response_directive()
        self.assertNotEqual(d, language_directive("pl"))
        self.assertNotEqual(d, language_directive("en"))


class TestArchitectureInvariants(unittest.TestCase):
    def _imports(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                out.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                out.add(n.module)
        return out

    def test_13_response_mode_module_has_no_speech_planner_or_tts_dep(self) -> None:
        imported = self._imports(CONV_PKG / "response_mode.py")
        for bad in imported:
            low = bad.lower()
            self.assertNotIn("speech_planner", low)
            self.assertNotIn("voice_tts", low)
            self.assertNotIn("piper", low)
            self.assertNotIn("tts", low)
            self.assertNotIn("pipecat", low)

    def test_14_conversation_package_never_imports_tts_or_pipecat(self) -> None:
        offenders = []
        for f in sorted(CONV_PKG.glob("*.py")):
            for m in self._imports(f):
                low = m.lower()
                bad_kw = ("pipecat", "piper", "tts", "voice_tts", "speech_planner")
                if any(k in low for k in bad_kw):
                    offenders.append((f.name, m))
        self.assertEqual(offenders, [], f"nexa.conversation must stay TTS-free: {offenders}")

    def test_15_no_second_session_persona_or_history_authority(self) -> None:
        tree = ast.parse((CONV_PKG / "response_mode.py").read_text(encoding="utf-8"))
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        # exactly one class — the StrEnum. No session/context/history/persona.
        self.assertEqual(classes, ["ResponseMode"])
        # it imports nothing that could be a second authority
        for m in self._imports(CONV_PKG / "response_mode.py"):
            self.assertIn(m, {"__future__", "enum"}, f"unexpected import: {m}")
        # no dataclass field / attribute assignment naming a conversation surface
        assigned = {
            t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
        } | {
            n.target.id for n in ast.walk(tree)
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
        }
        for bad in ("_history", "system_prompt", "provider", "options", "session_id"):
            self.assertNotIn(bad, assigned)

    def test_voice_adapter_still_only_calls_session_send(self) -> None:
        src = (VOICE_CONV_PKG / "adapter.py").read_text(encoding="utf-8")
        # it may import response_mode (a type), but must not touch the
        # language module or construct a session/provider.
        imports = self._imports(VOICE_CONV_PKG / "adapter.py")
        self.assertNotIn("nexa.conversation.language", imports)
        self.assertNotIn("nexa.conversation", imports)  # bare
        self.assertIn("nexa.conversation.response_mode", imports)
        for name in ("detect_response_language", "language_directive", "ConversationContext"):
            self.assertNotIn(name, src)


if __name__ == "__main__":
    unittest.main()
