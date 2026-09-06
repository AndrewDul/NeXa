# R0014 — M2.4B.1A: CPU contention / realtime scheduling spike

- **Date:** 2026-09-07
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.1A — research + benchmark
  spike, plus instrumentation-accuracy fixes**
- **Related:** `docs/reports/R0013_m2_4b_1_gap_profiler_20260906.md`
  (the M2.4B.1 profiler this corrects and extends),
  `docs/reports/R0012_m2_4b_speech_flow_research_20260906.md`,
  `docs/architecture/M2_4_STREAMING_TTS_ARCHITECTURE.md` (frozen M2.4 —
  commit `36ec1e4`), `docs/research/m2_4b_speech_flow/` (harnesses + raw).

**Research/benchmark + instrumentation-accuracy corrections only. No CPU
scheduling product policy is shipped. No speech behaviour changed — the
timing wrapper yields an identical frame stream; with no `--report` flag
the probe is the frozen M2.4 path. No semantic chunking, no dynamic speech
speed, no fillers, no M2.5.**

---

## TASK RESULT

**PASS (spike + metric fixes).**

- **The primary product failure is confirmed and characterised:** long
  silence *inside* one assistant response — the audio pipeline running dry
  mid-reply because `gemma4:e4b` cannot produce text fast enough while
  Piper synthesis is also running on the shared 4 cores.
- **Key answer: Piper on ONE Pi 5 core still synthesizes `pl_PL-gosia-medium`
  at RTF ≈ 0.49 — comfortably ~2× faster than real time.** Piper is never
  the bottleneck; it needs at most 1 core.
- **Recommended topology (benchmark-verified): renice the Piper HTTP server
  process to +10, leave affinity alone.** This is the one strategy whose
  LLM recovery was measured cleanly: `gemma4:e4b` `prompt_eval` **24 s →
  0.91 s**, generation **1.3 → 3.1 tok/s** (back to uncontended), while
  Piper still synthesised at RTF 0.44. No `sudo`, no PID supervisor, no
  system change — Piper is NeXa-owned. Pinning Piper to 1 core (no nice) is
  a harder-walled alternative to confirm in B.3.
- **Contention is severe when both run free:** with a Piper synthesis loop
  competing on all 4 cores, `gemma4:e4b` `prompt_eval` went **~0.7 s →
  24 s** and generation **~2.9 → 1.3 tok/s**. This is the mechanism behind
  the operator's intra-response silence.
- **B.1 metric corrections applied and committed separately** (`fix:
  correct realtime speech flow metrics`): the R0013 `TtsSegment` "RTF"
  measured the Pipecat *audio-context lifecycle span*, not HTTP synthesis —
  it read ~0.7–0.9 when true synthesis RTF is ~0.14–0.24, which spuriously
  produced `Dominant wait: TTS SYNTHESIS`. A measurement-only
  `TimedPiperHttpTTSService` now records the true per-request HTTP timing;
  `diagnose_dominant_wait` uses it. `estimate_vs_real_stop_error_s` used the
  *nearest* `BotStopped` in either direction — an ambiguous quantity for a
  bursty multi-`BotStopped` reply (the operator's ≈ 18 s). Redefined as
  `buffer_drain_to_stop_lag_s` (delay to the **next** `BotStopped`) +
  `underruns_without_following_stop`.

## REAL OPERATOR GAP EVIDENCE

From the operator's M2.4B.1 `--report` baseline (their run; numbers as
relayed): intra-response silences of **≈ 11.6 s** and **≈ 22.4 s**, and one
turn with `estimate_vs_real_stop_error ≈ 18 s`. The ~48 s first question is
a cold model load (`load_duration`, R0013) — accepted, not the complaint.

**Mechanism reproduced** two ways here:

1. *Contention* (`cpu_strat_fast.py`, CONTROL): a continuous Piper
   synthesis loop on the shared 4 cores drives `gemma4:e4b` `prompt_eval`
   to 24 s and generation to 1.3 tok/s. A reply that needs ~40 tokens then
   takes ~30 s of wall time to generate — far slower than it is spoken —
   so the audio buffer empties mid-reply. This is the primary path to the
   operator's silence.
