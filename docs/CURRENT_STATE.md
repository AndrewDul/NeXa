# CURRENT_STATE

Short operational truth. Keep this file current after every meaningful task.
Runtime / test evidence outranks anything else in this repo.

---

- **Last verified:** 2026-09-06
- **Repository:** `AndrewDul/NeXa` (`https://github.com/AndrewDul/NeXa.git`)
- **Local workspace:** `/home/devdul/Projects/NeXa_IkiGai`
- **Branch:** `main` — see `git log -1` for the current hash (not pushed)
- **Latest report:** `docs/reports/R0009_m2_3_voice_conversation_adapter_20260906.md`
- **Current milestone:** **M1 — Natural Text Conversation — COMPLETE**;
  **M2 — Realtime Voice — IN PROGRESS (M2.1, M2.2, M2.3 COMPLETE, `OPERATOR-CONFIRMED`)**
- **Current substage:** M1.1 COMPLETE, `OPERATOR-CONFIRMED` (2026-09-05).
  M1.0B COMPLETE; operator blind test COMPLETE 2026-09-04; M1.1 local
  baseline FROZEN to `gemma4:e4b`, ADR-0002 Amendment 2, 2026-09-05. M2
  open-source-first research COMPLETE (2026-09-05) — `R0005`. M2.0A
  feasibility spikes COMPLETE (2026-09-05) — `R0006`. `ADR-0003` Accepted
  (2026-09-05). **M2.1 — Pipecat foundation + local audio + Silero VAD —
  COMPLETE, `OPERATOR-CONFIRMED` (2026-09-05)** — `R0007`. **M2.2 — local
  whisper.cpp STT adapter + explicit PL/EN language strategy — COMPLETE,
  `OPERATOR-CONFIRMED` (2026-09-06)** — `R0008`. **M2.3 — voice →
  `ConversationSession` adapter — COMPLETE, `OPERATOR-CONFIRMED`
  (2026-09-06)** — `R0009`: new `src/nexa/voice_conversation/` package
  (`VoiceConversationAdapter`, `SerialConversationQueue`) feeds M2.2's
  `TranscriptionResult` into the existing, unchanged `ConversationSession`
  — the identical path typed chat uses. Two real-hardware-driven
  fix-and-retest cycles: a conversation-turn concurrency defect (mirroring
  M2.2's STT concurrency fix one layer up — FIFO, max 1 turn in flight,
  confirmed live) and a response-language mirroring defect (a fresh English
  session answered in Polish) whose first fix caused its own latency
  regression (broke Ollama/llama.cpp prompt-prefix caching) — both found
  live, fixed, and reconfirmed before PASS. No TTS/barge-in yet.
- **Next substage:** **M2.4 — streaming/chunked Piper TTS integration**
  (**NOT STARTED** — see "Exact next recommended task")
- **Current objective:** none active — M2.3 is implemented, tested, and
  human-accepted on real hardware. Next is starting M2.4, a new,
  explicitly-started implementation task.

---

## What works (VERIFIED FACT)

- Repository is a well-formed, importable Python project; foundation tests pass
  (`python -m unittest discover -s tests`).
- Documentation + ADR + report systems in place (`R0001`–`R0009`; `ADR-0001`,
  `ADR-0002` + its M1.0B amendment + Amendment 2, `ADR-0003`).
