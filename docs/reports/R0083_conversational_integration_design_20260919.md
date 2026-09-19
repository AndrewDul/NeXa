# R0083 — Conversational Integration Design Gate (audit only, no implementation)

**Date:** 2026-09-19
**Status:** DESIGN GATE — read-only audit and plan. No production code changed. No hardware executed. No integration started.
**Precondition accepted this round:** R0082 REAL HARDWARE (`r0082h_silentseries_real_001`, run `20260919T105048Z`) — `VALID TEST -- PASS`, 10/10 continuous silent-user episodes, 0 accepted VAD starts, 0 confirmed interruptions, combined with R0082-G's 5/5 real deliberate-barge-in PASS. R0082-H audio/AEC/VAD validation is CLOSED. Full R0081 end-to-end acceptance remains OPEN. See `docs/reports/R0082_livekit_webrtc_audio_poc_20260918.md`, sections 124-125.

**Method:** direct reading of the CURRENT repository source (not old reports where source disagrees), the ACTUAL installed Python environment (`.venv`), and the frozen ADRs. Two independent research forks were used to trace the production call/data-flow and to inspect the installed `pipecat`/`livekit` package source directly; their findings are reproduced here with file:line citations, cross-checked and extended by the primary session's own direct reads (notably `ADR-0004` Amendment 2 and `src/nexa/realtime/gemini/service.py`/`simple_conversation.py`'s interruption-frame handling). No hardware, microphone, or speaker was touched. `git status --short` was checked before any file was written and is reproduced in §14; nothing pre-existing was staged, reverted, or disturbed.

---

## 0. Executive summary — the single most important finding

**There are currently TWO parallel, independently-maintained cloud-voice architectures in this repository, and R0082's validated audio/AEC/VAD path is compatible with, but not yet connected to, either of them:**

| | `apps/nexa_cloud_voice_app.py` → `nexa.realtime.gemini.runtime` (M2.6B.3+) | `apps/nexa_cloud_voice_simple.py` → `nexa.realtime.gemini.simple_conversation` (R0071) |
|---|---|---|
| Status (ADR-0004 Amendment 2, 2026-09-16) | **PAUSED** — real-hardware self-echo regression (this is what R0081 is actively investigating) | **ACCEPTED / FROZEN** — the current `CLOUD_PREFERRED` production baseline |
| Interruption authority | ONE NeXa-owned `BargeInController`/`InterruptionStateMachine`, local Silero VAD, Gemini's own server-side turn detection bypassed via manual turn framing | **No `BargeInController`.** Pipecat's own `LLMContextAggregatorPair` (local Silero VAD) + Gemini's own delayed `serverContent.interrupted` ack — two call sites, by explicit product decision (ADR-0004 Amendment 2 item 5) |
| Mic/speaker transport | `pipecat.transports.local.audio.LocalAudioTransport`, PyAudio-backed | Same: `LocalAudioTransport`, PyAudio-backed |
| Self-echo suppression | reSpeaker XVF3800's onboard **hardware** AEC DSP, ALSA far-end reference feed | Same XVF3800 hardware AEC, same `AecReferenceFeeder` (no gain scaling) |
| Uses LiveKit / WebRTC AEC / `rtc.PlatformAudio`? | **No.** | **No.** |

**R0082's PlatformAudio/WebRTC/LiveKit path is a fully separate, currently-unconnected research artifact relative to BOTH production cloud-voice entry points.** Integrating it is a **transport-and-AEC-engine replacement**, not a pure addition, for whichever of the two architectures is chosen as the integration target — and that choice is itself the single highest-leverage open decision this report surfaces (§11.3). No hardware, microphone, or speaker is touched in this task; this section states findings from static source reading only.

---

## 1. Current production architecture as actually found

### 1.1 `apps/nexa_cloud_voice_app.py` → `nexa.realtime.gemini.runtime` (M2.6B.3+, PAUSED)

`apps/nexa_cloud_voice_app.py` (186 lines) is thin wiring only. `main()` builds `session = build_default_session()`, loads a Gemini credential, and calls `build_gemini_voice_runtime(session=session, api_key=..., policy=ConversationPolicy.CLOUD_PREFERRED, on_event=..., on_aec_change=..., dry=False, audio_config=LocalAudioConfig(...))`, then `await runtime.start(snapshot)`, idling until Ctrl+C.

`build_gemini_voice_runtime` — `src/nexa/realtime/gemini/runtime.py:1485` — is the entire ~1829-line production wiring module for this path. It builds **two Pipecat pipelines side by side**, bridged only by plain async calls / an event queue (never shared frame-processor state):

- (a) `GeminiLiveProvider`'s own **headless** pipeline (aggregators + Gemini service, no transport) — built inside `service.py`.
- (b) this module's **hardware pipeline** (`runtime.py:1793-1805`): `[transport.input(), (AcousticCaptureProcessor if --scheduled-aec-reference), vad_processor, bargein, bridge, (AecReferenceFeeder if not scheduled-ref), transport.output()]`.

Call/data flow (one full turn, exact classes at every arrow):

```
reSpeaker XVF3800 mic
  -> PyAudio (pipecat.transports.local.audio.LocalAudioTransport.input(), runtime.py:1725,1762)
  -> SileroVADAnalyzer + VADProcessor (local, 16 kHz, runtime.py:1763-1766, stop_secs=0.5)
  -> BargeInController (src/nexa/voice/bargein.py:108; owns InterruptionStateMachine
     internally, bargein.py:126; gated on AecReferenceHealth.barge_in_safe)
  -> _VadToProviderBridge (_make_vad_bridge_class, runtime.py:707; UtteranceBuffer,
     500 ms preroll)
  -> GeminiLiveProvider.user_turn_start()/send_user_audio()/user_turn_end()
     (manual turn framing, service.py:531,541,567; 16 kHz mono PCM)
  -> Gemini Live (cloud)
  -> provider.events() (AssistantAudioEvent / AssistantTranscriptionEvent /
     GenerationCompleteEvent / ProviderInterruptionEvent)
  -> _ResponseGenerationGuard check (runtime.py:614)
  -> hw_worker.queue_frames([TTSAudioRawFrame], 24 kHz)
  -> AecReferenceFeeder (ALSA loopback to XVF3800 far-end reference,
     src/nexa/voice_tts/aec_reference.py)
  -> LocalAudioTransport.output() -> PyAudio -> real USB speaker
on completion: _ResponseLifecycle (runtime.py:530) fires only once BOTH Gemini
  generation-complete AND a genuine BotStoppedSpeakingFrame (playout-aware,
  driven by TTSStoppedFrame in FIFO order after every audio chunk, never a
  timer) have occurred
  -> ConversationRouter.commit_cloud_turn() (src/nexa/realtime/router.py:289)
  -> ConversationSession.record_external_exchange()
```