2. *Bursty feed* (`buffer_validation.py`, 3rd scenario: 9 s + 7 s
   deliberate mid-reply stalls, real M2.4 output path; raw
   `buffer_validation_raw_20260907.txt`):
   - real audible gaps `gaps_ms = [6455, 3536]`, buffer estimate
     `min = −10.0 s`, 1 underrun.
   - `BotStopped@[11.78, 27.26, 38.58]` — one reply, three stops (context
     idle-timeout churn), which is what made the old nearest-stop metric
     ambiguous.
   - corrected `buffer_drain_to_stop_lag_s = −0.03 s`,
     `underruns_without_following_stop = 0` — the estimate and the
     transport agree on when the drain happened.
   - `diagnosis = LLM TEXT PRODUCTION` (correct — the injected LLM stall,
     not TTS).
   - `context-span "RTF" mean = 0.60` while true synthesis is ~0.24 — the
     metric-conflation bug, visible in the same run.

## CURRENT PROCESS / THREAD TOPOLOGY

`VERIFIED FACT`, this Pi 5 (Cortex-A76 ×4, 1 thread/core), 2026-09-07:

| process | cmd | threads (idle → active) | notes |
|---|---|---|---|
| `ollama` (serve) | `/usr/local/bin/ollama serve` | 11 (Go runtime) | systemd `ollama.service`, no `OLLAMA_*` thread env set |
| **`llama-server`** | `/usr/local/lib/ollama/llama-server --model …blobs/sha256-4c27… --port <p> -c 4096 -np 1 -b 512 -ub 512 --flash-attn auto --context-shift --keep 4` | **13 total; ~3–4 active compute** (main + ~8-idle pool + 3 with the utime) | **no `--threads` flag → llama.cpp defaults to nproc = 4 compute threads.** RSS ~10 GB. Spawned by `ollama`; **PID changes on every model reload.** |
| **`piper.http_server`** | external venv `python -m piper.http_server -m <voice> --data-dir <voices> --port 5001` | **5 idle → 8–13 during synthesis** | onnxruntime CPU EP spawns ~3 worker threads under load. Flask dev server (serialises HTTP), RSS ~46 MB idle. **NOT single-threaded.** |

## CURRENT CPU AFFINITY / PRIORITY

All three: **affinity `0-3` (all 4 cores), `nice 0`, ordinary CFS
(`SCHED_OTHER`).** No `taskset`, no `cpuset`, no `nice`, no cgroup CPU
limit is applied anywhere today. `os.sched_getaffinity(0)` for the NeXa /
probe process = `{0,1,2,3}`.

`llama-server` is owned by user `ollama` — the `devdul` user **cannot**
`taskset`/`renice` it directly (`Operation not permitted`); passwordless
`sudo` is available and was used for the benchmark, restored after every
case. Piper is spawned by NeXa's own user → freely adjustable.

## PIPER ONE-CORE RESULT

**YES — Piper on one Pi 5 core is comfortably faster than real time.**
`docs/research/m2_4b_speech_flow/piper_cores.py` — 6 reps of a fixed
`pl_PL-gosia-medium` workload per core budget (`taskset -a -pc`), uncontended:

| Piper affinity | mean RTF | worst RTF | cores actually used | faster than real time? |
|---|---|---|---|---|
| `0-3` (all 4, control) | **0.137** | 0.142 | ~2.47 | YES (~7×) |
| **`3` (1 core)** | **0.485** | **0.496** | ~0.77 | **YES — ~2× headroom** |
| `2,3` (2 cores) | 0.234 | 0.243 | ~1.49 | YES |
| `1,2,3` (3 cores) | 0.195 | 0.198 | ~2.12 | YES |

Under a *concurrent* `gemma4:e4b` load, 1-core Piper measured **RTF ≈ 0.49–0.52**
(unchanged — it is core-bound, not contention-sensitive at 1 core).

