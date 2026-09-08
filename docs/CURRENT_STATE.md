# CURRENT_STATE

Short operational truth. Keep this file current after every meaningful task.
Runtime / test evidence outranks anything else in this repo.

---

- **Last verified:** 2026-09-08
- **Repository:** `AndrewDul/NeXa` (`https://github.com/AndrewDul/NeXa.git`)
- **Local workspace:** `/home/devdul/Projects/NeXa_IkiGai`
- **Branch:** `main` — see `git log -1` for the current hash (not pushed)
- **Latest report:** `docs/reports/R0025_m2_4b_5_automatic_bilingual_pl_en_voice_input_20260908.md`
  (M2.4B.5 — **Automatic Bilingual PL/EN Voice Input — IMPLEMENTED + tests;
  live operator voice acceptance PENDING**. R0024's accepted architecture
  is built behind the existing STT boundary (ADR-0003 D11), on the ONE
  canonical `ConversationSession`; `gemma4:e4b` / `num_thread=2` /
  `keep_alive=30m` / warm-up / persona / SpeechPlanner / continuity / Piper
  voices+speed / `ggml-base-q8_0` / `-t 4` all **unchanged**.
  (1) `nexa.stt.WhisperCppLanguageDetector` — `ctypes` binding to the
  **pinned, already-installed** `libwhisper.so` (`v1.9.3`; no fork, no
  version change, `ctypes` is stdlib) → `p_pl`, `p_en`, raw top-1 lang.
  (2) `nexa.stt.LanguageIdGuard` — one authority: AUTO_ACCEPT vs
  FALLBACK_REDECODE from constrained `argmax(p_pl,p_en)` + raw lang +
  configurable confidence threshold (**0.60 = R0024 initial calibration,
  documented, telemetered**) + 2.0 s duration floor.
  (3) `nexa.stt.BilingualSpeechTranscriber` (implements `SpeechTranscriber`)
  — LID → guard → **exactly one** explicit `whisper-cli` decode in the
  guard-selected PL/EN language; the wrong-language transcript is never
  generated, so nothing unsafe reaches `ConversationSession`. Holds the
  transient `last_input_language` (`pl`|`en`|`None`; not a turn, not
  memory).
  (4) `nexa.conversation.ResponseLanguageResolver` — SEPARATE authority:
  mirror `InputSpeechLanguage` by default; explicit request ("Answer in
  English." / "Odpowiedz po polsku." / "Od teraz mów po angielsku." /
  "Wracamy do polskiego.") switches + sets a transient sticky session
  preference; a narrow deterministic detector with a content-question
  deny-list so ordinary mentions of a language don't switch. Threaded via
  `ConversationSession.send(response_language=…)` → per-turn
  `language_directive`, replay-stable (R0009); `None`/TEXT unchanged.
  (5) TTS voice maps from **ResponseLanguage** (`bridge.select_voice`),
  not the transcript/STT-decode language. `InputSpeechLanguage ≠
  ResponseLanguage ≠ TTSVoice` — 3 owners.
  **Headless latency** (`b5_latency_probe.py`, real ctypes LID + real CLI
  decode over the R0024 corpus): total **~2.83 s mean / 2.80 s median /
  3.00 s p90** per utterance = **≈ +1.13 s** vs the ~1.7 s explicit
  baseline (matches R0024 `-l auto`), **flat** — LID + one decode always;
  FALLBACK adds no extra pass. Guard replay over all 50 R0024 fixtures:
  30/30 monolingual AUTO_ACCEPT correct (incl. `ru`/`ko`/`he` third-lang
  recovery); 10/10 shorts FALLBACK → inherit (Tak.→"Tak." not "Talk.").
  Mixed/code-switch: **BEST EFFORT / DEFERRED (not guaranteed)** — no
  dual-decode; guard picks the same language `-l auto` would, so not worse
  than R0024. **ADR-0003 Amendment 1 added** (guarded `-l auto` accepted;
  code-switch not guaranteed; shorts inherit; input≠response language;
  R0024 = evidence authority; no unrelated decision rewritten).
  +39 `tests/test_bilingual_voice_input.py`; `pytest` 552 / `unittest`
  559; `ruff` clean; `git diff --check` clean. Live harness ready:
  `apps/nexa_bilingual_voice_probe.py` (one command, 10 utterances). Not
  pushed.)
- **Prior report — R0024** (`docs/reports/R0024_m2_4b_4_bilingual_voice_input_research_benchmark_20260908.md`,
  M2.4B.4 — **Bilingual Voice Input Research & Benchmark — COMPLETE,
  research + decision only, NO production change**. Operator recorded a
  50-utterance real-voice corpus (15 PL / 15 EN / 10 short-ambiguous /
  10 mixed-code-switch, `docs/research/m2_4b_bilingual_stt/`); benchmark
  ran the pinned production whisper.cpp `v1.9.3` `ggml-base-q8_0` `-t 4`
  over all 50 under 4 strategies (200 runs). **Key evidence:** whisper
  v1.9.3 **never confuses PL with EN** (0/15 PL→EN, 0/15 EN→PL);
  `-l auto` monolingual LID **90 %** (27/30), all 3 misses are
  low-confidence slips to a *third* language (`ru`/`ko`/`he`, p 0.35–0.53
  vs 0.6–0.99 for confident hits); `-l auto` transcript == explicit
  baseline on 28/30 monolingual (the 2 diffs are the third-lang misdetects
  → wrong-script S3). `-l auto` costs **~+1.1 s/turn**; `-dl`→explicit
  costs ~+1.3 s and adds nothing (same detector) — rejected. Short PL
  one-word utterances break under auto (Tak→"Talk.", Nie→"Не.",
  Dobra→"Доброе утро!") but at low confidence — gate-able. Mixed:
  `-l auto` 6 USABLE / 4 PARTIALLY_USABLE / 0 BROKEN (better than either
  forced language). Resources identical across strategies: RSS 221 MB,
  ≤70 °C, `throttled 0x0`. **No stronger-model download** (architecture
  decides on base/q8_0 evidence; PL transcript-quality S2 rate 13 % PL /
  20 % EN is a *pre-existing* `base/q8_0` issue — separate track, `R0016`,
  candidate `large-v3-turbo-q5_0` named for a future approved benchmark).
  **Decision:** future bilingual input = `-l auto` single pass +
  PL/EN-and-confidence guard on the label + inherit-previous-input-language
  fallback; response-language stays a *separate* resolver from STT.
  **ADR-0003 D5 verdict: PARTIALLY supersede** — automatic per-utterance
  PL↔EN switching for normal turns is evidence-backed; isolated shorts +
  true within-utterance code-switch stay deferred. Proposed D5 amendment
  recorded, **ADR not edited**. `pytest` 513 / `unittest` 520 (+19
  `tests/test_bilingual_stt_bench.py`, +14 `_corpus`). Production
  `VoiceRuntime` explicit-language behaviour unchanged. Not pushed.)