- **M2.3 — voice → `ConversationSession` adapter complete, `OPERATOR-CONFIRMED`**
  (`R0009`; `docs/architecture/M2_3_VOICE_CONVERSATION_ADAPTER_ARCHITECTURE.md`):
  new `src/nexa/voice_conversation/` — `VoiceConversationAdapter` (owns no
  history/context/persona/model of its own, `ast`-verified) feeds M2.2's
  `TranscriptionResult` into the existing, unchanged `ConversationSession`;
  `SerialConversationQueue` mirrors M2.2's STT-queue design one layer up
  (FIFO, non-blocking `submit()`, ≤1 conversation turn in flight, explicit
  bounded-overflow error). **Two real-hardware findings, found live and
  fixed before PASS**: (1) the initial design could run two
  `ConversationSession.send()` calls concurrently if a second utterance
  finished transcribing while the first was still generating — fixed by
  the FIFO queue above; reconfirmed live (`max concurrent conversation
  turns observed this session: 1`, real fast-second-utterance test).
  (2) A fresh English voice session answered "What is the speed of light?"
  in Polish — the M1.1 persona's all-Polish system prompt biased the model
  too strongly for its own single mirroring sentence to override. Fixed
  with a deterministic PL/EN directive
  (`nexa.conversation.language.detect_response_language`) recomputed after
  every historical user turn inside `ConversationContext.to_provider_messages()`
  — canonical `ConversationSession` policy, applying identically to typed
  and voice input, never the voice adapter's own concern. **A second
  finding inside that same fix**: injecting the directive only for the
  *current* turn (not replayed from history) permanently broke
  Ollama/llama.cpp's prompt-prefix KV-cache reuse the instant it was used
  once — "warm" turns (~2-6s) stayed at ~15-20s for the rest of any session
  that ever used it. Fixed by recomputing the identical directive from
  each turn's own stored (unmodified) text on every rebuild, restoring
  full cache reuse while keeping every turn's language correct — confirmed
  by a direct controlled experiment and real hardware retest (warm turns
  back to ~2-5s, correct PL/EN mirroring preserved through language
  switches). `ConversationTurn`/`session.history` remain the pure,
  unmodified real transcript throughout. Real Polish and English
  multi-turn voice conversations (with follow-up questions preserving
  cross-language context) both operator-confirmed. No TTS, no barge-in.
- **M2.2 — local whisper.cpp STT foundation complete, `OPERATOR-CONFIRMED`**
  (`R0008`; `docs/architecture/M2_2_LOCAL_STT_ARCHITECTURE.md`):
  `src/nexa/stt/` — pinned whisper.cpp `v1.9.3`/`base`/`q8_0` (R0006's
  measured baseline), built/installed outside git via
  `scripts/setup_whisper_cpp.py` into `~/.local/share/nexa/stt/`.
  `WhisperCppTranscriber` (the `SpeechTranscriber` boundary's only
  implementation) owns subprocess invocation (explicit arg list, no
  `shell=True`), JSON parsing, and typed errors — no silent fallback.
  `UtteranceBuffer` adds a pre-roll ring (`PRE_ROLL_MS=500`, justified by a
  192ms theoretical minimum plus a 288-352ms *empirical* measurement against
  real Silero VAD + real fixtures — the empirical number, not the
  theoretical one, drove the choice). `Language` is a strict `pl`/`en`
  enum with no `AUTO` member — auto-detect is structurally impossible to
  select (R0006 measured it misdetecting Polish as Japanese).
  **Two real hardware findings, found live and fixed before PASS**: (1) the
  first implementation could run two whisper.cpp subprocesses concurrently
  if a short utterance followed quickly — fixed with
  `SerialTranscriptionQueue` (FIFO, ≤1 execution at a time, non-blocking
  `submit()`, explicit bounded-overflow error, no orphan task on shutdown);
  reconfirmed live afterward (`max concurrent STT executions observed this
  session: 1`). (2) `apps/nexa_stt_probe.py` printed a fake `LISTENING` line
  from the transcription callback — fixed so only the real
  `VoiceStateMachine` event stream ever prints a state line. Real reSpeaker
  hardware verified: Polish and English phrases transcribed correctly with
  **no first-word truncation** in any case (the pre-roll's explicit
  acceptance condition). Same-corpus regression against R0006's exact 12
  fixtures: 0.3542 avg WER / 1.648s avg latency / 220.5MB avg peak RSS — no
  meaningful regression vs. R0006's ~0.354/~1.69s/~221MB. 4 threads
  (R0006's measured baseline), zero throttling. 54 new tests (52
  deterministic + 2 opt-in live-whisper.cpp), all passing. No LLM, no
  `ConversationSession` adapter, no TTS, no barge-in — `ast`-verified, not
  just asserted (ADR-0003 M2.2 scope).
