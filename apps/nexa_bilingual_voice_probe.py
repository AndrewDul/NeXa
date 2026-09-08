#!/usr/bin/env python3
"""NeXa M2.4B.5 — automatic bilingual PL/EN voice probe (live acceptance).

Thin wiring/presentation only (`apps/README.md`). ONE canonical
`ConversationSession` (`nexa.bootstrap.build_default_session` + the B.3.6
warm-up), gemma4:e4b unchanged. The STT boundary is
`BilingualSpeechTranscriber` = library-level LID
(`WhisperCppLanguageDetector`, p_pl/p_en) → `LanguageIdGuard`
(AUTO_ACCEPT | FALLBACK_REDECODE) → ONE explicit whisper.cpp decode in the
selected PL/EN language. The response language is resolved separately by
`ResponseLanguageResolver` (mirror the spoken language; honour an explicit
request / sticky preference) and drives the Piper voice.

No `--language pl|en`: the language is decided per utterance. `--bootstrap`
only sets the turn-1 fallback (default: pl, the operator's primary).

Try, in one continuous session:
    1.  Co to jest czarna dziura?
    2.  Okay, tell me more about it.
    3.  Z czego składa się gwiazda?
    4.  What is the actual colour of the Sun?
    5.  Dobra.
    6.  Tell me something interesting.
    7.  Odpowiedz po polsku.
    8.  Why is the sky blue?
    9.  Answer in English.
    10. Po co człowiekowi sen?
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import aiohttp  # noqa: E402
from loguru import logger  # noqa: E402
from pipecat.services.piper.tts import PiperHttpTTSService  # noqa: E402

from nexa.bootstrap import build_default_session, warm_up_session  # noqa: E402
from nexa.conversation import ResponseLanguageResolver, ResponseMode  # noqa: E402
from nexa.providers.base import ModelUnavailableError  # noqa: E402
from nexa.stt import (  # noqa: E402
    BilingualSpeechTranscriber,
    Language,
    LanguageIdGuard,
    SttBinaryNotFoundError,
    SttLibraryNotFoundError,
    SttModelNotFoundError,
    WhisperCppLanguageDetector,
    WhisperCppTranscriber,
)
from nexa.tts import (  # noqa: E402
    DEFAULT_PIPER_NICE,
    EN_VOICE,
    PL_VOICE,
    PiperHttpConfig,
    PiperHttpServer,
)
from nexa.tts.errors import (  # noqa: E402
    PiperHttpError,
    PiperServerStartError,
    PiperVenvNotFoundError,
    PiperVoiceNotFoundError,
    SentenceTokenizerDataMissingError,
)
from nexa.voice import HalfDuplexGate, LocalAudioConfig, VoiceRuntime  # noqa: E402
from nexa.voice.state import VoiceEvent  # noqa: E402
from nexa.voice_conversation import TurnLanguage, VoiceConversationAdapter  # noqa: E402
from nexa.voice_tts import (  # noqa: E402
    DEFAULT_CONTINUITY_TARGET_S,
    DEFAULT_TTS_CONTEXT_TIMEOUT_S,
    AssistantSpeechBridge,
    NexaSpeechContinuityController,
    NexaSpeechPlanner,
    TtsStatusObserver,
    ensure_sentence_tokenizer_data,
)

_gate = HalfDuplexGate()


def _quiet_pipecat() -> None:
    """Keep the terminal readable: drop Pipecat/loguru INFO/DEBUG spam."""
    logger.remove()
    logger.add(sys.stderr, level="WARNING")


def on_event(event: VoiceEvent) -> None:
    print(f"· {event.to_state.value.upper()}")


def _voice_name(lang: str) -> str:
    return PL_VOICE if lang == "pl" else EN_VOICE


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--bootstrap", choices=[lang.value for lang in Language], default=Language.PL.value,
        help="turn-1 fallback language only (used before any confident turn "
        "and for sub-2s ambiguous utterances at the very start). Default: pl.",
    )
    p.add_argument("--piper-nice", type=int, default=DEFAULT_PIPER_NICE)
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    _quiet_pipecat()
    bootstrap = Language(args.bootstrap)

    try:
        ensure_sentence_tokenizer_data()
    except SentenceTokenizerDataMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        base_transcriber = WhisperCppTranscriber()
        detector = WhisperCppLanguageDetector()
    except (SttBinaryNotFoundError, SttModelNotFoundError, SttLibraryNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("NeXa M2.4B.5 — Automatic Bilingual PL/EN Voice Probe")
    print(f"bootstrap (turn-1 fallback) language: {bootstrap.value}")
    print("starting external Piper HTTP server...")
    try:
        piper_server = PiperHttpServer(PiperHttpConfig(nice=args.piper_nice))
        await piper_server.start()
        await piper_server.prewarm()
    except (PiperVenvNotFoundError, PiperVoiceNotFoundError, PiperServerStartError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    session = build_default_session()
    print(f"model: {session.provider.describe().model}  "
          f"(num_thread={session.provider._num_thread}, keep_alive={session.provider._keep_alive})")
    try:
        await warm_up_session(session)
        print("warm-up: model + persona/VOICE prefix primed")
    except ModelUnavailableError as exc:
        print(f"error: model warm-up failed — {exc}", file=sys.stderr)
        sys.exit(1)

    guard = LanguageIdGuard()
    bilingual = BilingualSpeechTranscriber(
        base_transcriber, detector, guard=guard, session_default_language=bootstrap
    )
    resolver = ResponseLanguageResolver()

    bridge = AssistantSpeechBridge(en_voice=EN_VOICE, pl_voice=PL_VOICE, gate=_gate)
    planner = NexaSpeechPlanner(
        en_voice=EN_VOICE, pl_voice=PL_VOICE, default_language=bootstrap.value
    )
    continuity = NexaSpeechContinuityController(target_reserve_s=DEFAULT_CONTINUITY_TARGET_S)
    aiohttp_session = aiohttp.ClientSession()
    tts_service = PiperHttpTTSService(
        base_url=piper_server.config.synthesize_url,
        aiohttp_session=aiohttp_session,
        stop_frame_timeout_s=DEFAULT_TTS_CONTEXT_TIMEOUT_S,
    )
    tts_observer = TtsStatusObserver(
        on_tts_audio=lambda n, sr, ch: continuity.note_tts_audio(n, sr, ch),
    )

    def on_turn_language(t: TurnLanguage) -> None:
        d = t.language_decision
        print("\n" + "─" * 62)
        if d is not None:
            print(f"heard language     : {d.selected_language}   "
                  f"(raw whisper: {d.raw_detected_language} p={d.raw_confidence:.2f})")
            print(f"language confidence : p_pl={d.p_pl:.3f}  p_en={d.p_en:.3f}  "
                  f"(threshold {d.confidence_threshold:.2f})")
            print(f"guard decision     : {d.guard_decision.value.upper()}"
                  f"{'  → re-decoded in ' + d.selected_language if d.redecoded else ''}")
            print(f"                     {d.guard_reason}")
            print(f"STT latency        : detect {d.detect_latency_s:.2f}s + "
                  f"decode {d.decode_latency_s:.2f}s = {d.total_latency_s:.2f}s")
        print(f'canonical transcript: "{t.transcript}"')
        print(f"response language  : {t.response_language}   ({t.response_reason})")
        if t.preference_changed:
            print(f"                     ↳ sticky preference now: {t.sticky_preference}")
        print(f"voice              : {_voice_name(t.response_language)}")
        print("─" * 62)
        bridge.select_voice(t.response_language)

    def on_user_transcript(text: str) -> None:
        print("assistant: ", end="", flush=True)
        bridge.on_user_transcript(text)

    def on_assistant_token(tok: str) -> None:
        print(tok, end="", flush=True)
        bridge.on_assistant_token(tok)

    def on_assistant_complete(text: str) -> None:
        print()
        bridge.on_assistant_complete(text)

    def on_conversation_error(exc: Exception) -> None:
        print(f"\n[error] {exc}")
        bridge.on_conversation_error(exc)

    adapter = VoiceConversationAdapter(
        session,
        response_language_resolver=resolver,
        on_turn_language=on_turn_language,
        on_user_transcript=on_user_transcript,
        on_assistant_token=on_assistant_token,
        on_assistant_complete=on_assistant_complete,
        on_conversation_error=on_conversation_error,
        response_mode=ResponseMode.VOICE,
    )
    adapter_ref = [adapter]
    adapter.start()

    def on_transcription(result) -> None:
        adapter_ref[0].handle_transcription(result)

    def on_transcription_error(exc: Exception) -> None:
        adapter_ref[0].handle_transcription_error(exc)

    runtime = VoiceRuntime(
        LocalAudioConfig(),
        on_event=on_event,
        transcriber=bilingual,
        language=bootstrap,  # BilingualSpeechTranscriber treats this as bootstrap only
        on_transcription=on_transcription,
        on_transcription_error=on_transcription_error,
        extra_output_stages=[bridge, planner, continuity, tts_service, tts_observer],
        half_duplex_gate=_gate,
    )

    print("\nSpeak naturally — mix Polish and English freely. Ctrl+C to stop.\n")
    try:
        await runtime.run()
    finally:
        await adapter.shutdown()
        await aiohttp_session.close()
        detector.close()
        try:
            await piper_server.stop()
        except PiperHttpError:
            pass
    print("\nfinal voice state:", runtime.state_machine.state.value.upper())
    print(f"last input language (session state): {bilingual.last_input_language}")
    print(f"sticky response preference: {resolver.preference.sticky}")
    print(f"max concurrent STT: {runtime.max_observed_stt_concurrency}  "
          f"max concurrent turns: {adapter.max_observed_conversation_concurrency}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n(stopped)")
