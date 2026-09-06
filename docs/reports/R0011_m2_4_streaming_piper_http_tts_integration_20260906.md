# R0011 — M2.4: Streaming Local TTS (Piper HTTP + Pipecat) integration

- **Date:** 2026-09-06
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4 — implementation**
- **Related:** `docs/decisions/ADR-0003_realtime_voice_foundation.md`
  (D6 — TTS baseline, D7 — thread budgets), `docs/reports/R0010_m2_4a_pipecat_streaming_tts_spike_20260906.md`
  (the compatibility spike; this implements its Recommendation A),
  `docs/architecture/M2_4_STREAMING_TTS_ARCHITECTURE.md` (the verified
  architecture this report backs), `R0009`/`R0008`/`R0007` (the M2.3/M2.2/M2.1
  substages this builds on, unchanged).

---

## TASK RESULT

**PASS — operator-accepted on real hardware (2026-09-06).**

M2.4 delivers a streamed assistant reply → sentence-chunked local Piper TTS
→ dedicated USB speaker DAC, in the prior local NeXa assistant's own PL/EN
voices, with the microphone live on the same Pi. Built as Recommendation A
of the R0010 spike: Pipecat 1.8.1 `PiperHttpTTSService` + built-in SENTENCE
aggregation + `LocalAudioOutputTransport`, fed by a thin NeXa bridge; an
external Piper HTTP process for GPL isolation; response-language → voice
mapping reusing the canonical M2.3 language function.

Two real-hardware findings were caught and fixed before acceptance:

1. **Self-conversation loop.** With input and output both live, the
   reSpeaker heard NeXa's own TTS and whisper.cpp re-transcribed fragments
   of her answer ("Saturny nie jest czarną dziurą.", "Masz rację.", …) as
   new user turns. Fixed with a temporary **half-duplex self-echo safety
   gate** (`HalfDuplexGate` + `_MicGateFrameProcessor`) — mic audio is
   withheld before VAD/STT while NeXa's TTS is physically playing, resuming
   the instant she finishes. Not barge-in (M2.5); a pause.