- **M2.1 — local audio + Silero VAD foundation complete, `OPERATOR-CONFIRMED`**
  (`R0007`; `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md`):
  `src/nexa/voice/` — Pipecat (`pipecat-ai[local]==1.8.1`, current
  non-deprecated `PipelineWorker`/`WorkerRunner` API) local audio
  transport + Silero VAD → a NeXa-owned `VoiceStateMachine`
  (`IDLE`/`LISTENING`/`USER_SPEAKING`/`END_OF_TURN`/`ERROR`). Real reSpeaker
  XVF3800 hardware verified (mono/16kHz via its own ALSA `plug:` alias;
  discovered the system-wide default *output* device isn't currently
  connected — documented, not silently routed around). VAD endpointing
  required real retuning: the library default (`stop_secs=0.2`) measurably
  split one natural Polish sentence into 3 turns on real hardware; a
  deterministic offline calibration (real speech + real inserted silence
  gaps against the actual `SileroVADAnalyzer`, `docs/research/m2_1_vad_calibration/`)
  found `stop_secs=1.0` is the smallest value that holds 0.4/0.6/0.8s pauses
  as one utterance; a live hardware retest confirmed it. Idle/listening
  footprint: ~120 MB RAM, ~6.5% of one CPU core, no throttling. 31 new
  tests (30 deterministic + 1 opt-in hardware), all passing — verified
  precisely against the pre-M2.1 commit's 33-test baseline, not assumed
  (`R0007` "M2.1 DOCUMENTATION CHECK" corrects an earlier arithmetic
  error). No STT, no LLM call, no TTS, no `ConversationSession` —
  `ast`-verified, not just asserted (ADR-0003 M2.1 scope). Audio output was
  verified only at the stream open/close/write level — no audible sound was
  played or confirmed by the operator in M2.1 (accurate as of this
  correction; unaffected by M2.2, which does not need audio output).
- **M2.0A voice feasibility spikes complete** (`R0006`;
  `docs/research/M2_VOICE_FEASIBILITY_SPIKES.md`): real whisper.cpp benchmark
  against the exact same 12 legacy PL/EN audio fixtures faster-whisper was
  scored on — whisper.cpp's `base` model beat legacy's faster-whisper `base`
  on **both** accuracy and speed even at a matched beam size (0.354 avg WER
  at 1972 ms vs. legacy's 0.537–0.558 avg WER at 3486–3575 ms); `q8_0`
  quantization cost no accuracy at ~23% less latency. Auto-language-detection
  reproduced legacy's exact known failure (misdetects one Polish sentence as
  Japanese) — confirmed independent of STT engine, an explicit language hint
  is required. A real resource-budget test (VAD + whisper.cpp + the frozen
  `gemma4:e4b` + Piper TTS, 8 stages) found no RAM/swap/thermal ceiling
  (min free RAM ~3.8 GB, swap <70 MB, zero throttling, peak 64.8°C) but
  reproduced and precisely quantified legacy's CPU-contention failure mode
  under naive full concurrency (VAD ~73×, STT ~4.75×, TTS ~2.9×,
  LLM time-to-first-token ~2.9× slower; LLM steady-state tok/s barely moved,
  ~2%). Classified **`LOCAL_FEASIBLE_WITH_TUNING`** — local full pipeline is
  viable provided the M2 architecture sequences the pipeline instead of
  running everything concurrently. `gemma4:e4b` unchanged. NVIDIA
  Parakeet/Canary's license (CC-BY-4.0) and Polish support were confirmed
  from the primary model card (both `UNKNOWN` before); hardware path
  plausible but not benchmarked (would need a multi-GB conversion,
  correctly deferred).
- **M2 open-source-first research complete** (`R0005`;
  `docs/research/M2_REALTIME_VOICE_RESEARCH.md`): deep-dived pipecat-ai/pipecat
  and livekit/agents source code (not just docs) against the requirement that
  `ConversationSession` stay the canonical brain — both frameworks have a
  clean, confirmed integration seam (`Agent.llm_node`/custom
  `FrameProcessor`) that can delegate straight to it. License review caught a
  real risk by unpacking an actual PyPI wheel: LiveKit Agents' *default*
  local VAD/turn-detector models carry a proprietary, framework-locked
  license (usable only inside LiveKit Agents) — the open `livekit-plugins-silero`
  (MIT) must be substituted explicitly. Piper's actively-maintained successor
  (`OHF-Voice/piper1-gpl`) is GPL-3.0 (the original MIT `rhasspy/piper` is
  archived) — recommended subprocess-only invocation, not an in-process
  import. Real Pi 5 evidence pulled from the legacy repo (read-only) anchors
  every feasibility claim: legacy's own measured numbers show
  faster-whisper's fast configs (`tiny`/`base`) are too inaccurate for Polish
  (WER 0.54–0.68) while the accurate config (`small`) is too slow
  (35–92 s); Piper's real Polish-voice latency was ~7 s/utterance (much
  slower than generic English-voice benchmarks suggest); and a full
  LLM-answered voice turn took 27.6–54 s end-to-end, with a real CPU-contention
  failure once recorded when other processes shared the same 4 cores. No
  prototype was built or run — see `R0005`'s "UNRESOLVED".
