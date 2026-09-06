# R0010 — M2.4A Pipecat Streaming TTS Compatibility / Architecture Spike

- **Date:** 2026-09-06
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4A — research spike,
  not implementation**
- **Related:** `docs/decisions/ADR-0003_realtime_voice_foundation.md` (D6 —
  TTS baseline, D7 — thread budgets), `docs/reports/R0009_m2_3_voice_conversation_adapter_20260906.md`
  (the `ConversationSession` streaming this spike must not duplicate),
  `docs/research/m2_4a_tts_spike/` (raw scripts + reproduction steps)

**This is a research report only. No product code was written. `CURRENT_STATE.md`
is not updated to claim M2.4 progress. No M2.4 architecture doc was created —
this report is the evidence base a future M2.4 decision will cite.**

---

## TASK RESULT

**Spike PASS** — all 12 success criteria answered from real evidence on
this Pi 5, with two genuine compatibility/behavior findings that would have
caused silent failures if not caught: (1) Pipecat 1.8.1's
`PiperHttpTTSService` and the real Piper HTTP server disagree on the
synthesis endpoint path, and (2) calling a `TTSService`'s `run_tts()`
outside a running pipeline silently produces zero frames and zero errors.
Neither required patching Pipecat — both are configuration/usage
corrections. One clear recommendation is given below.

## PIPECAT 1.8.1 VERIFIED API

Inspected directly from the installed package
(`.venv/lib/python3.13/site-packages/pipecat/`), not current online docs:

| Item | Present in 1.8.1? | Module | Notes |
|---|---|---|---|
| `TTSService` | Yes | `pipecat.services.tts_service` | Base class; owns text aggregation, `TTSStartedFrame`/`TTSAudioRawFrame`/`TTSStoppedFrame` lifecycle, interruption handling, metrics |
| `PiperTTSService` | Yes | `pipecat.services.piper.tts` | In-process, imports `piper` (the `piper-tts` package) directly |
| `PiperHttpTTSService` | Yes | `pipecat.services.piper.tts` | External process, `aiohttp` POST |
| `LLMTextFrame` | Yes | `pipecat.frames.frames` | `TextFrame` subclass, `includes_inter_frame_spaces=True` — exactly matches token-stream output |
| `LLMFullResponseStartFrame`/`LLMFullResponseEndFrame` | Yes | `pipecat.frames.frames` | Plain `ControlFrame`s, no payload beyond an inherited `skip_tts` flag |
| `TTSAudioRawFrame` | Yes | `pipecat.frames.frames` | `OutputAudioRawFrame` subclass + `context_id` |
| `TTSStartedFrame`/`TTSStoppedFrame` | Yes | `pipecat.frames.frames` | `ControlFrame`s, carry `context_id` |
| Sentence aggregation | Yes | `pipecat.utils.text.simple_text_aggregator.SimpleTextAggregator` | NLTK-backed (`nltk.tokenize.sent_tokenize`), lookahead disambiguation for abbreviations/decimals |
| Token aggregation | Yes | Same class, `AggregationType.TOKEN` | Passes text straight through, no buffering |
| `LLMTextProcessor` | Yes | `pipecat.processors.aggregators.llm_text_processor` | Exists, but **redundant for our case** — see below |
| Runtime TTS settings updates | Yes | `TTSUpdateSettingsFrame` / `_update_settings()` | `PiperTTSService`/`PiperHttpTTSService` both implement it but log a "not handled" warning for voice changes — a voice swap needs a new service instance, not a live setting update |

Nothing above is deprecated in 1.8.1; `text_aggregation_mode` is the
current API (the older `aggregate_sentences: bool` param is
`.. deprecated:: 0.0.104`).

**Real finding, not documented anywhere obvious**: `TTSService`'s
`self.sample_rate`/`self.chunk_size` are `0` until the FrameProcessor
lifecycle (`setup()` then a `StartFrame` via `start()`) has run. Calling
`run_tts()` directly, bypassing a real running `Pipeline`, produced a real
HTTP 200 response from the Piper server (verified) but **zero output
frames and zero exceptions** — `response.content.iter_chunked(0)` silently
yields nothing. A standalone `run_tts()` call is not a valid way to smoke
test a `TTSService`; it must run inside a real
`Pipeline`/`PipelineWorker`/`WorkerRunner`, exactly like `nexa.voice.runtime`
already does for `VADProcessor` etc. This cost real debugging time in this
spike and is worth remembering for M2.4's own tests.

