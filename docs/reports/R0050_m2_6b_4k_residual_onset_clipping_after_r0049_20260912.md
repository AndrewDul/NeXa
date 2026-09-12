# R0050 — M2.6B.4K: Residual Onset Clipping After R0049

**Date:** 2026-09-12
**Milestone:** M2.6B.4K (forensic audit, following R0049's real-hardware
FAIL)
**Status:** FORENSIC EVIDENCE ONLY — no production fix implemented this
checkpoint (per the charter: R0050 = evidence, R0051 = final
correction). Root cause quantified from real captured PCM + installed
source; a concrete, evidence-backed candidate fix is identified but
**not implemented**. No Gemini call, no hardware touched by this
session, not pushed. **Hardware acceptance remains FAIL. `M2.6B` remains
IN PROGRESS.**

## REAL POST-R0049 HARDWARE RESULT

The operator re-ran the exact R0048/R0049 capture command
(`.venv/bin/python docs/research/m2_6_cloud_realtime_voice/
m2_6b4i_audio_ingress_parity_probe.py`) after R0049's fix. New capture:
`docs/reports/../research/m2_6_cloud_realtime_voice/ingress_captures/
ingress_capture_20260912T193226Z.json` (git-ignored, not committed) — 6
real utterances, `preroll_ms=300`, `vad_start_secs=0.2`,
`vad_stop_secs=0.5` (all confirmed from the JSON's own recorded
production config). The operator directly listened to the new RAW vs
PRODUCTION_FORWARDED WAV pairs: RAW remains complete in every take;
PRODUCTION_FORWARDED is **improved** compared with pre-R0049 but speech
onset is **still audibly clipped** — "Czarna dziura" can become "arna
dziura" or approximately "carna dziura"; English phrases show the same
beginning-of-utterance clipping (systemic, not Polish-specific).
**R0049 deterministic architecture proof: PASS (unchanged). R0049 REAL
HARDWARE POST-FIX ACCEPTANCE: FAIL.**

**File-name reuse note (as instructed):** the capture directory is
shared across runs; the post-R0049 run wrote exactly 6 files
(`001`–`006`, timestamped 20:32), overwriting the PRE-fix run's own
`001`–`006`. The pre-fix run's `007`–`010` (timestamped 19:44, from
`ingress_capture_20260912T184419Z.json`, R0048's own 10-take session)
were **not** overwritten (the post-R0049 session only produced 6 takes)
and are still present on disk. This is used deliberately below as a
genuine, real PRE-fix vs POST-fix comparison from actual captured PCM —
not merely the old JSON's own aggregate numbers.

## R0049 STATUS CORRECTION

Recorded verbatim in both `docs/reports/R0049_...md` and
`docs/CURRENT_STATE.md`/`docs/ROADMAP.md` (this checkpoint): the 300 ms
preroll **materially improved** onset retention (measured below: 200 ms
of the diagnostic's 500 ms pre-VAD-start raw window is now omitted,
down from a full 500 ms pre-fix) **but did not fully restore** real
hardware speech onset — the operator still hears the first
phoneme/syllable clipped, on both Polish and English phrases (systemic).
`M2.6B` remains IN PROGRESS. No Gemini re-test should happen until a
verified fix lands. **R0049's valid deterministic results (18/18
charter test requirements, architecture correctness, zero R0044/R0045/
R0046 regressions) are not retracted** — only the claim that 300 ms
restores real-hardware parity is corrected.

## POST-FIX RAW/FORWARDED ALIGNMENT TABLE

New tool this checkpoint (diagnostic-only, no production change):
`docs/research/m2_6_cloud_realtime_voice/wav_alignment.py`
(`find_alignment` — byte-exact `bytes.find`, never fuzzy; `rms_windows`
— plain PCM energy per fixed window, no invented detector), tested in
`tests/test_m2_6b4k_wav_alignment.py` (10 tests, synthetic PCM only).
Applied to the 6 real POST-R0049 pairs: all parameters identical
(16-bit, mono, 16000 Hz, confirmed for every file before comparison);
every `production_forwarded.wav` **is confirmed to be an exact,
byte-identical contiguous subsequence of its own `raw_with_context.wav`**
— no reordering, no corruption, no partial-frame artifacts:

| take | found | raw_prefix_before_forwarded | raw_suffix_after_forwarded |
|---|---|---|---|
| 1 | yes | 6400 bytes / 200.0 ms | 16000 bytes / 500.0 ms |
| 2 | yes | 6400 bytes / 200.0 ms | 16000 bytes / 500.0 ms |
| 3 | yes | 6400 bytes / 200.0 ms | 16000 bytes / 500.0 ms |
| 4 | yes | 6400 bytes / 200.0 ms | 16000 bytes / 500.0 ms |
| 5 | yes | 6400 bytes / 200.0 ms | 16000 bytes / 500.0 ms |
| 6 | yes | 6400 bytes / 200.0 ms | 16000 bytes / 500.0 ms |

Exactly 200.0 ms omitted at the start, exactly 500.0 ms omitted at the
end, in **every single take, byte-identically** — this is not
approximate; it mechanically confirms `preroll_ms=300` applied against
the diagnostic's own `PRE_CONTEXT_SECS=0.5` (500 − 300 = 200 ms omitted)
and `POST_CONTEXT_SECS=0.5` (500 ms omitted, expected/unrelated to
onset — see R0048/R0049, forwarding correctly continues through the
`stop_secs` trailing window, this is diagnostic post-roll only, never a
real output-side loss).

For comparison, the same tool applied to the 4 still-present PRE-fix
pairs (`007`–`010`, `preroll_ms` effectively 0 before R0049): all 4 show
`prefix = 500.0 ms` exactly (the ENTIRE diagnostic pre-context window
was omitted, as expected pre-fix), `suffix = 500.0 ms` (unchanged,
same mechanism). This confirms the R0049 fix measurably works exactly
as designed — it is not a placebo; it recovered exactly 300 ms of real
audio at the front of every utterance that previously reached the
provider not at all.

## EXACT OMITTED PREFIX PER TAKE

Restated from the table above for clarity: **every POST-R0049 take
omits exactly 200.0 ms** of the diagnostic's own pre-VAD-start raw
capture (the window from `vad_start_t − 500 ms` to `vad_start_t − 300 ms`)
— this is the entire forensic question: does that specific 200 ms
window contain real speech, or is it silence?

## OMITTED PREFIX ENERGY RESULT

Plain PCM RMS (root-mean-square amplitude), 20 ms windows, computed with
`wav_alignment.rms_windows` over the full 500 ms pre-VAD-start raw
window (25 windows; windows 0–9 = the 200 ms OMITTED prefix; windows
10–24 = the 300 ms RETAINED/forwarded prefix). No speech-onset detector
invented; RMS is reported as supporting evidence only, per the charter.

**POST-R0049 takes (1–6), omitted-prefix windows (index 0–9, each 20 ms,
counting up to the omission boundary) and the first few retained
windows (10–12) for context:**

| take | w0 | w1 | w2 | w3 | w4 | w5 | w6 | w7 | w8 | w9 | \| | w10 | w11 | w12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 10.7 | 9.3 | 11.1 | 11.9 | 10.5 | 11.2 | 9.7 | 25.8 | 99.0 | 119.8 | \| | 302.0 | 1362.0 | 2071.0 |
| 2 | 11.3 | 13.2 | 12.3 | 10.8 | 10.4 | 20.8 | 20.0 | 124.9 | 134.9 | 303.0 | \| | 1055.0 | 1435.0 | 1454.0 |
| 3 | 9.1 | 10.6 | 8.4 | 9.9 | 8.2 | 16.2 | 16.1 | 82.7 | 67.0 | 347.9 | \| | 1173.0 | 1732.0 | 2073.0 |
| 4 | 11.9 | 10.2 | 9.5 | 8.1 | 8.5 | 41.1 | 89.4 | 98.1 | 146.2 | 517.9 | \| | 599.0 | 678.0 | 1202.0 |
| 5 | 9.3 | 9.5 | 11.1 | 35.5 | 64.5 | 39.4 | 146.7 | 496.5 | 612.7 | 746.2 | \| | 945.0 | 1295.0 | 1273.0 |
| 6 | 506.3 | 699.1 | 920.7 | 1097.7 | 1063.3 | 1038.4 | 834.6 | 553.0 | 382.9 | 234.3 | \| | 154.0 | 98.0 | 70.0 |