**Minimum allocation Piper needs to stay comfortably faster than real time:
1 core.** More cores make it faster but it is never the bottleneck; the
spare capacity is better given to the LLM.

## CPU STRATEGY BENCHMARK

`docs/research/m2_4b_speech_flow/cpu_strat_fast.py` — per strategy: apply
affinity/nice (sudo for `llama-server`), run a concurrent LLM-generation
loop + Piper-synthesis loop for ~35 s, measure, restore. Ordinary CFS only
(no `SCHED_FIFO`/`RR`, no realtime). Raw:
`docs/research/m2_4b_speech_flow/cpu_strat_fast_raw_20260907.txt`.

Five strategies, ~35 s concurrent window each, `num_predict:24`, 90 s
hard per-request timeout. Full raw table:

| strategy | Piper affinity / nice | LLM `prompt_eval` | LLM `tok/s` | LLM n / err | Piper RTF mean / max | Piper audio-s per wall-s | per-core CPU |
|---|---|---|---|---|---|---|---|
| **CONTROL** | `0-3` / 0 | **24.24 s** | **1.27** | 1 / 0 | 0.28 / 0.33 | 2.93 | 99 / 99 / 99 / 99 |
| **A** — llama `0-2`, Piper `3` | `3` / 0 | — (timeout) | — | 0 / 1 | 0.49 / 0.52 | 0.80 | 100 / 100 / 100 / **49** |
| **B** — llama `0-1`, Piper `2-3` | `2,3` / 0 | — (timeout) | — | 0 / 1 | 0.29 / 0.34 | 1.42 | 100 / 100 / 45 / 44 |
| **C** — Piper **nice +10**, no affinity | `0-3` / **+10** | **0.91 s** | **3.09** | **1 / 0** | 0.44 / 0.55 | 1.77 | 99 / 99 / 99 / 99 |
| **D** — llama `0-2`, Piper `3` **+ nice +5** | `3` / **+5** | — (timeout) | — | 0 / 1 | **0.88 / 0.90** | 0.44 | 100 / 100 / 100 / 45 |

`temp` 60–62 °C, `throttled=0x0` throughout — **no thermal throttling**,
memory steady, no swap.

**Caveat — the timeouts in A/B/D are model-eviction noise, not the
strategy.** `gemma4:e4b`'s 5-minute `keep_alive` lapsed between cases, so
the first LLM request in those cases paid a ~31 s reload (`load_duration`,
R0013) and then, generating under contention, exceeded the 90 s cap. Their
**Piper** numbers and **per-core CPU** are clean; their **LLM** numbers are
unusable. Re-running with a warm-keep-alive ping per case is a
B.3 follow-up. What the benchmark **does** establish cleanly:

1. **CONTROL is the failure case, reproduced:** with both processes free on
   4 cores a continuous Piper synthesis loop drives `gemma4:e4b`
   `prompt_eval` to **24 s** and generation to **1.3 tok/s** — this *is*
   the mechanism behind the operator's intra-response silence.
2. **C (Piper niced +10, no affinity) is the one clean, uncontaminated LLM
   measurement, and it is a decisive win:** `prompt_eval` **0.91 s**,
   **3.09 tok/s** — i.e. essentially the uncontended warm-`gemma4:e4b`
   figures (R0013), while Piper *still* synthesised continuously at RTF
   **0.44** (≈ 2.3× real time). CFS gave the LLM's nice-0 threads the
   cycles; Piper's nice-+10 threads yielded. All four cores stayed 99 %
   busy — the LLM got the work done because the *scheduler* arbitrated,
   not because cores were idle.
3. **A confirms 1-core containment works mechanically:** core 3 sat at
   49 % (Piper alone), cores 0–2 pegged (LLM alone); Piper RTF 0.49 =
   its uncontended 1-core figure. Piper is fully walled off.
4. **D is a warning: never stack 1-core affinity *and* nice on Piper.**
   Piper on one core, niced, against a 3-core-saturated LLM was **starved
   to RTF 0.88** — real-time headroom nearly gone. Affinity and nice are
   alternatives, not a combination.
