# R0012 — M2.4B: Natural Speech Flow / Streaming Pacing — research & design

- **Date:** 2026-09-06
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B — research + design only**
- **Related:** `docs/reports/R0011_m2_4_streaming_piper_http_tts_integration_20260906.md`
  (the accepted, frozen M2.4 baseline this builds on — commit
  `36ec1e4dbbc61613b72717f913fd9efa690525cb`),
  `docs/architecture/M2_4_STREAMING_TTS_ARCHITECTURE.md`,
  `docs/reports/R0010_m2_4a_pipecat_streaming_tts_spike_20260906.md`
  (the Pipecat-1.8.1 spike whose findings this extends),
  `docs/decisions/ADR-0003_realtime_voice_foundation.md` (D6 TTS, D7
  thread budgets), `docs/research/m2_4b_speech_flow/` (profiler + raw
  measurements).

**This is a research report only. No product code was changed.
`CURRENT_STATE.md` already names M2.4B as the next task; runtime behaviour
is unchanged. Candidate numbers below (buffer seconds, `length_scale`
values) are TEST values, not product truth — none is adopted without
operator A/B acceptance.**

---

## TASK RESULT

**Research + design PASS.** The speech-flow failure mode is fully
characterised from real measurements on this Pi 5, not guessed. Every
proposed technique is tagged `VERIFIED IN INSTALLED PIPECAT` /
`VERIFIED UPSTREAM` / `INFERENCE` / `NOT AVAILABLE`. A concrete
framework-reuse-first architecture is recommended, with a staged plan.
First-token latency is investigated and reported **separately** — and a
concrete contention cause was found.