- **M1.1 — Minimal Canonical Text Conversation Path implemented and verified**
  (`R0004`; `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`):
  `ConversationSession` → `ConversationContext` (bounded, deterministic) →
  `ModelProvider` → streamed tokens → appended `ConversationTurn`, in
  `src/nexa/conversation/` + `src/nexa/providers/`. `LocalModelProvider`
  (Ollama) is the first, live-verified implementation, configured for the
  frozen baseline `gemma4:e4b`; `LlamaServerProvider` is a second
  implementation behind the same interface (portability path), verified at the
  interface level only — see limitations below. One versioned persona
  (`configs/personas/nexa_persona_v1.json`). Repo-local `./.venv` created
  (stdlib-only runtime — `pyproject.toml` `dependencies = []` unchanged; `dev`
  extras `pytest`/`ruff` installed into it). 33 deterministic tests (unit +
  fake-HTTP-server integration) pass via both `python -m unittest` and
  `pytest`; `ruff check` clean. One live Ollama integration test (opt-in,
  `NEXA_RUN_LIVE_TESTS=1`) passed against the real `gemma4:e4b`: one Polish and
  one English turn, streamed, history correct, model unloaded after. A manual
  multi-turn conversation through the real `apps/nexa_chat.py` CLI harness
  also verified end to end (real recall across turns, e.g. translating its own
  earlier Polish reply into English on request).
- **`OPERATOR-CONFIRMED` (2026-09-05):** Andrzej personally ran a real
  multi-turn conversation through `apps/nexa_chat.py` → `ConversationSession`
  → `ConversationContext` → `ModelProvider` → Ollama → `gemma4:e4b` (the exact
  canonical path, not a test-only harness) and recorded **"M1.1 HUMAN
  ACCEPTANCE: PASS"** — everything worked correctly and conversation quality
  was satisfactory. This is the human-acceptance evidence tier above the
  agent's own manual-CLI verification recorded in `R0004`; see `R0004`'s
  "Operator acceptance" addendum for the exact record.
- M1.0 research complete: Pi hardware/runtime/model inventory verified;
  legacy conversation stack audited (read-only); external research done;
  local models benchmarked on the Pi.
- **M1.0B complete:** 8 current small models (Qwen3.5 ×2, Gemma 4 ×2, Phi-4-mini,
  Bielik ×2 configs, + the `qwen3:4b-instruct` control) benchmarked on the Pi —
  perf + `CONV-PL-S1` + `CONV-EN-S1` for all; Phase 2 deep-dive
  (`CONV-MIX-1`, `CONV-NATURAL-S1`, `CONV-HONESTY-S1`, persona A/B,
  standardized-vs-recommended, long-context decay, weighted score) for the 3
  finalists. Ollama upgraded 0.30.10 → 0.33.2 for valid Qwen3.5/Gemma 4 runs.
- Throwaway benchmark harness `scripts/m1_bench/` works (stdlib only, no deps);
  raw results under `docs/research/m1_bench_results/` (M1.0B under `.../m1_0b/`).

## What is partial