5. Piper's CPU-while-synthesising falls from ~2.5 cores (free) to ~0.77
   cores (1-core pinned); niced but unpinned it took ~1.77 audio-s/wall-s
   worth of work without blocking the LLM.

## BEST VERIFIED CPU TOPOLOGY

**Renice the Piper HTTP server process to +10. Leave affinity alone
(strategy C).** This is the topology the benchmark actually verified with a
clean LLM measurement.

- `renice -n 10 -p <piper_pid>` (or start it under `nice -n 10`). Piper is
  spawned by NeXa's own user — **no `sudo`, no capability, no system
  change**. It survives `llama-server` model reloads because it never
  touches `llama-server`. One call, no supervisor loop.
- Measured effect: LLM `prompt_eval` 24 s → **0.91 s**, `tok/s` 1.3 →
  **3.1** (back to uncontended), Piper RTF still **0.44** (≈ 2.3× real
  time — it always refills the buffer between phrases).
- **Alternative if nice proves too soft under a heavier future load:** pin
  Piper to 1 core (`taskset -a -pc 3 <piper_pid>`) *without* nice
  (strategy A). Also NeXa-owned, no privilege. Mechanically it is a harder
  wall (Piper RTF 0.49, LLM gets 3 clean cores), but its LLM benefit was
  not cleanly measured here (eviction) and must be confirmed in B.3 with a
  warm model. Do **not** also renice a 1-core-pinned Piper — strategy D
  shows that starves it (RTF 0.88).
- `llama-server` affinity was **not** the useful lever: it is owned by the
  `ollama` user (needs `sudo`), its PID churns on every model reload
  (needs a supervisor, not a one-shot), and strategy C gets the LLM back
  to full speed *without* touching it. An `ollama.service` drop-in
  (`CPUAffinity=`, or `Nice=`) would be the clean way to do it if ever
  needed — a deliberate, operator-approved system change, i.e. an
  ADR-level decision, not this spike's to make.

**This is a recommendation for the M2.4B.3 implementation task. No
scheduling change is shipped in B.1A.**

## LLM PERFORMANCE BY STRATEGY

| strategy | first-token* | `prompt_eval` | `tok/s` | usable? |
|---|---|---|---|---|
| CONTROL (free/free) | 24.3 s | **24.24 s** | **1.27** | yes — the failure case |
| A — llama `0-2` / Piper `3` | — | — | — | no — model eviction, request timed out |
| B — llama `0-1` / Piper `2-3` | — | — | — | no — model eviction, request timed out |
| **C — Piper nice +10** | 41.3 s* | **0.91 s** | **3.09** | **yes — the clean win** |
| D — llama `0-2` / Piper `3` + nice +5 | — | — | — | no — model eviction, request timed out |

\* first-token in C includes a ~31 s cold model reload (`load_duration`,
eviction between cases); the `prompt_eval` and `tok/s` that follow it are
the warm, under-contention figures and are the meaningful numbers. R0013's
uncontended warm baseline is `prompt_eval` ~0.7 s / ~2.9 tok/s — C matches
it. CONTROL's 24 s `prompt_eval` is the contention cost this stage exists
to remove.

## PIPER RTF BY STRATEGY

TRUE per-request HTTP synthesis RTF (`pl_PL-gosia-medium`, 22050 Hz), each
under a concurrent `gemma4:e4b` load:

| strategy | Piper true HTTP RTF (mean / worst) | faster than real time |
|---|---|---|
| CONTROL — free on 4 cores | 0.28 / 0.33 | YES (~3.5×) |
| A — pinned to 1 core | 0.49 / 0.52 | YES (~2×) |
| B — pinned to 2 cores | 0.29 / 0.34 | YES (~3.4×) |
| **C — nice +10, no affinity** | **0.44 / 0.55** | **YES (~2.3×)** |
| D — 1 core **+** nice +5 | 0.88 / 0.90 | YES but headroom gone |