Interruption confirm path: `BargeInController` calls Pipecat's own `broadcast_interruption()` (media-queue/output-audio cancellation) plus an injected `on_confirmed` hook. In this runtime, `on_confirmed` (`runtime.py`, ~line 1700) additionally invalidates the current Gemini response-generation id via `_ResponseGenerationGuard` (`runtime.py:614`, added M2.6B.4/R0038 specifically because `broadcast_interruption()` alone did not stop already-in-flight-but-not-yet-queued Gemini audio) and calls `router.on_interruption()` / `provider.cancel()`.

`provider.cancel()` (`service.py:603-615`) constructs exactly one `InterruptionFrame`, records its `id` in `self._local_cancel_frame_ids` *before* queuing it, and emits `CancellationCompleteEvent`. `_translate_frame` (`service.py:640-662`) later classifies every `InterruptionFrame` it sees by that id: `source="local_cancel"` if it is NeXa's own queued frame, else `source="remote_server_ack"` — the latter case is, **by elimination, confirmed exhaustively from installed source** (the module's own comment cites this), Gemini's own `serverContent.interrupted` triggering `GeminiLiveLLMService`'s own `broadcast_interruption()`. **Both sources still funnel into ONE consumer**: `ConversationRouter.handle_provider_event()` (`router.py:342`) calls `self.on_interruption()` regardless of `source`, which delegates to `CloudTurnAccumulator.on_interruption()`; the adjacent `commit_cloud_turn()` docstring states `CloudTurnAccumulator` "guarantees at most one commit per turn." **This was not independently re-verified for `on_interruption()` itself this round** (only `commit`'s guarantee is documented at the call site) — flagged as an open item in §15, not assumed.

`RealtimeVoiceProvider` (`src/nexa/realtime/provider.py:191`, `ABC`) defines a genuinely provider-agnostic interface (`capabilities`, `readiness`, `start`, `stop`, `user_turn_start`, `send_user_audio`, `user_turn_end`, `cancel`, `events`) — `GeminiLiveProvider(RealtimeVoiceProvider)` (`service.py:209`) is one concrete implementation. This confirms "Gemini is a replaceable provider" is already structurally true in code, not aspirational.

`INPUT_SAMPLE_RATE_HZ = 16_000`, `OUTPUT_SAMPLE_RATE_HZ = 24_000` (`service.py:104-105`) — mono in/out, no 48 kHz stage anywhere in this path. Turn boundaries are driven **manually** by `_VadToProviderBridge` while a locally-detected VAD turn is open; no `automatic_activity_detection`/`realtime_input_config` flag was found anywhere in `service.py` (grepped, zero hits) — the "Gemini server VAD stays off" claim is strong indirect evidence from the manual-framing usage pattern, but **not pinned to one literal config line**; flagged open (§15).

**ConversationSession boundary (this path):** clean and singular. No file under `src/nexa/realtime/gemini/` imports `ConversationSession` directly except `router.py` (checked). `ConversationRouter.commit_cloud_turn()` (`router.py:289`) is, per its own docstring, "the only place a provider's output ever reaches `ConversationSession`" — it calls `ConversationSession.record_external_exchange(...)`. No violation of the "audio transport ≠ conversation state" / "Gemini Live provider ≠ ConversationSession" separations was found in this path.

### 1.2 `apps/nexa_cloud_voice_simple.py` → `nexa.realtime.gemini.simple_conversation` (R0071, ACCEPTED / FROZEN — the actual current `CLOUD_PREFERRED` production baseline)

**This is the file the user should mentally substitute wherever an older document says "the cloud voice production path" without further qualification.** `ADR-0004` Amendment 2 (2026-09-16, `docs/decisions/ADR-0004_cloud_realtime_voice_provider_boundary.md:1772-1871`) is the explicit product decision: R0071 found the dual-pipeline runtime above produces sustained self-echo on the current Pi topology even after fixing a real ownership bug and a controlled gain A/B; a same-day, same-hardware re-run of the ORIGINAL M2.6A probe (ONE Pipecat pipeline, Gemini's own native VAD-driven interruption, **no** NeXa-owned interruption-authority layer) was clean, operator-rated 10/10. Decision: **pause** the dual-pipeline runtime (kept, not deleted — a resumable investigation) and make the simple, golden-derived path the accepted `CLOUD_PREFERRED` baseline. Real-hardware acceptance for this simple path passed the same day (`ADR-0004` §"Real-hardware acceptance (2026-09-16, same day)").

`simple_conversation.py`'s own docstring (lines 1-55) is explicit about what it deliberately does **not** reuse: `BargeInController`, the candidate/reject state machine, `_ResponseGenerationGuard`, atomic provider replacement, the hardware/provider dual-pipeline bridge — "Interruption is handled the way M2.6A proved works: Pipecat's own `LLMContextAggregatorPair`... broadcasts `InterruptionFrame` natively on a new `UserStartedSpeakingFrame`... NeXa's own local speaker-stop authority is Pipecat's, not a second NeXa-owned state machine layered on top of it."

It DOES reuse, unchanged: `ConversationSession` (via `ConversationRouter`), `CloudContextSnapshot`, `ConversationPolicy`, the same `nexa.realtime.provider` event types, the same `ConversationRouter.handle_provider_event` write path (§1.1's boundary), `nexa.voice.config.LocalAudioConfig`, `nexa.voice.device.find_device_index`, `nexa.voice_tts.aec_reference.AecReferenceFeeder` fed **without** `gain_source` (byte-for-byte golden's own unscaled reference). Same `LocalAudioTransport` (PyAudio-backed) confirmed imported at `simple_conversation.py:149`. Sample rates: `INPUT_SAMPLE_RATE_HZ = 16000`, `OUTPUT_SAMPLE_RATE_HZ = 24000` (`simple_conversation.py:90-91`), `VAD_STOP_SECS = 0.5` (`:93`, golden M2.6A's own Silero stop_secs).

**Interruption-signal multiplicity — already documented in this file's own code comments, confirmed against the installed `pipecat==1.8.1` source (`simple_conversation.py:284-320`, R0081's own finding):** up to **TWO independent call sites** can invoke Pipecat's `broadcast_interruption()` for a single physical interruption — (1) `LLMContextAggregatorPair`'s own local-VAD-driven aggregator (`llm_response_universal.py:1292` in the installed pipecat package), firing synchronously with a fresh local `UserStartedSpeakingFrame`; (2) `GeminiLiveLLMService` itself (`gemini_live/llm.py:1333`), firing when Gemini's own server sends `serverContent.interrupted=True` — a **delayed** acknowledgement of the `activity_start` NeXa already sent when local VAD fired, arriving on Gemini's own network schedule, not paired with any new local VAD event. Each call site's own `broadcast_interruption()` additionally fans out an upstream + downstream frame instance (R0043's original finding, still true) — **up to 4 total `InterruptionFrame` sightings can legitimately correspond to ONE physical interruption.** `frame.id`/`frame.broadcast_sibling_id` let the code's own diagnostic tap (`_vad_active` bookkeeping) distinguish fan-out siblings of the same call from two genuinely independent calls. This is exactly the "PlatformAudio/WebRTC VAD + LiveKit transport VAD + Pipecat interruption logic + BargeInController + Gemini server-side interruption all independently deciding" risk the frozen constraints warn against (item 4 of this task) — **except it is not hypothetical: it is the currently-accepted production behavior**, tolerated because both call sites trace back to the SAME underlying local-VAD-detected event (not two competing authorities making different decisions), and Pipecat's own frame plumbing is relied on to make repeated sightings idempotent at the playback-cancellation layer. This module's own `CloudRealtimeConversationTap` (a `FrameProcessor` subclass) taps `InterruptionFrame`/`UserStartedSpeakingFrame`/etc. and republishes to `ConversationRouter.handle_provider_event()` — the **same** event vocabulary and the **same** `ConversationSession` boundary as §1.1.

### 1.3 The current self-echo-sensitive boundary (both paths, identical)

**Input:** `pipecat.transports.local.audio.LocalAudioTransport`, backed directly by **PyAudio** (`runtime.py:1725`: `import pyaudio; pa = pyaudio.PyAudio()`), device indices resolved via `nexa.voice.device.find_device_index` against `LocalAudioConfig.input_device_name`/`output_device_name`. **One `LocalAudioTransport` instance owns both `.input()` and `.output()`** (`runtime.py:1762,1793,1804`) — the same single-engine-for-capture-and-playback shape R0082 validated, but via **PyAudio/ALSA, not LiveKit/`rtc.PlatformAudio`/WebRTC AEC**.

**Self-echo suppression:** the reSpeaker XVF3800's own **onboard hardware AEC DSP**, fed a far-end reference over ALSA via `AecReferenceFeeder` (`src/nexa/voice_tts/aec_reference.py`, unmodified since M2.5, used identically by both cloud paths and the local-voice path) — or, opt-in only via `--scheduled-aec-reference`, `ScheduledReferenceTransport`/`AlsaReferenceSink`/`ScheduledNativeFrontend` (`src/nexa/voice/acoustic/`, the R0064-R0070 track, visible in the current working tree as pre-existing uncommitted files — a parallel, not-yet-merged AEC investigation, flagged only, not audited further here since it is out of R0083's scope and untouched by this task). **Neither is WebRTC AEC. Neither uses LiveKit or `rtc.PlatformAudio` anywhere.**

