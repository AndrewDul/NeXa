# R0019 — M2.4B.3.2: Small speech look-ahead / continuity controller

- **Date:** 2026-09-07
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.3.2 —
  implementation**
- **Related:** `docs/reports/R0018_m2_4b_3_2a_speech_rate_budget_20260907.md`
  (**the mathematical authority** — `realtime_text_ratio ≈ 0.53`),
  `docs/reports/R0017_…` (B.3.1 — Piper `nice +10` + `stop_frame_timeout_s
  = 8`, both kept), `docs/reports/R0016_…` (B.2/B.2A speech planner,
  `OPERATOR-CONFIRMED`),
  `docs/research/m2_4b_speech_flow/b32_replay.py` (+ `_raw` — offline
  replay through the real controller policy),
  `docs/research/m2_4b_speech_flow/rate_budget_sim.py`.

**Scope: one small NeXa-owned `FrameProcessor`. It never creates a session
/ model client / TTS service / audio transport; never splits, invents,
summarises or rewrites text (the planner stays the sole normaliser);
never changes speech rate; never inserts silence; never batches-to-grow.
It preserves phrase-0 first-audio latency. B.3.1's Piper `nice +10` and
`stop_frame_timeout_s = 8` are unchanged. `gemma4:e4b`, `num_predict`,
voice, `length_scale`, persona/prompt, STT, the half-duplex gate,
`ConversationSession`/history — all untouched. No M2.5. Not pushed.**

---

## TASK RESULT