2. **Output routed to the wrong device after a USB replug.** M2.1's config
   defaulted output to the reSpeaker's own alias (documented at the time as
   a deliberate choice because the USB DAC was then unplugged). With the
   dedicated DAC reconnected, output must be an **independent** selection.
   Fixed: `LocalAudioConfig.output_device_name` default → `"usb_speaker"`
   (the DAC's stable ALSA alias); input stays `"respeaker"`; two separate
   name-resolved `find_device_index()` calls, no shared index, no silent
   fallback.

Also fixed: a genuine **instrumentation** bug in the probe's turn-timing
(a later turn overwriting an earlier turn's timestamps before its TTS
finished playing) — `TurnTimingTracker` gives each turn its own
FIFO-correlated record; `first_tts_audio_at` / `first_sentence_ready_at` are
written once per response.

## M2.4 FINAL STATUS

**COMPLETE — `OPERATOR-CONFIRMED` (2026-09-06).** Functional baseline frozen
by this commit. Natural-speech-flow quality work is deferred to a named
follow-up substage `M2.4B` (see "KNOWN FOLLOW-UP"). No barge-in — M2.5.

## OPERATOR ACCEPTANCE

Andrzej ran `./.venv/bin/python3 -u apps/nexa_voice_tts_probe.py --language pl`
for an extended real conversation and explicitly confirmed:

- full local voice conversation works
- reSpeaker input works
- UACDemoV1.0 output works
- old NeXa Piper voice is audible
- NeXa no longer talks to herself
- half-duplex gate works acceptably
- multi-turn conversation works
- `ConversationSession` preserves conversational context
- responses are coherent and useful
- overall system behaviour is good

Preceding direct-hardware steps, each separately operator-confirmed the same
day:

| Step | Confirmed |
|---|---|
| Audio devices re-detected after reboot + USB replug; `UACDemoV10` present (card 3, `hw:CARD=UACDemoV10`, PipeWire sink, system default) | yes |
| Direct 440 Hz test tone via `pw-play` to the UACDemoV1.0 sink | "heard it clearly" |
| Direct `pl_PL-gosia-medium` "Cześć, jestem NeXa." (config defaults, no overrides) → UACDemoV1.0 | "heard + sounds like old NeXa" |
| Direct `en_GB-jenny_dioco-medium` "Hello, I am NeXa." → UACDemoV1.0 | "heard + sounds like old NeXa" |
| Pipecat-only path (no mic, no STT, no Ollama): `AssistantSpeechBridge` → `PiperHttpTTSService` → `TtsStatusObserver` → `LocalAudioOutputTransport` → UACDemoV1.0 | "heard it clearly from UACDemoV10" |
| Full voice conversation (this acceptance) | confirmed — see list above |

This is the human-acceptance tier above the automated suites, matching the
M1.1 / M2.1 / M2.2 / M2.3 pattern.

## FINAL ARCHITECTURE

Full detail: `docs/architecture/M2_4_STREAMING_TTS_ARCHITECTURE.md`. Summary:

```
mic → Pipecat → Silero VAD → UtteranceBuffer → whisper.cpp        (M2.1/M2.2, unchanged)
    → VoiceConversationAdapter → SerialConversationQueue           (M2.3, unchanged)
    → ConversationSession.send(text)   ← identical to apps/nexa_chat.py   (M1.1, unchanged)
        on_user_transcript / on_assistant_token(…) / on_assistant_complete / on_conversation_error
    → AssistantSpeechBridge         (NEW) → LLMFullResponseStart / LLMTextFrame… / LLMFullResponseEnd
                                            + one TTSUpdateSettingsFrame on a voice change
    → PiperHttpTTSService           (Pipecat 1.8.1) — built-in SENTENCE aggregation,
                                     one HTTP POST/sentence → 127.0.0.1:5001/synthesize
    → TtsStatusObserver             (NEW) — observes TTSStarted/AudioRaw/Stopped/Error, forwards
    → LocalAudioOutputTransport     (Pipecat) — auto-resamples 22050 Hz Piper audio → 16 kHz
    → UACDemoV1.0 USB speaker DAC   (output device, resolved by name)

    ── half-duplex gate ──
    transport.input() → _MicGateFrameProcessor (NEW) → VADProcessor
      while BotStartedSpeaking..BotStoppedSpeaking (real playback frames): InputAudioRawFrame dropped here
```

**Pipecat 1.8.1 media/TTS orchestration reused as-is (no Pipecat patch):**
`PiperHttpTTSService` (configured with the full `/synthesize` URL —
R0010 found `POST /` → 405; this is configuration, not a bug); built-in
`SimpleTextAggregator` SENTENCE mode (NLTK `punkt_tab`), final-fragment flush
on `LLMFullResponseEndFrame`; `LocalAudioOutputTransport` with automatic
resampling; `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` (broadcast
upstream) as the gate's playback source of truth; `Pipeline`/`PipelineWorker`/
`WorkerRunner` (same primitive M2.1 uses). No `LLMTextProcessor` (redundant),
no in-process Piper import.

**NeXa-owned, new:** `src/nexa/tts/` (external-process boundary — `config.py`,
`server.py`, `errors.py`; no Pipecat, no `piper` import) and
`src/nexa/voice_tts/` (integration — `bridge.py` with `AssistantSpeechBridge`
+ `TtsStatusObserver` + `voice_for_language`, `preflight.py`, `timing.py`).
`src/nexa/voice/` extended: `gate.py` (new), `config.py` (output default),
`runtime.py` (`_MicGateFrameProcessor`, `half_duplex_gate`/`extra_output_stages`
params). `ConversationSession` / context / persona / model — untouched.

**External local Piper HTTP process:** `python -m piper.http_server -m
en_GB-jenny_dioco-medium --data-dir <voices> --host 127.0.0.1 --port 5001`,
started/prewarmed/stopped by `PiperHttpServer`. One process serves both
languages (lazy per-voice load, cached); `prewarm()` pays the ~2 s
cross-voice load once at startup. Launched with **no** `--length-scale` /
`--noise-scale` / `--noise-w-scale` / `--sentence-silence` flags → uses the
voice `.onnx.json` embedded defaults, matching the prior assistant exactly.

**localhost 127.0.0.1 boundary:** NeXa's process holds only an
`aiohttp.ClientSession` to `http://127.0.0.1:5001`. No non-loopback traffic.
GPL `piper-tts` runs only in its own venv (`~/.local/share/nexa/tts/piper-http-venv/`),
its own OS process. `nexa.tts` / `nexa.voice_tts` never `import piper`
(`ast`-guarded). `pyproject.toml` unchanged.

## HARDWARE PATH

`VERIFIED FACT` (2026-09-06, this Pi, after reboot + USB replug):

| Role | Device | ALSA card / stable alias | PyAudio (this boot) | NeXa config |
|---|---|---|---|---|
| **INPUT** | reSpeaker XVF3800 4-Mic Array | card `Array` → `hw:CARD=Array,DEV=0`; `/etc/asound.conf` `plug:` alias `respeaker` | idx 3 `respeaker` | `input_device_name = "respeaker"`, `require_input=True` |
| **OUTPUT** | Jieli `UACDemoV1.0` USB speaker DAC | card `UACDemoV10` → `hw:CARD=UACDemoV10,DEV=0`; `/etc/asound.conf` `plug:` alias `usb_speaker`; system default sink | idx 2 `usb_speaker` | `output_device_name = "usb_speaker"`, `require_output=True` |

- **Independent selection** — two separate `find_device_index()` calls;
  never a shared index. Resolving the DAC does not disturb the mic
  resolution.
- **By name only** — PortAudio enumeration order is not stable across
  reboots/USB reconnects (this fix was *caused* by a replug re-enumeration).
  A name matching nothing raises `AudioDeviceNotFoundError` — **no silent
  fallback** to another output-capable device.
- Stable ALSA aliases from the pre-existing `/etc/asound.conf`
  (`usb_speaker` → `hw:CARD=UACDemoV10`, `respeaker` → `hw:CARD=Array`,
  `ctl.!default` → card `UACDemoV10`). Both are `plug` devices: NeXa's fixed
  16 kHz / mono is converted in software to each device's native format
  (reSpeaker 16 kHz stereo; UACDemoV1.0 48 kHz stereo).
- Pipecat's `LocalAudioOutputTransport` resamples Piper's 22050 Hz "medium"
  audio to the transport's 16 kHz automatically; ALSA `plug` then converts
  16 kHz → 48 kHz for the DAC.

## VOICE CONFIGURATION

Not changed by this task. The exact prior local NeXa assistant voices:

| Lang | Voice id | Model sha256 | Config sha256 |
|---|---|---|---|
| PL | `pl_PL-gosia-medium` | `38f66464…3fbc` (63,201,294 B) | `1aefb31a…510d` |
| EN | `en_GB-jenny_dioco-medium` | `469c630d…6b01` (63,201,294 B) | `a9a7a93a…4dd4` |

- **Byte-identical** to the legacy repo's `voices/piper/` files (model and
  config, both languages — sha256 verified).
