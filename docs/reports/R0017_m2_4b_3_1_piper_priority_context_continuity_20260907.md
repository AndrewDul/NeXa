# R0017 — M2.4B.3.1: Piper CPU priority + TTS context continuity

- **Date:** 2026-09-07
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.3.1 —
  implementation (first, incremental step of B.3)**
- **Related:** `docs/reports/R0014_m2_4b_1a_cpu_scheduling_spike_20260907.md`
  (the benchmark this ships — strategy C: Piper `nice +10`),
  `docs/reports/R0012_m2_4b_speech_flow_research_20260906.md`
  (§F: the 3 s `stop_frame_timeout_s` churn; §"LOOK-AHEAD"),
  `docs/reports/R0015`/`R0016` (M2.4B.2/2A speech planner — now
  `OPERATOR-CONFIRMED`), `/tmp/nexa_m24b2_operator.jsonl` +
  `_console.txt` (the accepted pre-B.3 baseline run).

**Scope: the smallest CPU/config change — start the external Piper process
at `nice +10`, and raise Pipecat's `stop_frame_timeout_s` 3 s → 8 s. NO
refill controller, NO buffer scheduler, NO timer-based phrase release, NO
delayed first phrase, NO artificial sleep, NO speech acceleration, NO
fillers, NO M2.5. NO `sudo`, NO `taskset`, NO system/global scheduling
change; NeXa's own process and `llama-server` are untouched.
`ConversationSession` text/history and the speech-planner output rules are
unchanged.**

---

## TASK RESULT

**PASS (implementation + measured).** B.3.1 is a correct, low-risk
foundation: it guarantees Piper is never the CPU bottleneck and removes
`BotStopped`/`BotStarted` churn for short (3–8 s) stalls. It does **not**
remove the audible multi-second intra-response silence — the B.3.1
hardware comparison still shows **~8–11 s gaps**, `audio-s per wall-s
~0.5`, `diagnose = LLM TEXT PRODUCTION`. **M2.4B.3.2 (buffer-aware
look-ahead / refill) is still needed** — see DECISION GATE.

## B.2 / B.2A OPERATOR ACCEPTANCE

Fresh real-microphone acceptance run after M2.4B.2A. **Operator verdict:**

> *"The first run is expected to take longer. The spoken response itself is
> good if we ignore the pauses."*

**→ M2.4B.2 and M2.4B.2A are `OPERATOR-CONFIRMED` (2026-09-07).**

Evidence from that run:

- STT correct: *"Powiedz mi, z czego składa się gwiazda."*
- B.2A LaTeX fix visibly working: canonical model output contained
  `$\text{H}$` / `$\text{He}$`; Piper received *"wodoru (H) i helu (He)"*
  and later *"Wodór (H): …"* / *"Hel (He): …"* — **no literal LaTeX
  reached Piper**.
- `tiny_text_chunk_count = 0`.
- true Piper mean HTTP RTF = **0.268**.
- The ~48 s first-token latency was the first turn after startup (cold
  model load) — accepted, explicitly **not** a B.3 optimisation target.
- The run again hit a truncated final list item (*"Inne pier…"*) — the
  known `GenerationOptions.num_predict = 200` cap; a separate model-serving
  follow-up, **not** the cause of the intra-response silence.

## M2.4B.3.1 STATUS

**Implemented, unit-tested, and measured on hardware (scripted).**
Two product changes:

1. `PiperHttpConfig.nice` (default `10`) → the external Piper process is
   started at `nice +10`.
2. `nexa.voice_tts.DEFAULT_TTS_CONTEXT_TIMEOUT_S` (`8.0`) → passed to
   `PiperHttpTTSService(stop_frame_timeout_s=…)` by the probe.

No refill controller. The pipeline is exactly
`AssistantSpeechBridge → NexaSpeechPlanner → PiperHttpTTSService → output`;
the planner emits natural phrases exactly as before.

## PIPER NICE IMPLEMENTATION

**Where:** `src/nexa/tts/config.py` + `src/nexa/tts/server.py`.

