"""M2.4B.3.5 — operator-blind voice model A/B launcher (RESEARCH TOOLING).

Runs the REAL NeXa realtime voice path (real microphone + whisper.cpp STT
+ ConversationSession + ResponseMode.VOICE + SpeechPlanner + continuity
controller + Piper `nice +10`) bound to whichever model the sealed
mapping assigns to ``--candidate {A,B}`` — and **never prints the model
name**. The operator sees only "Candidate A" / "Candidate B".

It is a thin fork of ``apps/nexa_voice_tts_probe.py`` (same library
components, same defaults) with three differences:
  1. blinding — model tag resolved from the sealed mapping, never shown;
  2. all per-turn metrics / resource samples go to files under
     ``blind_results/``, not the operator terminal;
  3. a pre-warm step (model load + persona+VOICE prefix priming + one
     discarded warm generation) so the operator test is the always-on
     NeXa experience, not a cold load.

The production default model (`nexa.config.DEFAULT_LOCAL_MODEL`) is
untouched; this builds a throwaway session only.

Usage:
    python docs/research/m2_4b_llm_bench/operator_blind_ab.py --selftest
    python docs/research/m2_4b_llm_bench/operator_blind_ab.py --candidate A --language pl
    python docs/research/m2_4b_llm_bench/operator_blind_ab.py --candidate A --language en
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[2]
SRC = REPO_ROOT / "src"
for _p in (str(SRC), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aiohttp  # noqa: E402
from blind_ab_common import (  # noqa: E402
    BLIND_KEEP_ALIVE,
    BLIND_NUM_THREAD,
    RESULTS_DIR,
    build_blind_session,
    production_default_model,
    resolve_model,
)

from nexa.conversation.response_mode import ResponseMode  # noqa: E402
from nexa.stt import (  # noqa: E402
    Language,
    SttBinaryNotFoundError,
    SttModelNotFoundError,
    WhisperCppTranscriber,
)
from nexa.tts import (  # noqa: E402
    EN_VOICE,
    PL_VOICE,
    PiperHttpConfig,
    PiperHttpError,
    PiperHttpServer,
    PiperServerStartError,
    PiperVenvNotFoundError,
    PiperVoiceNotFoundError,
)
from nexa.tts.errors import SentenceTokenizerDataMissingError  # noqa: E402
from nexa.voice import HalfDuplexGate, LocalAudioConfig, VoiceRuntime  # noqa: E402
from nexa.voice.state import VoiceEvent, VoiceState  # noqa: E402
from nexa.voice_conversation import VoiceConversationAdapter  # noqa: E402
from nexa.voice_tts import (  # noqa: E402
    DEFAULT_CONTINUITY_TARGET_S,
    DEFAULT_TTS_CONTEXT_TIMEOUT_S,
    ActivityFlags,
    AssistantSpeechBridge,
    MetricsCollector,
    NexaSpeechContinuityController,
    NexaSpeechPlanner,
    ResourceSampler,
    TimedPiperHttpTTSService,
    TtsStatusObserver,
    TurnReportJsonlWriter,
    ensure_sentence_tokenizer_data,
    render_turn_report,
)

WARMUP = {"pl": "Ile jest osiem razy siedem?", "en": "What is eight times seven?"}
PRIME_TEXT = {"pl": "Rozgrzewka: powiedz krótko dzień dobry.",
              "en": "Warm-up: say a short hello."}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--candidate", choices=["A", "B"],
                   help="which blind candidate to run (resolved from the sealed mapping)")
    p.add_argument("--language", choices=[lang.value for lang in Language],
                   default=Language.PL.value, help="explicit STT language (pl|en)")
    p.add_argument("--selftest", action="store_true",
                   help="build both candidate sessions offline, verify wiring, exit "
                   "(no audio, no operator, does not reveal the mapping)")
    p.add_argument("--piper-nice", type=int, default=10)
    p.add_argument("--tts-context-timeout-s", type=float,
                   default=DEFAULT_TTS_CONTEXT_TIMEOUT_S)
    p.add_argument("--continuity-target-s", type=float,
                   default=DEFAULT_CONTINUITY_TARGET_S)
    return p.parse_args()


# --------------------------------------------------------------------------- #
# --selftest — deterministic, offline, does NOT reveal the mapping
# --------------------------------------------------------------------------- #

def run_selftest() -> int:
    from nexa.conversation.session import ConversationSession
    from nexa.conversation.turn import Role
    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
        ok = ok and cond

    sessions = {}
    for cand in ("A", "B"):
        s = build_blind_session(cand)
        sessions[cand] = s
        check(isinstance(s, ConversationSession), f"Candidate {cand}: real ConversationSession")
        check(s.history == (), f"Candidate {cand}: fresh history")
        prov = s.provider
        check(getattr(prov, "_num_thread", None) == BLIND_NUM_THREAD,
              f"Candidate {cand}: num_thread={BLIND_NUM_THREAD} applied")
        check(getattr(prov, "_keep_alive", None) == BLIND_KEEP_ALIVE,
              f"Candidate {cand}: keep_alive={BLIND_KEEP_ALIVE} applied")
        model = prov.describe().model
        check(model in {"gemma4:e4b", "gemma4:e2b"}, f"Candidate {cand}: model is an allowed tag")

    from blind_ab_common import ALLOWED_MODELS
    a_model = sessions["A"].provider.describe().model
    b_model = sessions["B"].provider.describe().model
    check(a_model != b_model, "Candidate A and Candidate B are different models")
    check({a_model, b_model} == set(ALLOWED_MODELS),
          "the two candidates are exactly the two allowed models (identities not shown)")
    check(sessions["A"] is not sessions["B"], "A and B are separate session objects")
    check(sessions["A"].system_prompt == sessions["B"].system_prompt,
          "same persona system prompt for both")
    check(sessions["A"].options == sessions["B"].options,
          "same GenerationOptions (num_ctx/temperature/num_predict/…) for both")
    check(production_default_model() == "gemma4:e4b",
          "production default model unchanged (untouched by this harness)")

    # a VOICE-mode turn through the real context builder must inject the
    # voice directive and must NOT persist it in history
    ctx = sessions["A"].build_context()
    from nexa.conversation.response_mode import voice_response_directive
    wire = ctx.to_provider_messages(response_mode=ResponseMode.VOICE)
    check(any(m.content == voice_response_directive() for m in wire),
          "ResponseMode.VOICE injects the voice directive into the wire prompt")
    check(all(t.role in (Role.USER, Role.ASSISTANT) for t in sessions["A"].history),
          "history holds only real user/assistant turns")

    print(f"\nselftest: {'OK' if ok else 'FAILED'}  (mapping NOT revealed)")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# blind live run
# --------------------------------------------------------------------------- #

async def prewarm(candidate: str, language: str) -> None:
    """Load the model and prime the persona + ResponseMode.VOICE prompt
    prefix into the llama.cpp KV cache with one discarded generation, on a
    throwaway session — so the operator's turns run warm (R0021)."""
    warm_session = build_blind_session(candidate)
    async for _ in warm_session.send(PRIME_TEXT[language], response_mode=ResponseMode.VOICE):
        pass
    await asyncio.sleep(1.0)