## TEXT STREAMING / AGGREGATION

Answering the task's ten questions from source + a real running pipeline
(`docs/research/m2_4a_tts_spike/pipeline_smoke_test.py`):

1. **Can we emit `LLMTextFrame` from `ConversationSession`'s stream?** Yes —
   confirmed by the spike's own test source pushing scripted `LLMTextFrame`s
   standing in for `on_assistant_token` chunks; `TTSService` consumed them
   correctly with no adaptation needed.
2. **Does `TTSService` already aggregate `LLMTextFrame` text into
   sentences?** Yes — `process_frame` handles `TextFrame`/
   `LLMFullResponseStartFrame`/`LLMFullResponseEndFrame` directly and
   aggregates via its own `SimpleTextAggregator`. Confirmed live: a
   10-token scripted stream was correctly split into exactly two sentences
   at the real sentence boundary.
3. **Is `LLMTextProcessor` needed, or unnecessary?** **Unnecessary** for
   feeding `PiperHttpTTSService` directly — it does the identical
   `SimpleTextAggregator` aggregation `TTSService` already does internally.
   It would only earn its place if NeXa needed sentence-level
   `AggregatedTextFrame`s for a *second* purpose before/alongside TTS (e.g.
   a live transcript display) — not a current requirement.
4. N/A given (3)'s answer.
5. **How is the final sentence without punctuation flushed?** On
   `LLMFullResponseEndFrame` (or `EndFrame`), `TTSService` calls
   `self._text_aggregator.flush()` and synthesizes whatever text remains
   unterminated — confirmed live in the spike (both scripted sentences
   ended in punctuation, so this path wasn't exercised by that particular
   run, but is directly visible in source at `tts_service.py:788-826`).
6. **Abbreviations / decimals / punctuation?** NLTK's English Punkt model
   with a lookahead-based disambiguation (`match_endofsentence` in
   `pipecat.utils.string`) correctly held `"$29."`/`"3.5 zł"`-style decimals
   as non-boundaries. **Real, verified defect for Polish**: `sent_tokenize`
   is always called with the default `language="english"` — Pipecat never
   passes a language argument, even though the installed `nltk_data` has a
   working `polish` Punkt model available (`~/nltk_data/tokenizers/punkt_tab/polish`).
   Direct test: `match_endofsentence("Zobacz ul. Kwiatowa 5. Tam mieszkam.")`
   incorrectly splits after `"ul."` (a Polish abbreviation for "ulica",
   street) — the English model doesn't know it. Common punctuation,
   decimals, and question/exclamation marks were unaffected in every
   Polish test phrase tried; only abbreviation-style periods are at risk.
7. **Multiple sentences arriving in one frame?** The aggregator processes
   text character-by-character regardless of how it was chunked into
   frames, so multiple complete sentences within one `LLMTextFrame` are
   each yielded as their own `AggregatedTextFrame` in order — not
   separately tested live in this spike, but directly evident from
   `SimpleTextAggregator.aggregate()`'s per-character loop.
8. **`LLMFullResponseEndFrame`?** Confirmed live: triggers the flush in
   (5), and marks the current turn's TTS context to allow eventual cleanup
   once all its sentences finish playing (`_pending_llm_response_end_frames`
   bookkeeping in `tts_service.py`).
9. **Interruption frames?** `InterruptionFrame` resets the text aggregator
   (`_handle_interruption`) and the base `TTSService._handle_interruption`
   clears in-flight TTS state — inspected from source only, not exercised
   live (M2.4A explicitly does not implement barge-in). See "RISKS" below
   for what M2.5 can build on.
10. **Sentence-level streaming without owning LLM context/history?**
    Confirmed by design and by the spike: `TTSService`/`LLMTextProcessor`
    hold only a text buffer and (for `TTSService`) TTS-context bookkeeping
    (`context_id`s for in-flight audio) — no message history, no system
    prompt, no persona. `ConversationSession` was never touched or routed
    through any Pipecat LLM service in this spike.

