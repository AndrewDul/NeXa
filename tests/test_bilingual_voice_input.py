"""M2.4B.5 — deterministic tests for automatic bilingual PL/EN voice input.

Offline. No microphone, no whisper.cpp model/library, no Ollama. The STT
language detector + base transcriber are faked at the real external
boundary; everything NeXa-owned (guard, BilingualSpeechTranscriber,
ResponseLanguageResolver, ConversationSession, adapter, bridge voice) runs
for real.
"""
from __future__ import annotations

import ast
import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
for p in (str(SRC), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fakes import FakeModelProvider  # noqa: E402
from nexa import config as nexa_config  # noqa: E402
from nexa.conversation.context import ConversationContext  # noqa: E402
from nexa.conversation.language import language_directive  # noqa: E402
from nexa.conversation.response_language import (  # noqa: E402
    ResponseLanguageResolver,
    ResponsePreference,
    detect_explicit_language_request,
)
from nexa.conversation.response_mode import ResponseMode  # noqa: E402
from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.conversation.turn import Role  # noqa: E402
from nexa.stt.bilingual import (  # noqa: E402
    MIXED_CODE_SWITCH_STATUS,
    BilingualSpeechTranscriber,
    LanguageDecision,
)
from nexa.stt.config import Language  # noqa: E402
from nexa.stt.language_detection import LanguageDetectionResult  # noqa: E402
from nexa.stt.language_guard import GuardConfig, GuardDecision, LanguageIdGuard  # noqa: E402
from nexa.stt.transcriber import TranscriptionResult  # noqa: E402
from nexa.voice_conversation import TurnLanguage, VoiceConversationAdapter  # noqa: E402
from nexa.voice_tts.bridge import AssistantSpeechBridge  # noqa: E402

SYSTEM_PROMPT = "Jesteś NeXa — testowa persona."


async def _drain(stream) -> str:
    return "".join([c async for c in stream])


# --------------------------------------------------------------------------- #
# fakes at the external boundary
# --------------------------------------------------------------------------- #

class _FakeDetector:
    """Returns a scripted LanguageDetectionResult per call (FIFO)."""

    def __init__(self, results: list[LanguageDetectionResult]) -> None:
        self._results = list(results)
        self.calls = 0

    async def detect(self, audio: bytes) -> LanguageDetectionResult:
        self.calls += 1
        return self._results.pop(0)


class _FakeBaseTranscriber:
    """Records every (audio, language) it is asked to decode and returns a
    deterministic transcript keyed by language."""

    def __init__(self, by_lang: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[bytes, str]] = []
        self._by_lang = by_lang or {"pl": "polski tekst", "en": "english text"}

    async def transcribe(self, audio: bytes, *, language: Language) -> TranscriptionResult:
        self.calls.append((audio, language.value))
        return TranscriptionResult(
            text=self._by_lang.get(language.value, f"[{language.value}]"),
            language=language,
            audio_duration_s=len(audio) / (16_000 * 2),
            wall_latency_s=0.01,
        )


def _det(p_pl: float, p_en: float, raw: str, raw_p: float | None = None) -> LanguageDetectionResult:
    return LanguageDetectionResult(
        p_pl=p_pl, p_en=p_en, raw_language=raw,
        raw_confidence=raw_p if raw_p is not None else max(p_pl, p_en),
    )


# duration helper: bytes for N seconds of 16k mono pcm16
def _audio(seconds: float, fill: int = 1) -> bytes:
    return bytes([fill % 256, 0]) * int(seconds * 16_000)


# --------------------------------------------------------------------------- #
# LanguageIdGuard
# --------------------------------------------------------------------------- #

class TestLanguageIdGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.g = LanguageIdGuard()

    def test_confident_pl_accepts_pl(self) -> None:
        o = self.g.evaluate(_det(0.97, 0.003, "pl"), audio_duration_s=4.0,
                            last_input_language=None)
        self.assertEqual(o.decision, GuardDecision.AUTO_ACCEPT)
        self.assertEqual(o.selected_language, "pl")
        self.assertTrue(o.updates_last_input_language)

    def test_confident_en_accepts_en(self) -> None:
        o = self.g.evaluate(_det(0.004, 0.85, "en"), audio_duration_s=3.0,
                            last_input_language=None)
        self.assertEqual(o.decision, GuardDecision.AUTO_ACCEPT)
        self.assertEqual(o.selected_language, "en")

    def test_supported_raw_language_trusted_even_at_low_absolute_p(self) -> None:
        # R0024: 0 PL<->EN confusion; raw 'pl' at p=0.27 is still trusted
        o = self.g.evaluate(_det(0.27, 0.08, "pl"), audio_duration_s=4.2,
                            last_input_language="en")
        self.assertEqual(o.selected_language, "pl")
        self.assertEqual(o.decision, GuardDecision.AUTO_ACCEPT)

    def test_third_language_but_decisive_split_recovers(self) -> None:
        # 002: raw ru, p_pl 0.237 vs p_en 0.011 -> decisive -> pl
        o = self.g.evaluate(_det(0.237, 0.011, "ru"), audio_duration_s=3.5,
                            last_input_language=None, bootstrap_language="en")
        self.assertEqual(o.decision, GuardDecision.AUTO_ACCEPT)
        self.assertEqual(o.selected_language, "pl")

    def test_short_utterance_always_falls_back(self) -> None:
        # 031 "Tak." — raw 'en' confident, but 1.5s < 2.0s -> fallback
        o = self.g.evaluate(_det(0.046, 0.563, "en"), audio_duration_s=1.5,
                            last_input_language="pl")
        self.assertEqual(o.decision, GuardDecision.FALLBACK_REDECODE)
        self.assertEqual(o.selected_language, "pl")
        self.assertFalse(o.updates_last_input_language)

    def test_no_last_language_uses_bootstrap_on_fallback(self) -> None:
        o = self.g.evaluate(_det(0.05, 0.30, "ru"), audio_duration_s=1.4,
                            last_input_language=None, bootstrap_language="pl")
        self.assertEqual(o.decision, GuardDecision.FALLBACK_REDECODE)
        self.assertEqual(o.selected_language, "pl")

    def test_threshold_is_configurable(self) -> None:
        strict = LanguageIdGuard(GuardConfig(confidence_threshold=0.99))
        # third-language with cc 0.39 and a non-decisive ratio -> fallback
        o = strict.evaluate(_det(0.30, 0.20, "it"), audio_duration_s=3.0,
                            last_input_language="en")
        self.assertEqual(o.decision, GuardDecision.FALLBACK_REDECODE)
        self.assertEqual(o.threshold, 0.99)


# --------------------------------------------------------------------------- #
# BilingualSpeechTranscriber
# --------------------------------------------------------------------------- #

class TestBilingualTranscriber(unittest.IsolatedAsyncioTestCase):
    async def test_confident_pl_then_en_alternate_in_one_session(self) -> None:
        det = _FakeDetector([_det(0.97, 0.003, "pl"), _det(0.004, 0.85, "en")])
        base = _FakeBaseTranscriber()
        bt = BilingualSpeechTranscriber(base, det, session_default_language=Language.PL)

        r1 = await bt.transcribe(_audio(4.0))
        self.assertEqual(r1.language, Language.PL)
        self.assertEqual(bt.last_input_language, "pl")

        r2 = await bt.transcribe(_audio(3.0))
        self.assertEqual(r2.language, Language.EN)
        self.assertEqual(bt.last_input_language, "en")

        self.assertEqual([lang for _, lang in base.calls], ["pl", "en"])

    async def test_low_confidence_third_language_triggers_redecode(self) -> None:
        det = _FakeDetector([_det(0.05, 0.28, "ru")])  # short + junk
        base = _FakeBaseTranscriber()
        bt = BilingualSpeechTranscriber(base, det, session_default_language=Language.PL)
        r = await bt.transcribe(_audio(1.4))
        self.assertIs(r.language_decision.redecoded, True)
        self.assertEqual(r.language_decision.guard_decision, GuardDecision.FALLBACK_REDECODE)
        # decoded in the session bootstrap language, never in a third language
        self.assertEqual([lang for _, lang in base.calls], ["pl"])
        self.assertEqual(r.language, Language.PL)

    async def test_rejected_auto_text_never_produced_only_pl_or_en_decode(self) -> None:
        det = _FakeDetector([_det(0.29, 0.10, "ru")])
        base = _FakeBaseTranscriber({"pl": "Nie.", "en": "Yeah.", "ru": "Не."})
        bt = BilingualSpeechTranscriber(base, det, session_default_language=Language.PL)
        r = await bt.transcribe(_audio(1.6))
        # the ONLY decode was pl -> "Nie."; the Cyrillic never exists
        self.assertEqual(r.text, "Nie.")
        self.assertNotIn("Не", r.text)
        self.assertTrue(all(lang in ("pl", "en") for _, lang in base.calls))

    async def test_redecode_uses_the_original_audio_bytes(self) -> None:
        det = _FakeDetector([_det(0.05, 0.30, "ko")])
        base = _FakeBaseTranscriber()
        bt = BilingualSpeechTranscriber(base, det, session_default_language=Language.EN)
        original = _audio(1.5, fill=7)
        await bt.transcribe(original)
        self.assertEqual(len(base.calls), 1)
        self.assertEqual(base.calls[0][0], original)

    async def test_no_last_language_bootstrap_is_deterministic(self) -> None:
        det = _FakeDetector([_det(0.10, 0.20, "he")])
        base = _FakeBaseTranscriber()
        bt = BilingualSpeechTranscriber(base, det, session_default_language=Language.PL)
        r = await bt.transcribe(_audio(1.3))
        self.assertEqual(r.language, Language.PL)  # bootstrap, not random
        self.assertIsNone(bt.last_input_language)  # fallback did not "learn"

    async def test_language_decision_telemetry_is_complete(self) -> None:
        det = _FakeDetector([_det(0.97, 0.003, "pl")])
        base = _FakeBaseTranscriber()
        bt = BilingualSpeechTranscriber(base, det, session_default_language=Language.PL)
        r = await bt.transcribe(_audio(4.0))
        d = r.language_decision
        self.assertIsInstance(d, LanguageDecision)
        for attr in ("raw_detected_language", "p_pl", "p_en", "selected_language",
                     "guard_decision", "guard_reason", "confidence_threshold",
                     "redecoded", "detect_latency_s", "decode_latency_s"):
            self.assertTrue(hasattr(d, attr))
        self.assertEqual(d.selected_language, "pl")

    async def test_mixed_status_is_not_marked_guaranteed(self) -> None:
        self.assertNotIn("guaranteed", MIXED_CODE_SWITCH_STATUS.replace("not guaranteed", ""))
        self.assertIn("deferred", MIXED_CODE_SWITCH_STATUS)


# --------------------------------------------------------------------------- #
# ResponseLanguageResolver
# --------------------------------------------------------------------------- #

class TestResponseLanguageResolver(unittest.TestCase):
    def test_default_mirrors_input_language(self) -> None:
        r = ResponseLanguageResolver()
        self.assertEqual(r.resolve("Co to?", input_language="pl").response_language, "pl")
        self.assertEqual(r.resolve("What?", input_language="en").response_language, "en")

    def test_explicit_one_turn_request_switches_and_sets_sticky(self) -> None:
        r = ResponseLanguageResolver()
        d = r.resolve("Answer in English.", input_language="pl")
        self.assertEqual(d.response_language, "en")
        self.assertTrue(d.preference_changed)
        self.assertEqual(r.preference.sticky, "en")

    def test_sticky_en_holds_while_speaking_pl(self) -> None:
        r = ResponseLanguageResolver(ResponsePreference(sticky="en"))
        d = r.resolve("Po co człowiekowi sen?", input_language="pl")
        self.assertEqual(d.response_language, "en")  # input pl, response en

    def test_sticky_pl_request(self) -> None:
        r = ResponseLanguageResolver(ResponsePreference(sticky="en"))
        d = r.resolve("Wracamy do polskiego.", input_language="en")
        self.assertEqual(d.response_language, "pl")
        self.assertEqual(r.preference.sticky, "pl")

    def test_od_teraz_mow_po_angielsku_is_sticky(self) -> None:
        r = ResponseLanguageResolver()
        r.resolve("Od teraz mów po angielsku.", input_language="pl")
        self.assertEqual(r.preference.sticky, "en")
        nxt = r.resolve("Jak działa komputer?", input_language="pl")
        self.assertEqual(nxt.response_language, "en")

    def test_ordinary_content_mentioning_a_language_does_not_switch(self) -> None:
        for txt in (
            "Tell me about Polish history.",
            "What is the English word for kot?",
            "How do you say 'thank you' in Polish?",
            "Explain the difference between Polish and English grammar.",
            "I am learning English.",
        ):
            self.assertIsNone(detect_explicit_language_request(txt), txt)

    def test_input_and_response_language_stay_distinct(self) -> None:
        r = ResponseLanguageResolver(ResponsePreference(sticky="en"))
        d = r.resolve("Dlaczego niebo jest niebieskie?", input_language="pl")
        self.assertEqual(d.response_language, "en")
        self.assertNotEqual(d.response_language, "pl")


# --------------------------------------------------------------------------- #
# ConversationContext / ConversationSession — response_language threading
# --------------------------------------------------------------------------- #

class TestResponseLanguageInContext(unittest.IsolatedAsyncioTestCase):
    def _session(self, provider) -> ConversationSession:
        return ConversationSession(provider=provider, system_prompt=SYSTEM_PROMPT)

    async def test_resolved_response_language_overrides_text_detection(self) -> None:
        prov = FakeModelProvider([["ok"]])
        s = self._session(prov)
        # spoke Polish, but sticky EN resolved -> directive must be English
        await _drain(s.send("Dlaczego niebo jest niebieskie?",
                            response_mode=ResponseMode.VOICE, response_language="en"))
        sent = [m.content for m in prov.calls[0]]
        self.assertIn(language_directive("en"), sent)
        self.assertNotIn(language_directive("pl"), sent)

    async def test_none_response_language_keeps_r0009_text_detection(self) -> None:
        prov = FakeModelProvider([["ok"], ["ok"]])
        s = self._session(prov)
        await _drain(s.send("Co to jest atom?"))  # TEXT, no response_language
        await _drain(s.send("What is an atom?"))
        self.assertIn(language_directive("pl"), [m.content for m in prov.calls[0]])
        self.assertIn(language_directive("en"), [m.content for m in prov.calls[1]])

    async def test_historical_turn_directive_is_replay_stable(self) -> None:
        prov = FakeModelProvider([["a"], ["b"], ["c"]])
        s = self._session(prov)
        await _drain(s.send("Pierwsze pytanie?", response_mode=ResponseMode.VOICE,
                            response_language="en"))  # PL text, EN resolved
        await _drain(s.send("Drugie pytanie?", response_mode=ResponseMode.VOICE,
                            response_language="en"))
        # in call 2's wire messages, the FIRST user turn must still carry the
        # EN directive that was actually used — not a recomputed PL one.
        wire2 = [(m.role, m.content) for m in prov.calls[1]]
        self.assertIn(("system", language_directive("en")), wire2)
        self.assertNotIn(("system", language_directive("pl")), wire2)

    async def test_history_never_stores_response_language(self) -> None:
        prov = FakeModelProvider([["ok"]])
        s = self._session(prov)
        await _drain(s.send("Cześć", response_mode=ResponseMode.VOICE, response_language="en"))
        for turn in s.history:
            self.assertFalse(hasattr(turn, "response_language"))
        self.assertEqual([t.content for t in s.history], ["Cześć", "ok"])

    def test_context_build_rejects_mismatched_length(self) -> None:
        from nexa.conversation.turn import ConversationTurn
        hist = [ConversationTurn(role=Role.USER, content="x")]
        with self.assertRaises(ValueError):
            ConversationContext.build(SYSTEM_PROMPT, hist, response_languages=["pl", "en"])


# --------------------------------------------------------------------------- #
# VoiceConversationAdapter — end to end wiring
# --------------------------------------------------------------------------- #

class TestAdapterBilingualWiring(unittest.IsolatedAsyncioTestCase):
    async def _run_one(self, adapter, tr):
        await adapter._run_turn(tr)

    async def test_pl_then_en_turns_hit_the_same_session_and_resolve_language(self) -> None:
        prov = FakeModelProvider([["odp"], ["ans"]])
        s = ConversationSession(provider=prov, system_prompt=SYSTEM_PROMPT)
        seen: list[TurnLanguage] = []
        adapter = VoiceConversationAdapter(
            s, response_language_resolver=ResponseLanguageResolver(),
            on_turn_language=seen.append,
        )
        await self._run_one(adapter, TranscriptionResult(
            text="Co to jest czarna dziura?", language=Language.PL,
            audio_duration_s=3.0, wall_latency_s=0.1))
        await self._run_one(adapter, TranscriptionResult(
            text="What is a star made of?", language=Language.EN,
            audio_duration_s=3.0, wall_latency_s=0.1))
        # same session, two real turns
        self.assertEqual(
            [t.role for t in s.history],
            [Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT],
        )
        self.assertEqual(seen[0].response_language, "pl")
        self.assertEqual(seen[1].response_language, "en")
        self.assertIn(language_directive("pl"), [m.content for m in prov.calls[0]])
        self.assertIn(language_directive("en"), [m.content for m in prov.calls[1]])

    async def test_sticky_request_makes_input_and_response_language_differ(self) -> None:
        prov = FakeModelProvider([["a"], ["b"]])
        s = ConversationSession(provider=prov, system_prompt=SYSTEM_PROMPT)
        seen: list[TurnLanguage] = []
        adapter = VoiceConversationAdapter(
            s, response_language_resolver=ResponseLanguageResolver(),
            on_turn_language=seen.append,
        )
        await self._run_one(adapter, TranscriptionResult(
            text="Answer in English.", language=Language.PL,
            audio_duration_s=2.5, wall_latency_s=0.1))
        await self._run_one(adapter, TranscriptionResult(
            text="Po co człowiekowi sen?", language=Language.PL,
            audio_duration_s=3.0, wall_latency_s=0.1))
        self.assertEqual(seen[1].input_speech_language, "pl")
        self.assertEqual(seen[1].response_language, "en")
        self.assertIn(language_directive("en"), [m.content for m in prov.calls[1]])

    async def test_rejected_wrong_script_text_never_reaches_session(self) -> None:
        # adapter is handed the BilingualSpeechTranscriber's already-safe
        # result — assert whatever text arrives is exactly what goes to send()
        prov = FakeModelProvider([["ok"]])
        s = ConversationSession(provider=prov, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(s, response_language_resolver=ResponseLanguageResolver())
        await self._run_one(adapter, TranscriptionResult(
            text="Nie.", language=Language.PL, audio_duration_s=1.6, wall_latency_s=0.1))
        self.assertEqual(s.history[0].content, "Nie.")

    async def test_no_resolver_keeps_pre_b5_behaviour(self) -> None:
        prov = FakeModelProvider([["ok"]])
        s = ConversationSession(provider=prov, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(s)  # no resolver
        await self._run_one(adapter, TranscriptionResult(
            text="Co to jest atom?", language=Language.PL,
            audio_duration_s=3.0, wall_latency_s=0.1))
        # response_language is None -> R0009 text detection -> pl directive
        self.assertIn(language_directive("pl"), [m.content for m in prov.calls[0]])


# --------------------------------------------------------------------------- #
# Bridge — TTS voice from ResponseLanguage
# --------------------------------------------------------------------------- #

class TestBridgeVoiceMapping(unittest.TestCase):
    def _bridge(self):
        return AssistantSpeechBridge(en_voice="EN_V", pl_voice="PL_V")

    def test_select_voice_maps_from_response_language(self) -> None:
        b = self._bridge()
        b.select_voice("pl")
        self.assertEqual(b._current_voice, "PL_V")
        b.select_voice("en")
        self.assertEqual(b._current_voice, "EN_V")

    def test_on_user_transcript_does_not_override_explicit_voice(self) -> None:
        b = self._bridge()
        b.select_voice("en")            # ResponseLanguage = EN
        b.on_user_transcript("To jest polski tekst z ą ę ł.")  # PL text
        self.assertEqual(b._current_voice, "EN_V")  # not switched to PL by text

    def test_without_explicit_voice_falls_back_to_text_detection(self) -> None:
        b = self._bridge()
        b.on_user_transcript("Dlaczego niebo jest niebieskie?")
        self.assertEqual(b._current_voice, "PL_V")

    def test_explicit_voice_flag_resets_after_response(self) -> None:
        b = self._bridge()
        b.select_voice("en")
        b.on_assistant_complete("done")
        b.on_user_transcript("Co to jest atom?")  # PL text, flag was reset
        self.assertEqual(b._current_voice, "PL_V")


# --------------------------------------------------------------------------- #
# scope guards — production authorities unchanged
# --------------------------------------------------------------------------- #

class TestScopeGuards(unittest.TestCase):
    def test_llm_serving_config_unchanged(self) -> None:
        self.assertEqual(nexa_config.DEFAULT_LOCAL_MODEL, "gemma4:e4b")
        self.assertEqual(nexa_config.DEFAULT_LOCAL_NUM_THREAD, 2)
        self.assertEqual(nexa_config.DEFAULT_LOCAL_KEEP_ALIVE, "30m")

    def test_no_e2b_or_second_model_in_new_code(self) -> None:
        for name in ("stt/bilingual.py", "stt/language_detection.py", "stt/language_guard.py",
                     "conversation/response_language.py", "voice_conversation/adapter.py"):
            src = (SRC / "nexa" / name).read_text(encoding="utf-8")
            self.assertNotIn("gemma4:e2b", src)
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn("gemma4:e2b", node.value)

    def test_bilingual_stt_never_constructs_a_conversation_or_provider(self) -> None:
        for name in ("stt/bilingual.py", "stt/language_detection.py", "stt/language_guard.py"):
            src = (SRC / "nexa" / name).read_text(encoding="utf-8")
            for forbidden in ("ConversationSession(", "LocalModelProvider(",
                              "build_default_session(", "ModelProvider"):
                self.assertNotIn(forbidden, src, f"{name}: {forbidden}")

    def test_warm_up_and_bootstrap_untouched(self) -> None:
        from nexa.bootstrap import build_default_session, warm_up_session  # noqa: F401
        s = build_default_session()
        self.assertEqual(s.provider.describe().model, "gemma4:e4b")
        self.assertEqual(s.provider._num_thread, 2)
        self.assertEqual(s.provider._keep_alive, "30m")

    async def _text_unchanged(self) -> None:
        prov = FakeModelProvider([["ok"]])
        s = ConversationSession(provider=prov, system_prompt=SYSTEM_PROMPT)
        await _drain(s.send("What is an atom?"))  # default TEXT, no response_language
        ctx = ConversationContext.build(SYSTEM_PROMPT, s.history[:1])
        expected = [m.content for m in ctx.to_provider_messages()]
        self.assertEqual([m.content for m in prov.calls[0]], expected)

    def test_text_mode_byte_for_byte_unchanged(self) -> None:
        asyncio.run(self._text_unchanged())


if __name__ == "__main__":
    unittest.main()