- **Prior report — R0023** (`docs/reports/R0023_m2_4b_3_6_production_local_model_serving_freeze_20260908.md`,
  M2.4B.3.6 — **Production Local Model Serving Freeze — PASS**. The
  R0021/R0022 serving findings are productionised on the one canonical
  conversation path with `gemma4:e4b` kept as `DEFAULT_LOCAL_MODEL`:
  (1) **`num_thread=2`** — one-time provider policy in `nexa.config`
  (`DEFAULT_LOCAL_NUM_THREAD`), sent as an Ollama request option by
  `LocalModelProvider` (no `taskset` / LLM `renice` / RT sched; Piper
  stays `nice +10`); (2) **`keep_alive = 30m`** (`DEFAULT_LOCAL_KEEP_ALIVE`,
  was stock `5m`; not infinite — ~10 GB resident model, deferred
  resource-policy decision); (3) **startup warm-up** —
  `nexa.bootstrap.warm_up_session()` sends the canonical persona +
  `ResponseMode.VOICE` prefix with EMPTY history (`num_predict=1`,
  discarded) so Ollama loads the model and fills the ~507-token KV
  prefix. **Never writes `ConversationSession` history, never creates a
  turn, no second session/provider/persona; raises `ModelUnavailableError`
  visibly if Ollama is down.** Measured (`b36_serving_freeze_probe.py`,
  eviction-controlled, real Pi): cold first real voice turn TTFT ~76.7 s
  (load 33.4 s + 507-tok prefix reprocess 43.3 s) → with warm-up ~3.2 s
  (`prompt_eval` 43.3 s → 3.2 s; prefix served from KV cache) — **~73 s
  saved on turn 1, warm-up is not a no-op.** `num_thread=2` Piper-active
  re-confirm: decode ~1.74 → ~2.85 tok/s (+64 %), Piper unaffected, no
  throttle. `ResponseMode.TEXT` byte-for-byte unchanged, `VOICE` policy
  unchanged, PL/EN language mechanism unchanged, **no `e2b` routing / no
  per-language model / no fallback model**. `pytest` 480 passed / 7
  skipped / 14 subtests; `unittest` 487 OK; +20
  `tests/test_production_serving_freeze.py`; `test_operator_blind_ab.py`
  `TestProductionUntouched` updated for the approved change. Not pushed.)
