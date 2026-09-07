# R0018 — M2.4B.3.2A: Speech production / consumption rate budget

- **Date:** 2026-09-07
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.3.2A —
  research + measurement only (no runtime change)**
- **Related:** `docs/reports/R0013_m2_4b_1_gap_profiler_20260906.md`
  (per-turn metric definitions),
  `docs/reports/R0014_m2_4b_1a_cpu_scheduling_spike_20260907.md`
  (RISKS: *"batching + a look-ahead buffer hides short LLM dips; it cannot
  fix a sustained rate below speech rate"*),
  `docs/reports/R0017_m2_4b_3_1_piper_priority_context_continuity_20260907.md`
  (B.3.1, and the "batch to buy 15–20 s of audio" idea this report
  evaluates), `/tmp/nexa_m24b2_operator.jsonl` (accepted B.2/B.2A operator
  run), `docs/research/m2_4b_speech_flow/b31_compare_raw_20260907.txt`
  (B.3.1 scripted comparison),
  `docs/research/m2_4b_speech_flow/rate_budget_sim.py` (+ `_raw` — the
  offline deterministic simulator built for this report).

**Research + arithmetic only. No refill controller, no phrase holding, no
prebuffer, no dynamic pacing, no speech-speed change, no model / prompt /
persona change, no fillers, no M2.5, no runtime behaviour change. One new
offline simulator under `docs/research/`. Not pushed.**

---

## TASK RESULT

**PASS (measurement).** The core question is answered from already-measured
data:

> **`realtime_text_ratio ≈ 0.53`** (0.50–0.58 across four real turns).
> `gemma4:e4b` produces spoken-equivalent text at **~53 %** of the rate
> `pl_PL-gosia-medium` consumes it. **This is below 1.0 — a hard
> mathematical constraint.** No finite steady-state refill controller can
> make an arbitrarily long response continuous at this rate. A prebuffer
> only moves silence from the middle of the reply to the front (total wall
> time is invariant). Batching does **not** move the first underrun (it
> cannot synthesize text that does not exist yet).

**Decision: conclusion C** (see DECISION). B.3.2 is still worth building —
for its real, bounded value — but it must be scoped to conversational-length
replies and paired with at least one of: shorter replies, a small initial
backlog, faster generation, a slightly slower voice.

## MEASURED LLM TEXT PRODUCTION RATE

`generated chars / generation duration` (first token → complete), per turn:

| turn | source | gen chars | gen dur | **chars/s** |
|---|---|---|---|---|
| operator star ("z czego składa się gwiazda", turn 5) | operator JSONL | 668 | 78.28 s | **8.53** |
| operator turn 2 (list reply) | operator JSONL | 470 | 55.23 s | **8.51** |
| B.3.1 CONTROL measured turn | b31 raw | 280 | 34.17 s | **8.19** |
| B.3.1 `nice +10` measured turn | b31 raw | 182 | 21.29 s | **8.55** |

**Weighted: 8.47 generated chars/s.** Remarkably stable — B.3.1's `nice
+10` did not move it (8.55 vs CONTROL 8.19 is within reply noise; see
R0017). Consistent with R0013's "warm ≈ 2.9 tok/s" (Polish ≈ 2.9–3.4
chars/token here). The speech planner strips ~3 % of generated chars as
markup, so spoken-text production ≈ 8.47 × 0.973 ≈ **8.24 spoken chars/s**.

## MEASURED SPEECH TEXT CONSUMPTION RATE

`spoken (post-planner) chars / produced audio seconds`. Per turn:

| turn | spoken chars | audio s | **chars/s** |
|---|---|---|---|
| operator star (turn 5) | 642 | 42.81 | **15.00** |
| operator turn 2 | 455 | 27.37 | **16.62** |
| B.3.1 CONTROL | 278 | 17.86 | **15.57** |
| B.3.1 `nice +10` | 181 | 12.33 | **14.68** |

**Weighted: 15.50 spoken chars/s** for `pl_PL-gosia-medium` at
`length_scale = 1.0`. Cross-checks R0013's independent "456 chars / 29.3 s
≈ 15.6 chars/s". Per-phrase consumption is tight (13.9–17.2 chars/s), so
this is a reliable constant.

## REALTIME TEXT RATIO

`realtime_text_ratio = (LLM spoken-char production rate) / (speech
consumption rate)`:

| turn | ratio |
|---|---|
| operator star (turn 5) | **0.547** |
| operator turn 2 | **0.496** |
| B.3.1 CONTROL | **0.523** |
| B.3.1 `nice +10` | **0.579** |
| **weighted** | **0.531** |

**ratio ≈ 0.53 < 1.0.** Interpretation (from the task's own definition):
*the speech pipeline consumes text ~1.9× faster than the LLM produces it;
no finite steady-state refill controller can make an arbitrarily long
response continuous without initial backlog, slower speech, shorter spoken
output, or faster model generation.*

Equivalently: **while NeXa is speaking, the audio buffer drains at
`1 − 0.53 = 0.47` audio-seconds per second of speech.**

## PER-PHRASE TABLE

`ready@` = turn-relative time the phrase *text* became available;
`audio_ready@` = when its synthesized audio is fully ready (serialized
Piper synthesis: `max(text_ready, prev_synth_end) + http_wall`).

**operator star question — turn 5** (`/tmp/nexa_m24b2_operator.jsonl`)

| # | chars | ready@ s | http_wall | audio_s | ch/s | audio_ready@ s |
|--:|--:|--:|--:|--:|--:|--:|
| 0 | 82 | 0.00 | 1.43 | 4.99 | 16.4 | 1.43 |
| 1 | 33 | 4.18 | 0.80 | 2.27 | 14.5 | 4.98 |
| 2 | 98 | **46.29** | 1.84 | 6.96 | 14.1 | 48.13 |
| 3 | 132 | 48.72 | 2.43 | 8.55 | 15.4 | 51.15 |
| 4 | 49 | 49.75 | 1.03 | 3.25 | 15.1 | 52.18 |
| 5 | 59 | 50.99 | 1.24 | 3.63 | 16.2 | 53.42 |
| 6 | 128 | 65.16 | 2.35 | 9.23 | 13.9 | 67.51 |
| 7 | 61 | 69.50 | 0.56 | 3.93 | 15.5 | 70.06 |

Phrases 0–1 (`4.99 + 2.27 = 7.26 s` audio) then a **42 s** wait for phrase
2's text. That single LLM stall is the whole ~39 s gap this turn.

**B.3.1 CONTROL** (`b31_compare_raw`)

| # | chars | ready@ s | http_wall | audio_s | ch/s | audio_ready@ s |
|--:|--:|--:|--:|--:|--:|--:|
| 0 | 52 | 0.00 | 1.24 | 3.56 | 14.6 | 1.24 |
| 1 | 110 | 13.78 | 1.89 | 6.51 | 16.9 | 15.67 |
| 2 | 116 | 28.13 | 1.08 | 7.79 | 14.9 | 29.21 |

**B.3.1 `nice +10`** (`b31_compare_raw`)

| # | chars | ready@ s | http_wall | audio_s | ch/s | audio_ready@ s |
|--:|--:|--:|--:|--:|--:|--:|
| 0 | 54 | 0.00 | 1.40 | 3.86 | 14.0 | 1.40 |
| 1 | 127 | 15.11 | 1.25 | 8.47 | 15.0 | 16.36 |

**operator turn 2** (list reply)

| # | chars | ready@ s | http_wall | audio_s | ch/s | audio_ready@ s |
|--:|--:|--:|--:|--:|--:|--:|
| 0 | 87 | 0.00 | 1.52 | 5.52 | 15.8 | 1.52 |
| 1 | 67 | 8.12 | 1.12 | 4.08 | 16.4 | 9.23 |
| 2 | 57 | 13.75 | 1.08 | 3.40 | 16.8 | 14.83 |
| 3 | 166 | 36.79 | 2.53 | 9.68 | 17.2 | 39.31 |
| 4 | 78 | 45.53 | 0.66 | 4.69 | 16.6 | 46.19 |

## BUFFER-DRAIN SIMULATION

Offline, deterministic (`rate_budget_sim.py`): playback starts
`prebuffer` seconds after phrase 0's audio is ready; audio plays at 1×;
each phrase's audio is available at its measured `audio_ready@`. **The sim
reproduces the measured gaps** (self-checked): CONTROL → `[10.87, 7.03]` s
vs measured `[10.888, 7.032]`; `nice +10` → one `11.1` s gap (the metric's
`8.1` s under-counts by ~3 s because `BotStopped` lags the true audio
end); operator star → one `39.4` s gap vs measured `39.07` s.

Scenario A = current behaviour (phrase 1 immediately, `prebuffer = 0`):

| turn | underruns | 1st underrun @ (after start) | total silence | max silence |
|---|--:|--:|--:|--:|
| operator star (turn 5) | 1 | +7.26 s | **39.44 s** | 39.44 s |
| operator turn 2 | 3 | +5.52 s | 24.80 s | 21.08 s |
| B.3.1 CONTROL | 2 | +3.56 s | 17.90 s | 10.87 s |
| B.3.1 `nice +10` | 1 | +3.86 s | 11.10 s | 11.10 s |

The **first underrun always lands when phrase 0's own audio is exhausted**
(+3.6 / +3.9 / +5.5 / +7.3 s) — i.e. as soon as playback catches up to the
only audio that existed at start.

## INITIAL PREBUFFER SWEEP

`prebuffer` A=0 B=1 C=2 D=3 E=5 s. `total silence` and `first-audio delay
added` and the min prebuffer for **zero** underruns:

**B.3.1 `nice +10` turn** (2 phrases)

| prebuffer | +1st audio | 1st underrun @ | underruns | total silence | wall to finish |
|--:|--:|--:|--:|--:|--:|
| 0 | 0 | +3.86 | 1 | 11.10 | 23.43 |
| 1 | 1 | +3.86 | 1 | 10.10 | 23.43 |
| 2 | 2 | +3.86 | 1 | 9.10 | 23.43 |
| 3 | 3 | +3.86 | 1 | 8.10 | 23.43 |
| 5 | 5 | +3.86 | 1 | 6.10 | 23.43 |
| **11.1** | **11.1** | none | **0** | 23.43 |

**B.3.1 CONTROL** (3 phrases)

| prebuffer | total silence | max silence | underruns | wall to finish |
|--:|--:|--:|--:|--:|
| 0 | 17.90 | 10.87 | 2 | 35.76 |
| 2 | 15.90 | 8.87 | 2 | 35.76 |
| 5 | 12.90 | 7.03 | 2 | 35.76 |
| **17.9** | 0 | 0 | **0** | 35.76 |

**operator star (turn 5)**

| prebuffer | total silence | underruns | wall to finish |
|--:|--:|--:|--:|
| 0 | 39.44 | 1 | 82.25 |
| 5 | 34.44 | 1 | 82.25 |
| **39.4** | 0 | **0** | 82.25 |

**operator turn 2**: prebuffer 0 → 24.80 s / 3 underruns; 3 s → 21.80 s /
2; 5 s → 19.79 s / 1; **min for 0 = 24.8 s**.

Three facts fall straight out of the sweep:

1. **`wall_to_finish` is invariant** across every prebuffer (23.43 /
   35.76 / 82.25 s). A prebuffer does not make the reply arrive sooner or
   the pipeline faster.
2. **`total_silence` decreases 1:1 with the prebuffer** — every second of
   prebuffer is a second of first-audio latency traded for a second less
   mid-reply silence. It is the *same silence*, relocated to the front.
3. **Min prebuffer for zero underruns ≈ "wait for essentially the whole
   reply's text"**: 11.1 s (2-phrase), 17.9 s (3-phrase), 24.8–39.4 s
   (long). Unusable as first-audio latency.

## BATCHING FEASIBILITY

The R0017 idea — *"batch phrases 2..N so one synthesis call buys 15–20 s
of audio"* — separated into its two claimed effects:

**1. TTS efficiency.** Real: measured `http_wall` per phrase is
0.56–2.53 s at Piper RTF ≈ 0.26, of which ~0.15–0.3 s is HTTP/context
overhead. Batching the three near-simultaneous phrases of turn 5
(`ready@` 48.7 / 49.8 / 51.0 s — 2.3 s apart) into one call saves roughly
**2 × ~0.25 s ≈ 0.5 s**, plus it avoids two `BotStopped`/`BotStarted`
cycles. Small.

**2. Text availability — the binding constraint.** Batching **cannot
synthesize phrase B/C/D until B/C/D text exists.** In every measured turn
the first underrun is caused by phrase 0's audio running out (+3.6 to
+7.3 s) while phrase 1's *text* is still 14–46 s away:

| turn | phrase-1 text `ready@` | phrase-0 audio | can phrase-0/1 be batched at t≈4 s? |
|---|--:|--:|---|
| B.3.1 `nice +10` | 15.1 s | 3.86 s | **no** (phrase 1 does not exist) |
| B.3.1 CONTROL | 13.8 s | 3.56 s | **no** |
| operator star | 4.2 s / then #2 at **46.3 s** | 4.99 s | **no** (#2 does not exist) |

**Quantified: batching alone changes the first-underrun time by 0 s** in
all four turns. It only helps *after* multiple phrases' text already
exists (turn 5 phrases 3–5), by which point the buffer was already ~17 s
healthy. **Batching is not a continuity mechanism** — it is a minor
overhead optimisation. The "buys 15–20 s of audio" framing from R0017 is
**retracted**: you cannot synthesize what the LLM has not generated.

## ANSWER-LENGTH SENSITIVITY

Steady-state model (validated by the sims): after phrase 0, the buffer
drains at `1 − ratio = 0.47` audio-s per second of speech. To keep an
`N`-second reply continuous you need an initial audio backlog
`B0 ≥ N × 0.47`:

| reply length | ~audio s | B0 needed for 0 underruns | with B0 = 2 s |
|---|--:|--:|---|
| 1 sentence | ~4 s | ~1.9 s | **0 underruns** (fits within phrase 0 + 2 s) |
| 2 sentences | ~8 s | ~3.8 s | ~1 short underrun (2 s < 3.8 s) |
| 3 sentences | ~12 s | ~5.6 s | 2 underruns (as CONTROL) |
| ~200-token answer | ~40 s | ~18.8 s | hopeless — multi-underrun |

**"How long can NeXa speak continuously after a 2 s initial buffer?"**
≈ phrase 0 audio (~4 s) + `2 / 0.47` (~4.3 s) ≈ **~8 seconds of speech**,
then it starves (given steady 8.5 chars/s and no early next phrase). To
sustain 20 s of continuous speech needs ~9.4 s of backlog; 40 s needs
~18.8 s.

**The single highest-leverage lever is reply length** — a 1–2 sentence
conversational answer (~4–8 s audio) is continuous with a ~2–4 s
prebuffer; a ~200-token answer is not continuous at any acceptable
prebuffer. (Persona/prompt is out of scope for this task — recorded, not
changed.)

## MINIMUM REQUIRED LLM RATE

To reach a given `realtime_text_ratio` at `pl_PL-gosia-medium`
(15.50 spoken chars/s; planner keeps ~97 %):

| target | LLM rate needed | vs current 8.5 chars/s |
|---|--:|--:|
| **asymptotic — unbounded speech, 0 buffer** (ratio = 1.0) | **≥ 15.9 generated chars/s** | **~1.88×** |
| 15 s reply + 2 s initial buffer (ratio ≥ 0.87) | ≥ 13.8 chars/s | ~1.63× |
| 15 s reply + 3 s initial buffer (ratio ≥ 0.80) | ≥ 12.8 chars/s | ~1.51× |

**For long steady-state speech the asymptotic minimum is ~15.9 generated
chars/s ≈ 2× the current `gemma4:e4b` throughput.** In tok/s (~2.9–3.4
chars/tok here) that is roughly **≥ 5 tok/s sustained**, versus the ~2–3
tok/s measured. (Target number only — no model change in this task.)

## LENGTH_SCALE SENSITIVITY

Analysis only — **Piper `length_scale` is NOT changed.** `length_scale`
scales phoneme durations ~linearly, so consumption rate ≈ `15.50 /
length_scale`:

| length_scale | consumed chars/s | ratio | deficit | % of the 0.469 deficit closed |
|--:|--:|--:|--:|--:|
| 1.00 | 15.50 | 0.531 | 0.469 | 0 % |
| 1.05 | 14.76 | 0.558 | 0.442 | **5.7 %** |
| 1.10 | 14.09 | 0.584 | 0.416 | **11.3 %** |

`length_scale` required to reach `ratio = 1.0` **by itself: ≈ 1.88** —
i.e. speak ~88 % slower, far past the operator's *"normal or slightly
slower, never faster"* ceiling of ~1.10. **`length_scale ≤ 1.10` closes at
most ~11 % of the deficit** — a minor, operator-acceptable assist, not a
solution.

## WHAT A REFILL CONTROLLER CAN SOLVE

- **First-audio latency stays protected** — release phrase 0 immediately;
  no prebuffer on the first phrase.
- **Cosmetic `BotStopped`/`BotStarted` churn** — combined with B.3.1's
  8 s `stop_frame_timeout_s`, keep one speaking context across a stall so
  short gaps don't fragment the utterance (already partly shipped).
- **Short conversational replies (1–2 sentences, ~4–8 s audio)** — with a
  **small fixed prebuffer (~1.5–2.5 s)** on phrases *after* the first,
  these reach **zero underruns** (sim: a 4 s reply needs only ~1.9 s
  backlog; an 8 s reply ~3.8 s).
- **Ordering / overhead** — batch phrases whose text *already exists* when
  the buffer is low, to shave synthesis overhead and cut context churn.
- **A principled, measured drain estimate** — `buffered_audio_seconds`
  and `realtime_text_ratio` give the controller a real signal for *when*
  to release the next phrase vs wait briefly.

## WHAT A REFILL CONTROLLER CANNOT SOLVE

- **Continuity of a reply longer than ~`B0 / 0.47 + phrase0_audio`
  seconds of speech** (~8 s with a 2 s buffer, ~10 s with 3 s). Beyond
  that the buffer *must* drain to zero — the LLM is producing text at
  ~53 % of consumption and the deficit accumulates without bound.
- **The ~200-token answer** the operator keeps hitting: ~40 s of audio
  needs ~19 s of backlog — that is "wait for the whole reply", not a
  controller.
- **A one-off multi-second LLM stall** (turn 5's 42 s gap before phrase
  2): no buffer of acceptable size covers it; only faster/steadier
  generation does.
- **Making the reply arrive sooner** — `wall_to_finish` is invariant; a
  prebuffer relocates silence, it does not remove it.
- **Batching does not create text** — it cannot move the first underrun.

## RECOMMENDED B.3.2 ARCHITECTURE

**Conclusion C** (from the task's list): *a controller helps short gaps,
but current LLM generation throughput (~8.5 chars/s) is ~53 % of
steady-state speech consumption (~15.5 chars/s), so continuity for
anything beyond a very short reply also requires one or more of: faster
generation, shorter conversational voice replies, a small initial backlog,
a slightly slower voice.*

Recommended B.3.2 (to be built next, still no speech-speed change, no
fillers, no M2.5):

1. **Small look-ahead controller, scoped to conversational replies.**
   Phrase 0 out immediately (protect first-audio). Phrases 1..N: hold only
   until `buffered_audio_seconds` is at/above a small target, then release
   the next *already-complete* phrase. Candidate target **~1.5–2.5 s**
   (sim: enough for a 1–2 sentence reply to be gapless; operator A/B picks
   the exact value). Never hold a ready phrase to grow a batch while the
   buffer is low (R0017 rule). Never accelerate the voice, never insert
   silence.
2. **Document the ceiling in the product.** The controller guarantees
   continuity only up to ~`target / 0.47 + first_phrase_audio` seconds of
   speech (~8–10 s). Longer replies will gap — that is expected and not a
   controller bug.
3. **Pair with reply-length shaping** (separate task — persona/prompt):
   steer `gemma4:e4b` toward 1–3 sentence spoken answers. This is the
   highest-leverage lever and makes the controller sufficient for the
   common case.
4. **Record the faster-generation target**: ~**15.9 generated chars/s
   (~2×, ~≥5 tok/s)** for unbounded continuity — an LLM-serving / model
   track, not B.3.2.
5. **`length_scale` 1.05–1.10 is an optional minor assist** (~6–11 % of
   the deficit) if the operator wants it on its own merits — analysis
   only here, no change.
6. **Re-validate `buffered_audio_seconds` as a control signal** on a real
   bursty turn first (R0014 "REAL BUFFER-ESTIMATE DISCREPANCY").

The B.3.1 infrastructure (Piper `nice +10`, `stop_frame_timeout_s = 8 s`)
stays — it is correct and free, it just cannot change the rate ratio.

## FILES CHANGED

| file | change |
|---|---|
| `docs/reports/R0018_m2_4b_3_2a_speech_rate_budget_20260907.md` | **new** — this report |
| `docs/research/m2_4b_speech_flow/rate_budget_sim.py` | **new** — offline deterministic production/consumption simulator (self-checks against the measured B.3.1 / operator gaps) |
| `docs/research/m2_4b_speech_flow/rate_budget_sim_raw_20260907.txt` | **new** — its full output |
| `docs/research/m2_4b_speech_flow/README.md` | B.3.2A note |
| `docs/CURRENT_STATE.md` | B.3.2A finding recorded; next-task refined |

**No `src/`, `apps/`, `tests/`, or `pyproject.toml` change. No runtime
behaviour change.** `pl_PL-gosia-medium`, `length_scale`, `num_predict`,
the speech planner, `stop_frame_timeout_s`, Piper `nice` — all untouched.

## GIT STATUS

Branch `main`; the B.3.2A commit sits on top of `47729a2` (B.3.1). Not
pushed. `git diff --check` clean; `ruff` clean; full test suite unchanged
(387 passed / 394 unittest — no test file touched).

## NEXT STEP

**M2.4B.3.2** — build the small look-ahead controller per RECOMMENDED
ARCHITECTURE (phrase 0 immediate; hold 1..N to a ~1.5–2.5 s
`buffered_audio_seconds` target; release the next complete phrase when
low; no batching-to-grow, no speed change, no silence). Scope it to
conversational replies and document the continuity ceiling. In parallel
(separate tasks, not blocking): reply-length shaping via persona/prompt
(highest leverage), and the ~2× faster-generation / model-serving track.
Then **M2.5 — barge-in**.

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

Measurement over assumption throughout: the rate ratio computed from four
real turns; the simulator self-validated against the measured gaps; the
R0017 batching idea evaluated and explicitly retracted rather than
carried forward; conclusion C reached from the evidence, not the prior
plan; no runtime change made. No gap exposed.