Piper stays faster than real time in **every** tested strategy. Only D
(the stacked one, explicitly not recommended) gets close to the RTF = 1.0
line.

## TRUE PIPER HTTP SYNTHESIS TIMING

The R0013 `TtsSegment.synthesis_duration_s` = `TTSStoppedFrame` −
`TTSStartedFrame`, i.e. the **Pipecat audio-context lifecycle span**. That
span is inflated by (a) playback drain — the context stays open while
buffered audio plays out at real time — and (b) the 3 s
`stop_frame_timeout_s` idle wait. Measured live: context-span "RTF" ≈
**0.70–0.92** for the same sentences whose **true HTTP synthesis RTF** was
**0.24** (CONTROL) / **0.14** (uncontended). A ~4–6× overstatement.

**Corrected instrumentation** (`nexa/voice_tts/timed_tts.py`,
`--report`-only): `TimedPiperHttpTTSService(PiperHttpTTSService)` iterates
`super().run_tts(...)` and re-yields every frame unchanged, wrapping a
stopwatch around it. Per HTTP request it records `HttpSynthCall`:
`http_wall_s` (request issued → whole WAV received; Piper returns it in one
body, so `ttfb_s ≈ http_wall_s`), `audio_s`, `http_rtf = http_wall_s /
audio_s`. `TurnMetrics.mean_http_rtf` is the TRUE synthesis RTF;
`TtsSegment.synthesis_*` was renamed `context_span_*` and is diagnostic
only; `diagnose_dominant_wait` now uses `mean_http_rtf`.

## B.1 METRIC CORRECTIONS

Committed separately as `fix: correct realtime speech flow metrics`.

1. **True HTTP synthesis timing** — `TimedPiperHttpTTSService` +
   `HttpSynthCall`; `TurnMetrics.http_calls`, `mean_http_rtf`,
   `mean_http_synthesis_s`. JSONL gains `tts_http_synthesis`.
2. **`TtsSegment` renamed** `synthesis_duration_s` → `context_span_s`,
   `synthesis_rtf` → `context_span_rtf` (+ `mean_context_span_rtf`), with
   docstrings stating it is NOT synthesis speed. `render_turn_report`
   prints both, labelled.
3. **`diagnose_dominant_wait`** now classifies `TTS SYNTHESIS` only from
   the **true** `mean_http_rtf` (≥ 0.85). An inflated context-span ratio
   alone no longer triggers it (regression test).
4. **`estimate_vs_real_stop_error_s` redefined** →
   `buffer_drain_to_stop_lag_s`: for each buffer-underrun event, the delay
   to the **next** `BotStoppedSpeaking` (not the nearest in any
   direction). Healthy ≈ 3 s (transport fallback). New
   `underruns_without_following_stop` counts underruns the transport never
   confirmed. JSONL keeps `estimate_vs_real_stop_error_s` as an alias to
   the corrected metric.
5. **`_head()` skips finalised turns** — a stray late TTS frame after a
   turn was finalised is no longer re-attributed to it.
6. **`audio_seconds_per_wall_second`** added (audio produced ÷ the turn's
   spoken window) — the "did the pipeline keep up" number the task asked
   for; ~1.0 = kept up, < 1.0 = ran dry.

`render_turn_report` / `to_dict` updated. 67 deterministic tests in
`tests/test_voice_tts_metrics.py` (was 57); full suite **313 passed** (was
303). `ruff` clean.

## REAL BUFFER-ESTIMATE DISCREPANCY

The operator's `estimate_vs_real_stop_error ≈ 18 s` was measured by an
**ambiguous metric**: the R0013 property took `min(|underrun_event −
stop|)` over **all** of the turn's playback-span stops — the *nearest*
`BotStopped` in either direction. For a bursty reply that drains mid-way,
resumes, and produces several `BotStopped`s (context idle-timeout churn),
that nearest stop can be an unrelated one, and the resulting number has no
consistent meaning. The scripted R0013 validation never exposed it because
its steady feed produced a single continuous playback span.