**Interpretation, stated only as an observation, never a threshold
claim:** for takes 1–5, the first 4–7 windows (0–80 ms to 0–140 ms into
the 500 ms window, i.e. the EARLIEST part of the pre-context) sit at a
quiet, roughly-flat baseline (≈8–13, except take 5 which starts rising
earlier, by window 3). Every one of takes 1–5 then shows a clear,
usually multi-fold RMS rise beginning somewhere in windows 5–8 (100–180
ms into the window, i.e. roughly 320–400 ms BEFORE `vad_start`) that
continues rising through the omission boundary (window 9/10) and peaks
well inside the RETAINED region (windows 11–14, typically 1000–2200
RMS). **This is exactly the shape of a rising onset transient straddling
the 300 ms boundary** — real acoustic energy, several times the quiet
baseline, exists inside the OMITTED 200 ms in 5 of 6 takes, immediately
adjacent to (not merely near) the cut point. Take 6 does not show a
quiet baseline at all — energy is already high (506+) at the very start
of the 500 ms window and DECAYS through the omitted region before a
second, smaller rise past the boundary; this is flagged as an outlier
that does not fit the clean "quiet-then-onset" pattern (plausibly this
utterance's own true onset — or a preceding sound/breath — began even
earlier than the diagnostic's 500 ms window reaches, or this specific
take had elevated ambient/room noise) rather than evidence against the
main finding.

**PRE-fix takes (7–10), for direct comparison, full 25 windows (0–24,
20 ms each, spanning the complete 500 ms pre-VAD-start window, ALL of
which was omitted pre-fix):**

| take | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 7 | 792 | 659 | 556 | 392 | 279 | 146 | 78 | 53 | 37 | 21 | 15 | 11 | 11 | 10 | 45 |
| 8 | 1074 | 1405 | 1625 | 1581 | 1828 | 1130 | 511 | 220 | 92 | 65 | 54 | 36 | 23 | 15 | 12 |
| 9 | 10 | 11 | 67 | 118 | 164 | 181 | 127 | 70 | 289 | 732 | 801 | 1000 | 1375 | 1768 | 1761 |
| 10 | 9 | 10 | 10 | 11 | 11 | 71 | 82 | 106 | 209 | 267 | 399 | 726 | 887 | 1083 | 1230 |

(windows 15–24 continue past the take's own peak, omitted here for
space — full data reproducible via the command at the end of this
report). Takes 9 and 10 show the SAME clean "quiet-then-rising-onset"
shape as POST-fix takes 1–5, with the rise beginning around window 2–5
(40–100 ms in, i.e. 400–460 ms BEFORE `vad_start`) — even further back
than the POST-fix takes, consistent with there being MORE total onset
content when nothing at all is retained. Takes 7 and 8 show elevated
energy from the very first window, decaying over ~150–180 ms before a
second rise past the boundary — the same outlier shape as POST-fix take
6, suggesting this specific recording session (or specific utterances)
had a real, recurring characteristic (bleed from a preceding sound,
breath noise, or room echo) distinct from the clean takes, not a flaw
in the measurement.

**Conclusion of this section:** in 8 of the 10 real captured takes
available (both pre- and post-fix), real, substantial, rising acoustic
energy — never explained as detector noise, computed from real PCM
alone — is present in the region a 300 ms preroll still omits. This
directly corroborates the operator's own listening result.

## ACTUAL M2.6A PREROLL BEHAVIOR

Re-audited, more rigorously, exactly as instructed — the trace below
does NOT support "M2.6A actually used 500 ms"; it confirms R0049's own
300 ms auto-sizing arithmetic was mechanically correct as far as it
went, but reveals the arithmetic itself understates the REAL total
latency (see next section).

* `pipecat/audio/vad/vad_controller.py` `VADController.start()` (line
  ~142): `await self.broadcast_frame(SpeechControlParamsFrame,
  vad_params=self._vad_analyzer.params)` — broadcast happens the moment
  the controller starts.