**PASS (implemented; honest no-op on today's LLM rate).**
`src/nexa/voice_tts/continuity.py` — `NexaSpeechContinuityController`
between `NexaSpeechPlanner` and `PiperHttpTTSService`. Phrase 0 always
immediate; phrases 1..N released the moment the **estimated** audio
reserve is low, held only *briefly* (≤ 0.4 s, gated on the reserve being
healthy) to avoid needless TTS-context churn. Never grows a batch, never
adds silence, never touches text or speech rate.

**Finding (R0018 confirmed by offline replay *and* real hardware):** at
`realtime_text_ratio ≈ 0.53` a runtime buffer **cannot** reduce the
intra-response gap of a reply whose LLM text-production is behind speech
consumption. In the scripted hardware A/B the controller made **`0`
holds** in all 12 turns at every target — with `gemma4:e4b` at ~8–9
chars/s the reserve is always below target when phrase 1..N arrive, so
every phrase is released immediately. It **neither helped nor hurt**
continuity or first-audio latency versus CONTROL.

**So what is it for?** (1) **Proven zero regression** — never a later
first audio, never an earlier/worse underrun, never more silence
(29 tests + replay of every R0018 timeline + the hardware A/B). (2) The
**release-metrics seam** (`controller_releases`: reserve estimate at
receive & release, reason, hold) — instrumentation the reply-length and
faster-generation tracks need. (3) It is **already correct for when the
LLM gets faster** — at ~13–16 chars/s the hold *would* engage and help; a
`--no-continuity` switch keeps it a zero-cost pass-through until then.
(4) The task mandated building it. **The gap on a multi-sentence reply is
an LLM-rate problem the separate tracks own.**

## M2.4B.3.2 STATUS

Implemented, unit-tested (29 deterministic tests), offline-replayed
against every R0018 timeline (no regression proven), and exercised on real
hardware (scripted A/B: CONTROL vs targets 1.5 / 2.0 / 2.5 s, warm-up
excluded — `0` holds everywhere, no regression, no improvement). Default
target `2.0 s`; a real mic A/B (operator) can pick a value, but on the
current LLM rate the choice is inert. **The controller ships behind the
default; `--no-continuity` is the off switch.**

## CONTROLLER ARCHITECTURE

```
AssistantSpeechBridge → NexaSpeechPlanner → NexaSpeechContinuityController
    → PiperHttpTTSService → TtsStatusObserver → LocalAudioOutputTransport
```

`NexaSpeechContinuityController(FrameProcessor)`:

* **input** — the planner's already-normalised `AggregatedTextFrame`
  phrases, plus `LLMFullResponseStartFrame` / `LLMFullResponseEndFrame` /
  `InterruptionFrame` / everything else (all forwarded unchanged, in
  order).
* **phrase 0** → released immediately (`FIRST_PHRASE`), no matter the
  reserve. No prebuffer.
* **phrases 1..N** → `decide_release(reserve_estimate, …)` (a pure
  function):
  * reserve unknown (no audio yet) → release now (`BUFFER_LOW`);
  * reserve `< near_empty` (0.5 s) → release now (`BUFFER_NEAR_EMPTY`);
  * reserve `< target` (2.0 s default) → release now (`BUFFER_LOW`);
  * reserve `≥ target` → **hold** for `min(reserve − target, max_hold)`
    with `max_hold = 0.4 s` — a *release deadline*, not pacing: it is
    exactly when the estimated reserve would reach `target`, capped at
    0.4 s, and it is cancelled the instant a later phrase arrives
    (`NEXT_PHRASE`, released first, in order — never reordered, never
    stacked) or the turn ends (`TURN_COMPLETE`).
* **never batches-to-grow** — a newly-arrived phrase always flushes the
  held one immediately; a hold is never extended to accumulate text.
* **reset** on `LLMFullResponseStartFrame` and `InterruptionFrame` (held
  phrase flushed `RESET`, per-turn state cleared).
* **the only wait in the module** is that one bounded `_hold_then_release`
  sleep. No `time.sleep`, no sleep-based pacing, no fillers.
* `enabled=False` → pure pass-through (releases every phrase immediately,
  reason `CONTROLLER_DISABLED`) — the A/B "off" baseline.
* `on_release(ControllerRelease)` → the B.1 metrics sink (measure-only).

It holds no `session` / `history` / `provider` reference; imports only
`asyncio`, `time`, `collections`, `dataclasses`, `loguru`, `pipecat`.

## BUFFER SIGNAL

**ESTIMATE — explicitly, everywhere.** The controller sits *upstream* of
the TTS service and cannot read `LocalAudioOutputTransport`'s own audio
queue depth without an invasive reach into the transport object, so it
does **not** claim an exact value. It estimates:

    reserve_estimate_s(now) = Σ produced-audio-seconds − (now − first-audio-at)

fed by the **real** `TTSAudioRawFrame` byte counts observed by the
downstream `TtsStatusObserver` (fanned out to
`controller.note_tts_audio(...)`). This is the same quantity R0013's
`BufferEstimate` validated against real `BotStoppedSpeaking` (error
≈ 0 s at 3 tok/s), now re-checked in the B.3.2 path by the offline replay
and the hardware A/B (`reserve_at_release` vs the actual subsequent gap in
the `--report` output). Every field is named `*_estimate_s` /
`reserve_at_*`; the report prints "(reserve = ESTIMATE)". If the estimate
had proven unreliable enough to be unsafe for control, the task said to
stop and report — it did not: the replay reproduces the measured gaps
exactly, and the controller only ever uses the estimate to decide
*immediate release vs a ≤ 0.4 s hold*, the most conservative possible use.

## CANDIDATE TARGET VALUES

`target_reserve_s` — CANDIDATE (R0018): **1.5 / 2.0 / 2.5 s** (default
2.0). `near_empty_s = 0.5`, `max_hold_s = 0.4` (deliberately small — a
hold is only ever a churn-avoidance nicety and must never add perceptible
tail latency). All constructor-overridable; `--continuity-target-s` on the
probe; operator A/B picks the final target.

## FIRST PHRASE POLICY

`decide_release(reserve, is_first=True, …)` → `(FIRST_PHRASE, 0.0)`
unconditionally. Tests `test_1` / `test_2` prove phrase 0 is pushed
immediately (same frame object, text byte-identical) **even with a 30 s
reserve**. First-audio latency is never worsened.

## FOLLOWING PHRASE POLICY

`BUFFER HEALTHY` (`reserve ≥ target`) → may hold ≤ 0.4 s (deadline =
when the estimate reaches `target`); a later phrase or turn-end releases
it sooner. `BUFFER LOW` (`< target`) → release immediately. `BUFFER
NEAR EMPTY` (`< 0.5 s`) → release immediately (distinguished only for
metrics). **Never** hold to grow a batch — `test_6` proves three phrases
arriving while the buffer is low are all released immediately, in order.

## OFFLINE REPLAY

`docs/research/m2_4b_speech_flow/b32_replay.py` — the measured per-phrase
`(text_ready_at, http_wall, audio_s)` timelines from R0018, forward-
simulated through the **real** `decide_release` policy + a bounded-hold
timer + serialized Piper synthesis + the buffer-drain sim. Self-checks
against the measured gaps.

| turn | baseline 1st-underrun@ / total silence | controller (target 1.5 / 2.0 / 2.5) |
|---|---|---|
| B.3.1 `nice +10` | +3.86 s / 11.10 s | **identical** — no hold, no change |
| B.3.1 CONTROL | +3.56 s / 17.90 s | **identical** — no hold, no change |
| operator star (turn 5) | +7.26 s / 39.44 s | **identical** — phrase 1 held ≤ 0.4 s at target ≤ 2.0, first-underrun and total silence unchanged |
| synthetic "LLM keeps up" (sentence 2 ready +2 s) | 0 underruns | 0 underruns; phrase 1 held 0.4 s; first-audio unchanged |

**Verified:** the controller **never** makes first audio later, **never**
makes the first underrun earlier, **never** increases total silence.
**Honest:** it **cannot** eliminate the operator-star ~39 s gap (a 42 s
LLM stall before phrase 2's *text* exists) — R0018's rate constraint. On
the R0018 timelines the controller is a no-op on continuity; its holds
only ever engage when the LLM is *ahead*, and then only ≤ 0.4 s.

## REAL HARDWARE A/B

Scripted (`b32_ab.py`, raw `b32_ab_raw_20260907.txt`): real `gemma4:e4b` +
real Piper (`nice +10`, `stop_frame_timeout_s = 8`) + real
`LocalAudioOutputTransport` + real `ConversationSession`; mic/STT scripted.
Warm-up `"Ile jest osiem razy siedem?"` (excluded). Then the 3 short-answer
questions for CONTROL (`--no-continuity`) and targets 1.5 / 2.0 / 2.5 s.

Questions:

1. `Co to jest czarna dziura? Odpowiedz krótko.`
2. `Z czego głównie składa się gwiazda? Odpowiedz w dwóch zdaniach.`
3. `A jak powstaje hel w gwieździe? Krótko.`

**The single most important number: the controller made `0` holds in
every one of the 12 turns, at every target (`held_count = 0`, `max_hold =
0.00 s` throughout).** With real `gemma4:e4b` at ~8–9 chars/s, even a
"short" 2-sentence reply has sentence 2's text arriving ~10–20 s after
sentence 1 — by which point the estimated reserve is already below every
candidate target, so every phrase is released immediately
(`BUFFER_LOW` / `FIRST_PHRASE`). This is exactly what the offline replay
predicted; the hardware confirms it. `tiny_text_chunk_count = 0` and true
Piper RTF ≈ 0.14–0.28 throughout (B.2 and B.3.1 intact).

Per-turn (Q1 replies were often 1 sentence → no phrase after phrase 0;
`gemma4:e4b` is non-deterministic so reply length/rate varies run to run):

| config · Q | reply | LLM ch/s | chunks | controller holds | max gap | underruns | audio-s/wall-s | diagnosis |
|---|---|--:|--:|--:|--:|--:|--:|---|
| CONTROL Q1 | 1 sent | 9.7 | 1 | — | — | 0 | — | FIRST TOKEN |
| CONTROL Q2 | 2 sent | 8.8 | 2 | — | — | 1 | 0.76 | UNKNOWN |
| CONTROL Q3 | 2 sent | 8.1 | 2 | — | — | 1 | 1.38 | UNKNOWN |
| target 1.5 Q1 | 1 sent | 9.3 | 1 | 0 | — | 0 | 1.00 | UNKNOWN |
| target 1.5 Q2 | 2 sent | 9.2 | 2 | 0 | 5.5 s | 1 | 0.58 | LLM TEXT PRODUCTION |
| target 1.5 Q3 | 2 sent | 8.4 | 2 | 0 | 4.6 s | 1 | 0.63 | LLM TEXT PRODUCTION |
| target 2.0 Q1 | 2 sent | 8.7 | 2 | 0 | — | 0 | 1.00 | UNKNOWN |
| target 2.0 Q2 | 2 sent | 9.5 | 2 | 0 | 2.0 s | 2 | 0.66 | LLM TEXT PRODUCTION |
| target 2.0 Q3 | 3 sent | 8.2 | 3 | 0 | 8.8 s | 1 | 0.53 | LLM TEXT PRODUCTION |
| target 2.5 Q1 | 1 sent | 8.6 | 1 | 0 | — | 0 | 1.00 | UNKNOWN |
| target 2.5 Q2 | 2 sent | 8.2 | 2 | 0 | — | 1 | 1.32 | UNKNOWN |
| target 2.5 Q3 | 2 sent | 8.7 | 2 | 0 | — | 1 | 1.43 | UNKNOWN |

## CONTINUITY RESULT

- **1-sentence replies (`Odpowiedz krótko` often produces one): no gap,
  ever** — phrase 0 is the whole answer, nothing after it. This is the
  case worth engineering for, and it is a *reply-length* property, not a
  controller one.
- **2–3 sentence replies still gap** (2–9 s, `diagnose = LLM TEXT
  PRODUCTION`) at targets 1.5 and 2.0 — the LLM has not produced
  sentence 2's text when sentence 1's audio runs out. **The controller
  cannot change this** (it held nothing; even if it could hold, holding
  adds no audio).
- Target 2.5's three gap-free runs are **confounded** — those replies
  happened to be shorter / the model faster that run (`audio-s/wall-s`
  1.0–1.4, one `BotStarted`/`BotStopped`), and the controller did **0**
  holds, so it did not cause the difference. Not evidence for 2.5.

**Net: on real short conversational turns the controller neither helped
nor hurt continuity — it was a no-op, as R0018 predicts.** The gap on a
multi-sentence reply is an LLM-rate problem for the separate tracks.

## FIRST-AUDIO LATENCY RESULT

`END_OF_TURN → first TTS audio` ≈ **12.5–17.1 s** across *all* configs
(CONTROL 12.8–16.3 s; target 1.5 13.1–15.9 s; 2.0 12.5–16.3 s; 2.5
12.9–17.1 s). The spread is `gemma4:e4b` run-to-run variance (first-token
~3 s + generating the whole first sentence at ~8.5 chars/s ≈ 10–13 s +
synth ≈ 1.5 s). **The controller adds nothing** — it releases phrase 0
immediately and made 0 holds. Target 2.5's high end (17.1 s) vs CONTROL
(16.3 s) is within that variance, and structurally cannot be the
controller (0 holds).

## UNDERRUN RESULT

`output_underrun_count` (buffer-estimate crossings): **0** on every
1-sentence reply and on target-2.0 Q1; **1–2** on the 2–3 sentence
replies. Identical distribution CONTROL vs controller-on — the controller
did not add or remove an underrun. Where a real audible gap followed
(targets 1.5/2.0), it was 2–9 s, all `LLM TEXT PRODUCTION`. **No
regression; no improvement — a no-op on this workload.**

## CONTINUITY CEILING

From R0018 (`realtime_text_ratio ≈ 0.53`; buffer drains at ~0.47 audio-s
per second of speech while speaking):

> The controller guarantees continuity only up to roughly
> **`target / 0.47 + first_phrase_audio`** seconds of speech — **~8 s with
> a 2 s target, ~10 s with a 3 s target** — and only if the LLM keeps
> producing at ~8.5 chars/s. A 1–2 sentence reply (~4–8 s audio) fits; a
> ~200-token reply (~40 s audio) needs ~19 s of backlog and **will** gap.

Longer-reply continuity requires the **separate** tracks R0018 identified:
reply-length shaping (persona/prompt — highest leverage) and/or ~2×
faster generation (~15.9 chars/s, LLM-serving). **B.3.2 does not and
cannot solve those; it is not a defect when a 30–40 s reply still gaps.**

## TEST RESULTS

- `tests/test_voice_tts_continuity.py` — **29 tests**:
  - phrase policy 1–6: first phrase immediate; no first-phrase prebuffer
    even with a huge reserve; healthy buffer holds phrase 2 then releases
    (`HOLD_EXPIRED`, hold ≈ deadline); low buffer → immediate
    (`BUFFER_LOW`); near-empty → immediate (`BUFFER_NEAR_EMPTY`); never
    waits to grow a batch while low (3 phrases, all immediate, in order).
  - ordering/integrity 7–11: order preserved; end-of-turn flush releases
    the held phrase exactly once (`TURN_COMPLETE`) + forwards the end
    frame; no duplicate / no lost phrases; frame text is the same object,
    byte-identical.
  - architecture/reset 12–20: holds no session/history/provider ref; new
    turn flushes held (`RESET`) + resets index & audio counter;
    `InterruptionFrame` flushes + resets + forwards; a raising
    `on_release` callback does not break speech; `enabled=False` is pure
    pass-through; unknown frames pass through.
  - source guards: no `nexa.conversation`/`bootstrap`/`config`/`providers`
    import; no planner-normaliser import; defines no text-normalization
    function; no `length_scale` / `noise_scale` / `filler` /
    `LocalAudioTransport` / `pyaudio` / `renice` / `num_predict`;
    the only `_sleep` call is inside `_hold_then_release`; imports only
    stdlib + loguru + pipecat.
  - `decide_release` (pure) + metric math: thresholds; hold cap;
    `hold_s = released − received` clamped ≥ 0; `MetricsCollector`
    attributes releases FIFO; `held_count` / `max_hold_s` / `mean_hold_s`
    + the `speech_continuity_controller` `to_dict` block.
- Full suite: **`pytest` 416 passed / 7 skipped / 14 subtests** (was 387);
  **`python -m unittest discover -s tests` 423 OK, 7 skipped**.
- `ruff check src tests apps scripts/setup_piper_http.py` — clean.
  `git diff --check` — clean.
- All 59 B.2/B.2A planner tests + 22 B.3.1 tests still green.

## FILES CHANGED

| file | change |
|---|---|
| `src/nexa/voice_tts/continuity.py` | **new** — `NexaSpeechContinuityController`, `decide_release`, `ControllerRelease`, `ReleaseReason` |
| `src/nexa/voice_tts/__init__.py` | export the above + `DEFAULT_CONTINUITY_TARGET_S` |
| `src/nexa/voice_tts/metrics.py` | measure-only: `TurnMetrics.controller_releases` + `controller_held_count` / `controller_max_hold_s` / `controller_mean_hold_s`; `MetricsCollector.controller_release`; `speech_continuity_controller` `to_dict` block + `render_turn_report` section |
| `apps/nexa_voice_tts_probe.py` | insert the controller between planner and TTS service; `--continuity-target-s` / `--no-continuity`; fan out `on_tts_audio` to the controller (always) + metrics (report mode) |
| `tests/test_voice_tts_continuity.py` | **new** — 29 tests |
| `docs/reports/R0019_…md` | **new** — this report |
| `docs/research/m2_4b_speech_flow/b32_replay.py` + `b32_replay_raw_20260907.txt` | **new** — offline replay through the real `decide_release` policy |
| `docs/research/m2_4b_speech_flow/b32_ab.py` + `b32_ab_raw_20260907.txt` | **new** — scripted hardware A/B harness + raw |
| `docs/research/m2_4b_speech_flow/README.md` | B.3.2 note |
| `docs/CURRENT_STATE.md` | B.3.2 done; next-task update |

Not changed: `nexa.tts` (Piper `nice`/timeout untouched), `nexa.voice`,
`nexa.stt`, `nexa.voice_conversation`, `nexa.conversation`,
`nexa.providers`, the speech planner, `pyproject.toml`, the frozen M2.4
path. No `num_predict`, STT, voice, `length_scale`, persona, filler,
M2.5.

## COMMIT HASH

One coherent local commit — `feat: add short-reply speech continuity
control` (tip of `main`; see `git log -1`).

## GIT STATUS

Branch `main`, working tree clean, **11 commits ahead of `origin/main`,
not pushed**. `git diff --check` clean; no model/voice binaries.

## NEXT STEP

**Reply-length shaping** (persona/prompt so `gemma4:e4b` gives 1–3
sentence spoken answers — R0018's highest-leverage lever; makes this
controller sufficient for the common case) **and/or the ~2× faster-
generation / model-serving track**. Then **M2.5 — barge-in**, replacing
the temporary half-duplex gate. Non-blocking: Ollama `keep_alive`
(first-token eviction), STT quality (R0016), `num_predict = 200` review.

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

R0018 was treated as the authority: the controller was scoped to what a
buffer can actually do, the "batch to buy 15–20 s" idea stayed retracted,
the offline replay proved no-regression before any hardware run, the
buffer signal is labelled an estimate everywhere, and the honest result
(the controller does not reduce the rate-deficit gap) is stated up front
rather than dressed up. No gap exposed.