**Redefined** as `buffer_drain_to_stop_lag_s` — for each underrun event,
the delay to the **next** `BotStopped` only (`None` if none follows), plus
`underruns_without_following_stop`. This is a directed, interpretable
quantity: small = the transport confirmed the drain promptly (healthy);
large = the estimate said "drained" and no stop followed for that long (a
real discrepancy worth chasing).

Reproduced on hardware with a deliberately bursty feed — 9 s quiet before
sentence 3, 7 s before sentence 4, through the real M2.4 output path
(`docs/research/m2_4b_speech_flow/buffer_validation.py`, third scenario;
raw `buffer_validation_raw_20260907.txt`):

- `BotStopped@[11.78, 27.26, 38.58]`, one `underrun-event@[11.81]`, two
  real audible gaps `gaps_ms=[6455, 3536]`.
- **`buffer_drain_to_stop_lag_s = -0.03 s`** — the estimate crossed zero at
  11.81 s and the transport's `BotStopped` was at 11.78 s. They agree to
  within 30 ms. `underruns_without_following_stop = 0`.
- `diagnosis = LLM TEXT PRODUCTION` — correctly attributes the silence to
  the injected LLM stall, **not** TTS (the old inflated context-span RTF
  would have mis-fired here; `mean_http_rtf` is `None` in this harness — no
  `TimedPiperHttpTTSService` — so the classifier abstains from TTS).
- Non-bursty scenarios (3 tok/s, 2 tok/s): 0 underruns, `lag = None`,
  0 gaps — unchanged from R0013.

**What could NOT be ruled out** without the operator's raw JSONL: whether
their specific 18 s was purely the nearest-stop ambiguity, or a genuine
18 s-late `BotStopped` after a real drain (which the redefined metric would
now report *as* an 18 s lag — correctly, as a signal). Candidate
mechanisms still open: `stop_frame_timeout_s` context recreation ordering,
a delayed downstream `BotStopped`, audio already queued in the transport
at the reference instant.

**Verdict:** the estimate is a sound *diagnostic* once the comparison
metric is directed. It is **not yet cleared as a control signal** — before
M2.4B.3 keys synthesis off `buffered_audio_seconds` it must be re-validated
on the operator's real bursty `gemma4:e4b` `--report` baseline (with
`TimedPiperHttpTTSService` wired in), confirming `buffer_drain_to_stop_lag_s`
stays within a few seconds and `underruns_without_following_stop` = 0.

## RECOMMENDED FUTURE REFILL POLICY

**Research only — not implemented in B.1A.** Control variable =
`buffered_audio_seconds` (validated diagnostic; to be re-cleared as a
control signal in B.3). Timers and speech acceleration are explicitly out.

```
LLM generation ──▶ future text ──▶ speech-planner queue
                                          │
                            buffered_audio_seconds ?
                    ┌──────── healthy (≥ target) ────────┐
                    │  do NOT synthesize;                │
                    │  LLM keeps its 3 cores             │
                    └───────────────────────────────────┘
                    ┌──────── low (< target) ────────────┐
                    │  one short Piper burst of the next │
                    │  prepared phrase/batch;            │
                    │  Piper RTF ~0.44 refills the buffer │
                    │  in a fraction of the audio it adds │
                    └───────────────────────────────────┘
                    Piper completes ──▶ CPU returns to the LLM
```

- **CPU topology it assumes:** Piper niced +10 (this spike's
  recommendation), so a Piper burst never blocks the nice-0 LLM even while
  both are runnable; between bursts Piper is idle and the LLM has all 4
  cores at full weight.
- **`target`:** the R0012/R0013 candidate range (0.5–3.0 s) stands; with
  Piper RTF ≈ 0.44 (niced, under load) a burst of one ~6 s sentence costs
  ~2.6 s wall and adds ~6 s of buffer, so a `target` of ~1.5–2.0 s should
  never underrun while keeping first-audio latency low. **Not decided —
  operator A/B in B.3/B.4.**
