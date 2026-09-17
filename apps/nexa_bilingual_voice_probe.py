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

from nexa.bootstrap import (  # noqa: E402
    build_default_context_runtime,
    build_default_session,
    warm_up_session,
)
from nexa.conversation import (  # noqa: E402
    ProviderWindow,
    ResponseLanguageResolver,
    ResponseMode,
)
from nexa.conversation.context_projection import make_local_context_provider  # noqa: E402
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
    build_bargein_stack,
    ensure_sentence_tokenizer_data,
)

# _gate is (re)constructed in main() once we know whether --bargein is set.
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
    p.add_argument(
        "--bargein", action=argparse.BooleanOptionalAction, default=False,
        help="M2.5B production barge-in / interruption. Default OFF = R0026 "
        "whole-response half-duplex. When ON, NeXa's TTS PCM is also fed to "
        "the XVF3800 AEC far-end reference (plug:respeaker) so the mic can "
        "stay hot; a sustained interruption cancels the reply. If the AEC "
        "reference cannot start, barge-in disables itself for safety.",
    )
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
    # M2.5B.2 — bound the *provider-facing* context to a prefix-stable
    # window so a long voice session never hits the sliding-window KV-cache
    # collapse (R0029). Canonical ``session.history`` stays complete. This
    # is a latency fix with no conversation-semantics change, so it applies
    # to both ``--bargein`` and ``--no-bargein`` (the R0026 mic/half-duplex
    # policy is unaffected).
    session.provider_window = ProviderWindow()
    print(f"model: {session.provider.describe().model}  "
          f"(num_thread={session.provider._num_thread}, keep_alive={session.provider._keep_alive})")
    pw = session.provider_window
    print(f"provider window: keep={pw.keep_entries} soft={pw.soft_entries} "
          f"hard={pw.hard_entries} entries (canonical history stays complete)")

    # R0080 §2: ONE ContextRuntime for the whole application lifetime --
    # never rebuilt per turn (same composition-root pattern R0077 already
    # established for apps/nexa_chat.py). make_local_context_provider()
    # never filters cloud_eligibility -- local voice may legitimately use
    # LOCAL_ONLY content (R0079's own proven local-voice parity).
    context_runtime = build_default_context_runtime()
    context_provider = make_local_context_provider(context_runtime.context_engine)
    print("Core Context: ContextRuntime ready, context_provider wired into "
          "local voice (R0080)")
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

    # ---- M2.5B barge-in wiring (single source: build_bargein_stack) -----
    global _gate
    bargein_on = bool(args.bargein)

    def _aec_status(active: bool) -> None:
        msg = "✓ AEC REF ACTIVE" if active else "✗ AEC REF DOWN — barge-in in R0026 safe mode"
        print(f"\n  {msg}")

    def _on_candidate(rid: int | None) -> None:
        print(f"\n  ⟂ INTERRUPT CANDIDATE (response_id={rid}) — hold for 300 ms…")

    def _on_candidate_rejected() -> None:
        print("  ⟂ candidate rejected (speech stopped before 300 ms)")

    def _on_confirmed(ctx) -> None:
        print(f"  ✂ INTERRUPT CONFIRMED — cancelling response_id="
              f"{ctx.invalidated_response_id} ({ctx.reason})")
        adapter_ref[0].interrupt_active_turn()

    stack = build_bargein_stack(
        enabled=bargein_on,
        sample_rate=LocalAudioConfig().sample_rate,
        channels=LocalAudioConfig().channels,
        on_confirmed=_on_confirmed,
        on_candidate=_on_candidate,
        on_candidate_rejected=_on_candidate_rejected,
        aec_status=_aec_status,
    )
    _gate = stack.gate
    bargein_ctl = stack.controller

    bridge = AssistantSpeechBridge(
        en_voice=EN_VOICE, pl_voice=PL_VOICE, gate=_gate,
        on_interruption=(stack.note_interruption if bargein_on else None),
    )
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
        on_tts_text=(stack.note_tts_sentence if bargein_on else None),
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
        bridge.on_user_transcript(text)  # -> gate.notify_response_dispatched()
        stack.note_response_dispatched()  # allocates response_id + starts tracker

    def on_assistant_token(tok: str) -> None:
        print(tok, end="", flush=True)
        bridge.on_assistant_token(tok)

    def on_assistant_complete(text: str) -> None:
        print()
        bridge.on_assistant_complete(text)

    def on_conversation_error(exc: Exception) -> None:
        print(f"\n[error] {exc}")
        bridge.on_conversation_error(exc)

    # M2.4B.5A: strict pre-M2.5 half-duplex — utterances captured / STT
    # results that arrive while NeXa is answering are DROPPED (not queued,
    # not replayed). Print them so the operator sees the protection working.
    def on_utterance_dropped(rec) -> None:
        print(f"  ⨯ {rec.reason}: dropped {rec.audio_ms}ms of audio captured while "
              f"NeXa is answering (session total {rec.dropped_count_this_session}, "
              f"stt_queue_depth {rec.stt_queue_depth})")

    def on_turn_dropped(rec) -> None:
        print(f"  ⨯ {rec.reason}: dropped STT result {rec.transcript[:40]!r} that "
              f"arrived mid-response (session total {rec.dropped_count_this_session}, "
              f"conv_queue_depth {rec.conversation_queue_depth})")

    def on_turn_interrupted(rec) -> None:
        print(f"  ✂ interrupted turn committed — outcome={rec.outcome}, "
              f"spoken_chars={rec.spoken_chars}, "
              f"llm_cancel_completed={rec.llm_cancel_completed} "
              f"(session total {rec.interrupted_count_this_session})")

    adapter = VoiceConversationAdapter(
        session,
        response_language_resolver=resolver,
        on_turn_language=on_turn_language,
        on_user_transcript=on_user_transcript,
        on_assistant_token=on_assistant_token,
        on_assistant_complete=on_assistant_complete,
        on_conversation_error=on_conversation_error,
        on_turn_dropped=on_turn_dropped,
        on_turn_interrupted=(on_turn_interrupted if bargein_on else None),
        response_mode=ResponseMode.VOICE,
        context_provider=context_provider,
        response_id_source=(stack.response_id_source if bargein_on else None),
        spoken_prefix_source=(stack.spoken_prefix_source if bargein_on else None),
        interruption_complete_hook=(
            stack.on_interruption_complete if bargein_on else None
        ),
    )
    adapter_ref = [adapter]
    if bargein_on:
        stack.bind_adapter(adapter)  # controller -> adapter capture callbacks
    adapter.start()

    def on_transcription(result) -> None:
        adapter_ref[0].handle_transcription(result)

    def on_transcription_error(exc: Exception) -> None:
        adapter_ref[0].handle_transcription_error(exc)

    # AEC feeder is inserted after the Piper TTS stage, before the observer.
    out_stages = stack.output_stages([bridge, planner, continuity], tts_service, tts_observer)

    runtime = VoiceRuntime(
        LocalAudioConfig(bargein_enabled=bargein_on),
        on_event=on_event,
        transcriber=bilingual,
        language=bootstrap,  # BilingualSpeechTranscriber treats this as bootstrap only
        on_transcription=on_transcription,
        on_transcription_error=on_transcription_error,
        on_utterance_dropped=on_utterance_dropped,
        extra_output_stages=out_stages,
        half_duplex_gate=_gate,
        bargein_controller=bargein_ctl,
    )

    if bargein_on:
        print("\nBARGE-IN: ON  — the mic stays hot during replies; feeding the "
              "XVF3800 AEC far-end reference (plug:respeaker).")
        print("           If 'AEC REF ACTIVE' does not appear below, barge-in "
              "stays in the R0026 safe mode for safety.")
    else:
        print("\nBARGE-IN: OFF (R0026 whole-response half-duplex). Use --bargein "
              "to enable.")
    print("\nSpeak naturally — mix Polish and English freely. Ctrl+C to stop.\n")
    try:
        await runtime.run()
    finally:
        await adapter.shutdown()
        await aiohttp_session.close()
        detector.close()
        context_runtime.connection.close()
        try:
            await piper_server.stop()
        except PiperHttpError:
            pass
    print("\nfinal voice state:", runtime.state_machine.state.value.upper())
    print(f"last input language (session state): {bilingual.last_input_language}")
    print(f"sticky response preference: {resolver.preference.sticky}")
    print(f"max concurrent STT: {runtime.max_observed_stt_concurrency}  "
          f"max concurrent turns: {adapter.max_observed_conversation_concurrency}")
    print(f"busy-drop (utterances at capture): {runtime.dropped_busy_utterances}  "
          f"busy-drop (STT results at adapter): {adapter.dropped_busy_turns}")
    print(f"final STT queue depth: {runtime.stt_queue_depth}  "
          f"final conversation queue depth: {adapter.conversation_queue_depth}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n(stopped)")
