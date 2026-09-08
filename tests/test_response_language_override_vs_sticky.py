"""R0027 — one-turn override vs sticky ResponseLanguagePreference.

Offline, deterministic. Proves the corrected `ResponseLanguageResolver`
semantics and that nothing else moved (STT / LLM / serving / gate / TEXT).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fakes import FakeModelProvider  # noqa: E402
from nexa import config as nexa_config  # noqa: E402
from nexa.conversation import (  # noqa: E402
    ConversationContext,
    ConversationSession,
    ResponseLanguageResolver,
    ResponsePreference,
    detect_language_request,
)
from nexa.conversation.language import language_directive  # noqa: E402
from nexa.conversation.turn import Role  # noqa: E402

SYS = "Jesteś NeXa — testowa persona."


async def _drain(stream) -> str:
    return "".join([c async for c in stream])


class TestOneTurnOverride(unittest.TestCase):
    def test_1_odpowiedz_po_angielsku_from_pl_is_one_turn_en_no_sticky(self) -> None:
        r = ResponseLanguageResolver()
        d = r.resolve("Odpowiedz po angielsku.", input_language="pl")
        self.assertEqual(d.response_language, "en")
        self.assertEqual(d.request_kind, "one_turn")
        self.assertFalse(d.preference_changed)
        self.assertIsNone(r.preference.sticky)

    def test_2_next_pl_turn_after_one_turn_override_is_pl(self) -> None:
        r = ResponseLanguageResolver()
        r.resolve("Odpowiedz po angielsku.", input_language="pl")
        d = r.resolve("Jak działa komputer?", input_language="pl")
        self.assertEqual(d.response_language, "pl")
        self.assertIsNone(r.preference.sticky)

    def test_3_answer_in_polish_from_en_is_one_turn_pl_no_sticky(self) -> None:
        r = ResponseLanguageResolver()
        d = r.resolve("Answer in Polish.", input_language="en")
        self.assertEqual(d.response_language, "pl")
        self.assertEqual(d.request_kind, "one_turn")
        self.assertFalse(d.preference_changed)
        self.assertIsNone(r.preference.sticky)

    def test_4_next_en_turn_after_one_turn_override_is_en(self) -> None:
        r = ResponseLanguageResolver()
        r.resolve("Answer in Polish.", input_language="en")
        d = r.resolve("Why is the sky blue?", input_language="en")
        self.assertEqual(d.response_language, "en")

    def test_reply_in_english_and_answer_in_english_are_one_turn(self) -> None:
        for txt in ("Reply in English.", "Answer in English.", "Odpowiedz po polsku."):
            req = detect_language_request(txt)
            self.assertIsNotNone(req, txt)
            self.assertEqual(req.kind, "one_turn", txt)


class TestStickyPreference(unittest.TestCase):
    def test_5_od_teraz_mow_po_angielsku_sets_sticky_en(self) -> None:
        r = ResponseLanguageResolver()
        d = r.resolve("Od teraz mów po angielsku.", input_language="pl")
        self.assertEqual(d.response_language, "en")
        self.assertEqual(d.request_kind, "sticky")
        self.assertTrue(d.preference_changed)
        self.assertEqual(r.preference.sticky, "en")

    def test_6_next_pl_turn_while_sticky_en_is_en(self) -> None:
        r = ResponseLanguageResolver()
        r.resolve("Od teraz mów po angielsku.", input_language="pl")
        d = r.resolve("Jak działa komputer?", input_language="pl")
        self.assertEqual(d.response_language, "en")
        self.assertEqual(r.preference.sticky, "en")

    def test_from_now_on_english_variants_are_sticky(self) -> None:
        for txt in (
            "From now on speak English.",
            "From now on answer in English.",
            "Od teraz odpowiadaj po angielsku.",
        ):
            req = detect_language_request(txt)
            self.assertIsNotNone(req, txt)
            self.assertEqual((req.language, req.kind), ("en", "sticky"), txt)


class TestStickySwitch(unittest.TestCase):
    def test_7_wracamy_do_polskiego_sets_sticky_pl(self) -> None:
        r = ResponseLanguageResolver(ResponsePreference(sticky="en"))
        d = r.resolve("Wracamy do polskiego.", input_language="en")
        self.assertEqual(d.response_language, "pl")
        self.assertEqual(d.request_kind, "sticky")
        self.assertEqual(r.preference.sticky, "pl")

    def test_8_next_en_turn_after_switch_to_pl_is_pl(self) -> None:
        r = ResponseLanguageResolver(ResponsePreference(sticky="en"))
        r.resolve("Wracamy do polskiego.", input_language="en")
        d = r.resolve("Why is the sky blue?", input_language="en")
        self.assertEqual(d.response_language, "pl")

    def test_switch_phrases_map_to_sticky(self) -> None:
        cases = {
            "Wracamy do polskiego.": ("pl", "sticky"),
            "Let's go back to Polish.": ("pl", "sticky"),
            "Switch back to Polish.": ("pl", "sticky"),
            "Switch to English.": ("en", "sticky"),
        }
        for txt, want in cases.items():
            req = detect_language_request(txt)
            self.assertIsNotNone(req, txt)
            self.assertEqual((req.language, req.kind), want, txt)


class TestContentMentionSafety(unittest.TestCase):
    def test_9_ordinary_language_mentions_do_not_change_preference(self) -> None:
        for txt in (
            "Tell me about Polish history.",
            "What is the English word for kot?",
            "Why is Polish difficult?",
            "Translate this English sentence.",
            "What does this Polish word mean?",
            "Explain Polish grammar to me.",
            "I am learning English.",
            "How do you say 'thank you' in Polish?",
        ):
            self.assertIsNone(detect_language_request(txt), txt)
            r = ResponseLanguageResolver()
            before = r.preference.sticky
            d = r.resolve(txt, input_language="en")
            self.assertEqual(r.preference.sticky, before)  # unchanged
            self.assertEqual(d.response_language, "en")  # just mirrors input
            self.assertIsNone(d.explicit_request)


class TestArchitectureSeparation(unittest.TestCase):
    def test_one_turn_override_never_mutates_preference_even_repeatedly(self) -> None:
        r = ResponseLanguageResolver()
        for _ in range(5):
            r.resolve("Odpowiedz po angielsku.", input_language="pl")
        self.assertIsNone(r.preference.sticky)

    def test_input_vs_response_vs_preference_are_distinct(self) -> None:
        r = ResponseLanguageResolver(ResponsePreference(sticky="en"))
        d = r.resolve("Dlaczego niebo jest niebieskie?", input_language="pl")
        # input=pl, response=en (sticky), preference=en
        self.assertEqual(d.response_language, "en")
        self.assertEqual(r.preference.sticky, "en")
        self.assertNotEqual(d.response_language, "pl")

    def test_preference_dataclass_is_the_only_mutable_state(self) -> None:
        src = (REPO_ROOT / "src" / "nexa" / "conversation" / "response_language.py").read_text()
        # the resolver mutates exactly one thing, in exactly one place: the
        # sticky preference, and only on the "sticky" branch.
        self.assertEqual(src.count("self.preference.sticky ="), 1)
        self.assertNotIn("_history", src)
        self.assertNotIn("ConversationSession", src)


class TestNothingElseMoved(unittest.IsolatedAsyncioTestCase):
    async def test_10_text_behaviour_unchanged(self) -> None:
        prov = FakeModelProvider([["ok"], ["ok"]])
        s = ConversationSession(provider=prov, system_prompt=SYS)
        await _drain(s.send("Co to jest atom?"))  # default TEXT, no response_language
        await _drain(s.send("What is an atom?"))
        ctx = ConversationContext.build(SYS, s.history[:1])
        expected = [m.content for m in ctx.to_provider_messages()]
        self.assertEqual([m.content for m in prov.calls[0]], expected)
        self.assertIn(language_directive("pl"), [m.content for m in prov.calls[0]])
        self.assertIn(language_directive("en"), [m.content for m in prov.calls[1]])

    async def test_11_same_session_provider_model_authority(self) -> None:
        from nexa.stt import Language, TranscriptionResult
        from nexa.voice_conversation import VoiceConversationAdapter

        prov = FakeModelProvider([["a"], ["b"]])
        s = ConversationSession(provider=prov, system_prompt=SYS)
        adapter = VoiceConversationAdapter(
            s, response_language_resolver=ResponseLanguageResolver()
        )
        await adapter._run_turn(TranscriptionResult(
            text="Co to jest czarna dziura?", language=Language.PL,
            audio_duration_s=3.0, wall_latency_s=0.1))
        await adapter._run_turn(TranscriptionResult(
            text="What is a star made of?", language=Language.EN,
            audio_duration_s=3.0, wall_latency_s=0.1))
        self.assertIs(adapter._session, s)
        self.assertIs(s.provider, prov)
        self.assertEqual([t.role for t in s.history],
                         [Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT])

    def test_12_to_17_serving_stt_gate_unchanged(self) -> None:
        # 12/13/14 serving config
        self.assertEqual(nexa_config.DEFAULT_LOCAL_MODEL, "gemma4:e4b")
        self.assertEqual(nexa_config.DEFAULT_LOCAL_NUM_THREAD, 2)
        self.assertEqual(nexa_config.DEFAULT_LOCAL_KEEP_ALIVE, "30m")
        # 15 warm-up
        from nexa.bootstrap import warm_up_session  # noqa: F401

        # 16 bilingual STT — guard thresholds untouched
        from nexa.stt.language_guard import (
            DEFAULT_CONFIDENCE_THRESHOLD,
            DEFAULT_MIN_RELIABLE_DURATION_S,
        )
        self.assertEqual(DEFAULT_CONFIDENCE_THRESHOLD, 0.60)
        self.assertEqual(DEFAULT_MIN_RELIABLE_DURATION_S, 2.0)
        # 17 HalfDuplexGate B.5A fix — response_in_flight covers dispatch
        from nexa.voice.gate import HalfDuplexGate
        g = HalfDuplexGate()
        g.notify_response_dispatched()
        self.assertTrue(g.response_in_flight)
        self.assertTrue(g.mic_suppressed)


if __name__ == "__main__":
    unittest.main()