- Inference params come entirely from the `.onnx.json` (never overridden,
  matching the prior assistant): `sample_rate = 22050`, `length_scale = 1`,
  `noise_scale = 0.667`, `noise_w = 0.8`, `sentence_silence = 0.2 s` (Piper
  CLI default, not set), single-speaker (no speaker id), mono / 16-bit.
- No gain / volume / normalization anywhere — level is the system mixer
  only.
- Stored outside git at `~/.local/share/nexa/tts/voices/` (`.gitignore`
  `voices/`), provisioned by `scripts/setup_piper_http.py` — copied
  byte-for-byte from the legacy read-only reference if present, else
  downloaded fresh via the external venv's own `piper`.
- **Voice-model preference (softer/cozier) is out of scope** — a separate
  research task after the pipeline is stable, per operator instruction.

## HALF-DUPLEX SAFETY

Temporary M2.4 behaviour; M2.5 replaces it with true full-duplex barge-in.

- **What:** while NeXa's own TTS audio is physically playing, microphone
  audio is withheld **before** VAD / utterance capture / `SerialTranscriptionQueue`
  / any `ConversationSession` turn. After playback ends, capture resumes on
  the next frame. Not barge-in — nothing is cancelled; the user cannot
  interrupt NeXa mid-reply (accepted for M2.4).
- **Where:** `_MicGateFrameProcessor` (`nexa/voice/runtime.py`), inserted
  immediately after `transport.input()`, before `VADProcessor`. Drops
  `InputAudioRawFrame` while `HalfDuplexGate.mic_suppressed`; forwards
  everything else unchanged.
- **Source of truth:** real playback frames only —
  `BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame` from the output
  transport (broadcast upstream), plus `AssistantSpeechBridge`'s
  `notify_response_dispatched()` / `notify_response_finished()`. Never
  assistant-text generation, `ConversationSession`, timers, or a fake
  state. **No `SPEAKING` value added to the acoustic `VoiceState` enum**
  (`ast`-guarded).