* `pipecat/processors/aggregators/llm_response_universal.py` (M2.6A's
  own `LLMContextAggregatorPair` user-half): constructs a
  `VADController` in `__init__` when `vad_analyzer` is given (confirmed
  — exactly M2.6A's own `LLMUserAggregatorParams(vad_analyzer=...)`
  construction); `_start(self, frame: StartFrame)` calls
  `await self._vad_controller.start()` (line ~1136) — and the SAME
  method (`_start`) is invoked in response to `StartFrame`, with an
  explicit code comment: *"Push StartFrame before start(), because we
  want StartFrame to be [pushed downstream first]"* — i.e.
  `SpeechControlParamsFrame` is broadcast essentially immediately after
  `StartFrame`, at pipeline startup, well before any real microphone
  audio in a normal session (the user has not spoken yet when the
  pipeline starts).
* `GeminiLiveLLMService._handle_speech_control_params` (confirmed
  present, unchanged from R0048/R0049's own reading) applies
  `start_secs + AUTOSIZED_USER_AUDIO_PREROLL_MARGIN_SECS (0.1)` the
  moment this frame arrives — i.e. **before the FIRST real utterance of
  a normal M2.6A session**, `_user_audio_preroll_secs` is already 0.3,
  not the 0.5 s fallback default.

**Conclusion:** source does NOT show M2.6A retaining 0.5 s in normal
operation — the 0.3 s auto-sizing is mechanically confirmed to apply in
M2.6A's own topology too (both because the same VAD analyzer instance
lives in that same pipeline, AND because `SpeechControlParamsFrame`
reliably precedes any real speech). **R0049's 300 ms parity assumption,
as an arithmetic derivation, was not wrong on its own terms — M2.6A's
own effective preroll in practice was almost certainly ALSO ~300 ms,
not 500 ms.** This means M2.6A likely experienced the same underlying
onset-timing risk; its "operator-accepted, excellent" status reflected
overall conversational quality and Gemini's own recovery behavior (the
same clarify-then-understand pattern observed in the real Attempt #3
session itself — "Czorna Jura?" followed by correct understanding once
"Ah, a black hole!" was said), not a verified guarantee of zero onset
loss. The real bottleneck is not "M2.6A vs R0049 preroll size" — it is
that **300 ms itself is insufficient for this hardware's real Silero
confirmation latency**, a fact this exact codebase had already
discovered once before, for the LOCAL voice path (see next section).

## TOTAL VAD START LATENCY ANALYSIS

Re-derived from installed source, `pipecat/audio/vad/vad_analyzer.py`
and `pipecat/audio/vad/silero.py` (unchanged, re-confirmed this
checkpoint):

1. **Chunking**: `SileroVADAnalyzer.num_frames_required()` returns 512
   samples at 16 kHz = 32 ms per analysis chunk (confirmed from source,
   line 197).
2. **Confirmation window (the `start_secs` component)**: `_vad_start_frames
   = round(start_secs / (512/16000)) = round(0.2/0.032) = round(6.25) =
   6` — exactly 6 consecutive 32 ms chunks (**192 ms**, not a clean 200
   ms — a `round()` artifact) must be classified `speaking` in a row,
   AFTER the state machine first enters `STARTING`, before it confirms
   `SPEAKING` and fires `VADUserStartedSpeakingFrame`.
3. **Volume smoothing (NOT part of `start_secs` at all — a separate,
   additive source of latency)**: `_get_smoothed_volume()` applies
   exponential smoothing (`pipecat/audio/utils.py:exp_smoothing`,
   `smoothed = prev + factor*(value − prev)`) with a FIXED
   `_smoothing_factor = 0.2` — a low-alpha (slow-reacting) EWMA filter.
   A step change in raw volume needs roughly `ln(0.1)/ln(0.8) ≈ 10.3`
   chunks (**~330 ms**) to reach 90% of its new level, or `ln(0.5)/ln(0.8)
   ≈ 3.1` chunks (**~100 ms**) to reach 50%. The state machine can only
   enter `STARTING` once BOTH the Silero confidence (≥0.7) AND this
   SMOOTHED volume (≥`min_volume=0.6`) cross their gates for a given
   32 ms chunk — so a genuinely soft/gradual onset (exactly what a
   voiceless affricate like the Polish "cz" produces acoustically: a
   low-energy frication burst before full voicing) can be measurably
   delayed, by the smoothing filter alone, well beyond the raw acoustic
   onset — a real, quantifiable-from-source, DATA-DEPENDENT (not fixed)
   contributor.