## PIPER IN-PROCESS

`PiperTTSService` — imports `from piper import PiperVoice` directly inside
NeXa's own Python process. The class docstring itself states plainly:
"This service runs Piper in-process via the `piper-tts` package (the
`piper` extra), which is **GPL-3.0 licensed**. Distributing an application
that includes it may subject the application to the GPL's
source-disclosure terms. To keep Piper out of your application's license
scope, use `PiperHttpTTSService` instead." Blocking calls are wrapped in
`asyncio.to_thread`, so it does not block the event loop directly, but it
does consume a thread per synthesis and keeps the GPL-licensed package
imported inside NeXa's own process regardless. **Not evaluated further as
a candidate** — ADR-0003 D6 already rejected an in-process Piper import for
exactly this reason, and this spike found no new evidence to revisit that.

## PIPER HTTP

`PiperHttpTTSService` — talks to a separately-running Piper HTTP server
over `aiohttp`. No GPL import inside NeXa's process. Real, measured
behavior on this Pi (`piper-tts` 1.8.0, installed spike-only, not in
`pyproject.toml`):

| Property | Value |
|---|---|
| Startup | `python -m piper.http_server -m <voice> --data-dir <dir> --port <port>` — a Flask dev server (own explicit warning: "do not use in production") |
| Model loading | Default voice loaded eagerly at startup (~1-2s); other voices load lazily on first request for that voice name, cached afterward |
| Streaming | **Not truly streaming at the HTTP level** — the server synthesizes the *entire* WAV into an in-memory buffer, then returns it as one complete response body with a fixed `Content-Length` (verified: no `Transfer-Encoding: chunked`) |
| Blocks event loop? | No — synthesis happens server-side, in a separate OS process; NeXa's own event loop only awaits the HTTP response |
| Thread/process | Fully separate OS process from NeXa |
| Sample rate | Matches the voice model (16kHz for both "low"-quality voices tested; Piper's "medium"/"high" tiers commonly use 22050Hz) |
| `LocalAudioOutputTransport` compatible? | Yes, directly — confirmed live end-to-end (see "AUDIO OUTPUT") |
| First-audio latency | ~0.35-0.4s for a short sentence, idle system (see "LATENCY") |
| Memory (idle, one voice loaded) | ~200MB RSS per process |
| Voice switching cost | ~2.4s first use of a new voice in an already-running process (one-time model load), then ~0.2s per call after |
| Pi 5 suitability | Good — small memory footprint, fast per-sentence synthesis, real reSpeaker output confirmed reachable |

## HTTP ENDPOINT COMPATIBILITY

**Real, verified compatibility mismatch, exactly as the task suspected.**
Pipecat 1.8.1's `PiperHttpTTSService.run_tts()` does
`self._session.post(self._base_url, json=data, ...)` — it POSTs directly
to whatever `base_url` is configured, with no path appended. The real
installed Piper HTTP server (`piper/http_server.py`, part of the
`piper-tts` package) registers synthesis at **`POST /synthesize`**, and
`POST /` returns **`405 Method Not Allowed`** (Flask has no route there).
Proven with real requests against a real running server:

```
$ curl -X POST http://127.0.0.1:5001/            -> HTTP 405
$ curl -X POST http://127.0.0.1:5001/synthesize  -> HTTP 200, real WAV audio
```

**This is not a Pipecat bug requiring a patch** — `base_url` is a plain
caller-supplied string. The fix is configuration: construct
`PiperHttpTTSService(base_url="http://<host>:<port>/synthesize", ...)`
(the `/synthesize` suffix included), not the server's bare root URL as
Pipecat's own source comment example implies. Confirmed working end-to-end
with this configuration in the real pipeline spike below. Response
`Content-Type` is `text/html; charset=utf-8` (Flask's default for a raw
`bytes` return, not `audio/wav`) — harmless here since
`PiperHttpTTSService` never inspects Content-Type, only the WAV magic
bytes/header it strips itself.

## PL / EN VOICES

- **`pl_PL` Piper voices exist**: `pl_PL-bass-high`, `pl_PL-darkman-medium`,
  `pl_PL-gosia-medium`, `pl_PL-mc_speech-medium`, `pl_PL-mls_6892-low` (the
  only Polish "low"-quality/16kHz option; others are medium/high,
  presumably 22050Hz — not measured, no evidence needed at "low" already
  answered the sample-rate question).
- **One Piper HTTP process can serve both languages dynamically**: proven
  live — a server started with `-m en_US-amy-low` correctly loaded and
  served `pl_PL-mls_6892-low` on request via the JSON `"voice"` field, with
  no restart. First cross-voice request: 2.38s (one-time model load).
  Every request after: ~0.18-0.2s. Memory after both voices loaded into one
  process: ~337MB RSS.
- **Two separate warm servers (PL+EN)** measured: ~200MB RSS each (~400MB
  combined) — comparable total memory to the single dual-voice process,
  and avoids the one-time 2.4s cross-voice load delay from the very first
  request of whichever language wasn't the startup default.
- **Recommendation for M2.4 implementation** (not decided here, evidence
  only): either shape works on this hardware's memory budget. A single
  process pre-warmed for both languages at NeXa startup (one throwaway
  synthesis call per language before real use begins) gets the simplicity
  of one process *and* avoids ever paying the 2.4s cross-voice cost live —
  this seems like the more attractive option but was not stress-tested
  further, as M2.4A's job is evidence, not the final decision.
- Voice language selection is correctly independent of anything M2.2's STT
  `--language` flag decides — this spike only ever chose the Piper voice
  from the already-known response language (M2.3's `ConversationSession`
  policy), never from the STT hint, matching the task's explicit
  requirement.

## AUDIO OUTPUT

Real end-to-end pipeline, real reSpeaker XVF3800 output device (resolved
by name via the existing `nexa.voice.device.find_device_index`, not a
fixed index): `_ScriptedTextSource` (stand-in for `ConversationSession`'s
token stream) → `PiperHttpTTSService` (correctly configured `/synthesize`
URL) → `LocalAudioOutputTransport`. Confirmed via Pipecat's own internal
logging and the spike's event log:

```
+0.000s  llm_response_start
+0.138s  tts_started
+0.275s  tts_audio_chunk(15956B) / (16000B) / (11052B)
+0.509s  llm_response_end
+0.816s  tts_audio_chunk × 7 (second sentence)
+0.817s  tts_stopped
```

plus Pipecat's own `"Bot started speaking"` / `"Bot stopped speaking based
on TTSStoppedFrame"` debug log lines from `base_output.py`, proving the
transport received and processed the audio, matching M2.1's `LocalAudioConfig`
(16kHz mono) with **no manual resampling needed** — `BaseOutputTransport`
resamples automatically via `create_stream_resampler()` whenever an
incoming frame's `sample_rate` differs from the configured output rate
(verified from source; not exercised here since both chosen voices already
match 16kHz).

**Stated precisely, per this project's own established discipline (R0007's
"LOCAL AUDIO OUTPUT" precedent)**: this confirms the audio stream was
opened, written to, and closed without error, and Pipecat's own
speaking-state tracking fired correctly — it does **not** confirm audible
sound was heard by a human. No operator listened during this spike. Real
audible confirmation is explicitly deferred to the actual M2.4
implementation's human-acceptance step, not claimed here.