**VAD:** `pipecat.audio.vad.silero.SileroVADAnalyzer` + `pipecat.processors.audio.vad_processor.VADProcessor`, local, 16 kHz, in-pipeline — in BOTH cloud paths. Not delegated to Gemini's own server-side VAD in either path (§1.1's dual-pipeline explicitly bypasses it via manual framing; §1.2's simple path uses Gemini's *acknowledgement* of an already-locally-detected event, not an independent server-initiated detection).

**Conclusion for R0082 integration:** R0082's validated result is a **different, currently fully disconnected** self-echo-suppression mechanism (software WebRTC AEC inside `rtc.PlatformAudio`, real hardware, one LiveKit room, one bot-side + one hardware-side participant) from the one already live in either production cloud-voice entry point (XVF3800 hardware AEC over ALSA, PyAudio transport, no LiveKit). **Integrating R0082's path means replacing the transport-and-AEC layer, not adding a new one alongside the existing one** — running both simultaneously on the same physical mic/speaker would be nonsensical (two engines fighting over the same ALSA devices) and is explicitly not proposed anywhere in this document.

---

## 2. Installed Pipecat / LiveKit capability audit

Installed: `pipecat-ai==1.8.1`, pinned in `pyproject.toml:24` as `"pipecat-ai[local]==1.8.1"` — **only the `[local]` extra is installed**. `pip show pipecat-ai` confirms `Required-by: nexa`.

**`livekit`/`livekit-rtc` and `livekit-agents` are NOT installed in NeXa's project `.venv` at all.** `import livekit` raises `ModuleNotFoundError`. `pipecat-ai[livekit]` was never installed. The LiveKit transport module **is present on disk** at `.venv/lib/python3.13/site-packages/pipecat/transports/livekit/transport.py` (1411 lines) only because it ships inside the base `pipecat-ai` wheel — it is currently **dead/unimportable code**: its own top-level `try: from livekit import rtc ... except ModuleNotFoundError: raise ImportError(...)` fires immediately if imported today. **Adding LiveKit support to production requires adding a new dependency** (`pipecat-ai[livekit]` or bare `livekit`), not present today.

**No `livekit-agents`/`AgentSession` dependency exists anywhere in Pipecat's LiveKit transport code** — it imports only `from livekit import rtc`, the same base SDK R0082's own research harness used. This directly answers §9's question: using Pipecat's `LiveKitTransport` does **not**, by itself, pull in or tempt `AgentSession`/LiveKit-owned conversation state.

**Transport class detail** (`pipecat/transports/livekit/transport.py`):

- `LiveKitTransport(url, token, room_name, params=None, input_name=None, output_name=None)` — top-level `BaseTransport` subclass. It internally owns `LiveKitTransportClient`, which **creates and owns `rtc.Room` itself** (`self._room = rtc.Room(...)` in `setup()`) — it does **not** accept a pre-connected `rtc.Room`; the transport is the room owner, not injectable.
- Connect/disconnect happens **once per pipeline run** (`setup()` → `stop()`/`cancel()`/`cleanup()`), not per-turn — **no room/AEC reset between responses at this layer**, consistent with the R0082-validated invariant.
- On connect it calls `rtc.AudioSource(out_sample_rate, channels)` + `LocalAudioTrack.create_audio_track(...)` + `publish_track(..., source=SOURCE_MICROPHONE)` — **this transport is architecturally the "bot" participant** (it publishes a track), the exact same role as R0082's own `run_speech_role`, not the hardware-mic participant.
- `LiveKitOutputTransport.process_frame()`: **on `InterruptionFrame`, calls `self._client._audio_source.clear_queue()` directly.** Pipecat's own interruption-frame propagation already performs the exact `AudioSource.clear_queue()` mechanism R0082's harness hand-validated across all seven correction rounds. Production interruption-audio-cancellation could therefore ride on Pipecat's existing `InterruptionFrame` machinery, **provided the frame is only emitted on NeXa's own confirmed interruption** (a bare VAD start must never reach it) — wiring that trigger correctly is an open design question (§9), not proven here.
- `LiveKitInputTransport.__init__` sets `self._resampler = create_stream_resampler()` (`pipecat.audio.utils`) and resamples every incoming `rtc.AudioFrame` from its native rate to the configured `audio_in_sample_rate`. **This is Pipecat's own internal resampler, separate from R0082's `StreamingResampler`.**
- No `vad_analyzer` field exists on `TransportParams` in this installed version (checked the full field list) — VAD attachment is not a transport-constructor concern in 1.8.1; it is wired elsewhere in the pipeline, same as both current cloud paths already do.
- AEC: `LiveKitTransport`/`LiveKitTransportClient` contain **no AEC logic whatsoever** — a pure frame pipe (`rtc.AudioSource`/`rtc.AudioStream` publish/subscribe). AEC ownership is entirely external, on whichever room participant owns `rtc.PlatformAudio` — **compatible** with R0082's validated invariant *if and only if* the hardware-mic/speaker process remains a **separate LiveKit room participant** from the one Pipecat's `LiveKitTransport` runs as, exactly mirroring R0082's own `run_speech_role` (bot/Pipecat+Gemini side) vs. `run_hardware_role` (`rtc.PlatformAudio`+reSpeaker side) split. The transport's own architecture (one room-participant identity, one published track) is evidence this split is not just possible but assumed.