- **Raise `stop_frame_timeout_s`** (3 s → ~8–10 s) so an inter-sentence LLM
  stall does not fragment the audio context — supported by R0013's "the
  BotStopped/BotStarted churn is cosmetic while the buffer holds".
- The controller is **B.3**. B.1A only establishes that the CPU topology
  makes the policy feasible.

## WHAT B.2 SHOULD DO

Unchanged from R0013: TTS-only Polish-aware phrase segmentation + markdown/
abbreviation normalisation, emitting `AggregatedTextFrame`; one phrase per
boundary, no pacing. Judge against the operator's `--report` baseline
(now with corrected `mean_http_rtf` and `buffer_drain_to_stop_lag_s`).
B.2 does **not** touch CPU scheduling.

## WHAT B.3 SHOULD DO

1. **Apply the scheduling recommendation: `renice -n 10` the Piper HTTP
   server process at startup** (NeXa spawns it — one call, no privilege, no
   supervisor). Re-measure a warm `gemma4:e4b` reply under `--report` to
   confirm `prompt_eval` / `tok/s` hold near uncontended and Piper RTF
   stays < 0.6.
2. Implement the CPU-aware refill policy above, keyed on
   `buffered_audio_seconds`.
3. **First re-validate the buffer estimate as a control signal** on a real
   bursty `gemma4:e4b` turn (operator `--report`, with
   `TimedPiperHttpTTSService` wired in): `buffer_drain_to_stop_lag_s`
   within a few seconds, `underruns_without_following_stop` = 0. Until then
   the estimate is diagnostic-only.
4. Raise `stop_frame_timeout_s` to ~8–10 s so an inter-sentence LLM stall
   does not fragment the audio context into multiple `BotStopped`s.
5. Sweep `target` ∈ {0.5,1,1.5,2,3} s against `output_underrun_count`,
   `silence_gaps_ms`, `audio_seconds_per_wall_second`, and the LLM's
   `tok/s` from `--report` `resources` — operator A/B picks it.
6. **If nice proves too soft** under the real B.2 phrase-batched load,
   fall back to pinning Piper to 1 core (`taskset`, still NeXa-owned, no
   privilege) — but never nice *and* pin (strategy D starves Piper).
7. Parallel first-token track (R0013): raise Ollama `keep_alive` (eviction
   is the ~31 s stalls) — independent of this spike.

## RISKS

- **The `nice +10` win rests on one clean sample.** n=1 for strategy C's
  LLM figure (eviction ate A/B/D). The mechanism is sound (nice-0 vs
  nice-+10 under CFS on 4 saturated cores) and Piper's RTF is confirmed
  across reps, but B.3 must re-confirm the LLM side with a warm model.