4. **Confidence gate**: Silero's own neural-network confidence score is
   model-internal — genuinely data-dependent, not further quantifiable
   from source structure alone (this is the ONE component this report
   does not attempt to bound analytically, consistent with "do not
   invent a speech-onset detector").
5. **Buffering granularity**: `_run_analyzer` only processes once
   `len(self._vad_buffer) >= num_required_bytes` (line ~200) — up to one
   extra 32 ms chunk of alignment slop.

**Combined, source-backed conclusion:** the ONLY fixed, guaranteed
component of the nominal `start_secs=0.2` is the 192 ms confirmation
window (item 2) — it does NOT include however long it takes the
(exponentially-smoothed) volume and (model-computed) confidence to
FIRST cross their gates, which is a genuinely variable, data-dependent
quantity that can easily add 100–300+ ms for a gradual/soft onset (item
3, quantified from the smoothing filter's own time constant). This is
fully consistent with, and provides a source-level mechanism for, the
empirically-observed real onset energy 300–460 ms before `vad_start` in
§OMITTED PREFIX ENERGY RESULT above.

**This exact investigation was already performed once before, for the
LOCAL voice path** — `src/nexa/stt/utterance_buffer.py`'s own docstring
(unchanged, re-read this checkpoint, `git diff --stat -- src/nexa/stt`
remains empty): *"Theoretical minimum: ... 192ms of audio must already
have arrived before `VADUserStartedSpeakingFrame` fires. Empirical
measurement: running the real `SileroVADAnalyzer` against 3 real R0006
speech fixtures with manually-verified true onset (RMS profile
inspection) measured actual confirmation delay at 352ms, 352ms, and
288ms — noticeably larger than the theoretical minimum alone. Chosen
value covers the empirical worst case with a safety margin against
jitter, rounded to a clean number."* — `PRE_ROLL_MS = 500`. This is an
**independent, pre-existing, already-validated empirical measurement**,
on the SAME VAD mechanism, that reached the SAME conclusion (192 ms
theoretical is insufficient; real confirmation delay measured at
288–352 ms) BEFORE this checkpoint's own real-hardware RMS analysis
reached it again, independently, from different data.

## WHY 300 MS WAS INSUFFICIENT

Two independent lines of evidence converge on the same answer:

1. **Real, current hardware evidence** (this checkpoint): RMS analysis
   of 8 of 10 real captured takes shows rising acoustic energy 300–460
   ms before `vad_start`, i.e. reaching further back than the 300 ms
   preroll's own boundary.
2. **Pre-existing, independent empirical validation** (R0006-era, local
   voice, unchanged, still in the codebase): the SAME VAD mechanism's
   real confirmation delay was measured at 288–352 ms — already known,
   before R0049, to exceed the 192 ms theoretical minimum by a
   comparable margin to what this checkpoint just re-derived for the
   cloud path.

R0049's own derivation (`start_secs + 0.1 s margin = 0.3 s`) mirrors
Pipecat's OWN documented auto-sizing arithmetic faithfully — but that
arithmetic itself was never independently validated against REAL speech
on THIS hardware; it is a generic library default, not a measurement.
The codebase already had a better, measured number for the identical
underlying mechanism, in a different module, and R0049 did not reuse
it.

## ROOT CAUSE

**R0049's 300 ms preroll capacity, derived from Pipecat's own
documented (but unvalidated-against-real-speech) auto-sizing formula,
understates the REAL total acoustic-onset-to-`VADUserStartedSpeakingFrame`
latency on this hardware** — which real captured evidence (this
checkpoint) and a pre-existing, independent empirical measurement
(`nexa/stt/utterance_buffer.py`, R0006-era) both place in the 288–460 ms
range, not 192–300 ms. The architecture R0049 built (bounded rolling
preroll, seeded into R0045's active-utterance buffer, `UtteranceBuffer`
reused verbatim) is correct and requires no redesign — only its
CAPACITY parameter needs to change.

## MINIMUM NEXT FIX

**Not implemented this checkpoint** (per the charter). The
evidence-backed candidate, ranked:

- **Preferred: reuse `nexa.stt.utterance_buffer.UtteranceBuffer`'s own,
  already-validated `PRE_ROLL_MS = 500` default directly**, instead of
  deriving a NeXa/Pipecat-specific 300 ms figure — i.e. stop overriding
  `pre_roll_ms` in `_VadToProviderBridge`'s construction with the
  `vad_analyzer.params.start_secs + AUTOSIZED_PREROLL_MARGIN_SECS`
  computation, and use the SAME empirically-validated default the LOCAL
  voice path already trusts for the IDENTICAL underlying mechanism, on
  the SAME hardware. This is Option C-adjacent from R0049's own menu
  ("use Pipecat's own default 0.5 s preroll semantics if that is what
  the accepted path actually had") but grounded in the CORRECT source:
  not Pipecat's generic library default, but THIS codebase's own
  already-measured value for THIS exact VAD/hardware combination —
  arguably a stronger justification than either of R0049's original
  two named options.
- Would require zero new derivation logic, a smaller code diff than
  R0049 itself (delete the `AUTOSIZED_PREROLL_MARGIN_SECS`-based
  computation, pass `UtteranceBuffer`'s own default or a named constant
  equal to it), and no VAD/threshold changes of any kind.
- Should be re-validated with the SAME two tools this checkpoint used
  (`wav_alignment.py` byte-exact alignment + RMS) against a fresh real
  hardware capture before claiming REAL acceptance — do not fabricate a
  PASS a second time.

Not chosen or implemented here; left for R0051.

## FILES CHANGED

```
docs/reports/R0049_m2_6b_4j_restore_m2_6a_preroll_parity_20260912.md     | status corrected (real-hardware FAIL recorded)
docs/reports/R0050_m2_6b_4k_residual_onset_clipping_after_r0049_20260912.md | new (this report)
docs/CURRENT_STATE.md                                                     | corrected + new entry
docs/ROADMAP.md                                                           | new entry
docs/research/m2_6_cloud_realtime_voice/wav_alignment.py                  | new (diagnostic tool)
tests/test_m2_6b4k_wav_alignment.py                                       | new, 10 tests
```

**No `src/nexa/**` change of any kind** — this checkpoint is forensic
evidence only, exactly as directed ("Do NOT change preroll yet").

## TEST RESULTS

```
$ .venv/bin/python -m pytest tests/test_m2_6b4k_wav_alignment.py -q
10 passed in 0.03s
```

`ruff check docs/research/m2_6_cloud_realtime_voice/wav_alignment.py tests/test_m2_6b4k_wav_alignment.py`
— All checks passed. Full-suite/regression re-run deferred to R0051
(no production code changed this checkpoint to regress); the 981-test
baseline from R0049 stands unmodified.

## GIT STATUS

research/report commit: `b77792e`

Clean working tree (hash-record follow-up pending). Not pushed. The real WAV/JSON evidence
(`docs/research/m2_6_cloud_realtime_voice/ingress_captures/`) remains
git-ignored, per R0048/R0049's own `.gitignore` entry — not committed,
consistent with never distributing the operator's real voice recordings.

**Reproduce this report's exact numbers** (no Gemini, no hardware, pure
offline analysis of the already-captured real WAVs):

```
.venv/bin/python -c "
import sys; sys.path.insert(0, 'docs/research/m2_6_cloud_realtime_voice')
import wave, wav_alignment as wa
from pathlib import Path
D = Path('docs/research/m2_6_cloud_realtime_voice/ingress_captures')
for i in range(1, 7):
    with wave.open(str(D / f'{i:03d}_raw_with_context.wav')) as wf:
        raw = wf.readframes(wf.getnframes())
    with wave.open(str(D / f'{i:03d}_production_forwarded.wav')) as wf:
        fwd = wf.readframes(wf.getnframes())
    a = wa.find_alignment(raw, fwd)
    print(i, a.prefix_ms(sample_rate=16000), a.suffix_ms(sample_rate=16000))
"
```