Headline: **Piper synthesis is ~7× faster than real time and is not the
bottleneck.** The gaps come from (A) waiting for `gemma4:e4b` to finish
each next sentence's text — it runs at ~2.8 tok/s, i.e. *at* Polish speech
rate, with no headroom to build an audio cushion — compounded by (B)
`SimpleTextAggregator`'s per-boundary token-lookahead and English-only NLTK
sentence splitting (Polish `tzw.`/`np.`/`itd.` over-split), and (F)
Pipecat's 3 s `stop_frame_timeout_s` turning every LLM inter-sentence stall
into a `TTSStopped`/`TTSStarted` split (the operator's "stop/start between
chunks").

---

## CURRENT M2.4 BASELINE VERIFIED

- `HEAD` = `36ec1e4` (`feat: add local streaming speech output`), working
  tree clean before this research; `pytest`: **246 passed, 7 skipped, 14
  subtests**; `ruff` clean (M2.4 scope). No product file touched by this
  task — only `docs/reports/R0012_…md` and `docs/research/m2_4b_speech_flow/`
  added.
- Path in use (unchanged): `AssistantSpeechBridge` → `PiperHttpTTSService`
  (Pipecat 1.8.1, `SENTENCE` mode, one HTTP POST per sentence to
  `127.0.0.1:5001/synthesize`) → `TtsStatusObserver` →
  `LocalAudioOutputTransport` → `UACDemoV1.0`. Voices
  `pl_PL-gosia-medium` / `en_GB-jenny_dioco-medium`, config-json inference
  defaults, `length_scale=1.0`.

---

## INSTALLED PIPECAT 1.8.1 FINDINGS

Read directly from `.venv/lib/python3.13/site-packages/pipecat`.

| Mechanism | Status | Detail |
|---|---|---|
| `TextAggregationMode` = `SENTENCE` / `TOKEN` | `VERIFIED IN INSTALLED PIPECAT` | Only `SimpleTextAggregator`. `AggregationType` enum also names `WORD` but **no WORD/phrase/clause aggregator is implemented**. |
| Sentence detection (`utils/string.match_endofsentence` + `SimpleTextAggregator`) | `VERIFIED IN INSTALLED PIPECAT` | NLTK `sent_tokenize` — **always `language="english"`, no language arg is ever passed**. Polish abbreviations (`tzw.`, `np.`, `itd.`, `itp.`, `ul.`, `dr.`, `prof.`, `nr.`, `m.in.`, `r.`, `godz.`) are treated as sentence ends. Also: a **lookahead** — after sentence-ending punctuation it waits for the *next non-whitespace character* before calling NLTK, so the last sentence of a burst sits in the buffer until the LLM emits one more token. |
| Custom text aggregator injection | `VERIFIED IN INSTALLED PIPECAT` | Two supported ways: (1) **`LLMTextProcessor(text_aggregator=<BaseTextAggregator>)`** — a standalone `FrameProcessor` (`processors/aggregators/llm_text_processor.py`) that consumes `LLMTextFrame`s and emits `AggregatedTextFrame`s; `PiperHttpTTSService.process_frame` consumes `AggregatedTextFrame` directly (`_push_tts_frames`), bypassing its own `SimpleTextAggregator`. (2) A `PiperHttpTTSService` subclass reassigning `self._text_aggregator` after `super().__init__()` — the exact pattern Cartesia and Rime use for `SkipTagsAggregator`. `TTSService.__init__` itself has **no** `text_aggregator=` param (confirmed upstream too). |
| `text_transforms` | `VERIFIED IN INSTALLED PIPECAT` | `TTSService(text_transforms=[(AggregationType|'*', async (text,type)->str), …])` — applied per aggregation, **after** aggregation, **before** `run_tts`. TTS-only; the un-transformed text is what reaches the assistant context. A transform that raises → the turn produces **no audio**. |
| `text_filters` | `VERIFIED IN INSTALLED PIPECAT` | `TTSService(text_filters=[BaseTextFilter, …])`, applied after aggregation. **`MarkdownTextFilter`** (`utils/text/markdown_text_filter.py`) is ready-made — strips `**bold**`, headers, inline code, tables, code blocks; converts numbered-list markers (`1.` → removed); makes links readable. Needs the `markdown` pkg — **already installed (`markdown 3.10.3`, `pydantic 2.13.5`)** as a transitive dep. TTS-only. |
| `append_trailing_space` (default `False`) | `VERIFIED IN INSTALLED PIPECAT` | Appends a space so Piper doesn't vocalise a trailing "." as "dot". Sentence mode only. |
| One audio context per LLM turn | `VERIFIED IN INSTALLED PIPECAT` | `LLMFullResponseStartFrame` → one `_turn_context_id` shared by every sentence (`reuse_context_id_within_turn=True`). So `BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame` bracket the **whole reply** — *unless* the context idle-times-out (below). |
| `stop_frame_timeout_s = 3.0` | `VERIFIED IN INSTALLED PIPECAT` | `_handle_audio_context`: `await wait_for(queue.get(), timeout=3.0)`. Each new sentence's `_push_tts_frames` → `create_context_id()` → `_refresh_audio_context()` pushes a keepalive that resets the timer. So the timeout only trips when **no new sentence text arrives for 3 s** — i.e. the LLM stalled mid-reply. Then a premature `TTSStoppedFrame` is pushed and the loop `break`s; the next sentence hits `append_to_audio_context`'s *"Sometimes the HTTP service can take more than 3 seconds… recreating the context"* path → a fresh `TTSStartedFrame` → downstream **`BotStopped`→`BotStarted`**. |
| `"TTS context … completed with no audio"` | `VERIFIED IN INSTALLED PIPECAT` | `_record_context_audio_outcome` logs/errors this whenever a context ends with `received_audio == False`, and bumps `_consecutive_zero_audio_contexts` (≥ 3 ⇒ service declared unusable, `ProcessorUnusablePolicy` applies). Triggered by the 3 s-timeout context recreate landing on an empty context, or a chunk that filters/whitespaces to empty. Non-fatal singly; a burst is not. |
| `pause_frame_processing` (default `False`) | `VERIFIED IN INSTALLED PIPECAT` | NeXa leaves it off. Not a factor. If ever enabled it would *hold* input frames until playback drains — the opposite of what we want here. |
| Pre-buffer / "N seconds of audio ahead of playback" | `NOT AVAILABLE` | No batching-by-remaining-audio, no look-ahead knob. Synthesis is driven purely by text arrival, and text arrives at LLM speed. |
| Output audio buffer / backpressure | `VERIFIED IN INSTALLED PIPECAT` | `base_output._audio_queue` is an **unbounded** `FrameQueue`; `write_audio_frame` → PyAudio **blocking** `stream.write()` → real-time playback pace. **So a look-ahead cushion *does* accumulate at the transport whenever audio is produced faster than it plays.** The gap problem is that production never gets ahead — not that the buffer can't hold it. |
| `chunk_size` | `VERIFIED IN INSTALLED PIPECAT` | 0.5 s of audio bytes — a *download-start* threshold for chunked TTS, not a playout buffer. |
| `BOT_VAD_STOP_SECS = 0.35`, `BOT_VAD_STOP_FALLBACK_SECS = 3` | `VERIFIED IN INSTALLED PIPECAT` | The `without_mixer` audio-queue-drain fallback that also fires `_bot_stopped_speaking` uses the **3 s** value for TTS audio (0.35 is only for the `SpeechOutputAudioRawFrame` stream case). |
| `SentenceAggregator` (`processors/aggregators/sentence.py`) | `VERIFIED IN INSTALLED PIPECAT` | A simple standalone processor; same English-only `match_endofsentence`. Not better than `SimpleTextAggregator` for our case. |
| `PatternPairAggregator`, `SkipTagsAggregator` | `VERIFIED IN INSTALLED PIPECAT` | For `<tag>…</tag>`-delimited content; not relevant to Polish prose flow. Useful precedent that custom `BaseTextAggregator`s are a first-class extension point. |

---

## UPSTREAM PIPECAT FINDINGS

Checked `github.com/pipecat-ai/pipecat` `main` + release notes + docs.

- `TTSService.__init__` on `main` **still has no `text_aggregator=` param**; `SimpleTextAggregator` hardcoded; `stop_frame_timeout_s=3.0`; no phrase aggregator; no pre-buffer. — `VERIFIED UPSTREAM` (raw `tts_service.py` fetched).
- `BaseTextAggregator` was introduced in PR **#1379**; `PatternPairAggregator` in **#1387**; `SkipTagsAggregator` ships for Cartesia/Rime. Custom `BaseTextAggregator` subclassing is the documented extension mechanism. — `VERIFIED UPSTREAM`.
- No open upstream feature that adds audio-duration-based text pacing to Pipecat. Choppy-audio issues in the tracker are about WebRTC transports / network jitter, not local-Piper sentence pacing. — `INFERENCE` (from issue search; not exhaustive).
- `MarkdownTextFilter` and `text_transforms` are current, non-deprecated API. — `VERIFIED UPSTREAM`.

---

## LIVEKIT COMPARISON

**Ideas only — LiveKit is NOT introduced into the runtime** (ADR-0003; task
rule). Read `livekit/agents` `main`.

- **`StreamAdapter(tts, sentence_tokenizer=…, text_pacing: bool | SentenceStreamPacer = False)`** wraps a non-streaming TTS. Default tokenizer = **blingfire** `SentenceTokenizer(retain_format=True)` — *not* NLTK. — `VERIFIED UPSTREAM`.
- **`SentenceStreamPacer(min_remaining_audio: float = 5.0, max_text_length: int = 300)`** — `VERIFIED UPSTREAM` (raw `stream_pacer.py`):
  - First sentence: **flushed to TTS immediately**, regardless of audio state (keeps first-audio latency low).
  - Subsequent sentences: **batched** (concatenated up to `max_text_length` chars) and flushed **only when** `generation_stopped` **and** `remaining_audio <= min_remaining_audio`, where
    `remaining_audio = audio_start_time + audio_emitter.pushed_duration() − now`
    (i.e. estimated from the duration of audio frames already emitted, minus wall-clock elapsed).
  - It **never changes speech rate. Never inserts silence.** When the buffer is low it simply flushes the next batch. A timer re-checks every 0.2 s during generation, 0.5 s+ otherwise.
  - Stated rationale: *"improve speech quality by sending larger chunks of text with more context"* and *"reduce waste from interruptions."*
- This is exactly the shape M2.4B needs. NeXa cannot import the class, but the **algorithm** (first phrase now; batch-and-hold the rest keyed on estimated remaining audio; never speed up) ports cleanly into a small NeXa-owned processor.
- Note the known LiveKit bug (`agents#3133`): `text_pacing=True` + a *streaming* TTS wrapped in `StreamAdapter` misbehaves. NeXa's Piper HTTP is non-streaming (whole WAV), so this class of bug does not apply — but it's a reminder to only pace a non-streaming synth.

---

## PIPER FINDINGS

Read the installed `piper/http_server.py` (external venv) + OHF-Voice
`piper1-gpl` `docs/API_HTTP.md`, and measured on this Pi 5.

- **`POST /synthesize` per-request JSON:** `text`, `voice`, `speaker`,
  `speaker_id`, **`length_scale`**, `noise_scale`, `noise_w_scale`.
  **`sentence_silence` is NOT a per-request field** — only the server
  startup flag `--sentence-silence` (default `0.0`; NeXa's server starts
  with no flag). — `VERIFIED` (installed source + upstream docs).
- **Response = the whole WAV**, built server-side in a `BytesIO`, returned
  with a fixed `Content-Length` (no `Transfer-Encoding: chunked`). **No
  incremental / raw-PCM / streaming output option.** — `VERIFIED`.
- Piper does its **own** sentence segmentation of the request text (via
  espeak-ng), yields per-sentence audio, and concatenates sub-sentences
  with `int(sample_rate * sentence_silence * 2)` silence bytes between them
  — currently **0.0 s**, so a multi-sentence request has **no breathing
  gap**. — `VERIFIED IN INSTALLED SOURCE`.
- Flask `app.run()` with **no `threaded=True`** → the server **serialises
  all requests**. Overlapping HTTP requests queue at the server. — `VERIFIED`.
- **Pipecat's `PiperHttpTTSService.run_tts` sends only `{"text","voice"}`**
  — it does **not** pass `length_scale`. To use `length_scale` per request
  NeXa needs a `run_tts` override in a subclass; for a *static* cozy
  profile the server `--length-scale` startup flag is enough. — `VERIFIED`.
- **Measured, this Pi 5, `pl_PL-gosia-medium`, uncontended:**

  | request | chars | synth s | audio s | RTF |
  |---|---|---|---|---|
  | sentence | 65–112 | 0.62–0.91 | 4.6–6.8 | **0.13–0.14** |
  | whole reply | 456 | 4.03 | 29.3 | 0.14 |

  Synthesis is **~7× faster than real time**. A 6-second sentence costs
  ~0.8 s to synthesise. Synthesis latency is **not** the gap source.
- Voice-switch cost (cold `pl_PL-gosia-medium` first load): ~2.2 s
  (`prewarm()` already pays this once).
- Can multiple Piper requests overlap safely / can B synthesise while A
  plays? — **A-plays-while-B-synthesises: yes** (B's `run_tts` runs on the
  event loop while A's already-queued audio drains at the transport).
  **Two concurrent Piper HTTP requests: no** — the Flask server serialises,
  and `PiperHttpTTSService` issues one `run_tts` at a time anyway (its
  `process_frame` is single-tasked). `INFERENCE`: on a 4-core Pi 5, forcing
  concurrent Piper synths would also just move the contention (R0006/R0010).

---

## WHY CURRENT SPEECH HAS GAPS

Measured with `docs/research/m2_4b_speech_flow/gap_profiler.py` — a real
`AssistantSpeechBridge` → `PiperHttpTTSService` → `TtsStatusObserver` →
`LocalAudioOutputTransport` pipeline fed a scripted token stream at fixed
rates. Raw log: `gap_profile_raw_20260906.txt`.

**Measured Polish speech rate (gosia):** 456 chars / 29.3 s ≈ **15.6
chars/s ≈ 2.5 words/s** (normal).

**Scripted pipeline, 3 tok/s feed (≈ warm `gemma4:e4b`):**
- First audio at **+7.9 s** — the whole first sentence (~6.8 s of
  "generation") plus ~0.9 s synth. The listener hears nothing for 8 s.
- Production gaps between consecutive audio chunks: **4.5 s, 5.9 s, 2.1 s,
  4.8 s**.
- **3× `BotStopped`→`BotStarted`** cycles mid-reply (the stop/start the
  operator observed), each preceded by
  `cleaning up TTS context …` / `recreating audio context`.

**Scripted pipeline, 2 tok/s feed (stress):** first audio **+12.3 s**;
gaps **6.9 / 6.8 / 4.4 / 6.5 s**; more stop/start churn.

Mapping to the task's candidate causes:

| Cause | Verdict |
|---|---|
| **A — waiting for enough LLM text** | **Primary.** `gemma4:e4b` warm = ~2.8 tok/s ≈ Polish speech rate. Zero headroom to get ahead. Any dip (STT running, a Piper synth burst, history growth) drops it below speech rate → the audio buffer drains → silence. |
| **B — sentence tokenizer waiting** | **Contributing.** `SimpleTextAggregator` needs the *next non-whitespace token* after `.` before it even calls NLTK — one extra token-time of delay per boundary, and the *last* sentence waits for `LLMFullResponseEndFrame`. |
| **C — bad boundary detection** | **Contributing.** English-only NLTK splits after Polish `tzw.`/`np.`/`itd.`/`ul.` → an extra tiny chunk (`"…jest tzw."`) synthesised alone, then a wait for `"horyzont zdarzeń…"`. Exactly the operator's example. |
| **D — Piper synthesis latency** | **Not a factor.** RTF ≈ 0.13; ~0.8 s per sentence. |
| **E — serialised TTS requests** | **Minor.** One HTTP POST per sentence, serialised — but each is fast; the serialisation cost is small next to (A). Fewer, larger requests would still help (less overhead, Piper handles intra-request flow). |
| **F — Pipecat TTS-context behaviour** | **Contributing / visible.** The 3 s `stop_frame_timeout_s` converts an LLM inter-sentence stall > 3 s into a premature `TTSStoppedFrame` + context recreate + fresh `TTSStartedFrame` → the audible/observable stop/start. |
| **G — audio output drain** | **Not a factor.** Unbounded queue, real-time blocking write. It faithfully plays whatever it's given; it just runs dry when nothing is given. |
| **H — CPU contention** | **Real, and the dominant cause of the *worst* stalls** (see FIRST-TOKEN LATENCY). Bursts of Piper synth + STT + the LLM on 4 cores push `gemma4:e4b` well below speech rate. |
| **I — something else** | The lookahead in (B) and the whole-first-sentence wait are the "something else" beyond a naive reading; covered above. |

**In one sentence:** the reply is spoken as a sequence of independent
sentence clips because Pipecat only ever has ~one sentence of text in hand,
and `gemma4:e4b` produces text no faster than gosia speaks it.

---

## WHAT OTHER REAL SYSTEMS DO

From LiveKit source and current (2026) voice-agent latency write-ups
(primary sources / official docs only; SEO bl​ogs excluded):

- **Batch sentences with a "remaining audio" gate** (LiveKit
  `SentenceStreamPacer`): first phrase out immediately; hold and
  concatenate the rest; release the next batch when the *already-emitted
  audio duration minus elapsed time* drops below a target (LiveKit default
  5 s). Never speeds speech; never pads silence. — `VERIFIED UPSTREAM`.
- **Feed TTS at clause/sentence boundaries, not tokens** — universally
  recommended; recovers the most latency budget while keeping prosody.
- **Interstitial fillers are used for LLM/tool waits, not speech-flow
  micro-gaps** — "delays from the LLM… are easier to mask with
  interstitial fillers." Cached/pre-generated, only on a genuine wait,
  never every turn.
- **Prosody / breathing comes from punctuation + a small fixed
  inter-sentence silence inside the synth**, not from `sleep()` in the
  orchestrator.
- **Markdown/formatting is stripped before TTS**, transcript kept intact.
- **First-token latency is attacked at the LLM layer** (KV-cache reuse,
  prompt-prefix caching, smaller context, warm model) — not hidden.

---

## WHAT CAN BE REUSED

1. **`PiperHttpTTSService` + `LocalAudioOutputTransport` + the one-context-
   per-turn model + `BotStarted/StoppedSpeakingFrame`** — unchanged. The
   M2.4 gate, `TtsStatusObserver`, `AssistantSpeechBridge` frame vocabulary
   — unchanged.
2. **`MarkdownTextFilter`** (`text_filters=[…]` on the TTS service) — for
   TTS-only markdown stripping. Zero new code, `markdown` already
   installed. `VERIFIED IN INSTALLED PIPECAT`.
3. **`text_transforms`** on the TTS service — for TTS-only Polish
   normalisation (`"tzw."` → `"tak zwany"`, digits, symbols). Applied after
   aggregation, before synth, never touches history. `VERIFIED`.
4. **`LLMTextProcessor(text_aggregator=<custom BaseTextAggregator>)`** OR a
   `PiperHttpTTSService` subclass reassigning `self._text_aggregator` — the
   supported injection point for a NeXa Polish-aware phrase segmenter.
   `VERIFIED` (Cartesia/Rime precedent).
5. **Piper `--sentence-silence` startup flag** — a small (≈ 0.15 s)
   built-in inter-sentence breath when NeXa sends multi-sentence chunks.
   `VERIFIED IN INSTALLED SOURCE`.
6. **Piper per-request `length_scale`** (via a thin `run_tts` override) or
   the `--length-scale` startup flag — the *only* sanctioned "cozy" lever;
   `≥ 1.0` only.
7. **The transport's existing unbounded audio queue** — already the
   look-ahead buffer; M2.4B just needs to keep it fed.

## WHAT MUST BE NEXA-OWNED

The one verified gap Pipecat does not fill: **audio-duration-based text
pacing / phrase batching** ("send phrase A now; hold B, C… and release a
batch when the buffered audio is about to run low; never speed up"). A
small NeXa-owned `FrameProcessor` — call it the **speech planner** —
placed between `AssistantSpeechBridge` and `PiperHttpTTSService`:

- consumes `LLMFullResponseStartFrame` / `LLMTextFrame` /
  `LLMFullResponseEndFrame`;
- segments the token stream into **speech phrases** with a NeXa-owned,
  Polish-aware boundary policy (explicit abbreviation bl( )list; clause /
  comma / semicolon / colon / sentence boundaries; a min-phrase-length
  floor so we don't emit `"…jest tzw."`);
- normalises for TTS only (markdown out, list markers out, `"**"` never
  spoken) — **the assistant transcript in `ConversationSession` history is
  never mutated** (the planner only ever sees the already-emitted token
  copy, exactly like the bridge does today);
- emits `AggregatedTextFrame(text, AggregationType.SENTENCE)` — one per
  batch — which `PiperHttpTTSService` consumes directly, bypassing
  `SimpleTextAggregator`;
- **pacing:** phrase 1 (or the first ~1 clause) emitted immediately; every
  later batch held until `estimated_buffered_audio_seconds ≤ target` OR
  generation has finished, batched up to a char cap. Buffered-audio
  estimate = Σ `len(TTSAudioRawFrame.audio)` seen by `TtsStatusObserver` ÷
  (sample_rate·2) − (now − first_audio_at). This mirrors LiveKit's
  `SentenceStreamPacer.remaining_audio` with NeXa's own observer as the
  audio-duration source.

Everything the planner needs (`LLMTextFrame` in, `AggregatedTextFrame` out,
`TtsStatusObserver` audio counts) already exists in the M2.4 pipeline.

`ConversationSession` / context / persona / model — untouched, forever.

---

## RECOMMENDED SPEECH-FLOW ARCHITECTURE

```
ConversationSession streamed tokens  (M2.3, unchanged)
        │  on_assistant_token / _complete
        ▼
AssistantSpeechBridge          (M2.4, unchanged — FIFO LLM-frame vocabulary)
        ▼
NexaSpeechPlanner              (NEW, M2.4B — the only new runtime component)
   ├─ phrase segmentation  (Polish-aware; abbrev block‑list; clause/comma/;/: /sentence; min-len floor)
   ├─ TTS-only normalisation  (MarkdownTextFilter-style; never mutates history)
   └─ pacing:  phrase 1 → now;  later batches → hold until buffered_audio_s ≤ target, cap ~200–300 chars
        │  AggregatedTextFrame(text, SENTENCE)   ── one per batch
        ▼
PiperHttpTTSService            (Pipecat 1.8.1, unchanged — consumes AggregatedTextFrame directly)
   · optional: subclass to pass length_scale ≥ 1.0 per request  (cozy profile; A/B only)
   · optional: Piper server started with --sentence-silence 0.15  (breath between sub-sentences)
        ▼
TtsStatusObserver  (M2.4, unchanged) ── also feeds the planner its audio-second counter
        ▼
LocalAudioOutputTransport  (Pipecat, unchanged — unbounded queue = the look-ahead buffer)
        ▼
UACDemoV1.0
```

Framework-reuse order honoured: (1) Pipecat capability where one exists
(`AggregatedTextFrame` intake, `text_filters`, `text_transforms`,
one-context-per-turn, unbounded output queue); (2) small composition
(`LLMTextProcessor(text_aggregator=…)` is available as an alternative
intake); (3) the single NeXa-owned layer for the one verified gap
(audio-duration pacing + Polish phrasing). **No** Pipecat replacement, **no**
LiveKit in runtime, **no** second conversation/TTS engine, **no** parallel
media pipeline.

Secondary, low-risk, independently valuable:
- Raise Pipecat's `stop_frame_timeout_s` (constructor arg on
  `PiperHttpTTSService`) from 3 s to e.g. 8–10 s so an LLM inter-sentence
  stall no longer forces a `TTSStopped`/context-recreate/`TTSStarted`
  split. `VERIFIED` this is a plain constructor parameter. Candidate
  value only; measure.

---

## SEMANTIC CHUNKING RECOMMENDATION

A **NeXa-owned phrase boundary policy** (not NLTK-Polish, not fighting an
unsupervised model):

- **Never split** immediately after a token in an explicit abbreviation
  block‑list: `tzw. np. itd. itp. m.in. ul. al. pl. dr. prof. inż. mgr.
  św. nr. str. r. w. godz. min. sek. tys. mln. mld. ok. tj. cd. dot. par.`
  (extend from the curated Polish-abbreviation list referenced in
  `docs/research/m2_4b_speech_flow/README.md` sources).
- **Prefer** these boundaries, in priority order: end of sentence (`. ! ?`
  not preceded by an abbrev / not inside a number like `3.5`); `;`; `:`;
  `,` **only** when the resulting left phrase is ≥ a min length (candidate
  ~40 chars / ~6 words) and the LLM has paused (no token for ~150 ms) or a
  batch cap is hit.
- **Min phrase length floor** (candidate ~25–40 chars) so `"…jest tzw."`
  can never be emitted alone — it stays attached to `"…tak zwany horyzont
  zdarzeń,"`.
- **TTS-only normalisation**, applied to the phrase text only:
  - strip markdown emphasis (`**`, `*`, `_`), headings (`#`), inline code
    backticks, block fences, table pipes — reuse `MarkdownTextFilter`
    (installed) via `text_filters`, or a NeXa equivalent inside the
    planner;
  - drop bare list markers so `"1."` / `"2."` / `"- "` are never spoken as
    "one dot";
  - optional lexical expansion via `text_transforms` (`"tzw."` → `"tak
    zwany"`, `"np."` → `"na przykład"`, `"itd."` → `"i tak dalej"`, `"%"` →
    `"procent"`, `"@"` → `"małpa"`), PL/EN aware.
- **Invariant:** the planner/normaliser only ever operates on the
  already-emitted token copy the bridge streams. `ConversationTurn`,
  `session.history`, and everything `ConversationSession` stores stay the
  pure, unmodified transcript — same guarantee M2.3/M2.4 already hold, to
  be re-asserted with an `ast`/behaviour test.

Do **not** rewrite semantic meaning. Lexical abbreviation expansion is
pronunciation, not meaning; keep it conservative and configurable.

---

## LOOK-AHEAD / BUFFER RECOMMENDATION

- **Unit: seconds of *buffered audio*, not characters/tokens.** Estimate =
  Σ `TTSAudioRawFrame` bytes (from `TtsStatusObserver`) ÷ (16000·2) −
  (now − first_audio_at). (`INFERENCE` that this is accurate enough — the
  transport's queue is FIFO and real-time; validate in M2.4B.1.)
- **Policy:** emit phrase 1 immediately (protect first-audio latency).
  Hold phrases 2..N; release a batch (concatenated, cap ~200–300 chars)
  when `buffered_audio_s ≤ target` **or** generation finished.
- **Candidate `target` values to A/B (experimental only, not product
  truth):** 0.5 / 1.0 / 1.5 / 2.0 / 3.0 s. Given Piper RTF ≈ 0.13 and
  `gemma4:e4b` ≈ 2.8 tok/s, a plausible sweet spot is **~1.0–1.5 s** — big
  enough that one Piper synth (~0.8 s) refills it before it empties, small
  enough that first audio is not delayed. **Find the smallest target that
  removes audible gaps** on real hardware; do not assume.
- **Metrics the M2.4B.1 profiler must emit per turn:**
  `assistant_first_token_at`, `first_phrase_ready_at`, per batch
  {`tts_request_at`, `tts_first_audio_at`, `tts_complete_at`,
  `audio_seconds_produced`}, `buffered_audio_seconds` sampled over time,
  `synthesis_rtf`, `llm_chars_per_s`, `tts_batch_queue_depth`,
  `output_underrun_events`, `silence_gap_ms` (per gap), `BotStarted/Stopped`
  count. (These extend M2.4's existing `TurnTimingTracker`.)
- The transport buffer itself needs **no** change — it is already
  unbounded and real-time; M2.4B only changes *when text is handed to
  synthesis*.

---

## PACING RECOMMENDATION

**Hard rule honoured: never faster than normal. `length_scale` for
adaptive pacing is `≥ 1.0` only; `< 1.0` is forbidden.**

- **Primary: a single static "cozy" profile.** Candidate `length_scale`
  test values **1.00 / 1.05 / 1.10** (1.00 = today's baseline). Set once —
  Piper server `--length-scale`, or a one-line `run_tts` override that puts
  `length_scale` in the JSON. **Operator A/B picks the value**; nothing is
  assumed. A static slightly-slower voice is very likely all that's needed
  and is the least risky.
- **Buffer-adaptive `length_scale` per batch: research, do not ship
  unless A/B proves it.** Risk: changing `length_scale` between adjacent
  Piper requests can make the voice sound inconsistent (tempo stepping
  mid-answer). If tested: clamp to a narrow band (e.g. 1.00–1.12), change
  at most once per few seconds, and only ever *slower* as the buffer
  drops. If the A/B shows any audible wobble, drop it and keep the static
  profile.
- **When the buffer is genuinely about to underrun:** prefer a **natural
  pause** at the next phrase boundary (let Piper's `--sentence-silence`
  breath do its job, and/or the planner simply not having text to send —
  which reads as a thinking pause) over speeding anything up. If it
  underruns often at the chosen `target`, the fix is a bigger `target` or
  addressing the LLM rate — not faster speech.

---

## PAUSE RECOMMENDATION

- **No `sleep()` calls. No fixed delay after every sentence.**
- Natural pauses come from: (1) punctuation the planner keeps in the phrase
  text; (2) Piper's `--sentence-silence` (candidate **0.10–0.20 s**) for a
  breath between sub-sentences of a batched request — this is *inside* the
  synth, not an orchestrator delay; (3) the planner naturally having no
  text to emit while the LLM thinks (a real, meaningful pause, not
  padding).
- Piper has **no SSML** and `sentence_silence` is **not per-request**, so
  fine-grained per-boundary pause control would require a NeXa-owned
  silence-frame insertion between batches — **not recommended for M2.4B**
  (adds complexity, easy to overdo; the goal is *fewer* gaps). Revisit
  only if the batched-request `--sentence-silence` breath proves
  insufficient.
- `push_silence_after_stop` / `silence_time_s` on the TTS service exist for
  transports that clip the utterance tail; the local transport does not,
  so leave them off.

---

## FILLER RECOMMENDATION

**Do not implement fillers in the first M2.4B implementation pass.**
Research verdict: fillers are for masking *LLM/tool* waits, not
speech-flow micro-gaps, and the speech-flow fix above (batching + pacing +
better phrasing) addresses the actual problem. Adding fillers now would
risk making every answer sound mannered.

If, after M2.4B.2–B.5, a genuine *pre-first-audio* wait remains (LLM
first-token still seconds long) and the operator wants it covered:

- only on a real wait beyond a threshold (candidate ~1.2 s with no audio
  yet), **not** every answer;
- **cached / pre-generated** short audio (`"mhm"`, `"hmm"`, `"chwila"`,
  `"zobaczmy"` PL; `"hmm"`, `"let me see"` EN), language-appropriate,
  subtle, played straight to the transport **without** going through
  `PiperHttpTTSService` (so it never serialises against or delays the real
  synth);
- **never** written into `ConversationSession` / history;
- configurable + off by default.

This is M2.4B.6, optional, gated on evidence that B.2–B.5 didn't already
solve it.

---

## FIRST-TOKEN LATENCY — SEPARATE FINDINGS

Instrumented separately, as required. **This is an LLM-serving / resource
problem, not a speech-flow problem, and must not be masked with fillers.**

`VERIFIED FACT`, this Pi 5, 2026-09-06:

- `gemma4:e4b` is served by **Ollama's own `llama-server` backend** (PID
  under `ollama.service`; `/usr/local/lib/ollama/llama-server … --model
  …blobs/sha256-4c27… --port 35525 -c 4096 -np 1 … -b 512 -ub 512
  --context-shift --keep 4 --flash-attn auto`). **CPU-only** (`size_vram:
  0`). Context loaded at **`-c 4096`**, not 8192.
- **Uncontended, warm, tiny prompt:** first-token **1.7 s**
  (`prompt_eval 1.70 s`), generation **2.8 tok/s** (36 tok / 12.7 s),
  `load 0.00 s`. Swap 0. No thermal throttle (`throttled=0x0`, 66 °C).
- **Under contention** (the Piper HTTP server running + two back-to-back
  scripted TTS pipeline sessions on the same 4 cores): a fresh
  `gemma4:e4b` request **produced no token for > 3.5 minutes** in this
  session's measurement — i.e. the operator's "tens of seconds" first-token
  waits reproduced, and worse. `llama-server` sat at ~300 % CPU (3 of 4
  cores) throughout.

`INFERENCE` on cause of the long stalls: **CPU contention on the 4-core Pi
5 between `llama-server` (needs ~3 cores to generate) and bursty Piper
synth + the audio/resample/event-loop work of an active turn** — R0006's
original contention finding, now confirmed to hit *first-token* not just
tok/s. Contributors to watch, to be pinned down in M2.4B.1:

- `-c 4096` + growing conversation history ⇒ larger `prompt_eval` each
  turn; `--context-shift --keep 4` reprocessing when the window fills.
- Prompt-prefix cache behaviour across turns (R0009 fixed a
  cache-breaking regression once; re-verify it still holds now that TTS
  runs concurrently).
- Model eviction: Ollama's `expires_at` (~5 min idle) ⇒ a "warm" turn
  after a quiet gap is actually a reload.
- No evidence of swap or thermal throttle in any measurement.

**M2.4B.1 must measure, per turn, on real hardware:**
`ollama load_duration`, `prompt_eval_duration`, `eval_count`/`eval_duration`
(tok/s), first-token wall time, concurrent CPU load, whether STT/TTS were
active, `free`/`swap`, `vcgencmd get_throttled`. Then a **separate**
investigation decides the fix (bigger idle keep-alive; confirm/repair
prefix caching under concurrency; sequence TTS synth away from LLM
first-token per ADR-0003 D7; possibly a smaller context). **`gemma4:e4b`
is not changed in M2.4B** (ADR-0002 Amendment 2).

---

## PROPOSED M2.4B SUBSTAGES

Order adjusted slightly from the brief's suggestion, per the evidence
(normalisation/boundary work is cheap and de-risks everything after it;
pacing needs the profiler first):

- **M2.4B.1 — Instrumentation / gap profiler (in-tree).** Extend
  `TurnTimingTracker` + the probe with the metrics listed under LOOK-AHEAD
  and FIRST-TOKEN above. Add a `--report` mode that prints per-turn
  buffered-audio-over-time, silence gaps, LLM chars/s, synth RTF,
  BotStarted/Stopped count, and the LLM-serving numbers. Deterministic
  unit tests for the metric math. **No behaviour change.** This is the
  measurement baseline every later stage is judged against.
- **M2.4B.2 — TTS-only normalisation + Polish-aware phrase boundaries.**
  The `NexaSpeechPlanner` phrase segmenter + normaliser (markdown strip,
  list markers, abbreviation block‑list, min-phrase floor, optional
  lexical expansion). Emits `AggregatedTextFrame`. **Assistant transcript
  provably unchanged** (`ast` + behaviour test). At this stage it can emit
  one phrase per boundary (no pacing yet) — already fixes bad splits and
  spoken `"**"`.
- **M2.4B.3 — Look-ahead / batching + pacing.** Add the buffered-audio
  gate: phrase 1 now; batch-and-hold the rest; release on
  `buffered_audio_s ≤ target` or generation done. Sweep `target` ∈
  {0.5,1.0,1.5,2.0,3.0} with the B.1 profiler; pick the smallest that
  removes audible gaps without delaying first audio. Raise
  `stop_frame_timeout_s` to ~8–10 s here too.
- **M2.4B.4 — Continuous playback / buffer tuning + operator A/B #1.**
  Real-hardware listening: is the reply now one continuous answer? Tune
  `target`, batch char-cap, Piper `--sentence-silence` (0.10–0.20 s).
  Operator confirms "no robotic stop/start, natural breathing."
- **M2.4B.5 — Cozy pacing experiments + operator A/B #2.** Static
  `length_scale` ∈ {1.00,1.05,1.10} blind A/B; operator picks. Only if the
  operator still wants it *and* it demonstrably helps: a narrow-band,
  slow-only, rate-limited buffer-adaptive `length_scale` trial — dropped
  immediately if any voice wobble is heard.
- **M2.4B.6 — Optional fillers.** Only if a real pre-first-audio wait
  remains after B.3–B.5. Cached PL/EN audio, threshold-gated, off by
  default, never in history.
- **(parallel track) First-token latency investigation.** Uses B.1's
  LLM-serving metrics. Separate report. Does not block B.2–B.5.

Each B.2–B.5 stage ends with the same human-acceptance gate as
M2.1/M2.2/M2.3/M2.4.

---

## RISKS

- **`gemma4:e4b` ≈ speech rate is a hard floor.** Batching + a look-ahead
  buffer *hides* short LLM dips; it cannot fix a sustained rate below
  speech rate, or a multi-second first-token. If real conversations still
  gap after M2.4B.3, the lever is the LLM track, not more buffering.
- **Delaying batches raises time-to-*full*-answer.** The buffer trades a
  smoother reply for the tail arriving slightly later. First-audio latency
  is protected (phrase 1 immediate); total-reply latency grows by roughly
  one `target`.
- **Buffered-audio estimate accuracy.** Byte-count ÷ rate minus elapsed is
  an approximation (ignores the transport's own small internal buffering
  and any resample rounding). B.1 must validate it against observed
  underruns before B.3 trusts it.
- **Adaptive `length_scale` voice inconsistency.** Real risk of audible
  tempo stepping mid-answer. Mitigation: static profile is the default;
  adaptive only ships on a clean A/B.
- **Custom Polish segmenter over/under-splitting.** An abbreviation
  block‑list is maintainable but never complete; a min-phrase floor is the
  safety net. Keep lexical expansion conservative (pronunciation only).
- **`text_transforms` raising ⇒ silent turn.** Any NeXa transform must be
  total (never raises) and must fall back to the input text.
- **Raising `stop_frame_timeout_s`** delays the *legitimate* end-of-turn
  `TTSStoppedFrame` when a reply really has ended without punctuation —
  keep it bounded (~8–10 s) and rely on `LLMFullResponseEndFrame` for the
  real end.
- **CPU budget (ADR-0003 D7).** Larger Piper requests are still cheap
  (RTF 0.13) but come in bursts; B.1 must confirm the batched pattern
  doesn't worsen LLM first-token contention.
- **Half-duplex gate interaction.** More/longer `BotStarted/Stopped`
  cycles must not confuse the M2.4 `HalfDuplexGate`'s multi-sentence latch
  — its existing tests should be re-run and, if batching changes the frame
  pattern, extended.

---

## WHAT NOT TO DO

- Do **not** replace Pipecat; do **not** add LiveKit to the runtime; do
  **not** create a second conversation engine, a second TTS brain, or a
  parallel media pipeline.
- Do **not** mutate the assistant transcript / `ConversationSession`
  history for TTS. Normalisation is TTS-only, on the emitted token copy.
- Do **not** speed speech above `length_scale = 1.0`. No `< 1.0` for
  adaptive pacing, ever.
- Do **not** add `sleep()`s or a fixed post-sentence delay.
- Do **not** implement fillers in the first pass; do **not** put filler
  text in history.
- Do **not** mask a multi-second LLM first-token stall with a filler —
  instrument and fix it on its own track.
- Do **not** pick a final `target` buffer size or `length_scale` without
  operator A/B acceptance.
- Do **not** start M2.5 (barge-in); keep the M2.4 half-duplex gate.
- Do **not** change `gemma4:e4b` (ADR-0002 Amendment 2).
- Do **not** touch the frozen M2.4 commit's behaviour.

---

## FILES CREATED / CHANGED

Created (research only — no product code, no runtime behaviour change):

- `docs/reports/R0012_m2_4b_speech_flow_research_20260906.md` (this report)
- `docs/research/m2_4b_speech_flow/README.md`
- `docs/research/m2_4b_speech_flow/gap_profiler.py` (throwaway profiler)
- `docs/research/m2_4b_speech_flow/ollama_timing.py` (LLM first-token probe)
- `docs/research/m2_4b_speech_flow/gap_profile_raw_20260906.txt` (raw run)

Not changed: any file under `src/`, `apps/`, `tests/`, `scripts/`;
`CURRENT_STATE.md` (already names M2.4B as the next task);
`pyproject.toml`.

---

## GIT STATUS

`HEAD` = `36ec1e4` (frozen M2.4). Working tree: only the untracked
`docs/reports/R0012_…md` and `docs/research/m2_4b_speech_flow/` directory
added. `pytest`: 246 passed / 7 skipped / 14 subtests (unchanged). Not
committed by this task unless the operator asks; **not pushed**.

---

## NEXT RECOMMENDED ACTION

Begin **M2.4B.1** (instrumentation / gap profiler, in-tree, no behaviour
change), then **M2.4B.2** (Polish-aware TTS-only phrase segmentation +
markdown normalisation). Run the first-token-latency investigation in
parallel using B.1's LLM-serving metrics. Nothing in B.3+ ships without
the B.1 profiler numbers and an operator A/B.

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

Research followed the evidence/labeling discipline (`VERIFIED
INSTALLED` / `VERIFIED UPSTREAM` / `INFERENCE` / `NOT AVAILABLE` on every
technique; measurements over guesses; the contention finding disclosed in
full). No gap in `AGENTS.md` was exposed.