- `PiperHttpConfig.nice: int = field(default_factory=default_piper_nice)`.
  `default_piper_nice()` = `NEXA_PIPER_NICE` env var if an integer, else
  `DEFAULT_PIPER_NICE = 10`; **negatives are clamped to 0** in
  `__post_init__` so a misconfiguration can never make Piper need
  privilege or fail to start. An explicit `PiperHttpConfig(nice=…)` wins
  over the env var.
- `PiperHttpServer._launch_prefix()` returns `(["<nice>", "-n", "10"], None)`
  when `nice > 0` and `shutil.which("nice")` succeeds — the coreutils
  `nice` binary, which `exec`s the target Python **in place**, so the
  child's PID *is* the Python process, just at the requested priority. No
  `preexec_fn` thread-safety caveat. Fallback (only if `nice` is somehow
  not on `PATH`): a `preexec_fn` that makes a single
  `os.setpriority(os.PRIO_PROCESS, 0, n)` syscall in the forked child (no
  locks, no allocation — the canonical safe use of `preexec_fn`).
  `nice == 0` → no prefix, no `preexec_fn` (byte-for-byte the pre-B.3.1
  launch).
- **Positive niceness needs no privilege.** NeXa's own process is never
  reniced (`os.getpriority` before/after building the prefix is
  unchanged — tested). `llama-server` is never touched. No
  `sudo`/`taskset`/`sched_setaffinity`/`setuid`/`SCHED_FIFO`/`SCHED_RR`/
  `pkexec` anywhere in `server.py` (grep-tested).
- **Lifecycle restart preserves the policy:** `start()` is idempotent and
  `stop()` clears `_process`; a stop→start cycle re-runs the same
  `create_subprocess_exec` with the same prefix (tested: two starts, both
  through `nice -n 10`).
- **Configurable:** `--piper-nice N` on the probe (default 10; `0`
  disables). `NEXA_PIPER_NICE` env var. `10` is the benchmark-backed
  default for this Pi 5 (R0014 strategy C).
- Integration proof of the mechanism: a trivial child launched through the
  exact same `nice -n 10` wrapper reports `os.getpriority(...) == 10`
  while the parent stays at `0`.

## TTS CONTEXT TIMEOUT

**Where:** `nexa.voice_tts.DEFAULT_TTS_CONTEXT_TIMEOUT_S = 8.0`, passed to
both `PiperHttpTTSService` and `TimedPiperHttpTTSService` by the probe as
`stop_frame_timeout_s`.

Re-inspected in installed Pipecat 1.8.1 (`services/tts_service.py`):
`_handle_audio_context` does
`await asyncio.wait_for(queue.get(), timeout=self._stop_frame_timeout_s)`;
each new phrase's `_push_tts_frames` pushes a `_CONTEXT_KEEPALIVE` that
resets the timer. So the timeout only fires when **no new phrase text
arrives for that long** — i.e. `gemma4:e4b` stalled mid-reply. On the
timeout it pushes a premature `TTSStoppedFrame` and the loop `break`s; the
next phrase then lands on `append_to_audio_context`'s "recreating the
context" path → a fresh `TTSStartedFrame` → downstream
`BotStopped`→`BotStarted`.

Raising it to **8 s** (`R0014` recommended ~8–10 s; 8 tested first, 10
available via `--tts-context-timeout-s`):

- **keeps one speaking context alive** across a normal inter-phrase stall
  of up to 8 s;
- **adds no silence** — it does not delay or pad anything; playback of
  already-queued audio is unaffected;