**Existing NeXa Pipecat transport usage (repo-wide grep, `src/` + `apps/`):** `from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams` at `src/nexa/voice/runtime.py:39` (local-voice path), and the **same** import at `src/nexa/realtime/gemini/runtime.py:434` and `simple_conversation.py:149` (both cloud paths). `src/nexa/voice/acoustic/local_transport.py:12-13` imports `BaseOutputTransport`/subclasses `LocalAudioOutputTransport` further — part of the separate, pre-existing, uncommitted R0064-R0070 track (§1.3). **No file anywhere in `src/`/`apps/` currently imports `pipecat.transports.livekit`.** Today's "cloud voice" runs Gemini-as-provider over the exact same local-PyAudio transport as the fully local voice path.

**A vs. B framing (evidence, not a decision — see §11.3):** using Pipecat's `LiveKitTransport` directly is structurally compatible with the frozen constraints — never touches `livekit-agents`, is a peer-level room participant matching R0082's proven split, and its `InterruptionFrame`→`clear_queue()` behavior already matches R0082's validated cancellation semantics. Costs: a new dependency; it owns the room/connect lifecycle itself (cannot wrap an already-connected `rtc.Room` the way R0082's own harness scripts do — any NeXa room-naming/token/identity logic must go through the constructor). A thin NeXa-owned adapter (Option B) would only be needed if that ownership inversion proves incompatible with how NeXa would mint tokens/room names in production — not determined here.

---

## 3. Interruption-authority enumeration (current state, both cloud paths)

| Signal producer | Signal | Consumer | Status today |
|---|---|---|---|
| Local Silero VAD (`SileroVADAnalyzer`+`VADProcessor`, 16 kHz, in-pipeline) | `UserStartedSpeakingFrame`/`UserStoppedSpeakingFrame` | §1.1: `BargeInController`. §1.2: Pipecat's own `LLMContextAggregatorPair` | **Authoritative** in both paths — the only thing that ever *decides* a candidate interruption has begun |
| `BargeInController` (§1.1 only; `bargein.py:108`) | confirm/reject after `confirm_hold_secs`, gated on `AecReferenceHealth.barge_in_safe` | Pipecat `broadcast_interruption()` + `on_confirmed` hook (→ `_ResponseGenerationGuard`, `router.on_interruption()`, `provider.cancel()`) | **Authoritative, single instance** in §1.1. **Does not exist** in §1.2 (deliberately, ADR-0004 Amendment 2) |
| `GeminiLiveLLMService`'s own `broadcast_interruption()` on `serverContent.interrupted` (installed pipecat, both paths' provider layer) | `InterruptionFrame` (`source="remote_server_ack"`) | §1.1: same `ProviderInterruptionEvent`→`router.on_interruption()` consumer as the local-cancel path (idempotency **not independently re-verified this round**, §15). §1.2: same `CloudRealtimeConversationTap`→router path | **Non-authoritative echo of an already-locally-decided event** in both paths, by design (delayed server ack, never an independent trigger) — but it IS a second live call site producing a real frame, tolerated not eliminated |
| Gemini Live's own native server-side VAD/turn detection (as a *decision maker*, not just an ack) | N/A found | N/A | **Bypassed** in §1.1 (manual turn framing; not pinned to one literal disable flag, §15 open item). **Not applicable as an independent decision-maker** in §1.2 either — Gemini's role there is still an ack of NeXa-observed local VAD, not a server-initiated interruption with no local correlate found in the audited code |
| `PlatformAudio`/WebRTC AEC/VAD (R0082 research harness) | none, currently | none, currently | **Not wired into production at all.** R0082's own harness never claims interruption authority — its `InterruptionStateMachine`/confirm-hold logic (audited earlier this session, `src/nexa/voice/interruption.py`) is the SAME class §1.1's `BargeInController` already owns; R0082 exercises it, does not duplicate it |
| Pipecat's `LiveKitTransport` (§2, not installed/wired) | `InterruptionFrame` → `AudioSource.clear_queue()` | itself (playout cancellation only) | **Not wired.** If adopted, this becomes a new PLAYOUT-CANCELLATION consumer, not a new interruption DECIDER — it must only ever receive a frame after the single authority above has already decided |

**Canonical rule preserved by design in the audit above:** `PlatformAudio`/WebRTC AEC is (and, per R0082's own findings and Pipecat's own `LiveKitTransport` code, would remain) an audio-cleaning mechanism, never a second conversational-interruption authority. The one place multiplicity genuinely exists TODAY is the two-call-site `InterruptionFrame` pattern documented in §1.1/§1.2 — pre-existing, not introduced by R0082, and out of R0083's scope to fix (flagged, not designed around, per instruction "report it, do not fix it yet" in §5 of the task — applied here to §3/§4 findings equally).

---

## 4. ConversationSession boundary (proven, both paths)

Voice enters/exits `ConversationSession` through exactly ONE call path in both architectures: `ConversationRouter.commit_cloud_turn()` (`router.py:289`) → `ConversationSession.record_external_exchange(user_text, assistant_text, interrupted=...)`. `ConversationRouter` is constructed with `session: ConversationSession` injected once (`router.py:47`). No file under `src/nexa/realtime/gemini/` imports `ConversationSession` directly except `router.py` (grepped for both paths).

`ConversationRouter.has_turn_awaiting_assistant()` / `begin_cloud_turn()` / `on_interruption()` / `on_user_transcription()` / `on_assistant_transcription()` / `recover_from_mid_turn_loss()` are the turn-lifecycle methods the provider-adapter code calls INTO the router — never the reverse; the router never reaches back into transport/provider internals. `CloudTurnAccumulator` (owned by the router, not the provider) is where "at most one commit per turn" is enforced.

