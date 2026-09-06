# M2.4 — Streaming Local TTS: Piper HTTP + Pipecat (verified architecture)

Status: **`VERIFIED FACT`** — implemented, tested, and operator-confirmed on
real hardware 2026-09-06. Per ADR-0003 D6 (TTS baseline) and D7 (thread
budgets), using Recommendation A of the M2.4A spike (`R0010`). Unlike
`docs/architecture/FOUNDATION_ARCHITECTURE.md`'s Voice boundary (still
conceptual for barge-in), this document describes real code.

Related: `docs/decisions/ADR-0003_realtime_voice_foundation.md` (D6/D7 —
the decision this implements), `docs/reports/R0010_m2_4a_pipecat_streaming_tts_spike_20260906.md`
(the compatibility spike whose Recommendation A this follows),
`docs/reports/R0011_m2_4_streaming_piper_http_tts_integration_20260906.md`
(the implementation report + real-hardware acceptance evidence),
`docs/architecture/M2_3_VOICE_CONVERSATION_ADAPTER_ARCHITECTURE.md` (the
streamed assistant text this substage consumes, unchanged),
`docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` (the audio
transport + device model this extends).

---

## 1. Scope (ADR-0003 M2.4)

Streamed assistant text (M2.3's `on_assistant_token`/`on_assistant_complete`)
→ sentence-chunked local TTS → **the dedicated USB speaker DAC**, spoken in
the same PL/EN voices the prior local NeXa assistant used, while the
microphone is running on the same machine.

**In scope:** Pipecat 1.8.1 media orchestration for TTS; an external local
Piper HTTP server process (GPL isolation); a thin NeXa bridge translating
M2.3's callbacks into Pipecat's LLM-response frame vocabulary; response-
language → Piper-voice mapping reusing the canonical M2.3 language function;
real TTS-playback status observation; independent input/output audio device
selection; and a **temporary half-duplex self-echo safety gate**.

**Explicitly NOT in scope (M2.5):** barge-in / interruption — no cancellation
of TTS, LLM generation, or `ConversationSession` when the user speaks; true
full-duplex; own-voice acoustic suppression / AEC. **Also not in scope:**
voice-model research (a softer/cozier voice is a later, separate task —
`M2.4B`/voice-selection), and natural-speech-flow / streaming-pacing
improvements (`M2.4B`, recorded in §10).

## 2. The one path

```
microphone -> Pipecat -> Silero VAD -> UtteranceBuffer -> whisper.cpp        (M2.1/M2.2, unchanged)
    |
    v
TranscriptionResult -> VoiceConversationAdapter -> SerialConversationQueue    (M2.3, unchanged)
    |
    v
ConversationSession.send(text)   <-- the EXACT SAME method apps/nexa_chat.py calls  (M1.1, unchanged)
    |
    |  on_user_transcript / on_assistant_token(...) / on_assistant_complete / on_conversation_error
    v
AssistantSpeechBridge          (new, M2.4 — nexa.voice_tts)
    |    translates the callbacks into Pipecat's frame vocabulary, in strict FIFO:
    |    LLMFullResponseStartFrame / LLMTextFrame(token) ... / LLMFullResponseEndFrame
    |    (+ a one-off TTSUpdateSettingsFrame when the response language's voice changes)
    v
PiperHttpTTSService            (Pipecat 1.8.1, built-in)
    |    built-in SENTENCE aggregation (NLTK punkt_tab), one HTTP POST per sentence
    |    to  http://127.0.0.1:5001/synthesize   (external Piper process, its own venv)
    v
TtsStatusObserver             (new, M2.4 — nexa.voice_tts; observes only, forwards unchanged)
    |    TTSStartedFrame / TTSAudioRawFrame / TTSStoppedFrame / ErrorFrame  -> real status callbacks
    v
LocalAudioOutputTransport     (Pipecat, built-in — auto-resamples 22050 Hz Piper audio to 16 kHz)
    |
    v
UACDemoV1.0 USB speaker DAC   (output device, resolved by name — see §6)
```

`ConversationSession`, `ConversationContext`, the persona, and the model
choice (`gemma4:e4b`, frozen) are **not touched**. The bridge owns no
history, context, persona, model, or provider — `ast`-verified
(`tests/test_voice_tts_architecture.py`).

## 3. Package layout

### `src/nexa/tts/` — the external-Piper-process boundary (no Pipecat, no `piper` import)

| File | Responsibility |
|---|---|
| `config.py` | Canonical source of truth: `PIPER_TTS_VERSION = "1.8.0"` (external venv pin), `EN_VOICE = "en_GB-jenny_dioco-medium"`, `PL_VOICE = "pl_PL-gosia-medium"` (the exact prior-assistant voices), `PiperHttpConfig` (host `127.0.0.1`, port `5001`, `/synthesize` URL, timeouts), and the external-data-dir path helpers (`~/.local/share/nexa/tts/…`, XDG-overridable) — the same pattern `nexa.stt.config` established for whisper.cpp. |
| `server.py` | `PiperHttpServer` — owns the external `python -m piper.http_server` subprocess lifecycle: start + readiness-poll (`/info`), `prewarm()` (one throwaway synth per voice, so the first real reply never pays the ~2 s cross-voice load), one-off `synthesize()` for health checks, clean `stop()`. Validates the venv + default voice model exist at construction (typed `PiperVenv/VoiceNotFoundError`). No Pipecat dependency. |
| `errors.py` | `TtsError` hierarchy — every failure explicit, no silent fallback, no fabricated audio-success. |

### `src/nexa/voice_tts/` — the cross-boundary integration layer (Pipecat + `nexa.tts` + `nexa.conversation.language`)

| File | Responsibility |
|---|---|
| `bridge.py` | `AssistantSpeechBridge` (`FrameProcessor`) — pushes M2.3's streamed callbacks into the pipeline as `LLMFullResponseStartFrame`/`LLMTextFrame`/`LLMFullResponseEndFrame` in strict FIFO (internal queue + single worker, the same pattern `SerialTranscriptionQueue`/`SerialConversationQueue` use). `voice_for_language()` — pure PL/EN → voice-id map. `TtsStatusObserver` (`FrameProcessor`) — watches the TTS service's own `TTSStartedFrame`/`TTSAudioRawFrame`/`TTSStoppedFrame`/`ErrorFrame` (which flow *downstream* from the service, so it sits *after* it) and reports real status via callbacks — never a fabricated one. |
| `preflight.py` | `ensure_sentence_tokenizer_data()` — raises `SentenceTokenizerDataMissingError` if NLTK's `punkt_tab` is absent; **never downloads it** (that is `scripts/setup_piper_http.py`'s job only). Called before starting a SENTENCE-aggregation pipeline so a missing offline asset fails fast instead of Pipecat silently reaching the network (R0010 risk). |
| `timing.py` | `TurnTiming` / `TurnTimingTracker` — FIFO-correlated per-turn latency record across the fully async STT → session → TTS pipeline. Fixes a real instrumentation bug found on hardware: a later turn's conversation processing can start while an earlier turn's TTS audio is still *playing* (`SerialConversationQueue` serializes generation, not playback), so a single shared "current turn" dict was overwritten before the earlier turn reported. `first_sentence_ready_at` and `first_tts_audio_at` are each written **once per response** (a later sentence must not overwrite them); `streaming_overlap_confirmed` is the exact PASS condition `first_tts_audio_at < assistant_complete_at`. |