async def live(args: argparse.Namespace) -> None:
    candidate = args.candidate
    language = Language(args.language)
    lang = language.value
    _ = resolve_model(candidate)  # validates the mapping; result deliberately unused/unprinted

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    stem = RESULTS_DIR / f"candidate_{candidate}_{lang}_{ts}"
    report_path = stem.with_suffix(".txt")
    jsonl_path = stem.with_suffix(".jsonl")
    report_f = report_path.open("w", encoding="utf-8")

    def _log(line: str) -> None:
        report_f.write(line + "\n")
        report_f.flush()

    print("=" * 60)
    print(f"  NeXa blind voice A/B  —  Candidate {candidate}  —  language: {lang}")
    print("=" * 60)
    print("  (model identity hidden; metrics -> file, not shown here)")

    try:
        ensure_sentence_tokenizer_data()
        transcriber = WhisperCppTranscriber()
    except (SentenceTokenizerDataMissingError, SttBinaryNotFoundError,
            SttModelNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("  starting Piper (nice +10) ...")
    try:
        piper = PiperHttpServer(PiperHttpConfig(nice=args.piper_nice))
        await piper.start()
        await piper.prewarm()
    except (PiperVenvNotFoundError, PiperVoiceNotFoundError, PiperServerStartError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("  warming model + priming persona/VOICE prefix ...")
    await prewarm(candidate, lang)

    # --- real conversation session (fresh history) --------------------------
    session = build_blind_session(candidate)
    _log(f"# blind candidate {candidate}  language {lang}  started {ts}")
    _log(f"# num_thread={BLIND_NUM_THREAD} keep_alive={BLIND_KEEP_ALIVE} "
         f"response_mode=VOICE  (model tag intentionally omitted)")

    gate = HalfDuplexGate()
    activity = ActivityFlags()
    sampler = ResourceSampler(period_s=0.4, activity=activity)
    jsonl = TurnReportJsonlWriter(str(jsonl_path))
    turn_no = [0]

    def on_turn_finalized(tm) -> None:
        turn_no[0] += 1
        n = turn_no[0]
        _log("\n" + "-" * 60)
        _log(f"TURN {n}" + ("   (WARM-UP — exclude from scoring)" if n == 1 else ""))
        _log(render_turn_report(tm))
        jsonl.write(tm)
        # operator sees only a neutral marker
        tag = "  · warm-up turn recorded (excluded)" if n == 1 else f"  · turn {n} recorded"
        print(tag, flush=True)

    metrics = MetricsCollector(
        on_turn_finalized=on_turn_finalized,
        resource_summary_fn=sampler.window_summary,
        activity=activity,
    )
    sampler.start()

    bridge = AssistantSpeechBridge(en_voice=EN_VOICE, pl_voice=PL_VOICE, gate=gate)
    planner = NexaSpeechPlanner(en_voice=EN_VOICE, pl_voice=PL_VOICE, default_language=lang)
    continuity = NexaSpeechContinuityController(
        target_reserve_s=args.continuity_target_s,
        on_release=lambda rel: metrics.controller_release(rel),
    )
    aio = aiohttp.ClientSession()
    tts = TimedPiperHttpTTSService(
        base_url=piper.config.synthesize_url, aiohttp_session=aio,
        stop_frame_timeout_s=args.tts_context_timeout_s,
        on_http_call=lambda call: metrics.http_synthesis(call),
    )

    last_eot = [None]
    pending: list[tuple[float, float, str, float | None]] = []

    def on_event(ev: VoiceEvent) -> None:
        print(f"  voice: {ev.to_state.value.upper()}", flush=True)
        if ev.to_state == VoiceState.END_OF_TURN:
            last_eot[0] = time.monotonic()
            activity.stt_active = True

    def on_transcription(result) -> None:
        activity.stt_active = False
        txt = result.text.strip()
        print(f'  you: "{result.text}"', flush=True)
        if txt and last_eot[0] is not None:
            pending.append((last_eot[0], time.monotonic(), txt, result.wall_latency_s))
        adapter.handle_transcription(result)

    def on_transcription_error(exc: Exception) -> None:
        activity.stt_active = False
        print(f"  [stt error] {exc}", flush=True)
        adapter.handle_transcription_error(exc)

    def on_user_transcript(text: str) -> None:
        eot, sttr, stt_text, stt_lat = pending.pop(0) if pending else (None, None, text, None)
        metrics.start_turn(end_of_turn=eot, stt_result=sttr,
                           stt_text=stt_text, stt_wall_latency_s=stt_lat)
        print("  NeXa: ", end="", flush=True)
        bridge.on_user_transcript(text)

    def on_assistant_token(tok: str) -> None:
        metrics.first_token()
        metrics.assistant_token(tok)
        print(tok, end="", flush=True)
        bridge.on_assistant_token(tok)

    def on_assistant_complete(full: str) -> None:
        metrics.assistant_complete(full)
        print(flush=True)
        bridge.on_assistant_complete(full)

    def on_conversation_error(exc: Exception) -> None:
        metrics.conversation_error(exc)
        print(f"\n  [conversation error] {exc}\n", flush=True)
        bridge.on_conversation_error(exc)

    observer = TtsStatusObserver(
        on_tts_started=metrics.tts_started,
        on_tts_first_audio=metrics.tts_first_audio,
        on_tts_stopped=metrics.tts_stopped,
        on_tts_error=lambda e: print(f"  [tts error] {e}", flush=True),
        on_tts_audio=lambda n, r, c: (continuity.note_tts_audio(n, r, c),
                                      metrics.tts_audio(n, r, c)),
        on_tts_text=metrics.tts_text,
        on_tts_response_end=metrics.tts_response_end,
        on_bot_started_speaking=metrics.bot_started_speaking,
        on_bot_stopped_speaking=metrics.bot_stopped_speaking,
    )

    adapter = VoiceConversationAdapter(
        session,
        on_user_transcript=on_user_transcript,
        on_assistant_token=on_assistant_token,
        on_assistant_complete=on_assistant_complete,
        on_conversation_error=on_conversation_error,
        response_mode=ResponseMode.VOICE,
    )
    adapter.start()

    runtime = VoiceRuntime(
        LocalAudioConfig(),
        on_event=on_event,
        transcriber=transcriber,
        language=language,
        on_transcription=on_transcription,
        on_transcription_error=on_transcription_error,
        extra_output_stages=[bridge, planner, continuity, tts, observer],
        half_duplex_gate=gate,
    )

    print("\n  Model is warm. Begin now.")
    print(f'  1) WARM-UP (excluded): say  "{WARMUP[lang]}"')
    print("  2) then speak the test turns naturally. Ctrl+C when done.\n")

    try:
        await runtime.run()
    finally:
        await adapter.shutdown()
        await aio.close()
        try:
            await piper.stop()
        except PiperHttpError:
            pass
        for tm in metrics.close():
            on_turn_finalized(tm)
        sampler.stop()
        jsonl.close()
        report_f.close()
        print(f"\n  Candidate {candidate} / {lang}: {turn_no[0]} turn(s) recorded")
        print(f"  metrics -> {report_path}")
        print("  (do NOT open the mapping file until after your A/B verdict)")


def main() -> int:
    args = parse_args()
    if args.selftest:
        return run_selftest()
    if not args.candidate:
        print("error: --candidate {A,B} is required (or use --selftest)", file=sys.stderr)
        return 2
    asyncio.run(live(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