- **Prior report — R0022** (`docs/reports/R0022_m2_4b_3_5_operator_blind_model_ab_20260908.md`,
  M2.4B.3.5 — **operator-blind local voice model A/B — CLOSED 2026-09-08**.
  Sealed `secrets.SystemRandom` mapping **revealed**: Candidate A =
  `gemma4:e2b`, Candidate B = `gemma4:e4b`. The operator ran A pl+en and
  B pl+en on the full realtime voice path and **chose `gemma4:e4b`** —
  reliability/quality over `e2b`'s ~1.6–2× speed; EN with `e4b` "idealny";
  PL slower but accepted (Polish latency + Polish STT are separate later
  tracks); NeXa stays bilingual; this is a model decision only, `e2b` not
  routed to. Operator gave verbatim qualitative observations, no 1–5
  scores (none invented). Objective `blind_results/` metrics (warm turns,
  `num_thread=2` + `keep_alive=30m` both): `e2b` PL ~18.4 / EN ~29
  chars/s, warm TTFT ~1.7 s; `e4b` PL ~11.2 / EN ~16.5 chars/s, warm
  TTFT ~3.0–3.5 s, END_OF_TURN→first-audio ~10–13 s; neither throttled,
  ≤ 67 °C; `e4b` holds ~2.4 GB more RAM. One Candidate B Polish attempt
  produced a 0-byte result and needed a Pi reset before the successful
  run — recorded, **not** attributed to the model. **B.3.3 now has real
  operator PL + EN voice evidence.** `R0021` = M2.4B.3.4 LLM serving &
  voice benchmark (`gemma4:e4b` PL ~9.4 chars/s / best PL quality;
  `gemma4:e2b` ~2× / repeatable factual regressions; `num_thread=2`
  +12–14 % decode); `R0020` = M2.4B.3.3 `ResponseMode` voice reply
  policy;
  `R0019` = M2.4B.3.2 continuity controller (no-op on today's rate);
  `R0018` = the rate-budget math authority; `R0017` … `R0011` as before.)
- **Prior report — R0021** (`docs/reports/R0021_m2_4b_3_4_local_llm_serving_voice_benchmark_20260907.md`,
  M2.4B.3.4 — local LLM serving & voice performance benchmark;
  **research only, no production model switch**. Measured `gemma4:e4b`
  under the real B.3.3 voice path: **PL ~9.4 generated chars/s** (~59 % of
  R0018's 15.9 target), **EN ~14.7** (~92 %); language ratio PL/EN ≈ 0.64;
  tok/s ~3.1; warm-prefix TTFT ~2.6 s, cold-prefix (fresh conversation
  turn 1) ~29 s (the ~535-tok persona+voice-directive preamble at
  ~20 tok/s), cold load ~33 s, ~10.2 GB resident; temp ≤ 72 °C, never
  throttled. Serving: **`num_thread=2` gives +14 % tok/s** (per-request
  Ollama option, no sudo) — still ~85 % of PL target; `num_batch`/`num_ctx`
  do nothing. Shortlist: **`gemma4:e2b` is the only real candidate** —
  ~2× faster (PL ~18.7 chars/s ✓ target, EN ~27), warm TTFT ~1.2 s,
  7.5 GB, same family so the persona + voice directive port unchanged —
  **BUT a repeatable quality regression** (factual self-contradictions,
  a hallucination, one EN→PL mirroring break, thinner reasoning). qwen3/
  qwen3.5:4b = same speed, garbled PL. qwen2.5:3b / llama3.2:3b = fast but
  word-salad PL. Bielik Q8_0/Q4KM = 2 tok/s / empty completions. **No
  model beats `gemma4:e4b` without quality loss.** Recommendation:
  operator A/B blind `e4b` vs `e2b`; take `num_thread=2` + warm-keep as
  free wins regardless. Also fixed (separate commit `c64b66b`): the
  continuity `_hold_then_release` self-cancel log warning + 2 regression
  tests. `R0020` = M2.4B.3.3 `ResponseMode` voice reply policy;
  `R0019` = M2.4B.3.2 continuity controller (no-op on today's rate);
  `R0018` = the rate-budget math authority; `R0017` = M2.4B.3.1 Piper
  `nice +10` + TTS context timeout 8 s; `R0016` = M2.4B.2A
  LaTeX/truncation fix; `R0015` = M2.4B.2 speech planner; `R0014` =
  M2.4B.1A CPU spike + metric fixes; `R0013` = M2.4B.1 instrumentation;
  `R0012` = M2.4B research; `R0011` = M2.4)
- **Current milestone:** **M1 — Natural Text Conversation — COMPLETE**;
  **M2 — Realtime Voice — IN PROGRESS (M2.1, M2.2, M2.3, M2.4 COMPLETE, `OPERATOR-CONFIRMED`)**
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
  **M2.4 — streaming local Piper TTS (Piper HTTP + Pipecat) — COMPLETE,
  `OPERATOR-CONFIRMED` (2026-09-06)** — `R0011`: new `src/nexa/tts/`
  (external Piper HTTP process boundary) + `src/nexa/voice_tts/`
  (`AssistantSpeechBridge`, `TtsStatusObserver`, `voice_for_language`,
  preflight, turn-timing) feed M2.3's streamed assistant text into
  Pipecat 1.8.1's `PiperHttpTTSService` + built-in SENTENCE aggregation +
  `LocalAudioOutputTransport`; PL/EN voice chosen by the canonical M2.3
  language function. Two real-hardware findings, fixed before PASS:
  (1) a self-conversation loop (the reSpeaker heard NeXa's own TTS and
  whisper.cpp re-transcribed her answer as new user turns) — fixed with a
  **temporary half-duplex self-echo gate** (`HalfDuplexGate` +
  `_MicGateFrameProcessor`, mic audio withheld before VAD/STT while real
  TTS playback is active; not barge-in — that is M2.5); (2) output routed
  to the reSpeaker's own alias after a USB replug — fixed to independent
  by-name selection (`LocalAudioConfig.output_device_name = "usb_speaker"`
  = the dedicated USB DAC's stable ALSA alias; input stays `"respeaker"`).
  Plus a probe turn-timing instrumentation fix. No barge-in.
- **Next substage:** **M2.4B — Natural Speech Flow / Streaming Pacing**
  (**IN PROGRESS**). Research/design frozen at `f8c3964` (`R0012`).
  **M2.4B.1 — realtime speech-flow instrumentation / gap profiler:
  IMPLEMENTED** (`R0013`, `509da2d`): `src/nexa/voice_tts/metrics.py` +
  `apps/nexa_voice_tts_probe.py --report` — measure-only. **M2.4B.1A —
  CPU contention spike + metric-accuracy fixes: DONE** (`R0014`,
  `42591f5` + `24d88ee`): intra-response silence is primarily LLM text
  starvation under CPU contention; Piper on 1 Pi 5 core stays ~2× realtime;
  `renice +10` on the Piper process is the recommended (not-yet-shipped)
  scheduling fix; true Piper HTTP timing + `buffer_drain_to_stop_lag_s`
  corrected. **M2.4B.2 — Polish-aware speech planner + TTS-only
  normalisation + M2.4B.2A LaTeX/truncation-tail fix: COMPLETE,
  `OPERATOR-CONFIRMED` (2026-09-07)** — `R0015` + `R0016`.
  `src/nexa/voice_tts/speech_planner.py` (`NexaSpeechPlanner` between the
  bridge and `PiperHttpTTSService`, one natural-phrase `AggregatedTextFrame`
  at a time; Polish/EN abbreviation-aware boundaries; Markdown/list → prose;
  conservative abbreviation expansion; `tzw.` not expanded; `_strip_math`
  for inline LaTeX; `_tidy_spoken` trims a dangling `"("` from a
  `num_predict`-truncated reply, mid-word partials kept = transcript
  truth). No pacing. Transcript invariant proven (`ast` + behaviour).
  Operator verdict on the fresh mic run: *"The spoken response itself is
  good if we ignore the pauses"* — B.2/B.2A confirmed; `$\text{H}$` never
  reached Piper (`"wodoru (H) i helu (He)"`), `tiny_text_chunk_count = 0`,
  true Piper RTF ~0.27. **M2.4B.3.1 — Piper CPU priority + TTS context
  continuity: IMPLEMENTED** (`R0017`): the external Piper process starts at
  `nice +10` (`PiperHttpConfig.nice`, no sudo, NeXa's own process untouched,
  `llama-server` untouched); Pipecat `stop_frame_timeout_s` raised 3 s → 8 s
  (`DEFAULT_TTS_CONTEXT_TIMEOUT_S`) so a short inter-phrase stall keeps one
  speaking context. No refill controller. **M2.4B.3.2A — speech rate
  budget: RESEARCH DONE** (`R0018`, offline sim
  `docs/research/m2_4b_speech_flow/rate_budget_sim.py`):
  **`realtime_text_ratio ≈ 0.53`** — `gemma4:e4b` produces spoken text at
  ~53 % of the rate `pl_PL-gosia-medium` consumes it (8.5 vs 15.5
  chars/s). This is a hard constraint: no finite steady-state buffer makes
  an arbitrarily long reply continuous; a prebuffer only relocates silence
  to the front (`wall_to_finish` invariant); batching cannot move the
  first underrun (can't synthesize text the LLM hasn't generated);
  `length_scale ≤ 1.10` closes ≤ 11 % of the deficit. **M2.4B.3.2 —
  small speech continuity controller: IMPLEMENTED** (`R0019`,
  `src/nexa/voice_tts/continuity.py`): `NexaSpeechContinuityController`
  between the planner and `PiperHttpTTSService` — phrase 0 always
  immediate (no prebuffer); phrases 1..N released the moment the
  ESTIMATED audio reserve (`Σ produced-audio-s − wall-since-first-audio`,
  fed by the downstream observer's real byte counts) is below a target
  (`DEFAULT_CONTINUITY_TARGET_S = 2.0`, candidate A/B 1.5/2.0/2.5), held
  ≤ 0.4 s only while healthy; never batches-to-grow, never adds silence,
  never touches text/speech-rate. B.3.1's `nice +10` + `stop_frame_timeout_s
  = 8` unchanged. **Offline replay of the R0018 timelines: the controller
  never makes first-audio later / the first underrun earlier / total
  silence higher, and on those (LLM-behind) turns it changes nothing —
  it CANNOT fix the 0.53 rate deficit.** Its value is proven
  no-regression + the metrics seam + correct bounded behaviour for the
  genuinely short-and-fast reply. **M2.4B.3.3 — conversational voice
  response policy: IMPLEMENTED** (`R0020`,
  `src/nexa/conversation/response_mode.py`): new `ResponseMode.{TEXT,
  VOICE}` `StrEnum` + `voice_response_directive()`. `ConversationSession.
  send(..., response_mode=TEXT)` threads it to
  `ConversationContext.to_provider_messages(..., response_mode=…)`, which
  for `VOICE` inserts ONE constant bilingual `system` message at a fixed
  index (right after the persona) — "answer directly, ~1–3 sentences for
  an ordinary question, short first sentence, no lecture/list by default,
  BUT honour an explicit request for detail/steps/list/comparison/more".
  `VoiceConversationAdapter` defaults to `VOICE`; typed chat stays `TEXT`
  = byte-for-byte pre-B.3.3. Same session/history/persona/model; the
  directive is NEVER stored in history; it does NOT decide the response
  language (the per-turn `language_directive` still does); it is a
  generation policy, not truncation (`num_predict` unchanged). Follows the
  R0009 KV-cache discipline (constant string, fixed position). Hardware
  A/B (`b33_ab.py`): the four ordinary questions asked *without* "krótko"
  → `2/2/3/2` sentences under `VOICE` (was `2/2/5/11` under `TEXT`; two
  markdown lectures/lists became flat prose), mean answer chars 395→186;
  "wyjaśnij dokładnie" still answered in 5 sentences/522 chars, uncut;
  first-audio equal-or-better on 4 of 5 (Q-B −8.0 s, Q-E −16.4 s);
  ordinary intra-response gaps 41.6 s→≤5 s. **`realtime_text_ratio ≈
  0.53` is unchanged** — a long answer still gaps; this stage is reply
  *shape*, not rate. **M2.4B.3.4 — local LLM serving & voice performance
  benchmark: DONE, research only** (`R0021`, `docs/research/m2_4b_llm_bench/`;
  **no production model change**). Measured `gemma4:e4b` under the real
  B.3.3 voice path: PL ~9.4 generated chars/s (~59 % of R0018's 15.9
  target), EN ~14.7 (~92 %); PL/EN throughput ratio ≈ 0.64; ~3.1 tok/s;
  warm-prefix TTFT ~2.6 s, cold-prefix ~29 s, cold load ~33 s, ~10.2 GB
  resident; never throttled. Serving: `num_thread=2` = +14 % tok/s
  (per-request Ollama option, no sudo; still ~85 % of PL target);
  `num_batch`/`num_ctx` inert. `gemma4:e2b` is the only viable faster
  model (~2× — PL ~18.7 chars/s clears target, warm TTFT ~1.2 s, 7.5 GB,
  same family/persona/voice-policy) **but has a repeatable quality
  regression** (factual self-contradictions, a hallucination, one EN→PL
  break, thinner reasoning). qwen3/3.5:4b same speed + garbled PL;
  qwen2.5:3b/llama3.2:3b fast but word-salad PL; Bielik 2 tok/s / empty
  completions. **No model beats `gemma4:e4b` without quality loss** —
  recommend operator A/B blind `e4b` vs `e2b`; `num_thread=2` + warm-keep
  are free wins regardless. Also fixed separately (`c64b66b`): continuity
  `_hold_then_release` self-cancel log warning + 2 regression tests.
  **M2.4B.3.5 — operator-blind model A/B: COMPLETE, CLOSED 2026-09-08**
  (`R0022`). Mapping revealed — A = `gemma4:e2b`, B = `gemma4:e4b`; the
  operator ran A pl+en / B pl+en and **chose `gemma4:e4b`** (quality over
  `e2b`'s ~1.6–2× speed; EN "idealny"; PL slower but accepted; bilingual
  kept; `e2b` not routed to). Verbatim operator observations only, no 1–5
  scores. **M2.4B.3.6 — Production Local Model Serving Freeze: COMPLETE,
  PASS** (`R0023`). On the ONE canonical path, `gemma4:e4b` kept as
  `DEFAULT_LOCAL_MODEL`: `num_thread=2` + `keep_alive=30m` are now
  one-time provider policy in `nexa.config`
  (`DEFAULT_LOCAL_NUM_THREAD` / `DEFAULT_LOCAL_KEEP_ALIVE`, env-overridable)
  wired through `LocalModelProvider` (extra Ollama option only; no
  `taskset` / LLM `renice` / RT sched — Piper stays `nice +10`) +
  `build_default_session()`; new `nexa.bootstrap.warm_up_session()` primes
  the persona/`ResponseMode.VOICE` KV prefix at startup (empty history,
  `num_predict=1`, output discarded — never enters history, no second
  authority, `ModelUnavailableError` visible). Measured
  (`b36_serving_freeze_probe.py`, eviction-controlled): cold first real
  voice turn TTFT ~76.7 s → with warm-up ~3.2 s (`prompt_eval` 43.3 s →
  3.2 s; 507-tok prefix from KV cache) — **~73 s saved, not a no-op**;
  `num_thread=2` Piper-active decode ~1.74 → ~2.85 tok/s (+64 %), Piper
  unaffected, no throttle. `ResponseMode.TEXT` byte-for-byte unchanged,
  `VOICE` policy unchanged, PL/EN language mechanism unchanged, no `e2b`
  routing / per-language model / fallback model. +20
  `tests/test_production_serving_freeze.py`. Separate flagged tracks
  (deferred, NOT in B.3.6): Polish throughput (~11 vs 15.9 chars/s target);
  Polish STT accuracy (whisper.cpp `base`/`q8_0`); bilingual auto-STT /
  code-switch; `keep_alive` = infinite residency (resource-policy
  decision). **M2.4B.4 — Bilingual Voice Input Research & Benchmark:
  COMPLETE (research + decision, NO production change)** — `R0024`. Real
  50-utterance operator corpus + `whisper.cpp v1.9.3 base/q8_0` benchmark
  (4 strategies, 200 runs). PL↔EN never confused (0/30); `-l auto` LID
  90 %, all 3 misses low-confidence third-language slips; `-l auto`
  transcript == explicit baseline when the label is right; `-l auto`
  +~1.1 s/turn, `-dl`→explicit +~1.3 s and no gain (rejected). Decision:
  future bilingual input = guarded `-l auto` (PL/EN + confidence gate +
  inherit-previous-input-language fallback), response-language a *separate*
  resolver. **ADR-0003 D5: PARTIALLY supersede** (per-utterance PL↔EN
  switching OK; isolated shorts + within-utterance code-switch deferred);
  proposed D5 amendment recorded, ADR not edited. No stronger-model
  download (PL S2 rate 13 %/20 % is a pre-existing `base/q8_0` issue —
  separate track, candidate `large-v3-turbo-q5_0` named for a future
  approved benchmark). **M2.4B.5 — Automatic Bilingual PL/EN Voice Input:
  IMPLEMENTED, live operator acceptance PENDING** — `R0025`, `ADR-0003
  Amendment 1`. `nexa.stt.{WhisperCppLanguageDetector (ctypes → pinned
  libwhisper.so, p_pl/p_en), LanguageIdGuard (threshold 0.60 = R0024
  calibration, configurable; 2.0 s duration floor), BilingualSpeechTranscriber
  (LID→guard→ONE explicit PL/EN decode; wrong-lang transcript never
  generated; holds transient last_input_language)}` +
  `nexa.conversation.ResponseLanguageResolver` (separate authority; mirror
  input by default; explicit request → sticky pref; narrow detector, no
  false-switch on content mentions) threaded via
  `ConversationSession.send(response_language=…)` (replay-stable, R0009;
  TEXT unchanged); TTS voice from ResponseLanguage. Headless latency
  ~2.83 s mean / ~+1.13 s vs explicit, flat. Mixed = BEST EFFORT /
  DEFERRED. +39 tests; `pytest` 552 / `unittest` 559. **M2.4B is NOT
  complete.** Then **M2.5 — barge-in / interruption** (replaces the
  temporary half-duplex gate).
- **Current objective:** **operator runs the live bilingual acceptance
  harness** — `./.venv/bin/python apps/nexa_bilingual_voice_probe.py`, one
  continuous `ConversationSession`, the R0025 10-utterance PL↔EN + sticky
  sequence — to confirm live: per-utterance PL↔EN switching with no
  restart; "Dobra." inherits the surrounding language; "Odpowiedz po
  polsku." → later EN input answered PL; "Answer in English." → later PL
  input answered EN; the Piper voice follows the response language. On
  confirmation, mark M2.4B.5 `OPERATOR-CONFIRMED`, then **M2.5**. Also
  still owed: the B.3.6 operator live-voice confirmation (STT latency +
  END_OF_TURN→first-audio). `gemma4:e4b` + `num_thread=2` +
  `keep_alive=30m` + warm-up + `ggml-base-q8_0` / `-t 4` unchanged; the
  plain explicit `WhisperCppTranscriber` path (`--language pl|en`) is
  still fully supported alongside the new bilingual one.

---

## What works (VERIFIED FACT)

- Repository is a well-formed, importable Python project; foundation tests pass
  (`python -m unittest discover -s tests`).
- Documentation + ADR + report systems in place (`R0001`–`R0011`; `ADR-0001`,
  `ADR-0002` + its M1.0B amendment + Amendment 2, `ADR-0003`).
- **M2.4 — streaming local Piper TTS complete, `OPERATOR-CONFIRMED`**
  (`R0011`; `docs/architecture/M2_4_STREAMING_TTS_ARCHITECTURE.md`):
  `src/nexa/tts/` (external `python -m piper.http_server` process on
  `127.0.0.1:5001`, its own venv `~/.local/share/nexa/tts/piper-http-venv/`,
  GPL `piper-tts` never imported into NeXa's process — `ast`-verified;
  `pyproject.toml` unchanged) + `src/nexa/voice_tts/` (`AssistantSpeechBridge`
  translates M2.3's `on_assistant_token`/`_complete` callbacks into
  Pipecat's `LLMFullResponseStart`/`LLMTextFrame`/`LLMFullResponseEnd`
  vocabulary in FIFO; `TtsStatusObserver` reports only real
  `TTSStarted`/`AudioRaw`/`Stopped`/`Error` frames; `voice_for_language`
  maps the **canonical** `nexa.conversation.language.detect_response_language`
  result to `pl_PL-gosia-medium` / `en_GB-jenny_dioco-medium` — the exact
  prior-assistant voices, byte-identical files, config-json inference
  defaults). Pipecat 1.8.1 `PiperHttpTTSService` (configured with the
  `/synthesize` URL) + built-in SENTENCE aggregation +
  `LocalAudioOutputTransport` (auto-resamples 22050→16 kHz) reused as-is,
  no patch. Output is now the **dedicated USB DAC** (`UACDemoV1.0`,
  `usb_speaker` alias → `hw:CARD=UACDemoV10`), selected **independently**
  of the reSpeaker input (`respeaker` alias), both by name, no shared
  index, no silent fallback. **Two real-hardware findings fixed before
  PASS**: (1) self-conversation loop — the reSpeaker heard NeXa's own TTS
  and whisper.cpp re-transcribed fragments of her answer ("Saturny nie
  jest czarną dziurą.", "Masz rację.", …) as new user turns; fixed with a
  **temporary half-duplex self-echo gate** — `HalfDuplexGate`
  (event-backed boolean, driven by real
  `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` + the bridge's
  response-lifecycle notifications, no timers, no fake `VoiceState`) +
  `_MicGateFrameProcessor` (right after `transport.input()`, drops
  `InputAudioRawFrame` before VAD/STT while suppressed; multi-sentence
  latch never reopens the mic between chunks of one reply). NOT barge-in —
  nothing is cancelled, the user cannot interrupt NeXa mid-reply; M2.5
  replaces it. (2) Output routed to the reSpeaker's own alias after a USB
  replug re-enumeration; fixed to independent by-name selection.
  Plus a genuine probe **instrumentation** fix (`TurnTimingTracker` —
  per-turn FIFO-correlated latency record; `first_tts_audio_at` /
  `first_sentence_ready_at` written once per response). 91 new passing
  tests + 3 opt-in live-Piper skips, 11 new test files. No barge-in —
  `ast`-verified. Operator-confirmed real conversation: full local voice
  loop works, NeXa no longer talks to herself, context preserved,
  responses coherent. Known follow-up: natural-speech-flow / pacing
  (`M2.4B`, not started).
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

- **Barge-in / interruption (M2.5)** — not built. While NeXa speaks, the
  temporary M2.4 half-duplex gate withholds mic input entirely; the user
  cannot interrupt her, and nothing (TTS / LLM / `ConversationSession`) is
  cancelled on user speech. M2.5 replaces the gate with true full-duplex
  handling (interruption, own-TTS acoustic suppression, echo handling).
- **Natural speech flow / streaming pacing (`M2.4B`)** — not built. M2.4's
  spoken output is per-sentence Piper synthesis with audible gaps between
  chunks and occasional bad phrase-boundary splits (incl. Polish
  abbreviations — Pipecat's NLTK splitter runs English-only). Buffered/
  look-ahead generation and buffer-adaptive pacing are `M2.4B`.
- **Voice-model selection** — `pl_PL-gosia-medium` / `en_GB-jenny_dioco-medium`
  (the prior assistant's voices) are in use. A softer/cozier voice is a
  separate research task after the pipeline is stable, not M2.4/M2.4B.
- Robust context beyond M1.1's bounded window (M3), device awareness /
  capability registry (M4), long-term memory (M5), and everything later —
  not yet researched or decided.
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
- `OBSERVATION` (M2.4, non-fatal): a run once logged
  `"TTS context … completed with no audio"` before audio then appeared
  normally — no audible effect; not root-caused; deferred to `M2.4B`.
- `OBSERVATION` (M2.4, shutdown-only): abrupt Ctrl+C (`WorkerRunner.cancel`)
  can trip an ALSA-lib `snd_pcm_plugin_status` assertion during interpreter
  teardown, **after** all audio has played, on the `plug`→`dmix` chain. A
  graceful `EndFrame` stop is clean. Cosmetic; a fix needs
  `VoiceRuntime.run()` shutdown changes — deferred.
- `VERIFIED FACT` (M2.4): the two Piper voice files in
  `~/.local/share/nexa/tts/voices/` are byte-identical (sha256) to the
  legacy `smart-desk-ai-assistant/voices/piper/` files.
  `scripts/setup_piper_http.py` *optionally* copies them from that
  read-only legacy path if present (else downloads); NeXa's runtime code
  never reads the legacy path.

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
- **Real (`VERIFIED FACT`):** `docs/architecture/M2_4_STREAMING_TTS_ARCHITECTURE.md`
  — the M2.4 slice of the Voice boundary (streamed assistant text →
  sentence-chunked Piper TTS via an external HTTP process → dedicated USB
  speaker DAC; response-language → voice mapping; a temporary half-duplex
  self-echo gate; independent input/output device selection), implemented
  in `src/nexa/tts/` + `src/nexa/voice_tts/` + `src/nexa/voice/gate.py` +
  `src/nexa/voice/runtime.py`'s `_MicGateFrameProcessor` +
  `src/nexa/voice/config.py`. **Barge-in is NOT here** — M2.5.
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
- **M2.4 implemented per ADR-0003 D6, D7** (`src/nexa/tts/`,
  `src/nexa/voice_tts/`, `src/nexa/voice/gate.py`, `R0011`; follows R0010's
  Recommendation A): `AssistantSpeechBridge` → `PiperHttpTTSService`
  (Pipecat 1.8.1, `/synthesize` URL, built-in SENTENCE aggregation) →
  `TtsStatusObserver` → `LocalAudioOutputTransport` → the `UACDemoV1.0` USB
  DAC. External `python -m piper.http_server` process, its own venv, GPL
  `piper-tts` never imported in-process (`ast`-verified), `pyproject.toml`
  unchanged. Voice chosen from M2.3's canonical response-language function.
  A temporary **half-duplex self-echo gate** (`HalfDuplexGate` +
  `_MicGateFrameProcessor`) withholds mic audio before VAD/STT while real
  TTS playback frames say NeXa is speaking — NOT barge-in (M2.5). Output is
  now an **independent** by-name device selection from the reSpeaker input.
  See "What works" above for the two real-hardware findings
  (self-conversation loop; post-replug output routing) and their fixes.
- Product code now exists for M1.1 (`src/nexa/conversation/`,
  `src/nexa/providers/`, `src/nexa/config.py`, `src/nexa/bootstrap.py`,
  `apps/nexa_chat.py`), M2.1 (`src/nexa/voice/`, `apps/nexa_voice_probe.py`),
  M2.2 (`src/nexa/stt/`, `apps/nexa_stt_probe.py`), M2.3
  (`src/nexa/voice_conversation/`, `apps/nexa_voice_chat_probe.py`), and
  M2.4 (`src/nexa/tts/`, `src/nexa/voice_tts/`, `src/nexa/voice/gate.py`,
  `apps/nexa_voice_tts_probe.py`, `scripts/setup_piper_http.py`). **M2.4B
  has product code now:** `src/nexa/voice_tts/metrics.py` +
  `timed_tts.py` (B.1 / B.1A, measure-only), `src/nexa/voice_tts/speech_planner.py`
  (B.2/B.2A — `NexaSpeechPlanner`, `normalize_for_speech`,
  `find_phrase_cut`; wired into the probe's `extra_output_stages` between
  the bridge and the TTS service), and B.3.1 CPU/context tuning:
  `PiperHttpConfig.nice` (+ `DEFAULT_PIPER_NICE = 10`, `default_piper_nice()`)
  applied in `nexa/tts/server.py` via a `nice -n N` exec prefix, and
  `nexa/voice_tts.DEFAULT_TTS_CONTEXT_TIMEOUT_S = 8.0` passed to
  `PiperHttpTTSService(stop_frame_timeout_s=…)` by the probe. **B.3.2** —
  `src/nexa/voice_tts/continuity.py` (`NexaSpeechContinuityController`,
  `decide_release`; wired into `extra_output_stages` between the planner
  and the TTS service; `--continuity-target-s` / `--no-continuity`).
  **B.3.3** — `src/nexa/conversation/response_mode.py` (`ResponseMode`
  `StrEnum` + `voice_response_directive()`); `ConversationSession.send`
  and `ConversationContext.to_provider_messages` take a `response_mode`
  keyword (default `TEXT` = unchanged); `VoiceConversationAdapter`
  defaults to `VOICE`; probe flag `--response-mode {text,voice}`. M2.5
  (barge-in) still has no product code.

## Current test status

- Repo-local `./.venv` (system Python 3.13.5, **not** the legacy repo's venv)
  with `dev` extras (`pytest`, `ruff`) and `pipecat-ai[local]==1.8.1`
  installed.
- `python -m unittest discover -s tests`: **440 OK, 7 skipped**;
  `pytest`: **433 passed, 7 skipped, 14 subtests passed** (2026-09-07,
  after M2.4B.3.3 — `+17` `tests/test_conversation_response_mode.py`).
  The 7 skips are all opt-in / environment-gated: 1 live Ollama, 2 live
  whisper.cpp, 3 live Piper HTTP (`NEXA_RUN_LIVE_TTS_TEST=1`), 1 reSpeaker
  hardware probe. `ruff check src tests apps scripts/setup_piper_http.py`:
  clean. (`tests/test_voice_tts_metrics.py` 69 tests — B.1/B.1A/B.2;
  `tests/test_voice_tts_speech_planner.py` 59 tests — B.2 + B.2A;
  `tests/test_tts_server.py` — B.3.1 Piper `nice`;
  `tests/test_voice_tts_bridge.py` — B.3.1 TTS context timeout.)
  (Pre-existing unrelated `ruff` findings in `scripts/m1_bench/` — M1
  benchmark tooling, committed in `b79a752`, untouched.)
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
- **Hardware acceptance test (M2.4):** Andrzej ran the real
  `apps/nexa_voice_tts_probe.py` for an extended real voice conversation
  and explicitly confirmed the full local loop (mic → VAD → whisper.cpp →
  `ConversationSession`/`gemma4:e4b` → streamed reply → Piper TTS →
  `UACDemoV1.0` USB DAC): old NeXa Piper voice audible, NeXa no longer
  talks to herself (half-duplex gate), multi-turn context preserved,
  responses coherent. Preceded by separately-confirmed direct-hardware
  steps (device re-detection after reboot/replug, direct tone, direct old
  PL/EN Piper playback to the DAC, Pipecat-only playback without LLM) —
  see `R0011` for the full evidence chain. Optional live suite:
  `NEXA_RUN_LIVE_TTS_TEST=1 python -m unittest tests.test_tts_server_live`.
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
  M2.2, M2.3, or M2.4 — no implementation contradiction was found in any.
  **D6's TTS baseline is now real** (M2.4, `R0011`): external Piper HTTP
  process, no in-process GPL import — Piper stays explicitly *temporary*,
  not frozen.

## Current focus

- None active. M1 (Natural Text Conversation) is a complete,
  operator-confirmed chain through M1.1. M2's research (`R0005`) →
  feasibility spikes (`R0006`) → architecture decision (`ADR-0003`) → M2.1
  (`R0007`) → M2.2 (`R0008`) → M2.3 (`R0009`) → M2.4A spike (`R0010`) →
  M2.4 (`R0011`) implementations, all operator-confirmed on real hardware,
  are now complete. The M2.4 functional baseline is frozen by its commit.
  **M2.4B is in progress** — research frozen (`f8c3964`/`R0012`); M2.4B.1
  instrumentation (`R0013`), M2.4B.1A CPU spike + metric fixes (`R0014`),
  M2.4B.2 speech planner (`R0015`) + M2.4B.2A LaTeX/truncation-tail fix
  (`R0016`, `OPERATOR-CONFIRMED` 2026-09-07), M2.4B.3.1 Piper `nice +10` +
  TTS context-timeout 8 s (`R0017`), M2.4B.3.2A speech rate-budget
  research (`R0018`: `realtime_text_ratio ≈ 0.53`), M2.4B.3.2 continuity
  controller (`R0019` — no-op on today's rate), and M2.4B.3.3
  conversational voice response policy (`R0020` — `ResponseMode`) done.
  **Operator voice-session confirmation of B.3.3, then the ~2×
  faster-generation / model-serving track — is next.**

## Exact next recommended task

**Operator voice-session confirmation of M2.4B.3.3** (an ordinary voice
question with no "krótko"; an English turn; a "rozwiń" / "tell me more"
follow-up to check the explicit-detail override live). Then the **~2×
faster-generation / model-serving track** — R0018's asymptotic fix
(target ~15.9 generated chars/s, ~≥5 tok/s sustained). At today's
`realtime_text_ratio ≈ 0.53` (gemma4:e4b produces spoken text at ~53% of
the rate pl_PL-gosia-medium consumes it, 8.5 vs 15.5 chars/s) a finite
buffer CANNOT make an arbitrarily long reply continuous — a prebuffer
only relocates silence to the front (`wall_to_finish` invariant);
batching cannot move the first underrun (can't synthesize non-existent
text); `length_scale <= 1.10` closes <= 11% of the deficit. B.3.3
reduced the *number* of long spoken replies (ordinary voice answers are
now 1–3 sentences — `R0020`), but faster generation is the only path to
continuity on a genuinely long answer.

The B.3.2 continuity controller (`src/nexa/voice_tts/continuity.py`) is
shipped and correct for when the LLM gets faster (phrase 0 immediate;
phrases 1..N held ≤ 0.4 s only while the estimated reserve is healthy);
it makes 0 holds at today's rate. Non-blocking: Ollama `keep_alive` for
first-token eviction; STT quality (whisper.cpp "horyzont zdarzen" ->
"chory zezdarzyn" etc., R0016); `GenerationOptions.num_predict = 200`
truncation (it truncated both TEXT-mode long answers mid-word in the
B.3.3 A/B). The B.2 planner tunables, the B.3.1 values
(`DEFAULT_PIPER_NICE`, `DEFAULT_TTS_CONTEXT_TIMEOUT_S`), the B.3.2 target
and the B.3.3 `voice_response_directive()` wording are CANDIDATE and may
be adjusted here. `length_scale` 1.05-1.10 is an optional minor assist
(analysis only in R0018).

Then **M2.5 — barge-in / interruption / own-TTS suppression / echo
handling**, replacing the temporary half-duplex gate.

Original M2.4B goals (from `R0012`), for reference — build on M2.4, do not
destabilise it:

- Buffered / look-ahead generation so TTS is not driven one isolated
  sentence at a time; a continuous coherent spoken response rather than
  independent sentence clips.
- Pacing that adapts mildly to how much future generated/audio content is
  buffered.
- Better sentence/phrase boundaries — Pipecat's NLTK splitter runs
  `language="english"` only, so Polish abbreviations (`ul.`, `tzw.`) can
  split a phrase badly (e.g. `"…jest tzw."` / `"horyzont zdarzeń…"`).
- Investigate the non-fatal `"TTS context … completed with no audio"`
  event observed once in a real run.
- Keep the temporary half-duplex gate as-is (M2.5 replaces it).
- `gemma4:e4b` stays frozen; Piper stays the temporary TTS baseline.
- Thread-budget discipline (ADR-0003 D7) still applies — STT + LLM + TTS
  are three concurrent CPU consumers.

Then **M2.5 — barge-in / interruption / own-TTS suppression / echo
handling**, replacing the temporary half-duplex gate.

Separate, any time after the pipeline is stable (NOT M2.4B): voice-model
selection research (the operator would eventually prefer a softer/cozier
voice than `pl_PL-gosia-medium` / `en_GB-jenny_dioco-medium`).

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
