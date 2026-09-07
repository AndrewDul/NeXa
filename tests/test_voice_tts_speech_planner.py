"""M2.4B.2 — NeXa speech planner: TTS-only normalization + Polish-aware
phrase boundaries.

Deterministic, offline. No microphone / Piper server / voice model / Ollama
/ network. Drives the real ``NexaSpeechPlanner`` FrameProcessor through real
Pipecat frames with a captured ``push_frame`` sink — the same pattern
``test_voice_tts_bridge.py`` uses.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipecat.frames.frames import (  # noqa: E402
    AggregatedTextFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402
from pipecat.services.piper.tts import PiperHttpTTSService  # noqa: E402

from nexa.voice_tts import NexaSpeechPlanner, find_phrase_cut, normalize_for_speech  # noqa: E402
from nexa.voice_tts.speech_planner import (  # noqa: E402
    MAX_PHRASE_CHARS,
    _ends_with_abbreviation,
)

EN_VOICE = "en_GB-jenny_dioco-medium"
PL_VOICE = "pl_PL-gosia-medium"

PLANNER_SRC = SRC / "nexa" / "voice_tts" / "speech_planner.py"


# --------------------------------------------------------------------------- #
# Test harness
# --------------------------------------------------------------------------- #


def _planner(**kw) -> tuple[NexaSpeechPlanner, list[Frame]]:
    p = NexaSpeechPlanner(en_voice=EN_VOICE, pl_voice=PL_VOICE, **kw)
    pushed: list[Frame] = []

    async def fake_push_frame(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    p.push_frame = fake_push_frame
    return p, pushed


async def _feed(
    planner: NexaSpeechPlanner,
    tokens: list[str],
    *,
    voice: str | None = None,
    start: bool = True,
    end: bool = True,
) -> list[Frame]:
    """Drive one reply through the planner; return every frame it pushed."""
    _, pushed = getattr(planner, "_test_sink", (None, []))
    pushed = []

    async def fake_push_frame(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    planner.push_frame = fake_push_frame

    if voice is not None:
        await planner.process_frame(
            TTSUpdateSettingsFrame(delta=PiperHttpTTSService.Settings(voice=voice)),
            FrameDirection.DOWNSTREAM,
        )
    if start:
        await planner.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    for t in tokens:
        await planner.process_frame(LLMTextFrame(t), FrameDirection.DOWNSTREAM)
    if end:
        await planner.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    return pushed


def _spoken(pushed: list[Frame]) -> list[str]:
    return [f.text for f in pushed if isinstance(f, AggregatedTextFrame)]


def _tok(text: str) -> list[str]:
    """Split a reply into small streaming tokens (~3 chars), keeping spaces."""
    out: list[str] = []
    i = 0
    while i < len(text):
        out.append(text[i : i + 3])
        i += 3
    return out


# --------------------------------------------------------------------------- #
# POLISH boundary policy
# --------------------------------------------------------------------------- #


class TestPolishBoundaries(unittest.IsolatedAsyncioTestCase):
    async def test_1_not_split_after_tzw(self) -> None:
        p, _ = _planner(default_language="pl")
        pushed = await _feed(p, _tok("Najważniejszą cechą jest tzw. horyzont zdarzeń. Koniec."))
        spoken = _spoken(pushed)
        self.assertTrue(spoken)
        # "tzw." never ends a phrase, and never stands alone
        for s in spoken:
            self.assertFalse(s.endswith("tzw."), s)
            self.assertNotEqual(s.strip(), "tzw.")
        self.assertIn("tzw. horyzont zdarzeń", " ".join(spoken))

    async def test_2_np_expands_and_does_not_break(self) -> None:
        p, _ = _planner(default_language="pl")
        pushed = await _feed(p, _tok("Podaj np. czarną dziurę i gwiazdę neutronową."))
        spoken = " ".join(_spoken(pushed))
        self.assertIn("na przykład czarną dziurę", spoken)
        self.assertNotIn("np.", spoken)

    async def test_2b_na_przyklad_comma_not_split(self) -> None:
        p, _ = _planner(default_language="pl")
        pushed = await _feed(p, _tok("Na przykład, czarna dziura jest ciężka."))
        # the short "Na przykład," clause is under the clause floor -> not split
        self.assertEqual(_spoken(pushed), ["Na przykład, czarna dziura jest ciężka."])

    async def test_3_itd_itp_min_abbrevs_no_false_break(self) -> None:
        p, _ = _planner(default_language="pl")
        pushed = await _feed(
            p, _tok("Mamy gwiazdy, planety itd. w galaktyce. Wśród nich m.in. Słońce.")
        )
        spoken = _spoken(pushed)
        joined = " ".join(spoken)
        self.assertNotIn(" itd.", joined)  # expanded
        self.assertIn("i tak dalej", joined)
        self.assertIn("między innymi Słońce", joined)
        for s in spoken:
            self.assertGreater(len(s.strip()), 3)

    async def test_4_decimal_number_not_split(self) -> None:
        p, _ = _planner(default_language="pl")
        pushed = await _feed(
            p, _tok("Promień wynosi około 3.5 kilometra dla tej gwiazdy neutronowej.")
        )
        spoken = _spoken(pushed)
        self.assertEqual(len(spoken), 1)
        self.assertIn("3.5 kilometra", spoken[0])

    async def test_5_quoted_text_kept_whole(self) -> None:
        p, _ = _planner(default_language="pl")
        pushed = await _feed(
            p, _tok('Powiedział "to jest. bardzo ważne" i skończył swoją myśl teraz.')
        )
        # the period inside the quotes must not create a phrase break
        spoken = _spoken(pushed)
        self.assertEqual(len(spoken), 1, spoken)
        self.assertIn('"to jest. bardzo ważne"', spoken[0])

    async def test_6_parentheses_not_split_inside(self) -> None:
        p, _ = _planner(default_language="pl")
        reply = "Zjawisko (widoczne tam. i nie tylko) jest znane astronomom na całym świecie."
        pushed = await _feed(p, _tok(reply))
        spoken = _spoken(pushed)
        self.assertEqual(len(spoken), 1, spoken)
        self.assertIn("(widoczne tam. i nie tylko)", spoken[0])

    async def test_7_colon_boundary_when_long_enough(self) -> None:
        p, _ = _planner(default_language="pl")
        pushed = await _feed(
            p,
            _tok(
                "Wyjaśniam teraz dokładnie następującą kwestię: czarna dziura "
                "zakrzywia czasoprzestrzeń."
            ),
        )
        spoken = _spoken(pushed)
        self.assertEqual(len(spoken), 2, spoken)
        self.assertTrue(spoken[0].endswith(":"))

    async def test_8_semicolon_boundary(self) -> None:
        p, _ = _planner(default_language="pl")
        pushed = await _feed(
            p,
            _tok(
                "Pierwsza obserwacja była przełomowa dla nauki; druga potwierdziła "
                "całą teorię ostatecznie."
            ),
        )
        spoken = _spoken(pushed)
        self.assertEqual(len(spoken), 2, spoken)
        self.assertTrue(spoken[0].endswith(";"))

    async def test_9_comma_only_splits_a_long_clause(self) -> None:
        short = normalize_for_speech("Tak, to prawda.", language="pl")
        self.assertIsNone(find_phrase_cut(short))
        long_left = (
            "To jest wyjątkowo długa i rozbudowana klauzula z dużą ilością treści merytorycznej, "
        )
        idx = find_phrase_cut(long_left + "a potem następuje jeszcze więcej dalszego tekstu tutaj.")
        self.assertIsNotNone(idx)
        self.assertTrue((long_left + "x")[:idx].rstrip().endswith(","))

    async def test_10_no_tiny_numbered_or_bullet_chunks(self) -> None:
        p, _ = _planner(default_language="pl")
        reply = (
            "Oto dwa przykłady:\n"
            "1. Czarna dziura Sagittarius A gwiazd.\n"
            "2. Czarna dziura M87 w centrum galaktyki.\n"
        )
        pushed = await _feed(p, _tok(reply))
        for s in _spoken(pushed):
            self.assertNotIn(s.strip(), {"1.", "2.", "1", "2", "-", "•"})
            self.assertGreater(len(s.strip()), 5, s)


class TestEndsWithAbbreviation(unittest.TestCase):
    def test_known_polish_abbrev(self) -> None:
        for x in ("jest tzw.", "widziano m.in.", "podaj np.", "zobacz itd.", "prof."):
            self.assertTrue(_ends_with_abbreviation(x), x)

    def test_plain_word_and_number_are_not_abbrev(self) -> None:
        for x in ("to jest koniec zdania.", "wartość 3.5.", "słowo bez kropki"):
            self.assertFalse(_ends_with_abbreviation(x), x)

    def test_single_letter_is_treated_as_initial(self) -> None:
        self.assertTrue(_ends_with_abbreviation("napisał to J."))


# --------------------------------------------------------------------------- #
# MARKDOWN normalization (TTS-only)
# --------------------------------------------------------------------------- #


class TestMarkdownNormalization(unittest.IsolatedAsyncioTestCase):
    async def test_11_bold_markers_not_spoken(self) -> None:
        p, _ = _planner(default_language="pl")
        out = await _feed(p, _tok("To jest **bardzo ważne** zagadnienie dla tej dyskusji."))
        spoken = " ".join(_spoken(out))
        self.assertNotIn("*", spoken)
        self.assertIn("bardzo ważne", spoken)

    async def test_12_italic_markers_not_spoken(self) -> None:
        p, _ = _planner(default_language="en")
        out = await _feed(p, _tok("The game *Call of Duty* is really well known here."))
        spoken = " ".join(_spoken(out))
        self.assertNotIn("*", spoken)
        self.assertIn("Call of Duty", spoken)

    async def test_13_numbered_list_markers_not_standalone(self) -> None:
        p, _ = _planner(default_language="pl")
        reply = "Dwa przykłady strzelanek:\n1. Call of Duty\n2. Battlefield\n"
        spoken = _spoken(await _feed(p, _tok(reply)))
        joined = " ".join(spoken)
        self.assertNotIn("1.", joined)
        self.assertNotIn("2.", joined)
        self.assertIn("Call of Duty oraz Battlefield", joined)

    async def test_14_bullets_not_spoken_as_symbols(self) -> None:
        p, _ = _planner(default_language="pl")
        reply = "Cechy:\n- horyzont zdarzeń\n- osobliwość\n- dysk akrecyjny\nto wszystko."
        spoken = " ".join(_spoken(await _feed(p, _tok(reply))))
        self.assertNotIn("- ", spoken)
        self.assertNotIn("•", spoken)
        self.assertIn("horyzont zdarzeń", spoken)
        self.assertIn("osobliwość", spoken)

    async def test_15_headings_cleaned(self) -> None:
        p, _ = _planner(default_language="pl")
        spoken = " ".join(
            _spoken(await _feed(p, _tok("# Czarne dziury\nSą fascynującym obiektem astronomii.")))
        )
        self.assertNotIn("#", spoken)
        self.assertIn("Czarne dziury", spoken)

    async def test_16_code_formatting_handled_safely(self) -> None:
        p, _ = _planner(default_language="en")
        reply = (
            "Use the value `c` for light speed here.\n"
            "```\nE = m c^2\n```\n"
            "That is the whole idea now."
        )
        spoken = " ".join(_spoken(await _feed(p, _tok(reply))))
        self.assertNotIn("`", spoken)
        self.assertNotIn("```", spoken)
        self.assertIn("light speed", spoken)

    def test_list_to_prose_example_from_brief(self) -> None:
        out = normalize_for_speech(
            "**Dwa przykłady strzelanek:**\n1. *Call of Duty*\n2. *Battlefield*", language="pl"
        )
        self.assertEqual(out, "Dwa przykłady strzelanek: Call of Duty oraz Battlefield.")

    def test_list_prose_english_connector(self) -> None:
        out = normalize_for_speech("Examples:\n1. Alpha\n2. Beta\n3. Gamma", language="en")
        self.assertIn("Alpha, Beta and Gamma", out)

    def test_normalizer_never_raises_returns_str(self) -> None:
        for junk in ("", "   ", "***", "```", "[", "|||", "###", "\n\n\n", "1."):
            self.assertIsInstance(normalize_for_speech(junk, language="pl"), str)

    def test_streaming_holds_incomplete_list_run(self) -> None:
        partial = "Przykłady:\n1. Alfa\n2. Bet"
        held = normalize_for_speech(partial, language="pl", streaming=True)
        self.assertEqual(held, "Przykłady:")
        full = normalize_for_speech(partial + "a\nto koniec.", language="pl", streaming=True)
        self.assertIn("Alfa oraz Beta", full)


# --------------------------------------------------------------------------- #
# STREAMING behaviour
# --------------------------------------------------------------------------- #


class TestStreaming(unittest.IsolatedAsyncioTestCase):
    async def test_17_incremental_tokens_combine_correctly(self) -> None:
        p, _ = _planner(default_language="pl")
        text = "Czarna dziura to obszar. Grawitacja jest ogromna. Nic nie ucieka stamtąd."
        pushed = await _feed(p, _tok(text))
        spoken = _spoken(pushed)
        self.assertEqual(len(spoken), 3, spoken)
        self.assertEqual(
            " ".join(spoken),
            "Czarna dziura to obszar. Grawitacja jest ogromna. Nic nie ucieka stamtąd.",
        )

    async def test_18_final_buffer_flushes_exactly_once(self) -> None:
        p, _ = _planner(default_language="pl")
        # no terminal punctuation -> only the flush can emit it
        pushed = await _feed(p, _tok("Krótka odpowiedź bez żadnej końcowej kropki w środku"))
        spoken = _spoken(pushed)
        self.assertEqual(spoken, ["Krótka odpowiedź bez żadnej końcowej kropki w środku"])

    async def test_19_no_duplicate_text_across_stream(self) -> None:
        p, _ = _planner(default_language="pl")
        text = (
            "Pierwsze zdanie jest dość długie i sensowne. Drugie zdanie również niesie treść. "
            "Trzecie zamyka całą wypowiedź teraz."
        )
        spoken = _spoken(await _feed(p, _tok(text)))
        self.assertEqual(" ".join(spoken), text)
        # every source word appears exactly once in the concatenation
        self.assertEqual(len(" ".join(spoken).split()), len(text.split()))

    async def test_20_no_empty_or_formatting_only_frames(self) -> None:
        p, _ = _planner(default_language="pl")
        reply = "**  **\n\n- \n\n## \n\n`` \n\nRealna treść pojawia się dopiero teraz w zdaniu."
        pushed = await _feed(p, _tok(reply))
        for f in pushed:
            if isinstance(f, AggregatedTextFrame):
                self.assertTrue(f.text.strip())
                self.assertTrue(any(ch.isalnum() for ch in f.text))

    async def test_21_response_reset_clears_prior_state(self) -> None:
        p, _ = _planner(default_language="pl")
        # first reply, cut short with no end frame (like Ctrl+C mid-reply)
        await _feed(p, _tok("Niedokończone zdanie bez konca"), end=False)
        self.assertNotEqual(p._raw, "")
        # a new reply starts -> planner must forget the stale buffer
        pushed = await _feed(p, _tok("Nowa odpowiedź jest kompletna i poprawna teraz."))
        spoken = _spoken(pushed)
        self.assertEqual(spoken, ["Nowa odpowiedź jest kompletna i poprawna teraz."])
        self.assertNotIn("Niedokończone", " ".join(spoken))

    async def test_start_frame_and_end_frame_are_forwarded(self) -> None:
        p, _ = _planner(default_language="pl")
        pushed = await _feed(p, _tok("Zdanie testowe kompletne tutaj teraz."))
        self.assertTrue(any(isinstance(f, LLMFullResponseStartFrame) for f in pushed))
        self.assertTrue(any(isinstance(f, LLMFullResponseEndFrame) for f in pushed))

    async def test_llm_text_frames_are_consumed_not_forwarded(self) -> None:
        p, _ = _planner(default_language="pl")
        pushed = await _feed(p, _tok("Zdanie testowe kompletne tutaj teraz."))
        self.assertFalse(any(isinstance(f, LLMTextFrame) for f in pushed))
        self.assertTrue(any(isinstance(f, AggregatedTextFrame) for f in pushed))

    async def test_unknown_frames_pass_through_unchanged(self) -> None:
        p, _ = _planner(default_language="pl")
        marker = TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1)
        await p.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        pushed: list[Frame] = []

        async def sink(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        p.push_frame = sink
        await p.process_frame(marker, FrameDirection.DOWNSTREAM)
        self.assertIs(pushed[-1], marker)

    async def test_language_tracked_from_tts_settings_frame(self) -> None:
        p, _ = _planner(default_language="en")
        # a PL voice settings frame flips the connector word to "oraz"
        pushed = await _feed(
            p, _tok("Przykłady:\n1. Alfa\n2. Beta\nto tyle."), voice=PL_VOICE
        )
        self.assertIn("Alfa oraz Beta", " ".join(_spoken(pushed)))

    async def test_emitted_frames_are_aggregated_sentence_type(self) -> None:
        p, _ = _planner(default_language="pl")
        pushed = await _feed(p, _tok("Zdanie pierwsze jest tutaj. Zdanie drugie też jest."))
        agg = [f for f in pushed if isinstance(f, AggregatedTextFrame)]
        self.assertTrue(agg)
        for f in agg:
            self.assertEqual(str(f.aggregated_by), "sentence")
            self.assertTrue(f.append_to_context)

    async def test_long_run_on_without_punctuation_is_capped(self) -> None:
        p, _ = _planner(default_language="pl")
        run_on = "słowo " * 80  # > MAX_PHRASE_CHARS, no punctuation
        pushed = await _feed(p, _tok(run_on), end=False)
        spoken = _spoken(pushed)
        self.assertTrue(spoken, "the hard cap must release something before flush")
        self.assertLessEqual(len(spoken[0]), MAX_PHRASE_CHARS + 10)


# --------------------------------------------------------------------------- #
# TRANSCRIPT INVARIANT + ARCHITECTURE
# --------------------------------------------------------------------------- #


class TestTranscriptInvariantAndArchitecture(unittest.IsolatedAsyncioTestCase):
    async def test_22_planner_holds_no_conversation_or_history_reference(self) -> None:
        p, _ = _planner(default_language="pl")
        await _feed(p, _tok("Test zdanie kompletne tutaj teraz jest."))
        for attr in vars(p):
            self.assertNotIn("session", attr.lower())
            self.assertNotIn("history", attr.lower())
            self.assertNotIn("context", attr.lower())

    async def test_23_source_token_copy_is_preserved_byte_identical(self) -> None:
        p, _ = _planner(default_language="pl")
        tokens = _tok("**Ważne**: to jest oryginalny tekst asystenta z formatowaniem.")
        await _feed(p, tokens, end=False)
        # the planner keeps the raw assistant copy verbatim; only its *output*
        # is normalized. It never rewrites the source it was handed.
        self.assertEqual(p._raw, "".join(tokens))

    def test_24_planner_does_not_import_model_provider_or_session(self) -> None:
        imported = _imported_modules(PLANNER_SRC)
        called = _called_names(PLANNER_SRC)
        for bad in ("nexa.conversation", "nexa.bootstrap", "nexa.config", "nexa.providers"):
            self.assertNotIn(bad, imported)
            for mod in imported:
                self.assertFalse(mod.startswith(bad + "."), mod)
        for bad in ("ConversationSession", "LocalModelProvider", "LlamaServerProvider",
                    "PersonaConfig", "ModelProvider"):
            self.assertNotIn(bad, called)

    def test_25_planner_owns_no_response_language_authority(self) -> None:
        tree = ast.parse(PLANNER_SRC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                self.assertNotIn(node.name, ("detect_response_language", "detect_language"))
        # and it does not import the canonical classifier either — it reads
        # the already-decided voice from the settings frame instead.
        self.assertNotIn("nexa.conversation.language", _imported_modules(PLANNER_SRC))

    def test_26_to_30_no_pacing_scheduling_or_barge_in(self) -> None:
        src = PLANNER_SRC.read_text(encoding="utf-8")
        forbidden = [
            "renice", "taskset", "sched_setaffinity", "sched_getaffinity",
            "length_scale", "noise_scale",  # no dynamic speech speed
            "asyncio.sleep", "time.sleep", "await sleep",
            "buffered_audio", "BufferEstimate", "remaining_audio",  # no B.3 buffer control
            "interstitial",  # no fillers
            "cancel_generation", "StopInterruptionFrame",  # no M2.5 barge-in
        ]
        hits = [tok for tok in forbidden if tok in src]
        self.assertEqual(hits, [], f"speech_planner.py must not reference: {hits}")

    def test_planner_imports_only_pipecat_and_stdlib(self) -> None:
        for mod in _imported_modules(PLANNER_SRC):
            top = mod.split(".")[0]
            self.assertIn(
                top,
                {"__future__", "re", "loguru", "pipecat"},
                f"unexpected import in speech_planner.py: {mod}",
            )


class TestEnglishStillWorks(unittest.IsolatedAsyncioTestCase):
    async def test_31_basic_english_sentence_boundaries(self) -> None:
        p, _ = _planner(default_language="en")
        text = "A black hole bends spacetime. Light cannot escape it. Time slows near it."
        spoken = _spoken(await _feed(p, _tok(text)))
        self.assertEqual(len(spoken), 3, spoken)
        self.assertEqual(" ".join(spoken), text)

    async def test_32_english_abbreviations_no_false_boundary(self) -> None:
        p, _ = _planner(default_language="en")
        text = "See the paper, e.g. Hawking et al. and others, i.e. the classic works here."
        spoken = _spoken(await _feed(p, _tok(text)))
        joined = " ".join(spoken)
        for s in spoken:
            self.assertGreater(len(s.strip()), 5, s)
        # "e.g." / "i.e." expanded; "et al." / "Dr." never end a phrase alone
        self.assertNotIn(" e.g.", joined)
        self.assertNotIn(" i.e.", joined)

    async def test_english_dr_mr_titles_do_not_break(self) -> None:
        p, _ = _planner(default_language="en")
        spoken = _spoken(
            await _feed(p, _tok("Dr. Smith and Mr. Jones wrote the paper together last year."))
        )
        self.assertEqual(len(spoken), 1, spoken)

    async def test_markdown_cleanup_is_language_neutral(self) -> None:
        sample = "This has **bold** and `code` and a # heading right here now."
        for lang in ("pl", "en"):
            p, _ = _planner(default_language=lang)
            spoken = " ".join(_spoken(await _feed(p, _tok(sample))))
            self.assertNotIn("*", spoken)
            self.assertNotIn("`", spoken)
            self.assertNotIn("#", spoken)


# --------------------------------------------------------------------------- #
# ast helpers (mirror test_voice_tts_architecture.py)
# --------------------------------------------------------------------------- #


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name:
                names.add(name)
    return names


if __name__ == "__main__":
    unittest.main()