- **`nice` is a soft lever.** Under a heavier future load (larger voice,
  B.2's batched phrases arriving faster) CFS may still let Piper perturb
  the LLM. Fallback = 1-core `taskset` on Piper (measured to work
  mechanically; strategy A). Never combine the two — strategy D starved
  Piper to RTF 0.88.
- **`llama-server` is deliberately left untouched** — it is `ollama`-owned
  (needs `sudo`), its PID churns on every model reload (needs a supervisor,
  not a one-shot), and strategy C recovers the LLM without it. If it is
  ever needed, the clean route is an `ollama.service` drop-in (`Nice=` or
  `CPUAffinity=`) — an operator-approved system change, ADR-level, not this
  spike's to make.
- **Benchmark A/B/D LLM numbers are eviction-contaminated** and unusable;
  only CONTROL (the failure case) and C (the win) gave clean LLM data.
- **1-core / niced Piper RTF headroom (~2×) is for `pl_PL-gosia-medium`
  medium at 22050 Hz.** A future warmer voice or `length_scale > 1` must be
  re-benched before relying on the topology.

## FILES CHANGED

| File | Change | Commit |
|---|---|---|
| `src/nexa/voice_tts/timed_tts.py` | **new** — `TimedPiperHttpTTSService` + `HttpSynthCall` (measurement-only) | metric fix |
| `src/nexa/voice_tts/metrics.py` | `TtsSegment` context-span rename; `http_calls`/`mean_http_rtf`/`mean_http_synthesis_s`/`mean_context_span_rtf`; `buffer_drain_to_stop_lag_s` + `underruns_without_following_stop`; `audio_seconds_per_wall_second`; `_head()` skips finalised; `diagnose_dominant_wait` uses true RTF; report/JSONL updated | metric fix |
| `src/nexa/voice_tts/__init__.py` | export `TimedPiperHttpTTSService`, `HttpSynthCall` | metric fix |
| `apps/nexa_voice_tts_probe.py` | `--report` uses `TimedPiperHttpTTSService` (wired to `MetricsCollector.http_synthesis`) | metric fix |
| `tests/test_voice_tts_metrics.py` | +10 tests (67 total) for the corrections | metric fix |
| `docs/reports/R0014_m2_4b_1a_cpu_scheduling_spike_20260907.md` | **new** — this report | docs (not committed with the fix) |
| `docs/research/m2_4b_speech_flow/piper_cores.py` + `piper_cores_raw_20260907.txt` | **new** — 1/2/3/4-core Piper RTF sweep + raw | docs |
| `docs/research/m2_4b_speech_flow/cpu_strat_fast.py` + `cpu_strat_fast_raw_20260907.txt` | **new** — 5-strategy contention benchmark + raw | docs |
| `docs/research/m2_4b_speech_flow/buffer_validation.py` | modified — bursty-stall scenario + corrected-metric output | docs |
| `docs/research/m2_4b_speech_flow/buffer_validation_raw_20260907.txt` | **new** — corrected-metric hardware run (incl. bursty) | docs |
| `docs/research/m2_4b_speech_flow/README.md` | modified — M2.4B.1A additions section | docs |

Not changed: `nexa.voice` / `nexa.stt` / `nexa.voice_conversation` /
`nexa.conversation` / `nexa.providers` / `nexa.tts`; `pyproject.toml`; the
frozen M2.4 speech path. No CPU scheduling policy code and no
`renice`/`taskset` call ships in this stage.

## TESTS

`tests/test_voice_tts_metrics.py`: **67 passed** (was 57). Full suite:
**313 passed, 7 skipped, 14 subtests passed** (was 303). `ruff check src
tests apps scripts/setup_piper_http.py`: clean. `git diff --check`: clean.
New coverage: true HTTP RTF math + `TimedPiperHttpTTSService` yields an
identical stream (same frame objects, same order) + builds no frames (ast)
+ imports no model/conversation (ast); `buffer_drain_to_stop_lag_s` uses
the next stop only; `underruns_without_following_stop`;
`audio_seconds_per_wall_second`; `_head()` skips a finalised turn;
`diagnose` ignores the context-span ratio.

## GIT STATUS

Two local commits on `main`: (1) `fix: correct realtime speech flow
metrics` — the instrumentation-accuracy corrections + tests; (2) `docs:
record M2.4B.1A CPU scheduling spike` — this report + research scripts.
**Not pushed.** `llama-server` and Piper affinity + nice verified restored
to `0-3` / `0` after every benchmark case. No system configuration
permanently altered (no unit-file edit, no sysctl, no cgroup, no
`renice`/`taskset` left in place).

## NEXT RECOMMENDED ACTION

**M2.4B.2** (Polish phrase segmentation + TTS-only normalisation) — CPU
scheduling untouched. Then **M2.4B.3**: `renice -n 10` the Piper process at
startup, implement the `buffered_audio_seconds`-keyed refill controller,
raise `stop_frame_timeout_s`, and re-validate the buffer estimate as a
control signal on a real bursty turn. Parallel: Ollama `keep_alive` for
first-token eviction.

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

Evidence discipline held (measured, not guessed; the eviction contamination
of the LLM benchmark disclosed; the metric bugs found, fixed, tested, and
committed separately as instructed). No gap exposed.