### `src/nexa/voice/` — extensions to the M2.1 runtime

| File | Change |
|---|---|
| `config.py` | `LocalAudioConfig.output_device_name` default `"respeaker"` → **`"usb_speaker"`** (the dedicated USB DAC's stable ALSA alias). Input stays `"respeaker"`. See §6. |
| `gate.py` | **new** — `HalfDuplexGate`, the temporary self-echo safety gate. See §7. |
| `runtime.py` | `VoiceRuntime(half_duplex_gate=…, extra_output_stages=[…])` — new optional params. `_MicGateFrameProcessor` (new) inserted right after `transport.input()` when a gate is given. `extra_output_stages` inserts the already-built `[bridge, tts_service, tts_observer]` just before `transport.output()` — `nexa.voice` only ever sees the generic Pipecat `FrameProcessor` type, never a TTS import (`ast`-verified). |

`nexa.voice` / `nexa.stt` / `nexa.voice_conversation` still import **no** TTS
engine and construct **no** `ConversationSession` — regression-guarded by
`tests/test_voice_tts_architecture.py::TestNoSecondLlmOrTtsAuthorityAcrossVoicePackages`.

## 4. Pipecat 1.8.1 media/TTS orchestration — what is reused

Verified from the installed package (`R0010`), not online docs. Reused
**as-is, no Pipecat patch**:

- **`PiperHttpTTSService`** — the concrete TTS service. Configured with the
  full `/synthesize` URL (R0010: `POST /` to the real Piper server returns
  405; `base_url` is a plain caller string, so this is configuration, not a
  bug). One HTTP POST per aggregated sentence; the server returns the whole
  WAV per sentence (not sub-sentence streaming).
- **Built-in SENTENCE text aggregation** (`SimpleTextAggregator`, NLTK
  `punkt_tab`) — `TTSService.process_frame` consumes `LLMTextFrame` /
  `LLMFullResponseStartFrame` / `LLMFullResponseEndFrame` directly and
  splits into sentences itself; the trailing unterminated fragment is
  flushed on `LLMFullResponseEndFrame`. No `LLMTextProcessor` (redundant).
- **`LocalAudioOutputTransport`** — playback + automatic resampling
  (`create_stream_resampler()`): Piper's 22050 Hz "medium" audio is
  resampled to the transport's 16 kHz with no NeXa-side format handling.
- **`BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame`** (emitted by the
  output transport from real `TTSAudioRawFrame` / `TTSStoppedFrame`,
  broadcast **upstream** as well as downstream) — the real source of truth
  for "NeXa is speaking", consumed by the half-duplex gate (§7).
- **`Pipeline` / `PipelineWorker` / `WorkerRunner`** — the same
  orchestration primitive M2.1 already uses; no new one added.

## 5. Response-language → TTS voice mapping

The Piper voice is chosen from the **response language**, never M2.2's STT
`--language` hint. `AssistantSpeechBridge.on_user_transcript()` calls
`nexa.conversation.language.detect_response_language(text)` — the **exact
same canonical function** `ConversationContext.to_provider_messages()`
already uses to build the LLM's own response-language directive (M2.3) — and
maps its result via `voice_for_language()`:

| `detect_response_language` result | Piper voice |
|---|---|
| `"pl"` | `pl_PL-gosia-medium` |
| `"en"` or `None` (no signal) | `en_GB-jenny_dioco-medium` |
| anything else | `UnsupportedTtsLanguageError` (explicit) |

On a change from the currently-selected voice, the bridge queues **one**
`TTSUpdateSettingsFrame(delta=PiperHttpTTSService.Settings(voice=…))` before
the `LLMFullResponseStartFrame`. `nexa.voice_tts` never defines its own
PL/EN classifier — `ast`-guarded
(`tests/test_voice_tts_architecture.py`).

## 6. Hardware path — independent input and output

`VERIFIED FACT` (2026-09-06, this Pi, after a reboot + USB replug —
operator-confirmed by a direct test tone and by direct old-NeXa-voice
playback):

| Role | Device | ALSA card / stable alias | Selected in NeXa as |
|---|---|---|---|
| **INPUT** | reSpeaker XVF3800 4-Mic Array | card `Array` → `hw:CARD=Array,DEV=0`; `/etc/asound.conf` `plug:` alias **`respeaker`** | `LocalAudioConfig.input_device_name = "respeaker"` → `find_device_index(pa, "respeaker", require_input=True)` |
| **OUTPUT** | Jieli `UACDemoV1.0` USB speaker DAC | card `UACDemoV10` → `hw:CARD=UACDemoV10,DEV=0`; `/etc/asound.conf` `plug:` alias **`usb_speaker`**, also the system default sink | `LocalAudioConfig.output_device_name = "usb_speaker"` → `find_device_index(pa, "usb_speaker", require_output=True)` |

- Input and output are **independent selections** — two separate
  `find_device_index()` calls, one `require_input=True`, one
  `require_output=True`. Never a single shared index. Selecting the DAC for
  output has no effect on what the mic name resolves to.
- Resolution is always **by name**, never a fixed PyAudio/PortAudio index —
  USB re-enumeration order is not stable across reboots/reconnects (the
  lesson the project's own `/etc/asound.conf` records, and which forced this
  exact fix after a replug). A name that matches nothing raises
  `AudioDeviceNotFoundError` — **no silent fallback** to another speaker,
  even one that is output-capable.
- Both `respeaker` and `usb_speaker` are ALSA `plug` devices, so
  `LocalAudioConfig`'s fixed 16 kHz / mono is converted in software to each
  device's native format (reSpeaker 16 kHz stereo; UACDemoV1.0 48 kHz
  stereo) — no channel/rate handling in NeXa.