- **Multi-sentence safety:** a response-level latch keeps the mic closed
  across inter-sentence `BotStopped`/`BotStarted` cycles until **both**
  generation is complete **and** audio has stopped — the mic does not
  re-open between two sentences of one answer. The think window (dispatch →
  first audio) leaves the mic open so a genuine follow-up still queues.
  Hard stops (`EndFrame`/`CancelFrame`/`ErrorFrame`) always release the gate.
- **Reused from Pipecat:** the `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame`
  events and semantics. **Not reused:** Pipecat's `turns/user_mute/`
  strategies (`AlwaysUserMuteStrategy` etc.) — they are consumed only by
  `processors/aggregators/llm_response_universal.py`, the universal LLM
  context aggregator, adopting which would introduce a second conversation
  authority (ADR-0003 D2). `STTMuteFrame` has no effect in NeXa's pipeline
  (NeXa uses `WhisperCppTranscriber`, not Pipecat's `STTService`).
- **No M2.5 logic:** no `StartInterruptionFrame` / `BotInterruptionFrame` /
  `EmulateUser*` / `handle_interruptions` / cancellation — `ast`-guarded
  (`tests/test_voice_half_duplex_gate.py::TestNoBargeInLogicIntroduced`).

## TEST RESULTS

Actual counts, not invented.

| Suite | Result |
|---|---|
| `pytest tests/` | **246 passed, 7 skipped, 14 subtests passed, 1 warning** (253 collected) |
| `python -m unittest discover -s tests` | **Ran 253 tests — OK (skipped=7)** |
| `ruff check src/ tests/ apps/ scripts/setup_piper_http.py` | **All checks passed** |
| `git diff --check` | clean |

Baseline before M2.4 (HEAD `b73afb8`): 155 passed, 4 skipped, 14 subtests.
**M2.4 adds 91 passing tests and 3 opt-in skipped (live Piper), across 11
new test files.** No tracked test file was modified.

The 7 skips are all opt-in, environment-gated (unchanged policy): 1 live
Ollama, 2 live whisper.cpp, 3 live Piper HTTP (`NEXA_RUN_LIVE_TTS_TEST=1`),
1 reSpeaker hardware probe.

New test files:

| File | n | Tier | Covers |
|---|---|---|---|
| `test_tts_config.py` | 9 | unit | `/synthesize` URL vs bare base_url, pinned version, PL/EN voice ids match the prior assistant, frozen/typed config |
| `test_tts_server.py` | 5 | unit | `PiperHttpServer` construction validation (missing venv / voice → explicit typed error), stop-without-start, synth error handling |
| `test_tts_server_live.py` | 3 | integration (opt-in) | real external Piper HTTP process — `NEXA_RUN_LIVE_TTS_TEST=1` |
| `test_voice_tts_bridge.py` | 18 | unit | `AssistantSpeechBridge` frame vocabulary + FIFO ordering, PL/EN/`None` → voice, `TTSUpdateSettingsFrame` only on change, `TtsStatusObserver` real-status mapping, unsupported language raises |
| `test_voice_tts_preflight.py` | 2 | unit | `ensure_sentence_tokenizer_data` — passes if present, raises (never downloads) if absent |
| `test_voice_tts_timing.py` | 14 (+subtests) | unit | `TurnTimingTracker` FIFO per-turn correlation; `first_tts_audio_at`/`first_sentence_ready_at` set once; `streaming_overlap_confirmed` = `first_tts_audio_at < assistant_complete_at`; cross-turn regression |
| `test_voice_tts_architecture.py` | 10 | unit (`ast`) | `nexa.tts` imports no Pipecat/`piper`/`nexa.conversation`; `nexa.voice_tts` builds no `ConversationSession`/provider/persona, imports no bootstrap/config/LLM client, defines no language classifier (must *call* the canonical one); `nexa.voice`/`nexa.stt`/`nexa.voice_conversation` still TTS/conversation-free |
| `test_voice_tts_probe_state_display.py` | 2 | unit (`ast`) | probe TTS/conversation callbacks never print a `voice state:` line or touch `VoiceStateMachine` |
| `test_voice_output_routing.py` | 7 | unit | input/output resolve to different indices; resolving the DAC doesn't disturb the mic; missing output raises, never falls back to another speaker |
| `test_voice_half_duplex_gate.py` | 17 | unit | `HalfDuplexGate` — closed only while NeXa speaks; multi-sentence latch never reopens between chunks; never latched shut on stop; no fake `VoiceState`; no barge-in machinery (`ast`) |
| `test_voice_mic_gate_processor.py` | 7 | unit | `_MicGateFrameProcessor` withholds `InputAudioRawFrame` while suppressed / forwards otherwise; no STT job possible from echo; multi-sentence never reopens; `_build_pipeline` inserts the gate stage only with a gate, M2.1 pipeline otherwise unchanged |

## KNOWN FOLLOW-UP — M2.4B

**`M2.4B — Natural Speech Flow / Streaming Pacing`** (suggested next
substage; **not started**). Do not destabilise the now-frozen M2.4 baseline
for these.

- Spoken replies are not yet sufficiently continuous/natural: noticeable
  pauses between TTS chunks; the output transport can stop/start between
  chunks.
- Sentence/chunk boundaries sometimes split a semantic phrase badly, e.g.
  `"Najważniejszą cechą jest tzw."` … later … `"horyzont zdarzeń…"`.
  Related: Pipecat's NLTK sentence splitter always runs `language="english"`
  (R0010), so Polish abbreviations (`ul.`, `tzw.`) can split early.
- Desired direction: buffered / look-ahead generation; pacing that adapts
  mildly to how much future generated/audio content is buffered; NeXa
  maintaining a continuous coherent spoken response rather than independent
  sentence clips.
- One real run showed a non-fatal `"TTS context … completed with no audio"`
  before audio then appeared normally — worth investigating alongside the
  chunk-timing work. Non-fatal, no audible effect observed.
- Shutdown-only ALSA assertion (`snd_pcm_plugin_status: Assertion … failed`)
  on abrupt Ctrl+C after audio has finished, on the `plug`→`dmix` chain.
  Cosmetic; graceful `EndFrame` stop is clean; fixing needs
  `VoiceRuntime.run()` shutdown changes.
- **Softer / cozier voice** — the operator would eventually prefer one; this
  is a **separate voice-selection research task after the pipeline is
  stable**, not part of M2.4 or M2.4B.
- **Barge-in / interruption / own-TTS acoustic suppression / echo handling**
  — **M2.5**. The half-duplex gate is the explicit temporary stand-in.

## WHAT I DID

- Inspected installed Pipecat 1.8.1 source for a built-in half-duplex /
  input-mute mechanism; documented the options (`AlwaysUserMuteStrategy`
  etc., `STTMuteFrame`, `BotStartedSpeakingFrame`) and why only the
  `BotStarted/StoppedSpeaking` events are safely reusable without adopting
  Pipecat's LLM-aggregator/STT-service stack.
- Re-detected all audio devices after the reboot/replug; confirmed
  `UACDemoV10` present; ran a direct tone and direct old-PL/EN Piper
  playback to the DAC (operator-confirmed each).
- Verified the legacy Piper voice files + config are byte-identical to what
  M2.4 uses; verified the external Piper venv + voices are provisioned.
- Implemented `HalfDuplexGate` + `_MicGateFrameProcessor`; wired
  `half_duplex_gate` through `VoiceRuntime` and the bridge; kept the M2.1
  pipeline byte-for-byte unchanged when no gate is passed.
- Changed `LocalAudioConfig.output_device_name` default to `"usb_speaker"`;
  updated its docstring to the post-replug verified state.
- Fixed the probe turn-timing instrumentation (`tts_started`/`tts_first_audio`
  set-once; explicit `streaming_overlap_confirmed`).
- Ran a Pipecat-only playback path (no mic/STT/LLM) to the DAC; smoke-ran
  the full probe; handed the retest command to the operator.
- Reconciled all M2.4 documentation with the implementation and hardware
  behaviour (this report, the architecture doc, `CURRENT_STATE.md`,
  `TEST_STRATEGY.md`, the `FOUNDATION_ARCHITECTURE.md` Voice pointer).

## WHAT I VERIFIED

- Full pytest + unittest suites: 246 pass / 7 opt-in skip / 253 total.
- `ruff` clean in M2.4 scope (`src/ tests/ apps/ scripts/setup_piper_http.py`);
  pre-existing unrelated `ruff` findings in `scripts/m1_bench/` are not
  touched by this task and not in the commit set.
- `git diff --check` clean; no binary/model files staged; no legacy
  *runtime* dependency (`nexa.*` imports none; the one legacy-path
  reference is an optional, read-only, developer-only convenience in
  `scripts/setup_piper_http.py` with a download fallback).
- No M2.5 implementation present — only prose noting deferral;
  `ast`-guarded.
- Operator acceptance recorded above.

## TESTS

See "TEST RESULTS". All deterministic; external boundaries faked
(PyAudio device list, subprocess, HTTP), real Pipecat frame types used
throughout. Live Piper HTTP is a 3-test opt-in suite
(`NEXA_RUN_LIVE_TTS_TEST=1`), matching the M2.2 live-whisper.cpp pattern.

## UNRESOLVED

- Natural speech flow (`M2.4B`) — folded into the follow-up substage above.
- The non-fatal `"TTS context … completed with no audio"` event was
  observed once but not root-caused; deferred to `M2.4B`.
- Shutdown-only ALSA assertion on abrupt Ctrl+C — cosmetic, deferred.
- Head-to-head SENTENCE-vs-token latency was not benchmarked (R0010 left it
  `UNKNOWN`); SENTENCE remains the product choice and `M2.4B` is where
  pacing gets measured.

## DOCUMENTATION / REPORTS UPDATED

- `docs/architecture/M2_4_STREAMING_TTS_ARCHITECTURE.md` — **new**.
- `docs/reports/R0011_m2_4_streaming_piper_http_tts_integration_20260906.md`
  — **new** (this file).
- `docs/CURRENT_STATE.md` — M2.4 marked COMPLETE/`OPERATOR-CONFIRMED`;
  test counts corrected (was a stale "159"); architecture-state, focus, and
  next-task sections updated; `M2.4B` recorded as the next substage.
- `docs/testing/TEST_STRATEGY.md` — new **M2.4 status** section.
- `docs/architecture/FOUNDATION_ARCHITECTURE.md` — Voice-boundary pointer
  updated: M2.4 (streaming local TTS) is now real; barge-in remains
  conceptual (M2.5).

## LEGACY NEXA USED

**YES — read-only.** (1) Verified the M2.4 Piper voice `.onnx`/`.onnx.json`
files are byte-identical (sha256) to `smart-desk-ai-assistant/voices/piper/`,
and confirmed the prior assistant never overrode Piper inference params
(config-json defaults only) — so M2.4 reproduces the same voice exactly.
(2) `scripts/setup_piper_http.py` *optionally* copies those two files from
the legacy read-only path if present (byte-for-byte, avoids a re-download),
falling back to a fresh download otherwise. NeXa's runtime code
(`nexa.*`) reads the legacy path **never** — only this one developer-run
setup step does. No legacy code imported, no legacy dependency in the
runtime.

## EXTERNAL RESEARCH USED

**NO** new external research this substage. The architecture was already
decided by ADR-0003 D6 and the R0010 compatibility spike (which did the
Pipecat-1.8.1 source study and the license/process-boundary analysis).

## CURRENT VERIFIED STATE

M1 complete (operator-confirmed through M1.1). M2 — Realtime Voice:
**M2.1, M2.2, M2.3, M2.4 all COMPLETE and `OPERATOR-CONFIRMED` on real
hardware.** Full local voice loop works: mic → VAD → whisper.cpp →
`ConversationSession` (`gemma4:e4b`) → streamed reply → Piper TTS → USB
speaker DAC, with a temporary half-duplex self-echo gate. No barge-in.
246 automated tests pass (7 opt-in skips). `ruff` clean (M2.4 scope).

## NEXT RECOMMENDED ACTION

**`M2.4B — Natural Speech Flow / Streaming Pacing`** — buffered/look-ahead
generation, pacing adaptive to buffered content, better sentence/phrase
boundaries (incl. Polish), investigate the non-fatal no-audio-context
event. Then **M2.5 — barge-in / interruption**, replacing the temporary
half-duplex gate. Voice-model selection (softer/cozier voice) is a separate
task, any time after the pipeline is stable. `gemma4:e4b` stays frozen
unless a future ADR changes it.

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

The substage followed the existing evidence/labeling discipline and the
"legacy is a knowledge base, never a runtime dependency" rule (the one
legacy-path reference is a documented, optional, developer-only convenience
with a fallback). No gap in `AGENTS.md` was exposed.