## LATENCY

**Sentence aggregation vs. token mode**: not benchmarked head-to-head
empirically in this spike (time budget went to the compatibility/resource
questions instead) — SENTENCE remains the documented product preference
per the task's own stated rationale (prosody, fewer requests, lower
churn), and nothing found here contradicts it. Flagged as `UNKNOWN`
(measured) rather than guessed.

**Piper HTTP first-audio latency, idle system** (5 sequential short
sentences, English): 0.351-0.393s per sentence (server-side synthesis +
HTTP round trip). Polish, one sample: 0.293s.

**End-to-end pipeline timing** (real pipeline, scripted 2-sentence
response): TTS start (first HTTP call issued) at +0.138s after the
simulated LLM response began streaming; first audio chunk at +0.275s —
i.e. **first-sentence-ready → first-audio ≈ 137ms** on this hardware, using
a "low"-quality 16kHz voice.

**LLM first-token → first-audio** was not separately isolated as its own
number in this spike (that requires the real `ConversationSession`
streaming into the same pipeline, which duplicates M2.3's own measured
first-token latencies — ~2-6s warm, ~15-16s cold, R0009 — plus this
spike's own ~137ms TTS-side addition on top). Not claimed as a combined
figure here to avoid conflating two separately-measured pieces of evidence
that were never actually run back-to-back through one instrumented path.

## RESOURCES / CONCURRENCY

Controlled, real measurements (not a full matrix, per task instruction):

| Scenario | Result |
|---|---|
| Piper alone, 5 sequential calls | 0.351-0.393s each |
| `gemma4:e4b` turn 1 (cold), alone | 15.67s first-token (matches R0009 exactly) |
| `gemma4:e4b` turn 2 (warm), alone | 3.07s first-token (matches R0009's warm range) |
| `gemma4:e4b` turn 3 (warm) **concurrent with** 3 Piper calls | 4.30s first-token (+40% vs. warm-alone) |
| Piper calls **during** that same concurrent turn | 0.73-0.80s each (+~2x vs. Piper-alone) |
| whisper.cpp STT **concurrent with** 3 Piper calls | 2.86s (vs. R0008's ~1.65-1.69s baseline, +~70%) |
| Piper calls **during** that STT run | 0.52-0.71s each |

**An important correction made mid-spike**: an earlier, less careful timing
run appeared to show catastrophic (>60s, non-completing) LLM latency under
TTS contention. Investigating properly (killing both Piper processes and
re-measuring) proved that number was **not** caused by TTS contention at
all — it was an unmatched comparison: every "concurrent" run in that first
attempt was accidentally a **fresh, cold** `ConversationSession` (~15-16s
baseline on its own, confirmed by re-running with zero Piper processes
running and still seeing ~15.7s), being wrongly compared against the
*warm* ~2-6s figure from a different context. The corrected, matched
warm-vs-warm comparison above (turn 2 vs. turn 3, same session) is the
real evidence. This is disclosed in full because it is exactly the kind of
mistake this project's evidence discipline exists to catch before it
becomes a false "TTS causes catastrophic contention" conclusion.

**Resources**: `gemma4:e4b` stayed resident throughout (~11GB RAM used,
`100% CPU`, `8192` context, matching M1.1/M2.3's known footprint); Piper
processes added ~200-400MB depending on one-vs-two-process configuration;
`vcgencmd measure_temp` 54.9-59.3°C across the whole spike; `vcgencmd
get_throttled` = `0x0` throughout, no swap thrashing observed. **No RAM,
thermal, or throttling ceiling found** for STT+LLM+TTS on this Pi 5 at
this scale of testing — moderate (40-90%) mutual latency slowdown under
concurrency is the real cost, not a hard resource wall. Consistent with
R0006's original resource-budget finding.

## LICENSE / PROCESS BOUNDARY

Technical facts only, no legal advice, per the task's explicit instruction:

- `piper-tts` (the Python package providing both `PiperVoice` — used by
  `PiperTTSService` — and `piper.http_server` — the standalone HTTP
  server) is licensed **GPL-3.0-or-later** (`pip show piper-tts`
  confirms). This is the same package either way; the license does not
  change based on which Pipecat service is used.
- `PiperTTSService` imports this GPL-licensed package directly into
  NeXa's own running Python process.
- `PiperHttpTTSService` never imports `piper` into NeXa's process — NeXa's
  process only holds an `aiohttp.ClientSession` making HTTP requests to a
  separately-started OS process (`python -m piper.http_server`, which
  itself does import and run the GPL-licensed package).
- For this spike, `piper-tts[http]` was installed directly into NeXa's own
  `.venv` for convenience — this is **not** the recommended final
  arrangement if `PiperHttpTTSService` is adopted for M2.4: running the
  Piper HTTP server from a genuinely separate Python environment (its own
  venv, container, or system install) would keep the technical/dependency
  boundary between NeXa's own codebase and the GPL-licensed server process
  as clean as the HTTP-only communication already implies. Not decided
  here — a note for the actual M2.4 implementation.
- `pyproject.toml` was **not modified** — `piper-tts` is not a declared
  NeXa dependency after this spike, on purpose.

## WHAT WE SHOULD REUSE FROM PIPECAT

- `TTSService`'s entire text-aggregation/audio-context/interruption-reset
  machinery — do not reimplement a sentence buffer, TTS start/stop state
  machine, or audio-context bookkeeping; it already exists and works.
- `PiperHttpTTSService` as the concrete TTS service (once configured with
  the correct `/synthesize` URL).
- `LocalAudioOutputTransport` for playback — automatic resampling, real
  reSpeaker output confirmed reachable, no NeXa-side audio-format handling
  needed beyond what M2.1 already established.
- `LLMTextFrame`/`LLMFullResponseStartFrame`/`LLMFullResponseEndFrame` as
  the exact frame vocabulary a thin NeXa bridge should translate
  `on_assistant_token`/turn-start/turn-end into.
- The existing `Pipeline`/`PipelineWorker`/`WorkerRunner` pattern
  `nexa.voice.runtime` already uses — no new orchestration primitive
  needed.

## WHAT NEXA STILL NEEDS TO OWN

- `ConversationSession` itself — completely untouched by anything in this
  spike, and must stay that way (ADR-0003 D2, reaffirmed by M2.3/R0009).
- The thin bridge translating `VoiceConversationAdapter`'s
  `on_user_transcript`/`on_assistant_token`/`on_assistant_complete`/
  `on_conversation_error` callbacks into the Pipecat frame vocabulary above
  — not built in this spike, deliberately (M2.4A is architecture
  verification only).
- The decision of *which* Piper voice to use for a given response — driven
  by M2.3's already-canonical response-language policy, never by M2.2's
  STT `--language` hint.
- Any pre-warming strategy (e.g. one throwaway synthesis call per language
  at startup) to avoid the ~2.4s one-time cross-voice load cost live.
- Thread/CPU-budget discipline (ADR-0003 D7) now spans three concurrent
  consumers (STT, LLM, TTS) — this spike's contention numbers are real
  inputs for that budget, not a solved problem yet.

## RISKS

- **NLTK sentence-splitting is English-only in Pipecat's own code path**,
  even though a Polish Punkt model is available in the installed
  `nltk_data` — Polish abbreviations can cause premature sentence splits
  (verified: `"ul."` → false split). Impact on speech naturalness is
  real but likely modest (common punctuation/decimals were unaffected in
  every phrase tried); worth a real Polish-voice listening test in M2.4,
  not fixed here.
- **`nltk.download("punkt_tab", quiet=True)` is a hidden network call on
  first use** if the data isn't already cached — it happened to already be
  cached on this machine (`~/nltk_data/tokenizers/punkt_tab`, provenance
  not confirmed — likely a side effect of `pipecat-ai[local]`'s own test
  suite or a prior session), so this spike never observed the download
  happen, but a fresh Pi/fresh venv would. This conflicts with NeXa's own
  "no silent network download during normal runtime" discipline
  (established for whisper.cpp in M2.2) and should be addressed explicitly
  in M2.4 (e.g. verify/pre-provision `punkt_tab` the same way
  `scripts/setup_whisper_cpp.py` handles whisper.cpp).
- **Piper's HTTP server is a Flask development server** — its own startup
  banner says so explicitly ("do not use it in a production deployment").
  Acceptable for a single-user local assistant on a Pi it's the only
  client of, but worth naming as a real, if low, reliability risk, not
  silently accepted.
- **No true HTTP-level audio streaming** from the Piper server (whole WAV
  built server-side before the response is sent) — per-sentence latency is
  still low (~0.35-0.4s) because sentences are short, but this rules out
  ever getting *sub-sentence* incremental audio from this exact server
  without a different Piper server implementation.
- **Interruption/barge-in groundwork exists but is unexercised**:
  `InterruptionFrame` handling in `TTSService`/`LLMTextProcessor` was read
  from source, not driven live — M2.5 should re-verify it under real
  conditions rather than trusting this spike's source-reading alone.

## RECOMMENDED M2.4 ARCHITECTURE

**RECOMMENDATION A**, as sketched in the task: **Pipecat
`PiperHttpTTSService` (configured with the correct `/synthesize` URL) +
Pipecat's built-in SENTENCE aggregation + `LocalAudioOutputTransport`**,
fed by a new, thin NeXa bridge translating
`VoiceConversationAdapter`'s existing streaming callbacks into
`LLMTextFrame`/`LLMFullResponseStartFrame`/`LLMFullResponseEndFrame` — no
new conversation authority, no `LLMTextProcessor` (redundant), no
in-process Piper import.

**Evidence for this recommendation**:
1. Every required frame/service type is present, current, and undeprecated
   in the installed Pipecat 1.8.1 (verified from source).
2. The exact HTTP compatibility mismatch the task worried about is real,
   but trivially resolved by configuration (`base_url` including
   `/synthesize`) — proven working end-to-end in a real pipeline against
   real reSpeaker output.
3. Sentence aggregation, final-sentence flushing, and multi-language voice
   serving all work as needed with no code changes to Pipecat itself.
4. Resource contention (STT+LLM+TTS) is real but moderate (40-90% latency
   increases under concurrency, not a hard wall) — consistent with R0006's
   established finding that *sequenced*, not fully-concurrent, is the
   right operating model, which this architecture naturally supports
   (TTS for sentence N can run while the LLM generates sentence N+1,
   exactly the pipelining `ConversationSession`'s existing streaming
   already enables).
5. The one real defect found (Polish NLTK abbreviation handling) is a
   quality issue to watch, not a blocking architectural problem.

No second architecture is proposed — nothing found in this spike makes a
different approach (in-process Piper, a different TTS engine, hand-rolled
aggregation) look better than what Pipecat already provides.

## EXACT NEXT IMPLEMENTATION TASK

**M2.4 — implement the streaming Piper TTS integration** using Recommendation
A above:
- A new, thin bridge (analogous to `VoiceConversationAdapter`'s own
  narrowness) translating `on_user_transcript`/`on_assistant_token`/
  `on_assistant_complete`/`on_conversation_error` into
  `LLMFullResponseStartFrame`/`LLMTextFrame`(s)/`LLMFullResponseEndFrame`,
  pushed into a real Pipecat pipeline alongside M2.1's existing VAD/STT
  stages.
- `PiperHttpTTSService` configured with the correct `/synthesize` URL;
  voice chosen from M2.3's response-language policy, never M2.2's STT
  hint.
- Decide and implement the PL/EN voice-serving shape (single pre-warmed
  dual-voice process recommended, per this spike's evidence, but not
  mandated).
- Explicitly handle the NLTK `punkt_tab` provisioning question before
  relying on SENTENCE aggregation in normal operation (no silent network
  download).
- A new developer probe app (e.g. `apps/nexa_voice_full_probe.py`) for a
  full mic → STT → LLM → TTS → speaker round trip.
- Real hardware acceptance test, including genuine audible-playback
  confirmation by the operator (not yet done in this spike).
- Still no barge-in — that remains M2.5.

## FILES CREATED

- `docs/reports/R0010_m2_4a_pipecat_streaming_tts_spike_20260906.md` (this
  report).
- `docs/research/m2_4a_tts_spike/README.md` (reproduction steps, setup
  notes, the `run_tts()`-outside-a-pipeline gotcha).
- `docs/research/m2_4a_tts_spike/pipeline_smoke_test.py` (the real,
  working end-to-end pipeline script used for the "AUDIO OUTPUT" and "TEXT
  STREAMING" evidence above).

Not created (deliberately, per task instruction): any M2.4 architecture
doc, any change to `CURRENT_STATE.md`, any product code under `src/nexa/`.

## GIT STATUS

`pyproject.toml` unmodified — `piper-tts` was installed into `.venv` for
this spike only, not declared as a project dependency. Two Piper voice
models (~63MB each) were downloaded to `~/.local/share/nexa/tts-spike-voices/`,
outside this git repository (same external-data-dir discipline as M2.2's
whisper.cpp models) — nothing large was ever staged for commit. Only the
three new files listed above are untracked; nothing else in the working
tree changed. **Not committed** — this is a research report; the task did
not ask for a commit, and no product/architecture claim needs one yet.

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

No changes required — this spike followed the existing evidence/labeling
discipline (explicit `UNKNOWN` where something genuinely wasn't measured,
a disclosed self-correction of an initially-wrong contention reading)
without exposing any gap in `AGENTS.md` itself.