- the real end of a turn is still driven by `LLMFullResponseEndFrame` (the
  bridge's `on_assistant_complete`), which flushes and closes the context
  cleanly regardless of the timeout.
- Configurable; `DEFAULT_TTS_CONTEXT_TIMEOUT_S` is a CANDIDATE value.

## ARCHITECTURE INVARIANT

- Pipeline unchanged: `bridge → planner → PiperHttpTTSService → observer →
  transport`. No new processor, no scheduler, no queue controller, no
  timer, no pacing authority.
- Speech-planner output rules unchanged — same phrases, same boundaries,
  same normalization (all 59 B.2/B.2A planner tests still green).
- `ConversationSession` text/history unchanged (the B.3.1 changes are
  entirely in process-launch config + one Pipecat constructor kwarg;
  neither touches `nexa.conversation`).
- No dynamic speech speed (`length_scale` untouched), no fillers, no
  `buffered_audio_seconds` control, no B.3.2 scheduler, no M2.5.
- `nice == 0` / `stop_frame_timeout_s == 3.0` → byte-for-byte the
  pre-B.3.1 behaviour.

## BEFORE / AFTER HARDWARE METRICS

Scripted comparison (`docs/research/m2_4b_speech_flow/b31_compare.py`, raw
`b31_compare_raw_20260907.txt`): real `gemma4:e4b` + real Piper + real
`LocalAudioOutputTransport` + real `ConversationSession`; only the
microphone/whisper.cpp is replaced. **Protocol (per the brief):** one
warm-up question first (`"Ile jest osiem razy siedem?"`, **not** counted),
then the measured question (`"Powiedz mi, z czego składa się gwiazda."`).

**Caveat:** `gemma4:e4b` is non-deterministic, so the three runs produced
*different* replies (pre-B.3 baseline 668 chars / 8 chunks; CONTROL here
280 chars / 3 phrases; B.3.1 here 182 chars / 2 phrases). Gap *magnitude*
scales with reply length, so read the comparison **structurally**, not
number-for-number. Also: the scripted runs carry **less** CPU contention
than the operator's real mic run (no concurrent VAD/STT/resample), which
*under*-represents the `nice +10` benefit.

| metric | pre-B.3 (operator, accepted) | CONTROL (nice 0 / 3 s), this Pi | **B.3.1 (nice 10 / 8 s)**, this Pi |
|---|---|---|---|
| reply | 668 chars, 8 chunks (num_predict hit) | 280 chars, 3 phrases | 182 chars, 2 phrases |
| LLM first token (warm) | — | 3.06 s | 3.02 s |
| LLM generation duration | ~78 s | 34.2 s | 21.3 s |
| **LLM chars/s** | 8.5 | **8.2** | **8.5** |
| text chunk `since_prev` | — | 14.4 s, 13.5 s | 15.0 s |
| **true Piper mean HTTP RTF** | **0.268** | **0.259** | **0.255** |
| tts context spans | 8 | 3 | 2 |
| **BotStarted / BotStopped** | 4 / 4 | **3 / 3** | **2 / 2** |
| `silence_gaps_ms` | `[0, 39074, 0]` | `[10888, 7032]` | `[8116]` |
| **max gap** | **31.4 s** | **10.9 s** | **8.1 s** |
| **mean gap** | **14.3 s** | **9.0 s** | **8.1 s** |
| **audio-s per wall-s** | **0.45** | **0.50** | **0.53** |
| diagnosis | LLM TEXT PRODUCTION | LLM TEXT PRODUCTION | LLM TEXT PRODUCTION |
| CPU total mean / peak | ~99 % / 100 % | 83 % / 100 % | 75 % / 100 % |
| llama-server CPU mean / peak | — | **284 % / 387 %** | (sampler missed the PID) |
| temperature / throttled | 64–68 °C / `0x0` | 65.5 °C / `0x0` | 66.1 °C / `0x0` |

## INTRA-RESPONSE GAP RESULT

**A meaningful multi-second gap remains: ~8–11 s.**

The gap ≈ *(wall time to generate the next phrase's text)* −
*(seconds of audio the previous phrase bought)*:

- CONTROL: phrase #0 = **3.56 s** audio; phrase #1 text took **14.4 s** →
  gap **10.9 s**.
- B.3.1: phrase #0 = **3.86 s** audio; phrase #1 text took **15.0 s** →
  gap **8.1 s**.

Both runs' `stop_frame_timeout_s` was *exceeded* by the gap (10.9 s / 15.0 s
> 8 s), so the context still tore down and `BotStopped`/`BotStarted`
still fired once per gap. The 8 s timeout would only *prevent* a teardown
for a gap in the 3–8 s band (which the operator's earlier run did have —
a 2.1 s and a 7.7 s gap); it cannot help a 10–15 s gap, and **it never
removes silence** (an alive-but-textless context produces no audio).

## LLM GENERATION RESULT

**`nice +10` did not measurably speed up `gemma4:e4b` in these scripted
runs.** CONTROL `chars/s = 8.2`, B.3.1 `chars/s = 8.5` — within
reply-variance noise, and the same ~8.5 the operator's contended run
showed. Why: in the scripted scenario Piper synthesises for only ~1.4 s
once every ~14 s, so there is very little Piper-vs-LLM contention to
relieve — `llama-server` already got ~284–387 % CPU under CONTROL. R0014's
large `nice +10` win (`prompt_eval` 24 s → 0.9 s) came from a *continuous*
synthesis loop pinning all 4 cores; that is not this workload.
`nice +10` is still the right, free safeguard — it guarantees Piper can
never regress the LLM under a heavier future load (bigger voice, B.3.2
batching that synthesises more often) — but on its own it does not close
the gap.

## PIPER RTF RESULT

**Unaffected by `nice +10`** — true HTTP RTF 0.255 (B.3.1) vs 0.259
(CONTROL) vs 0.268 (pre-B.3). Piper stays **~4× real time** at `nice +10`.
Confirms R0014's core finding: deprioritising Piper does not make it slow.

## BOT START/STOP RESULT

Fewer cycles, but that tracks reply length, not the config: CONTROL 3/3
for a 3-phrase reply, B.3.1 2/2 for a 2-phrase reply — one
`BotStopped`/`BotStarted` per inter-phrase gap in both, because every gap
exceeded the (raised) timeout. The context-continuity change is
*correct* and will reduce churn for the 3–8 s stalls seen in the
operator's earlier run, but this comparison could not exercise that band.

## TEST RESULTS

- `tests/test_tts_server.py` — new `TestPiperNicePriority` (10 tests) +
  `TestPiperNiceRealChildPriority` (1 integration test): default nice is
  10; configurable + negative clamped; env override but explicit arg
  wins; child launched through `nice -n 10 <python> -m piper.http_server`;
  `nice 0` → python launched directly, no wrapper, no `preexec_fn`;
  lifecycle restart re-applies the policy; `preexec_fn` fallback when
  `nice` missing; **no `sudo`/`taskset`/affinity/`setuid` in `server.py`**;
  **NeXa's own process priority never changed**; the real `nice -n 10`
  wrapper actually sets a child's priority to 10 while the parent stays 0.
- `tests/test_voice_tts_bridge.py` — new `TestTtsContextTimeout` (3
  tests): `DEFAULT_TTS_CONTEXT_TIMEOUT_S` is a positive float > Pipecat's
  3.0; `PiperHttpTTSService(stop_frame_timeout_s=8.0)` stores
  `_stop_frame_timeout_s == 8.0` (default stays 3.0);
  `TimedPiperHttpTTSService` forwards the kwarg.
- Full suite: **`pytest` 387 passed / 7 skipped / 14 subtests**;
  **`python -m unittest discover -s tests` 394 OK, 7 skipped**.
- `ruff check src tests apps scripts/setup_piper_http.py` — clean.
  `git diff --check` — clean.
- All 59 B.2/B.2A speech-planner tests + 69 metrics tests still green
  (planner output rules unchanged).

## FILES CHANGED

| file | change |
|---|---|
| `src/nexa/tts/config.py` | `DEFAULT_PIPER_NICE = 10` + `default_piper_nice()` (env `NEXA_PIPER_NICE`); `PiperHttpConfig.nice` field (factory default, negatives clamped to 0 in `__post_init__`) |
| `src/nexa/tts/server.py` | `_launch_prefix()` — start Piper via `nice -n N` exec prefix (`preexec_fn`+`os.setpriority` fallback); `start()` uses it; info log |
| `src/nexa/tts/__init__.py` | export `DEFAULT_PIPER_NICE` |
| `src/nexa/voice_tts/__init__.py` | `DEFAULT_TTS_CONTEXT_TIMEOUT_S = 8.0` (documented; exported) |
| `apps/nexa_voice_tts_probe.py` | `--piper-nice` / `--tts-context-timeout-s` flags; `PiperHttpServer(PiperHttpConfig(nice=…))`; `stop_frame_timeout_s=…` on both TTS-service constructors |
| `tests/test_tts_server.py` | +11 B.3.1 tests (Piper nice) |
| `tests/test_voice_tts_bridge.py` | +3 B.3.1 tests (TTS context timeout) |
| `docs/reports/R0017_…md` | **new** — this report |
| `docs/research/m2_4b_speech_flow/b31_compare.py` + `b31_compare_raw_20260907.txt` | **new** — scripted before/after harness + raw |
| `docs/research/m2_4b_speech_flow/README.md` | B.3.1 note |
| `docs/CURRENT_STATE.md` | B.2/B.2A `OPERATOR-CONFIRMED`; B.3.1 done; next = B.3.2 |

Not changed: `nexa.voice`, `nexa.stt`, `nexa.voice_conversation`,
`nexa.conversation`, `nexa.providers`; `pyproject.toml`; the speech
planner; the frozen M2.4 path. No `num_predict`, no STT, no cold-start, no
voice style, no fillers, no barge-in, no dynamic speech speed, no refill
controller.

## COMMIT HASH

One coherent local commit — `feat: prioritize LLM during realtime speech`
(tip of `main`; see `git log -1`).

## GIT STATUS

Branch `main`, working tree clean, **9 commits ahead of `origin/main`, not
pushed**. `git diff --check` clean. No voice/model binaries, no CPU
scheduling *policy* beyond the NeXa-owned Piper child's `nice` value, no
B.3.2 pacing, no M2.5.

## IS B.3.2 STILL NEEDED?

**YES.**

The B.3.1 comparison still shows **~8–11 s intra-response gaps**,
`audio-s per wall-s ≈ 0.5`, and every measured turn diagnoses `LLM TEXT
PRODUCTION`. The mechanism is a fixed ratio: the first natural phrase
buys ~4 s of audio, while `gemma4:e4b` needs ~14–15 s of wall time to
produce the next phrase's text. CPU priority (B.3.1) does not change that
ratio in this workload, and a longer context timeout keeps the context
*alive* but produces no audio during the stall. Closing a 3–4× deficit
needs **look-ahead batching** — emit phrase 1 immediately, then batch and
hold subsequent phrases so one synthesis call buys ~15–20 s of audio,
covering the next LLM stall.

**Recommend M2.4B.3.2 — buffer-aware look-ahead / refill controller.**
Not implemented in this run — the operator hears B.3.1 first.

### The B.3.2 product rule (record for the next task)

- **Do NOT hold a ready natural phrase merely to grow a batch while the
  audio buffer is low.**
- **BUFFER HEALTHY** → may wait briefly for a better/larger natural batch.
- **BUFFER LOW** → immediately synthesize the next already-complete
  natural phrase.
- **BUFFER NEAR EMPTY** → refill has priority.
- Never accelerate the voice. Never insert artificial silence. The
  controller must **reduce** latency, never create it.
- Control variable: `buffered_audio_seconds` (re-validate as a control
  signal first — R0014 "REAL BUFFER-ESTIMATE DISCREPANCY").

## NEXT STEP

**M2.4B.3.2** — buffer-aware look-ahead / refill controller (rule above).
Parallel, non-blocking: Ollama `keep_alive` (first-token eviction), the
STT-quality track (`"horyzont zdarzeń"` mistranscription), and a
`num_predict = 200` review if the operator wants long replies to finish.
Then **M2.5 — barge-in**, replacing the temporary half-duplex gate.

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

Incremental as instructed (B.3.1 alone, measured before B.3.2); the
smallest mechanism (`nice` binary, one Pipecat kwarg); no `sudo`/system
change; the scripted-comparison limitations (reply non-determinism, lower
contention than the mic run) disclosed; the DECISION GATE answered from
the evidence, not the prior plan. No gap exposed.