- Deterministic coverage: `tests/test_voice_output_routing.py` (mic/speaker
  resolve to different indices; resolving the DAC doesn't disturb the mic;
  missing output raises, never falls back).

## 7. Half-duplex self-echo safety gate (temporary — M2.5 replaces it)

### Why it exists

Real-hardware failure: with input and output both live, the reSpeaker heard
NeXa's own TTS from the speaker, Silero confirmed it as `USER_SPEAKING`,
whisper.cpp transcribed fragments of her own answer ("Saturny nie jest
czarną dziurą.", "Masz rację.", "Cieszę się, że mogłem pomóc."), and those
were fed back into `ConversationSession` as new user turns — a
self-conversation loop.

### What it does

**While NeXa's own TTS audio is physically playing, microphone audio is
withheld before it can create a new STT/conversation turn.** After playback
ends, capture resumes immediately, on the next frame. It is a *pause*, not
barge-in: nothing is cancelled, and the user cannot interrupt NeXa mid-reply
(accepted for M2.4).

### Mechanism

- **`HalfDuplexGate`** (`nexa/voice/gate.py`) — a pure, event-backed boolean
  (`mic_suppressed`). No timers. Not a `VoiceState` — the acoustic
  `VoiceState` enum gains **no** `SPEAKING` member (`ast`-guarded); this is a
  separate gate.
- **`_MicGateFrameProcessor`** (`nexa/voice/runtime.py`) — inserted
  **immediately after `transport.input()`, before `VADProcessor`**. For
  every frame it calls `gate.observe_frame(frame)`; if the frame is an
  `InputAudioRawFrame` and `gate.mic_suppressed`, it is **not forwarded**
  (dropped here, before VAD, before utterance capture, before
  `SerialTranscriptionQueue`, before any `ConversationSession` turn). Every
  other frame passes unchanged, both directions.

### Source of truth — real playback frames only

`mic_suppressed` is driven by:
- **`BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame`** — emitted by the
  output transport from real `TTSAudioRawFrame` / `TTSStoppedFrame`,
  broadcast upstream so `_MicGateFrameProcessor` sees them.
- **Response-lifecycle notifications** from `AssistantSpeechBridge`:
  `notify_response_dispatched()` (from `on_user_transcript`) and
  `notify_response_finished()` (from `on_assistant_complete` /
  `on_conversation_error`).

Never from assistant text generation, `ConversationSession`, timers, or a
fake state.

### Multi-sentence safety

One reply can produce more than one `BotStartedSpeakingFrame` /
`BotStoppedSpeakingFrame` pair (Pipecat's per-turn TTS audio context has a
3 s idle `stop_frame_timeout_s`). A raw `bot_speaking` boolean would re-open
the mic **between two sentences of the same answer**. The gate prevents this
with a response-level latch: once NeXa has started speaking a reply,
`mic_suppressed` stays `True` through any number of BotStarted/BotStopped
cycles until **both** generation is complete (`notify_response_finished`)
**and** audio has stopped (a `BotStoppedSpeakingFrame` at/after that point).
`notify_response_dispatched` only re-arms the latch — it does not close the
mic during the LLM think window, so a genuine follow-up spoken *before* NeXa
starts talking still queues normally. Hard stops (`EndFrame` / `CancelFrame`
/ `ErrorFrame`) always release the gate — it is never latched shut on
shutdown.

Residual (documented, M2.5 territory): a sub-second window could open if
generation completes *exactly* during an inter-sentence gap and a further
sentence's audio follows — the next `BotStartedSpeakingFrame` re-closes it
immediately.

### Coverage

`tests/test_voice_half_duplex_gate.py` (17) + `tests/test_voice_mic_gate_processor.py`
(7): TTS inactive → speech reaches capture normally; TTS active → mic audio
cannot create an STT job / `ConversationSession` turn; TTS stops → capture
resumes; multi-sentence chunks never reopen the input between them; no fake
`VoiceState`; `_build_pipeline` inserts the gate stage only when a gate is
supplied (M2.1/M2.2 pipeline otherwise byte-for-byte unchanged); no barge-in
machinery introduced (`ast`-guarded against `StartInterruptionFrame` /
`BotInterruptionFrame` / `EmulateUser*` / `cancel` / `handle_interruptions`).

## 8. TTS-playback status — observed, never faked

`TtsStatusObserver` sits **after** `PiperHttpTTSService` (the service's own
status frames flow downstream from it) and reports:
`on_tts_started` (first `TTSStartedFrame`), `on_tts_first_audio` (first
`TTSAudioRawFrame`), `on_tts_stopped` (`TTSStoppedFrame`), `on_tts_error`
(`ErrorFrame`). It forwards every frame unchanged. The probe's TTS callbacks
must never print a `voice state:` line or reference the `VoiceStateMachine`
— the acoustic `VoiceState` can have moved on while TTS is still speaking a
previous turn — regression-guarded by
`tests/test_voice_tts_probe_state_display.py`, the same lesson M2.2/M2.3
recorded for their probes.

## 9. GPL / process boundary

`piper-tts` (providing `piper.http_server`) is **GPL-3.0-or-later**. It runs
only in a **separate OS process from a separate virtual environment**
(`~/.local/share/nexa/tts/piper-http-venv/`, created by
`scripts/setup_piper_http.py`, never NeXa's `.venv`). NeXa's own process
holds only an `aiohttp` client to `http://127.0.0.1:5001` — `nexa.tts` and
`nexa.voice_tts` never `import piper` (`ast`-guarded,
`tests/test_voice_tts_architecture.py::TestTtsPackageHasNoForbiddenDependencies`).
`pyproject.toml` is unchanged — `piper-tts` is not a NeXa dependency.
`scripts/setup_piper_http.py` may *optionally* copy the two voice `.onnx`/
`.onnx.json` files from the legacy repo's read-only reference location if
present (byte-identical, sha256-verified, avoids a re-download), falling
back to a fresh download via the external venv's own `piper`; NeXa's runtime
never reads the legacy path.

## 10. Known limitations & follow-up

### Natural speech flow — `M2.4B` (recorded, not a blocker)

Operator-identified after acceptance: spoken replies are not yet
sufficiently continuous. Observed: noticeable pauses between TTS chunks;
sentence boundaries sometimes split a semantic phrase badly (e.g.
`"Najważniejszą cechą jest tzw."` … `"horyzont zdarzeń…"`); the output
transport can stop/start between chunks. Desired future direction:
buffered/look-ahead generation; pacing that adapts mildly to how much
future generated/audio content is buffered; a continuous coherent spoken
response rather than independent sentence clips. **Suggested next substage:
`M2.4B — Natural Speech Flow / Streaming Pacing`.** Not started.

### NLTK sentence splitting is English-only in Pipecat's path (R0010)

`sent_tokenize` is always called `language="english"`; Polish
abbreviations (`"ul."`, `"tzw."`) can cause premature splits. Modest impact
on naturalness; folded into `M2.4B`.

### Observed non-fatal `"TTS context … completed with no audio"`

Seen once in a real run before audio then appeared normally. Non-fatal, no
audible effect observed. To be investigated alongside `M2.4B`'s chunk-timing
work.

### Shutdown-only ALSA assertion

An abrupt `WorkerRunner.cancel()` (the Ctrl+C path) can trip an ALSA-lib
assertion `snd_pcm_plugin_status: Assertion 'status->appl_ptr == *pcm->appl.ptr' failed`
during interpreter teardown, **after** all audio has played, on the
`plug` → `dmix` chain. A graceful `EndFrame` stop is clean. Cosmetic;
fixing it needs `VoiceRuntime.run()` shutdown changes — out of M2.4 scope.

### Softer/cozier voice

The operator would eventually prefer a warmer voice. Voice-model selection
is a **separate research task after the realtime pipeline is stable** — not
part of M2.4 and not part of `M2.4B`.

### No barge-in

M2.5. The half-duplex gate is the explicit, temporary stand-in.

## 11. Real hardware acceptance

`OPERATOR-CONFIRMED` (2026-09-06). Andrzej ran
`./.venv/bin/python3 -u apps/nexa_voice_tts_probe.py --language pl` for an
extended real conversation and explicitly confirmed: full local voice
conversation works; reSpeaker input works; UACDemoV1.0 output works; the old
NeXa Piper voice is audible; NeXa no longer talks to herself; the
half-duplex gate works acceptably; multi-turn conversation works;
`ConversationSession` preserves context; responses are coherent and useful;
overall behaviour is good. Prior direct-hardware steps (device re-detection,
direct tone, direct old PL/EN voice playback, Pipecat-only playback without
LLM) were each separately operator-confirmed — see `R0011`.