- M1.0 + M1.0B conversation-quality scores were **`AGENT-ASSISTED`** (Claude vs
  transcripts + rubric). The **operator blind test is done and the baseline is
  frozen** (`docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md` §4/§8,
  `docs/testing/m1_operator_blind_results/`, `ADR-0002` Amendment 2): Andrzej
  talked blind to all 3 M1.0B finalists (~7–20 turns each), scored them, then
  the mapping was revealed. Result — `OPERATOR-CONFIRMED`: `gemma4:e4b` ranked
  best (4/5, everyday NeXa YES), `gemma4:e2b` second (3/5, maybe), `qwen3.5:2b`
  worst (2/5, no — operator independently caught a live hallucination, matching
  R0003's honesty-probe finding). This **superseded the M1.0B weighted-score
  `PROPOSAL`** (which had favored `gemma4:e2b` 4.0 vs. `gemma4:e4b` 3.9, driven
  by speed/RAM weighting, not a conversation-quality disagreement — see the
  test doc §8). The owner then explicitly signed off on `gemma4:e4b`'s
  RAM/latency cost (~10 GB resident, ~3 tok/s) and it is now **FROZEN** as the
  M1.1 local conversation baseline in `ADR-0002` Amendment 2 (2026-09-05).
- **M1.0B/blind-test model picture (historical vs. final — do not conflate):**
  the **M1.0B weighted-score recommendation** (`R0003`, sweep doc §19,
  `AGENT-ASSISTED`, 2026-09-01/02) was `gemma4:e2b` as the M1.1 baseline
  *candidate*. The **final, operator-confirmed decision** (2026-09-04/05) froze
  **`gemma4:e4b`** instead — see above. Both remain documented: `gemma4:e4b`
  (PL 4/5, EN 4/5) is the frozen baseline / quality leader; `gemma4:e2b`
  (PL 3.5/5, EN 4/5, ~2× faster, flattest long-context decay) is documented as
  the fast/low-RAM alternative; `qwen3:4b-instruct` (PL 3/5, EN 3.5/5) remains
  the reliable incumbent / safe fallback; `qwen3.5:2b` is English-only (broken
  Polish + a honesty-probe hallucination, operator-confirmed). Bielik re-test at
  temp 0.1 + Q4_K_M did **not** flip anything (Q8_0 weak EN / no TTFT warm-up;
  3rd-party Q4_K_M is a reliability failure). Full detail: `R0003`, sweep doc
  §8–§19 (historical M1.0B evidence and ranking — unedited); `ADR-0002`
  Amendment 2 (final decision).
- `llama.cpp`-direct vs Ollama head-to-head **still not measured** — blocked by
  Ollama blob-store permissions (`0700`/`ollama`-owned); needs an operator
  decision. The **same blocker** means `LlamaServerProvider` (built in M1.1)
  has never been run against a real `llama-server` process — no GGUF weight
  file is reachable outside that blob store on this machine. It is verified
  only at the interface/request-shaping level (fake-HTTP-server tests).
- Incumbent `qwen3:4b-instruct` was carried into the M1.0B head-to-head on
  Phase 1 data only (not run through the Phase 2 battery).
- Cancellation (`CancelToken`) is cooperative between received stream chunks;
  it cannot interrupt a token Ollama/llama.cpp is already computing
  mid-inference (neither backend's streaming HTTP API exposes finer-grained
  abort). Documented in `M1_1_TEXT_CONVERSATION_ARCHITECTURE.md` §6 as a known
  limitation for M2 (voice/barge-in) to account for, not a silent gap.

## What is not implemented (by design)

- Realtime voice (M2) — **architecture decided (`ADR-0003`, Accepted), zero
  product code**. Robust context beyond M1.1's bounded window (M3), device
  awareness / capability registry (M4), long-term memory (M5), and
  everything later — not yet researched or decided either.
- Model router / `AUTO`/`LOCAL ONLY`/`CLOUD PREFERRED` policy, MAS, tools,
  online model provider — all explicitly out of M1.1 scope (ADR-0002 D1,
  ROADMAP "Later").

## Known problems / notes

- `OBSERVATION`: the login shell's `python3`/`pip` resolve into the **legacy**
  repo's `.venv`. NeXa must always use its own `./.venv`.
- `VERIFIED FACT`: on this Pi, neither the V3D GPU (Vulkan) nor the Hailo-10H
  helps M1 LLM inference — CPU is the only viable path (see research doc §2.2–2.3).
- `VERIFIED FACT`: legacy `smart-desk-ai-assistant/config/settings.json` `/llm`
  block is stale (points at a llama-server + Qwen2.5-1.5B path the MAS does not
  actually use). Legacy is reference-only; not our file to fix.

## Current architecture state

- Conceptual boundaries: `docs/architecture/FOUNDATION_ARCHITECTURE.md`
  (conceptual only, except three pointers into the docs below).
- **Real (`VERIFIED FACT`):** `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`
  — the Conversation and (local) Model Providers boundaries, implemented in
  `src/nexa/conversation/` + `src/nexa/providers/`.
- **Real (`VERIFIED FACT`):** `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md`
  — the M2.1 slice of the Voice boundary (local audio transport + VAD only),
  implemented in `src/nexa/voice/`.
- **Real (`VERIFIED FACT`):** `docs/architecture/M2_2_LOCAL_STT_ARCHITECTURE.md`
  — the M2.2 slice of the Voice boundary (local whisper.cpp STT, pre-roll
  capture, serialized transcription execution), implemented in
  `src/nexa/stt/` + `src/nexa/voice/runtime.py`'s
  `_UtteranceCaptureFrameProcessor`.
- **Real (`VERIFIED FACT`):** `docs/architecture/M2_3_VOICE_CONVERSATION_ADAPTER_ARCHITECTURE.md`
  — the M2.3 slice of the Voice boundary (voice → `ConversationSession`
  wiring, serialized conversation-turn execution, response-language
  mirroring), implemented in `src/nexa/voice_conversation/` +
  `src/nexa/conversation/language.py`/`context.py`.
- **ADR-0002 (Accepted)** sets the M1 direction: one minimal canonical
  text-conversation path (`ConversationSession` → `ConversationContext` →
  `ModelProvider` → streamed tokens); model access via a minimal
  OpenAI-chat-shaped `ModelProvider` abstraction; **Ollama** as the first
  `LocalModelProvider` implementation (`llama.cpp` second / portability) —
  **both now implemented**, Ollama live-verified, `llama-server` interface-only
  (see "What is partial").
- **ADR-0002 M1.0B amendment (2026-09-02, informational — D1–D4 unchanged at
  the time):** the fair Bielik re-test is done and does **not** flip the
  baseline (D4 caveat (b) resolved); a better Polish persona lifted
  `qwen3:4b-instruct` PL 2/5 → 3/5 (caveat (a) partially addressed); new
  evidence — `gemma4:e4b` / `gemma4:e2b` out-converse the incumbent in both
  languages.
- **ADR-0002 Amendment 2 (2026-09-05, decisive — supersedes D4 on model choice
  only; D1–D3 unchanged):** operator blind test run and scored; owner signed
  off on the RAM/latency cost; **`gemma4:e4b` is FROZEN as the M1.1 local
  conversation baseline.** `gemma4:e2b` documented as the fast/low-RAM
  alternative, `qwen3:4b-instruct` as the swappable safe fallback. Provider/model
  abstraction (D1–D3) explicitly preserved — this model is the M1.1 *local*
  baseline, not NeXa itself; no router implemented.
- **ADR-0003 (Accepted, 2026-09-05)** sets the M2 direction — architecture
  and component choices decided; Pipecat (BSD-2-Clause) as the local voice
  orchestration framework, owning audio transport/VAD-wiring/turn-detection
  only; a NeXa-owned adapter (`VoiceConversationAdapter`, now built — M2.3
  — as a plain callback-driven class rather than the ADR's illustrative
  `FrameProcessor` sketch, which explicitly left the mechanism open) feeds
  the same, unchanged `ConversationSession` — no second history/persona/
  model choice for voice. Silero VAD (`USE AS-IS`).
  whisper.cpp `base/q8_0` as the initial local STT baseline (**not frozen** —
  same discipline as ADR-0002 D4; Parakeet/Canary and Hailo offload remain
  open candidates). Piper via subprocess as an explicitly **temporary**
  TTS baseline (**not frozen**; too slow for the final target, GPL-3.0
  successor). Explicit PL/EN language-hint strategy required — auto-detect
  rejected. Pipeline sequencing (VAD/STT before LLM generation; TTS may
  overlap generation) and explicit CPU-thread budgets are architectural
  requirements. Full barge-in is the M2 target, with new
  interruption-coordination work named (not built) beyond M1.1's
  `CancelToken`. LiveKit Agents is **deferred, not rejected** — no second
  orchestration framework installed for the first local implementation.
- **M2.1 implemented per ADR-0003 D1–D3, D10** (`src/nexa/voice/`,
  `R0007`): Pipecat local audio transport + Silero VAD →
  `VoiceStateMachine`. VAD `stop_secs` retuned from the library default
  (0.2s) to an evidence-based `1.0s` — see "What works" above for the full
  chain. No STT/LLM/TTS/barge-in — `ConversationSession` untouched.
- **M2.2 implemented per ADR-0003 D4, D5, D7, D11** (`src/nexa/stt/`,
  `R0008`): pinned whisper.cpp `base/q8_0` → `WhisperCppTranscriber` →
  `SerialTranscriptionQueue` → `on_transcription` callback, fed by a
  pre-roll-preserving `UtteranceBuffer` inside a new
  `_UtteranceCaptureFrameProcessor` in the M2.1 pipeline. Explicit `pl`/`en`
  language hint required — no `AUTO` member exists. See "What works" above
  for the two real-hardware findings (concurrency, state-display) and their
  fixes. No LLM/`ConversationSession` adapter/TTS/barge-in —
  `ConversationSession` untouched.
- **M2.3 implemented per ADR-0003 D2** (`src/nexa/voice_conversation/`,
  `R0009`): `VoiceConversationAdapter` → `SerialConversationQueue` →
  `ConversationSession.send()` — the exact same call typed chat makes.
  Response-language mirroring (`src/nexa/conversation/language.py`, wired
  into `ConversationContext.to_provider_messages()`) is canonical
  `ConversationSession` policy, not voice-specific — see "What works"
  above for the two real-hardware findings (conversation-turn concurrency;
  language mirroring plus its own cache-breaking latency regression) and
  their fixes. No TTS/barge-in yet.
- Product code now exists for M1.1 (`src/nexa/conversation/`,
  `src/nexa/providers/`, `src/nexa/config.py`, `src/nexa/bootstrap.py`,
  `apps/nexa_chat.py`), M2.1 (`src/nexa/voice/`, `apps/nexa_voice_probe.py`),
  M2.2 (`src/nexa/stt/`, `apps/nexa_stt_probe.py`), and M2.3
  (`src/nexa/voice_conversation/`, `apps/nexa_voice_chat_probe.py`). M2.4
  onward (TTS, barge-in) has no product code yet.

## Current test status

- Repo-local `./.venv` (system Python 3.13.5, **not** the legacy repo's venv)
  with `dev` extras (`pytest`, `ruff`) and `pipecat-ai[local]==1.8.1`
  installed.
- `python -m unittest discover -s tests` and `pytest`: **159 tests, all
  PASS**, 4 intentionally skipped (live Ollama test + M2.1 hardware probe +
  2 opt-in live-whisper.cpp tests). `ruff check src tests apps`: clean.
- Live Ollama integration test (`NEXA_RUN_LIVE_TESTS=1 python -m unittest
  tests.test_live_ollama_integration`): **PASS** against real `gemma4:e4b`
  (2026-09-05) — see `R0004` for the transcript evidence.
- **Human acceptance test (M1.1):** Andrzej ran a real multi-turn conversation
  through `apps/nexa_chat.py` (the actual canonical path, real `gemma4:e4b`)
  and recorded **"M1.1 HUMAN ACCEPTANCE: PASS"** (2026-09-05) — see `R0004`'s
  "Operator acceptance" addendum.
- **Hardware acceptance test (M2.1):** `NEXA_RUN_VOICE_HARDWARE_TEST=1
  python -m unittest tests.test_voice_hardware_probe`: **PASS** against the
  real reSpeaker XVF3800. Separately, Andrzej ran the real
  `apps/nexa_voice_probe.py` across a genuine tuning cycle (library-default
  `stop_secs=0.2` → deterministic offline calibration → confirmed live
  retest at `stop_secs=1.0`) — see `R0007` for the full evidence chain.
- **Hardware acceptance test (M2.2):** `NEXA_RUN_LIVE_STT_TEST=1 python -m
  unittest tests.test_stt_transcriber_live`: **PASS** against the real
  pinned whisper.cpp binary+model. Separately, Andrzej ran the real
  `apps/nexa_stt_probe.py` for real Polish/English transcription (no
  first-word truncation in any case) and, after a real concurrency defect
  was found live and fixed, a dedicated concurrency retest confirming
  `max concurrent STT executions observed this session: 1` — see `R0008`
  for the full evidence chain.
- **Hardware acceptance test (M2.3):** Andrzej ran the real
  `apps/nexa_voice_chat_probe.py` for real Polish and English multi-turn
  voice conversations (context preserved across turns, including a
  cross-language PL→EN follow-up), a dedicated fast-second-utterance
  concurrency retest (`max concurrent conversation turns observed this
  session: 1`), and — after a real response-language mirroring defect and
  its own cache-breaking latency regression were found live and fixed — a
  final retest confirming both correct PL/EN mirroring and restored warm
  first-token latency (~2-5s) — see `R0009` for the full evidence chain.
- `scripts/m1_bench/bench.py`: smoke-tested and used for real measurements
  (M1.0/M1.0B; unrelated to the M1.1/M2.1/M2.2/M2.3 product tests above).

## Active architectural decisions

- **ADR-0001** — NeXa project foundation (Accepted).
- **ADR-0002** — Text conversation foundation (Accepted for architecture /
  provider boundary / first runtime) + **M1.0B amendment** (2026-09-02,
  informational) + **Amendment 2** (2026-09-05, decisive: M1.1 local baseline
  model **FROZEN** to `gemma4:e4b`).
- **ADR-0003** — Realtime voice foundation (Accepted, 2026-09-05): Pipecat +
  unchanged `ConversationSession` + Silero VAD + whisper.cpp `base/q8_0`
  (baseline) + Piper/subprocess (temporary baseline) + sequencing/thread-budget
  rules + full barge-in target + LiveKit deferred. **whisper.cpp `base/q8_0`
  is now real** (M2.2, `R0008`); **D2's voice → `ConversationSession`
  adapter is now real** (M2.3, `R0009`, as a plain callback-driven class —
  the ADR's illustrative `FrameProcessor` mechanism was explicitly left
  open for this substage to decide) — see "Current architecture state"
  above for the full summary; full text in
  `docs/decisions/ADR-0003_realtime_voice_foundation.md`. Not modified by
  M2.2 or M2.3 — no implementation contradiction was found in either.

## Current focus

- None active. M1 (Natural Text Conversation) is a complete,
  operator-confirmed chain through M1.1. M2's research (`R0005`) →
  feasibility spikes (`R0006`) → architecture decision (`ADR-0003`) → M2.1
  (`R0007`) → M2.2 (`R0008`) → M2.3 (`R0009`) implementations, all
  operator-confirmed on real hardware, is now also complete. **M2.4 is
  clear to start**, as a new, explicitly-started task.

## Exact next recommended task

**M2.4 — streaming/chunked Piper TTS integration**, the next M2
implementation substage under `ADR-0003` D6. M2.1/M2.2/M2.3 are done —
build on them, don't re-decide them:

- Sentence/chunk-boundary triggering so TTS can start narrating before the
  full LLM reply is generated — consume `VoiceConversationAdapter`'s
  existing `on_assistant_token`/`on_assistant_complete` streaming exactly
  as M2.3 already surfaces it; no new conversation-authority wiring should
  be needed.
- Piper via subprocess (never an in-process import — ADR-0003 D6, R0005's
  license finding: `OHF-Voice/piper1-gpl` is GPL-3.0). Piper remains an
  explicitly **temporary** TTS baseline, not frozen.
- `gemma4:e4b` stays the frozen baseline unless a future ADR changes it.
- Continue thread-budget discipline (ADR-0003 D7) — TTS is a third
  concurrent CPU consumer alongside STT and the LLM; R0006's contention
  findings still apply.
- Still no barge-in in M2.4 — that remains M2.5 per `ADR-0003`'s substage
  table. An operator acceptance test (voice question in, spoken assistant
  reply out) before M2.4 is called done, matching the M1.1/M2.1/M2.2/M2.3
  human-acceptance pattern.

Optional, non-blocking, can run any time: a scoped Parakeet/Canary
conversion + benchmark spike (license and Polish support are confirmed
clean per `R0006`; only the hardware path is untested).

Optional, not required, M1.1 hardening the owner may still want at some
point (unrelated to M2, each its own small task):
- Resolve the pre-existing Ollama blob-store permission blocker (needs an
  explicit operator/`sudo` decision) so the `llama-server` adapter and the
  Ollama-vs-llama.cpp benchmark can actually run live.
- Decide whether cancellation needs a stronger guarantee before M2's
  barge-in depends on it (see `M1_1_TEXT_CONVERSATION_ARCHITECTURE.md` §6).