**No violation of `audio transport ≠ conversation state` or `Gemini Live provider ≠ ConversationSession` was found in either current cloud path.** The frozen separation already holds in the code, not merely in intent — this is a genuinely clean boundary to build the R0082 integration against; R0083 must preserve it exactly as-is (§8's file-level plan treats `router.py`/`session.py` as UNCHANGED, RELIED-UPON).

---

## 5. Gemini provider boundary

`RealtimeVoiceProvider(ABC)` (`provider.py:191`) is the seam: `capabilities`, `readiness`, `start`, `stop`, `user_turn_start`, `send_user_audio`, `user_turn_end`, `cancel`, `events`. `GeminiLiveProvider` is one implementation; nothing in either cloud path's higher layers (`router.py`, `ConversationSession`) imports Gemini-specific types. This interface already enforces "Gemini Live is a replaceable realtime voice provider only" at the type level — R0083 must not add any NeXa-identity/memory/state logic into `service.py`/`simple_conversation.py`'s own translation layer (none was found there this round; the translation layer's ONLY job is frame↔event mapping, confirmed by direct read of `_translate_frame`).

---

## 6. Audio format / sample-rate / resampling map

| Stage | Sample rate | Channels | Format | Owner |
|---|---|---|---|---|
| R0082 LiveKit room (real hardware, validated) | 48000 Hz | 1 | PCM16 via `rtc.AudioFrame` | `rtc.PlatformAudio` (mic+speaker), `rtc.AudioSource` (bot publish) |
| R0082 VAD-observation copy (research harness only) | 16000 Hz | 1 | PCM16 via `StreamingResampler` | R0082 harness only — **not production code** |
| Pipecat `LiveKitInputTransport` (if adopted) | configured `audio_in_sample_rate` (e.g. 16000 to match Gemini) | 1 (params-dependent) | `InputAudioRawFrame`/equivalent | Pipecat's own `create_stream_resampler()` — internal, separate from R0082's resampler |
| Gemini Live input | 16000 Hz | 1 | PCM16 | `GeminiLiveProvider`/`GeminiLiveLLMService` |
| Gemini Live output | 24000 Hz | 1 | PCM16 | same |
| Current production `LocalAudioTransport` (both cloud paths, unchanged today) | matches `LocalAudioConfig`, effectively the device's native rate resampled by Pipecat to 16k/24k as needed | 1 | PCM16 via PyAudio | `nexa.voice.config.LocalAudioConfig` |

**Finding:** Pipecat's `LiveKitInputTransport` already resamples 48 kHz (LiveKit room native) to whatever `audio_in_sample_rate` is configured (e.g. 16000, matching Gemini's own input rate) internally, via its own `create_stream_resampler()`. **R0082's custom `StreamingResampler` is very likely NOT needed in the production transport path** — it exists in R0082 purely to feed the research harness's OWN local VAD-observation copy (a diagnostic duplicate stream), a role production does not need if interruption authority stays with whichever mechanism is chosen in §11.3 (Pipecat's own local VAD via a correctly-configured transport, or a revived `BargeInController` fed directly from Pipecat's VAD frames — neither needs a second, harness-style resampled observation copy). **Do not duplicate resampling**: if `LiveKitTransport`'s own resampler already produces 16 kHz frames feeding the SAME `VADProcessor`/`SileroVADAnalyzer` used today, no second resampler is needed anywhere in the production path. This should be confirmed empirically at gate R0083-C (§12), not assumed further than stated here.

---

## 7. AEC ownership invariant — how production integration preserves it

R0082's validated result depends on **ONE `rtc.PlatformAudio`/WebRTC engine owning real mic + real playout + AEC reference simultaneously** (confirmed: 10/10 real-hardware episodes, 0 false starts, over a continuous 259.55s session, §124 of the R0082 report). Production integration preserves this invariant **only if**:

1. The hardware-facing process (mic capture + speaker playback) remains exactly ONE `rtc.PlatformAudio` instance, constructed once per session (never per-turn), exactly as R0082's `run_hardware_role_silent_series()` does today (audited byte-for-byte unchanged across all seven R0082-H corrections).
2. The Pipecat/Gemini-facing process (the "bot" side, whether via `LiveKitTransport` directly or a thin adapter) is a **separate LiveKit room participant** that only ever publishes/subscribes tracks over the room — it must never open its own competing ALSA/PortAudio device handle to the same physical mic/speaker.
3. No component anywhere reintroduces the XVF3800's OWN onboard hardware AEC (`AecReferenceFeeder`, ALSA loopback) alongside `rtc.PlatformAudio`'s WebRTC AEC on the same physical devices — that would be two AEC engines fighting over one signal, an explicitly non-negotiable anti-pattern per the task's own framing (item 7).

**Flagged risk, must be designed against, not merely noted:** if a future implementation gate accidentally lets Pipecat's `LiveKitTransport` (or any adapter) open its OWN separate audio device (rather than staying a pure LiveKit-room frame pipe with `rtc.PlatformAudio` as the sole real-device owner on the hardware side), the unified-clock/AEC property is destroyed. §12's file-level plan explicitly marks the hardware-role process as the ONLY place `rtc.PlatformAudio` may be constructed.

---

## 8. Response audio cancellation path — ownership design (not implemented)

R0082-G proved: human speech → accepted VAD start → ~0.3s confirm hold → `INTERRUPT_CONFIRMED` → stop future submission → `AudioSource.clear_queue()` → no software resume. Mapping this onto a Gemini-generating-live-audio production stack requires FOUR ownership decisions, deliberately kept separate (per the task's own instruction):

| Step | What it does | Owner today (either cloud path) | Owner in the target design (proposed, not implemented) |
|---|---|---|---|
| A. Stop provider generation | Tell Gemini to stop generating | `GeminiLiveProvider.cancel()` (`service.py:603`) — unchanged, provider-agnostic via the `RealtimeVoiceProvider` interface | **Unchanged.** This step has nothing to do with the transport; R0082 integration does not touch it |
| B. Stop Pipecat/downstream frame production | Stop the Pipecat pipeline from continuing to push already-generated audio frames downstream | Pipecat's `broadcast_interruption()` (native, both paths) | **Unchanged** — still Pipecat's job, still triggered by whichever single authority is chosen (§11.3) |
| C. Clear locally queued LiveKit/AudioSource playout | Discard audio already handed to the transport but not yet physically played | **Does not exist today** (no LiveKit anywhere in production) | Pipecat's `LiveKitOutputTransport.process_frame()` on `InterruptionFrame` → `AudioSource.clear_queue()` (§2, confirmed already implemented in the installed package) — **this is the one step R0082 directly hand-validated** (all `confirmed_during_drain` proofs across every R0082-H correction round used exactly this call) |
| D. Update `ConversationSession`/turn lifecycle | Commit the (possibly-interrupted) turn, reset turn state | `ConversationRouter.on_interruption()`/`commit_cloud_turn()` (§4) | **Unchanged** — the boundary is already correct and provider/transport-agnostic |

**Ordering constraint (design, not code):** C must never fire before the single interruption authority (§11.3) has genuinely confirmed the interruption — R0082's own seventh correction (fail-closed drain-timeout handling, and the explicit "a bare accepted START must never itself clear the queue" rule from Part 3 Case B of the sixth correction) is the exact discipline this ordering constraint borrows from. A must be idempotent under Gemini's own delayed server-ack re-firing (§1.2/§3's two-call-site finding) — this is already true today (`_local_cancel_frame_ids` membership check) and must be preserved, not re-invented, wherever the new transport sits.

---

## 9. No LiveKit AgentSession migration

Confirmed in §2: `livekit-agents` is not installed, not a dependency of Pipecat's `LiveKitTransport`, and the transport class imports only the base `livekit.rtc` SDK. Adopting Pipecat's `LiveKitTransport` for the "bot" side does **not** require or structurally tempt `AgentSession`/LiveKit-owned conversation state — `LiveKitTransport` is a `BaseTransport` subclass slotting into the SAME Pipecat `Pipeline` construction pattern already used at `runtime.py:1805`/`simple_conversation.py`'s own pipeline build. `ConversationSession`, NeXa Core, provider routing, and memory/context ownership all remain exactly where §4/§5 found them — LiveKit, if adopted, is scoped to being a room-participant frame-transport class, structurally no different in kind from `LocalAudioTransport` today.

---

## 10. Frozen-constraint compliance summary (as designed, not yet implemented)

| Constraint | Status of this design |
|---|---|
| ONE NeXa Core | Untouched — not in this design's scope |
| `ConversationSession` canonical | Preserved (§4) |
| Gemini Live replaceable provider only | Preserved (§5) |
| Pipecat remains orchestration layer | Preserved — `LiveKitTransport` is a Pipecat transport class, not a Pipecat replacement (§2, §9) |
| No wholesale LiveKit `AgentSession` migration | Preserved, confirmed not required (§9) |
| LiveKit = transport/audio infra, not the brain | Preserved by construction (§2, §9) |
| Exactly ONE `BargeInController`/interruption authority | **Open decision, not yet resolved** — the two CURRENT production paths already disagree with each other (§1.1 has one; §1.2 deliberately has none); R0083 must pick ONE target and this design does not yet do so (§11.3) |
| No local language-ID stage in cloud realtime path | Not found in either current path; not reintroduced by this design |
| 500 ms preroll canonical | §1.1's `UtteranceBuffer` already implements this (`runtime.py:707`); unaffected by this design |
| Sulafat selected voice | Untouched |
| Local voice baseline frozen | Untouched — R0083's proposed changes are scoped to `nexa.realtime.gemini.*`/a new LiveKit adapter only |
| No Gemini audio diagnostics unless re-approved | None proposed |
| Typed/voice converge on `ConversationSession` | Already true (§4); unaffected |

---

## 11. Target architecture (hypothesis, evaluated against the audit)

### 11.1 The originally-proposed target hypothesis, annotated with audit findings

```
REAL HARDWARE reSpeaker mic
    v
rtc.PlatformAudio / WebRTC AEC          <- MUST be the ONLY audio-device owner (§7); unchanged from R0082
    v
LiveKit local room/transport             <- confirmed real, hardware-validated, R0082
    v
Pipecat LiveKitTransport (or narrow adapter)  <- exists in installed wheel, needs [livekit] dep (§2);
                                              owns the room itself, not injectable (§2)
    v
NeXa realtime voice pipeline             <- §1.1's hardware-pipeline SHAPE (VAD -> interruption
                                             authority -> bridge), whichever authority §11.3 selects
    v
Gemini Live provider                     <- UNCHANGED, RealtimeVoiceProvider interface (§5)
    v
ConversationSession / NeXa Core          <- UNCHANGED boundary (§4)
    v
assistant audio
    v
LiveKit  -> PlatformAudio -> real speaker  <- same room, output side of the same PlatformAudio
                                              instance (§7 invariant)
```

### 11.2 What the audit changes about this hypothesis

- The "NeXa realtime voice pipeline" box is **not new** — it already exists (§1.1's hardware pipeline: `vad_processor`, `bargein`, `bridge`). The integration work is replacing ONLY `transport.input()`/`transport.output()` (currently `LocalAudioTransport`/PyAudio) with the LiveKit-transport-plus-`PlatformAudio` pair — **not** rebuilding the pipeline shape.
- R0082's `StreamingResampler` almost certainly does **not** need to ship to production (§6) — Pipecat's own `LiveKitInputTransport` resampler likely already does this job, pending gate-C empirical confirmation.
- The diagram's implicit assumption of "one linear pipeline" hides the real, audited shape: **two separate room participants** (bot-side Pipecat process, hardware-side `PlatformAudio` process) connected only via the LiveKit room, exactly like R0082's own two-role harness. This is a feature, not a simplification to fix — it is what makes the AEC-ownership invariant (§7) hold.

### 11.3 The one decision this report does not make: which production entry point is the integration target

This is the single highest-leverage open question, and per instruction this report presents it as a table for a human decision, not a unilateral choice:

| Option | Target file(s) | Interruption authority after integration | Pros | Cons |
|---|---|---|---|---|
| **(a) Revive the dual-pipeline path** | `apps/nexa_cloud_voice_app.py` / `nexa.realtime.gemini.runtime` | `BargeInController` (already exists, already singular, already the class the frozen constraint names) — only its VAD-source and transport change | Cleanest match to "exactly ONE `BargeInController`" as literally named in the frozen constraints; `_ResponseGenerationGuard`/manual turn-framing already solves the "already-in-flight audio" problem R0082 also had to solve | This path is PAUSED specifically because of a self-echo regression R0082 was arguably commissioned to fix — reviving it means re-litigating why it was paused, even though the failure mode (self-echo) is exactly what R0082 validated a fix for |
| **(b) Integrate into the accepted simple path** | `apps/nexa_cloud_voice_simple.py` / `nexa.realtime.gemini.simple_conversation` | Pipecat's own native interruption (`LLMContextAggregatorPair` + Gemini ack) — no `BargeInController` at all, by existing product decision | Smallest diff to the CURRENTLY ACCEPTED, real-hardware-passing baseline; `LiveKitOutputTransport`'s `InterruptionFrame`→`clear_queue()` (§2) slots in with zero new NeXa-owned interruption code | Does not literally have "a `BargeInController`" — satisfies "exactly ONE interruption authority" in spirit (Pipecat's own frame propagation) but not in the letter the frozen constraints use; would need explicit confirmation this satisfies intent |

**This report's recommendation is to surface the choice, not make it** — but notes the frozen-constraint wording ("Exactly ONE `BargeInController` / interruption authority") most literally favors (a), while the ACTUAL currently-accepted, real-hardware-validated production baseline is (b). Resolving this naming/intent tension is a product decision for the next session, explicitly deferred here.

---

## 12. Minimal file-level integration plan

### NEW files (would be created at implementation time, none created this round)

| File | Responsibility | Reason | Risk | Tests required |
|---|---|---|---|---|
| `src/nexa/realtime/livekit_transport.py` (name illustrative) | Thin construction wrapper around Pipecat's `LiveKitTransport` (room name/token minting, NeXa-specific `TransportParams`) | `LiveKitTransport` owns its own `rtc.Room` (§2) — some NeXa-specific glue (token minting matching R0082's `_make_token`, or NeXa's own auth) is needed regardless of §11.3's outcome | Medium — new dependency surface, must not leak LiveKit specifics above the transport boundary (§9) | Construction-only unit test (`--dry`-style, mirroring `nexa_cloud_voice_app.py --dry`) |
| `src/nexa/voice_hw/livekit_hardware_role.py` (name illustrative) | The hardware-side process: constructs `rtc.PlatformAudio` once, publishes the room's mic track, subscribes to the bot's assistant-audio track for playback | Must be a SEPARATE process/participant from the Pipecat/Gemini side (§7 invariant) — this is new production code, not a copy of R0082's research script (see RESEARCH files below) | High — this is the one new component directly responsible for the AEC-ownership invariant; any mistake here reintroduces self-echo | Real-hardware acceptance test mirroring R0082-H's own silent-series methodology, gate R0083-C |

### MODIFIED files (would change)

| File | Current responsibility | Proposed responsibility | Reason | Risk |
|---|---|---|---|---|
| `apps/nexa_cloud_voice_app.py` **or** `apps/nexa_cloud_voice_simple.py` (pick ONE per §11.3) | Thin wiring over `build_gemini_voice_runtime`/`CloudRealtimeConversationAdapter` | Same, plus a CLI flag selecting the LiveKit transport instead of `LocalAudioTransport` (mirroring the existing `--scheduled-aec-reference` opt-in pattern already in `nexa_cloud_voice_app.py`) | Smallest possible entry-point diff; keeps the old PyAudio path as the unconditional default (rollback-safe) | Low if additive/opt-in; the flag must default OFF |
| `nexa.realtime.gemini.runtime` (if (a)) **or** `nexa.realtime.gemini.simple_conversation` (if (b)) | Constructs `LocalAudioTransport` for `transport.input()`/`.output()` | Constructs the new LiveKit transport wrapper instead, when the new flag is set; pipeline SHAPE (VAD/bargein/bridge order) unchanged | This is the ONE swap-point identified by §1.1/§1.2's audit — everything downstream of `transport.input()` already works unmodified | Medium — must not change frame types the downstream `VADProcessor`/`BargeInController` expect (§6) |

### UNCHANGED but relied-upon files

`src/nexa/conversation/session.py` (`ConversationSession`), `src/nexa/realtime/router.py` (`ConversationRouter`), `src/nexa/realtime/provider.py` (`RealtimeVoiceProvider` interface), `src/nexa/realtime/gemini/service.py` (`GeminiLiveProvider`, if (a)) or the relevant slice of `simple_conversation.py` (if (b)), `src/nexa/voice/bargein.py`/`src/nexa/voice/interruption.py` (if (a)), `src/nexa/voice/aec.py` (`AecReferenceHealth`, if its `barge_in_safe` gate is reused — needs a WebRTC-AEC-equivalent health signal, an open design question, §15).

### RESEARCH files that must NOT be imported by production

`docs/research/r0082_livekit_webrtc_audio_poc/r0082f_live_vad_self_echo_poc.py` and every other R0082-* harness script — these deliberately reimplement production-equivalent VAD/ISM logic for OFFLINE/research measurement (per their own docstrings, audited exhaustively this session) and must never become an import target. Only DELIBERATELY-EXTRACTED, re-designed concepts may move into production — never a copy-paste of the harness, per the task's own explicit instruction (§10 of the task). `docs/research/m2_6_cloud_realtime_voice/*` similarly stays research/evidence-only, per `nexa_cloud_voice_app.py`'s own docstring ("it never calls the M2.6A research probe... which remains evidence/history only").

---

## 13. Staged R0083 gates

| Gate | Goal | Code allowed to change | Test | PASS condition | Rollback point |
|---|---|---|---|---|---|
| **R0083-A** (this report) | Architecture/current-runtime audit | None (docs only) | N/A | This report merged/accepted | Discard the report; no code touched |
| **R0083-B** | Decide §11.3 (a) vs (b); production transport adapter skeleton, NO Gemini change | New adapter file(s) only, behind an opt-in flag defaulting OFF | Construction-only (`--dry`-equivalent); `pytest` full suite green | Adapter constructs without touching mic/speaker/network when the flag is off; byte-identical old-path behavior when flag is off | Revert the new file(s); entry-point diff is additive-only |
| **R0083-C** | Real `PlatformAudio`/LiveKit mic+speaker plumbing, still NOT feeding Gemini | New hardware-role file + adapter wiring | Real-hardware silent-series test mirroring R0082-H methodology (0 false starts over a continuous session) | Same PASS bar as R0082-H itself, now measured through the PRODUCTION adapter, not the research harness | Flag OFF reverts to `LocalAudioTransport`; hardware-role file untouched by flag-off path |
| **R0083-D** | Production-equivalent VAD/interruption-authority integration (whichever §11.3 selects) | Transport-adjacent wiring only; §11.3(a): reuse `BargeInController` unmodified; §11.3(b): confirm `LiveKitOutputTransport`'s native `InterruptionFrame` handling suffices | Deliberate barge-in test mirroring R0082-G methodology (5/5 real stop, no auto-resume) | Same PASS bar as R0082-G, through the new transport | Flag OFF |
| **R0083-E** | Gemini Live audio provider connection through the new transport | `service.py`/`simple_conversation.py`'s transport construction call site only | End-to-end single-turn real conversation (manual, operator-observed) | One real assistant reply heard correctly, correctly routed to `ConversationSession` | Flag OFF |
| **R0083-F** | `ConversationSession` lifecycle integration confirmation | None expected (already proven clean, §4) — verification only | Multi-turn real conversation, interruption mid-response | `record_external_exchange` called correctly on both clean completion and confirmed interruption | Flag OFF |
| **R0083-G** | End-to-end regression/acceptance | None (test-only) | Full R0081 canonical acceptance suite (§14) | All items in §14 PASS | Flag OFF; §11.3's rejected option remains available as a documented fallback |

Every gate keeps the existing `LocalAudioTransport`/PyAudio/XVF3800-hardware-AEC path as the unconditional default, selected by the ABSENCE of a new opt-in flag — mirroring the exact pattern `apps/nexa_cloud_voice_app.py`'s existing `--scheduled-aec-reference` flag already establishes in this repository. This is the rollback strategy for every gate: flip the flag off, and the byte-for-byte previously-accepted behavior returns.

---

## 14. Bridge to final R0081 acceptance — explicitly NOT marked PASS from R0082 evidence

| R0081 canonical acceptance item | Status after R0082 | What R0083 must still produce |
|---|---|---|
| ≥10 normal REAL NeXa conversational bot responses, 0 false interruptions/self-conversation | R0082-H validated the AUDIO/AEC/VAD candidate only (fixed stimulus WAV, no real Gemini reply, no `ConversationSession`) — **NOT satisfied yet** | Real conversational turns generated through the integrated stack (gate R0083-E/F) |
| ≥5 deliberate human barge-ins, 5/5 stop, no automatic resume | R0082-G validated the SAME audio candidate's barge-in detection only, same caveat | Real barge-in against a live, generating Gemini reply through the integrated stack (gate R0083-D/E) |
| ORBIT-47 dynamic same-session recall | Unrelated to R0082/R0083's audio scope — already independently PASS per R0080 (Core recall, unaffected by transport choice) | Re-confirm once routed through whichever transport §11.3 selects (should be a no-op, since `CloudContextSnapshot`/recall sits above the transport boundary, §4/§5) |
| PL ≥2, EN ≥2, language switch | Untouched by R0082; language routing sits above the audio transport (confirmed by §4/§5's boundary) | Re-confirm through the new transport, gate R0083-G |
| Latency sanity | Not measured by R0082 (R0082 measured drain/gap timing, not end-to-end conversational latency) | New measurement required, gate R0083-G |
| `ConversationSession` canonicality | Proven correct at the CODE level for both existing cloud paths (§4) — the new transport does not change this boundary | Confirm empirically once real turns flow through it, gate R0083-F |
| Typed/voice semantic parity where applicable | Untouched by this audit | Out of R0083's scope unless a specific gap is found during R0083-F/G |

**R0082 validates the audio/AEC/VAD candidate only. None of the above may be marked PASS from R0082 evidence alone — this table exists precisely so that boundary is never blurred.**

---

## 15. Risks and open questions (explicitly flagged, not guessed)

1. **§11.3 is unresolved** — which of the two current production paths is the integration target. This is the single most consequential open decision.
2. `router.on_interruption()`/`CloudTurnAccumulator`'s idempotency under the two-call-site `InterruptionFrame` multiplicity (§1.1, §3) was not independently re-verified this round — only inferred from an adjacent docstring's "at most one commit" guarantee for `commit_cloud_turn`, a related but distinct method.
3. Whether Gemini Live's manual-turn-framing usage in §1.1 is backed by an explicit `automatic_activity_detection=False`-equivalent config, or is purely an emergent property of never calling the auto-VAD API surface, was not pinned to one literal source line (zero grep hits for `automatic_activity_detection`/`realtime_input_config` in `service.py`).
4. Whether `AecReferenceHealth.barge_in_safe`'s gating concept (currently keyed to the XVF3800's hardware AEC being confirmed running) has a meaningful WebRTC-AEC equivalent, or needs a new health signal entirely, is undesigned.
5. Token minting / room naming for a production LiveKit deployment (vs. R0082's fixed dev-server, hardcoded API key/secret research pattern) is completely undesigned — production would need real LiveKit credentials/infrastructure, not audited or provisioned this round.
6. Whether Pipecat's `LiveKitInputTransport` resampler fully replaces the need for a second, harness-style observation-copy resampler (§6) is stated as a strong likelihood, not proven — should be the first empirical check at gate R0083-C.
7. The R0064-R0070 `scheduled_aec_reference`/`nexa.voice.acoustic` track (pre-existing uncommitted working-tree files, §1.3) is a second, parallel AEC investigation whose relationship to R0082/R0083 was not audited — flagged as a possible source of confusion or duplicated effort for whoever picks up R0083-B, not resolved here.
8. This audit did not read `nexa.voice.runtime` (the LOCAL, non-cloud voice pipeline) in depth — Fork A explicitly scoped it out as out-of-directive. If §11.3 ever considers unifying local and cloud transports, that file needs its own pass.

---

## 16. Rollback strategy

Every proposed change (§12, §13) is additive and flag-gated: the existing `LocalAudioTransport`/PyAudio/XVF3800-hardware-AEC path remains the default, unconditional behavior at every gate until R0083-G's full acceptance passes. Rollback at any point is "do not set the new flag" — no code needs to be reverted mid-sequence for the existing, already-accepted production behavior to continue working exactly as it does today. This mirrors the exact pattern already established by `apps/nexa_cloud_voice_app.py`'s own `--scheduled-aec-reference` flag and `ADR-0004` Amendment 2's own "paused, not deleted" precedent for the dual-pipeline runtime.

---

## 17. Working tree at the start of this task (unchanged by this task)

```
$ git status --short
 M apps/nexa_cloud_voice_app.py
 M docs/CURRENT_STATE.md
 M docs/ROADMAP.md
 M docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py
 M src/nexa/voice/aec_gain.py
 M src/nexa/voice/config.py
?? docs/architecture/M2_6_ACOUSTIC_FRONTEND.md
?? docs/reports/R0064_gain_ab_experiment_execution_20260914.md
?? docs/reports/R0065_gain_ab_offset_aware_correlation_analysis_20260914.md
?? docs/reports/R0066_webrtc_aec3_feasibility_assessment_20260914.md
?? docs/reports/R0067_offline_aec3_first_processing_experiment_20260915.md
?? docs/reports/R0068_acoustic_render_scheduling_fix_20260915.md
?? docs/reports/R0069_scheduled_reference_hardware_acceptance_20260916.md
?? docs/reports/R0070_native_reference_continuity_repair_20260916.md
?? docs/research/m2_6_cloud_realtime_voice/r0065_offset_aware_correlation.py
?? docs/research/m2_6_cloud_realtime_voice/r0066_pipewire_aec3_module_echo_cancel.conf.example
?? docs/research/m2_6_cloud_realtime_voice/r0067_offline_aec3_gstreamer_runner.py
?? docs/research/m2_6_cloud_realtime_voice/r0081_aec_captures/
?? docs/research/r0082_livekit_webrtc_audio_poc/r0082_aec_captures/
?? docs/research/r0082_livekit_webrtc_audio_poc/r0082c_aec_captures/
?? docs/research/r0082_livekit_webrtc_audio_poc/r0082d_aec_captures/
?? docs/research/r0082_livekit_webrtc_audio_poc/r0082e_16k_captures/
?? docs/research/r0082_livekit_webrtc_audio_poc/r0082f_live_captures/ (many dated capture files, R0082-F/G/H)
?? docs/testing/R0068_HARDWARE_ACCEPTANCE.md
?? src/nexa/voice/acoustic/
?? tests/test_acoustic_scheduled_reference.py
?? tests/test_r0070_operator_cue.py
```

None of this was staged, reverted, checked out, or otherwise disturbed by this task. This task's only file-system effect is the creation of this report.

---

## Final checklist

* current production audio path identified: **yes**
* current ConversationSession boundary identified: **yes**
* current interruption authorities fully enumerated: **yes** (§3), with two explicitly-flagged open verification items (§15.2, §15.3)
* installed Pipecat LiveKit capability verified: **yes** (§2, direct installed-source read)
* single PlatformAudio AEC ownership preserved in proposed design: **yes** (§7, as a hard constraint on the file-level plan)
* single BargeInController authority preserved: **open — not yet resolved which mechanism is "the one authority"** (§11.3); neither current production path has BOTH a BargeInController AND relies on it exclusively without a second call site (§1.1 has one but also sees Gemini's ack; §1.2 has none by design)
* LiveKit limited to transport/audio infrastructure: **yes** (§9)
* Gemini remains replaceable provider only: **yes** (§5)
* minimal file-level integration plan produced: **yes** (§12)
* staged R0083 gates defined: **yes** (§13)
* full R0081 acceptance remains open: **yes** (§14)
* production code changed: **no**
* hardware executed: **no**
* report path: `docs/reports/R0083_conversational_integration_design_20260919.md`
* commit hash: (recorded after this report is committed — see the session's final message)
* pushed: **no**

**STOP before implementation**, per instruction.
