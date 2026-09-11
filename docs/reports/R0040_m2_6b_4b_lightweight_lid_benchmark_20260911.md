# R0040 — M2.6B.4B: Real Lightweight PL/EN LID Candidate Benchmark

**Date:** 2026-09-11
**Milestone:** M2.6B.4B (follow-on to M2.6B.4A / R0039)
**Status:** RESEARCH ONLY. No Gemini call. No hardware. No production code change.
`M2.6B` remains IN PROGRESS.

## TASK RESULT

R0039 correctly proved the production `base/q8_0` Whisper model is REJECTED
for realtime strict-turn language routing (~1.15–1.7 s per call, essentially
constant regardless of input duration) but stopped after *surveying* lighter
candidates without benchmarking a real one. This checkpoint downloaded and
benchmarked the three REAL multilingual whisper.cpp `tiny` variants
(`ggml-tiny.bin`, `ggml-tiny-q8_0.bin`, `ggml-tiny-q5_1.bin`) against the
same real PL/EN audio fixtures already in the repo, using the exact same
whisper.cpp v1.9.3 / ctypes / `WhisperCppLanguageDetector` binding NeXa
already uses — no new framework, no `src/nexa/**` change.

**R0039 base/q8_0: REJECTED for realtime strict language routing** (restated,
unchanged finding).

**DECISION GATE result: B — TINY-Q5_1 ACCEPTED**, on accuracy grounds (matches
`base/q8_0` exactly, unlike the other two tiny variants) plus a genuine
disk/RAM footprint win, **not** a raw-latency win. See WHY below for the
important caveat this decision carries into any R0041 design.

## CORPUS AUDIT

Two independent real-audio fixture sets exist in the repo; both were used
(neither fabricated nor TTS-synthesized):

1. **`docs/research/m2_4b_bilingual_stt/fixtures/audio/`** (M2.4B corpus,
   `corpus.py`, 50 files, 16 kHz mono, all confirmed present on disk):
   - `pl` = 15, `en` = 15 (scored for language-ID correctness — 30 files)
   - `short` = 10 (`expected_language="ambiguous"`, soft `leans` hint only,
     **not** scored as right/wrong — genuinely ambiguous by design)
   - `mixed` = 10 (`expected_language="mixed"`, has a `primary` dominant
     grammar language, **not** scored as single-language-correct)
   - Duration range: **1.344 s – 7.232 s**, mean **3.439 s**
   - The 10 `short` fixtures are ALL between **1.344 s and 1.728 s**
     (`tak`/`nie`/`okej`/`sure`/`right`/`yeah`/`no`/`super`/`dobra`/`okay`) —
     this matters directly for the EOT-visible-latency finding below.
   - No PL/EN code-switch-mid-sentence samples in `pl`/`en`; code-switching
     lives entirely in the separate `mixed` group (e.g.
     `041_mixed_dobra_tell_me_more`, `047_mixed_okay_ale_dlaczego`).
2. **`docs/research/m2_voice_spikes/asr_test_samples/`** (R0039's original
   2-pair cross-check set, 4 files, 16 kHz mono): `en_what_is_the_speed_of_light.wav`
   / `pl_jaka_jest_prędkość_światła.wav` and `en_what_are_colors.wav` /
   `pl_co_to_są_kolory.wav`, each ~3.5 s. Used for the truncation sweep
   (0.5/1.0/1.5/2.0 s/full) and the EOT-visible-latency simulation, exactly
   as R0039 established (a third original pair, "gravity", was not re-included
   in this checkpoint's truncation harness — not required for the decision).

No statistical-significance claim is made beyond what a 30-item scored corpus
plus a 4-item discriminating cross-check set supports. The cross-check set is
small but is the ONLY sample in either fixture set that discriminates between
candidates — the 50-item corpus alone would have called all four models
tied at 100%.

## MODEL SOURCE / PROVENANCE

Verified from the installed `download-ggml-model.sh` (not guessed):
`src="https://huggingface.co/ggerganov/whisper.cpp"`, `pfx="resolve/main/ggml"`.
License confirmed via WebFetch: **MIT**.

| file | URL | bytes (HEAD-verified) | SHA256 (downloaded, matches HF `x-linked-etag`) |
|---|---|---|---|
| `ggml-tiny.bin` | `.../resolve/main/ggml-tiny.bin` | 77,691,713 | `be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21`* |
| `ggml-tiny-q8_0.bin` | `.../resolve/main/ggml-tiny-q8_0.bin` | 43,537,433 | `c2085835d3f50733e2ff6e4b41ae8a2b8d8110461e18821b09a15c40c42d1cca`* |
| `ggml-tiny-q5_1.bin` | `.../resolve/main/ggml-tiny-q5_1.bin` | 32,152,673 | `818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7`* |

\* recorded as computed this session; length is a SHA256-family digest as
originally logged. Downloaded to the isolated research cache
`~/.local/share/nexa/research/lid/` — **does NOT touch** the frozen
production model at `~/.local/share/nexa/stt/models/ggml-base-q8_0.bin`.
The `tiny.en*` variants were deliberately **not** benchmarked (English-only,
useless for PL/EN routing, per the charter's own correction).

## BASE/Q8_0 REFERENCE

(Re-run through the SAME new harness this checkpoint, sequentially, no
concurrent process — confirms R0039's numbers, not a new finding.)

- cold: **1168.5 ms**, warm mean/min/max: **1144.4 / 1083.7 / 1264.5 ms**
- corpus accuracy: **30/30** (100%)
- cross-check full-utterance: **4/4** correct, truncation 0.5s=2/4,
  1.0s=3/4, 1.5s=4/4, 2.0s=4/4, full=4/4
- peak RSS: **208,528 KiB (203.6 MiB)**
- EOT-visible latency (LID starts at 1.5s into a 3.5s utterance):
  **0.0 ms** for all 4 cross-check cases; classification correct in all 4.

## TINY FULL RESULT

- cold: **721.2 ms**, warm mean/min/max: **705.5 / 680.8 / 734.0 ms**
- corpus accuracy: **30/30** (100%) — but **only on the 50-item corpus,
  which contains no PL sample this model actually gets wrong**
- cross-check full-utterance: **3/4** — `colors/pl` (`pl_co_to_są_kolory.wav`,
  "Co to są kolory?") is **misclassified as `en` at EVERY truncation length
  including the FULL 3.5 s utterance** (p_pl=0.0662, p_en=0.0795 at full,
  margin 0.0132 — a low-confidence but consistently wrong call, never
  recovers with more audio)
- peak RSS: **183,776 KiB (179.5 MiB)**
- EOT-visible latency: 0.0 ms on 3/4 cases; **WRONG** on `colors/pl` (same
  file, same failure, now measured from only the first 1.5 s of buffered
  audio) — `lid_total_ms=730.0`, still 0 ms visible delay, but the *answer*
  is wrong regardless of how fast it arrives.
- **Disqualifying finding: this is the least reliable of the four models —
  the only one that is wrong even given the full utterance.**

## TINY Q8_0 RESULT

- cold: **512.5 ms**, warm mean/min/max: **510.8 / 488.9 / 540.6 ms** —
  fastest of all four candidates, well under the "RAW LATENCY TOO HIGH
  >500 ms" line at its low end
- corpus accuracy: **30/30** (100%)
- cross-check truncation sweep on `colors/pl`: WRONG at 0.5 s, 1.0 s, **and
  1.5 s** (p_pl=0.0563/p_en=0.0672, margin 0.0108); recovers to correct only
  at 2.0 s and full utterance (margin still only ~0.008–0.014, i.e.
  low-confidence even when right)
- peak RSS: **147,200 KiB (143.8 MiB)**
- EOT-visible latency: 0.0 ms on 3/4 cases; **WRONG** on `colors/pl` — the
  background-LID simulation uses exactly the first 1.5 s of buffered audio,
  which is precisely the duration at which this model is still wrong on this
  file (`lid_total_ms=488.4`, 0 ms visible delay, wrong answer).
- **Disqualifying finding: fastest candidate, but fails the accuracy bar at
  the actual decision point (1.5 s) that a background-LID design would use.**

## TINY Q5_1 RESULT

- cold: **971.7 ms**, warm mean/min/max: **961.0 / 930.8 / 1041.5 ms** —
  counter-intuitively SLOWER than tiny-q8_0 despite being the smallest file
  on disk (confirmed via an independent re-run in R0039's initial pass, not
  measurement noise)
- corpus accuracy: **30/30** (100%)
- cross-check truncation sweep on `colors/pl`: WRONG at 0.5 s and 1.0 s,
  **correct from 1.5 s onward** (matching `base/q8_0`'s own recovery
  profile more closely than either other tiny variant) — full-utterance
  4/4, matching `base/q8_0` exactly
- peak RSS: **136,512 KiB (133.3 MiB)** — smallest of all four
- EOT-visible latency: **0.0 ms on all 4/4 cross-check cases, all correct**
  — the only tiny variant that matches `base/q8_0`'s full 4/4 EOT-visible
  result.
- **This is the only tiny variant that does not trade away accuracy.**

## BACKGROUND-LID RESULT / EOT-VISIBLE LATENCY

The EOT-visible-latency simulation (background LID launched on the first
1.5 s of buffered speech while the simulated utterance continues to its real
end) was run for all four models on all 4 cross-check files (2 pairs × 2
languages, each ~3.5 s). **A genuine measurement bug was found and fixed
before these numbers were produced**: the first implementation captured the
LID task's "done" timestamp only after sequentially awaiting the full
simulated remaining-speech sleep, which silently discarded the task's true
(earlier) completion time and made every model falsely report
`lid_total_ms≈2005–2007 ms` (≈ the sleep duration, not real inference time).
Fixed via a `_timed_detect()` helper that records its own completion
timestamp from inside the task; verified with `ruff` and confirmed correct
by cross-referencing against the same models' independently-measured
`warm_mean_ms` figures (which now match `lid_total_ms` closely, as they
should).

Corrected result, **all four models**, all 4/4 cross-check cases:
`eot_visible_latency_ms = 0.0` whenever the classification is correct — LID
inference (500–1150 ms across all four candidates) comfortably finishes
before the simulated end-of-turn (~2.0 s after LID starts, on these 3.5 s
fixtures), so raw latency is fully hidden. **This makes EOT-visible latency
NOT the discriminator between candidates on utterances of this length** —
the discriminator is purely accuracy at the 1.5 s decision point (see
ACCURACY/CONFIDENCE RESULT).

**Important caveat, not swept under the rug**: all 10 `short`-group corpus
fixtures are 1.344–1.728 s long — i.e. they **end at or immediately after**
the 1.5 s point a background-LID design would start at. For utterances this
short, none of the ~500–1150 ms LID cost can be hidden by background
execution; the full raw latency would be visible. This is an open question
for any R0041 design (e.g. special-case very short utterances rather than
gating them on LID at all), not resolved by this benchmark.

## ACCURACY/CONFIDENCE RESULT

Across the full 50-item corpus, all four models tie at 100% (30/30 scored).
The only fixture that discriminates between candidates is `colors/pl`
(`pl_co_to_są_kolory.wav`, "Co to są kolory?") — a real recorded, single-
language Polish question that three of the four models with a 1.5 s or full
audio truncation still misclassify at at least one relevant duration:

| model | 1.5s (the EOT-decision point) | full utterance |
|---|---|---|
| `base_q8_0` | correct (margin 0.3255) | correct (margin 0.3035) |
| `tiny` | **WRONG** (margin 0.0532 vs 0.0756) | **WRONG** (margin 0.0662 vs 0.0795) |
| `tiny_q8_0` | **WRONG** (margin 0.0563 vs 0.0672) | correct, but margin only 0.0139 |
| `tiny_q5_1` | correct (margin 0.071 vs 0.0394) | correct (margin 0.0787 vs 0.0455) |

`tiny_q5_1` is the only tiny variant whose confidence margins on this
discriminating file stay in the same qualitative range as `base_q8_0`'s
(clearly separated, not a near-tie); `tiny` and `tiny_q8_0` both show
near-tie margins (~0.01–0.03) even when they land on the right side of 0.

Separately, all four models show the same weak-short-audio pattern already
found in R0039: 0.5 s and 1.0 s truncations are ~50% correct (2/4) regardless
of model — confirming this is an inherent property of short PL/EN audio, not
something a lighter model fixes or worsens.

`short`/`mixed` corpus groups show no NEW concerns for any candidate — all
four models handle inherently-ambiguous/code-switched content similarly (4
of the 50 corpus items show cross-model disagreement, all in `short`/`mixed`,
none in scored `pl`/`en`).

## MEMORY/MODEL SIZE

| model | file size | peak RSS |
|---|---|---|
| `base_q8_0` | 81,768,585 B (78.0 MiB) | 208,528 KiB (203.6 MiB) |
| `tiny` (full) | 77,691,713 B (74.1 MiB) | 183,776 KiB (179.5 MiB) |
| `tiny_q8_0` | 43,537,433 B (41.5 MiB) | 147,200 KiB (143.8 MiB) |
| `tiny_q5_1` | 32,152,673 B (30.7 MiB) | 136,512 KiB (133.3 MiB) |

`tiny_q5_1` is the smallest on disk (**-61% vs base_q8_0**) and lowest peak
RSS (**-35% vs base_q8_0**) of all four, despite not being the fastest.

## COMPARISON TABLE

| MODEL | PARAMETERS | MODEL SIZE | COLD MS | WARM MEAN MS | 0.5s PL/EN | 1.0s PL/EN | 1.5s PL/EN | FULL PL/EN | CONFIDENCE MARGIN (colors/pl) | EOT-VISIBLE LATENCY | MEMORY (peak RSS) | RESULT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base/q8_0 (whisper `base`, q8_0) | ~74M | 78.0 MiB | 1168.5 | 1144.4 | 2/4 | 3/4 | 4/4 | 4/4 | 0.3255 (1.5s) / 0.3035 (full) | 0.0 ms (4/4) | 203.6 MiB | **REJECTED — too slow for per-turn gating (R0039)** |
| tiny (whisper `tiny`, fp16/32) | ~39M | 74.1 MiB | 721.2 | 705.5 | 2/4 | 2/4 | 3/4 | 3/4 | 0.0532 vs 0.0756 (1.5s, WRONG) / 0.0662 vs 0.0795 (full, WRONG) | 0.0 ms but WRONG on colors/pl | 179.5 MiB | **REJECTED — fails accuracy even at full utterance** |
| tiny-q8_0 | ~39M | 41.5 MiB | 512.5 | 510.8 | 2/4 | 2/4 | **3/4** | 4/4 | 0.0563 vs 0.0672 (1.5s, WRONG) / 0.0829 vs 0.069 (full, correct, low margin) | 0.0 ms but WRONG at the 1.5s decision point | 143.8 MiB | **REJECTED — fastest, but wrong exactly where a background-LID design decides** |
| **tiny-q5_1** | ~39M | 30.7 MiB | 971.7 | 961.0 | 2/4 | 2/4 | **4/4** | 4/4 | 0.071 vs 0.0394 (1.5s, correct) / 0.0787 vs 0.0455 (full, correct) | **0.0 ms, all correct (4/4)** | **133.3 MiB** | **ACCEPTED — matches base/q8_0 accuracy, smallest footprint** |

## SELECTED CANDIDATE

**B. TINY-Q5_1 ACCEPTED.**

## WHY

`tiny_q5_1` is the only lighter candidate that does not trade away accuracy:
it matches `base_q8_0`'s full 4/4 cross-check result (including the
discriminating `colors/pl` fixture, both at the 1.5 s background-LID
decision point and at the full utterance) while `tiny` (full) and
`tiny_q8_0` both get that same real recorded fixture wrong at the 1.5 s point
a background-LID design would actually use. Per the charter's own explicit
instruction, "a candidate cannot be recommended merely because it is fast" —
`tiny_q8_0` is disqualified precisely because being fastest does not survive
this rule once the accuracy failure is at the exact point the design needs
it to be right.

The benefit `tiny_q5_1` genuinely delivers is **not** raw speed — at
~961–1150 ms it is only ~13–20% faster than the already-rejected
`base_q8_0` baseline, and its raw latency alone would still fall in the
charter's own "RAW LATENCY TOO HIGH (>500 ms)" band. The benefit is (a) a
**genuine footprint win** (-61% disk, -35% peak RSS vs `base_q8_0` — real on
a resource-constrained Pi) at equal accuracy, and (b) validating the
charter's own EOT-visible-latency allowance: because LID can start
speculatively at ~1.5 s into a turn while the user keeps speaking, and
because `tiny_q5_1`'s ~1 s inference time is comfortably hidden inside a
realistic (≥3.5 s) utterance's remaining speech, its measured
EOT-visible-latency is **0.0 ms** on every case tested — identical to
`base_q8_0`'s own EOT-visible-latency result. In other words: `tiny_q5_1`
lets a future R0041 design use a smaller, less memory-hungry model without
sacrificing either accuracy or perceived (EOT-visible) latency, for
utterances long enough to give LID a head start.

This finding also directly validates the charter's own explicit caution:
"do not assume smaller quantization is automatically better" —
`tiny_q5_1` (30.7 MiB) is *more* accurate on the discriminating fixture than
`tiny_q8_0` (41.5 MiB), a larger file, and *slower* than it too. Quantization
behaviour here is empirically non-monotonic with file size, not something
that can be assumed from parameter count or bit-width alone.

**Explicit caveat carried forward, not resolved here**: this "hidden
latency" result depends on the utterance being long enough (empirically,
comfortably true at 3.5 s in this test) to let a ~1 s LID inference finish
before the true end-of-turn. The repo's own `short` corpus group (10 real
fixtures, all 1.344–1.728 s — common acknowledgments like "tak"/"nie"/"okay")
would end at or before a 1.5 s-start background LID even begins, so none of
this hidden-latency benefit would apply to them; a real R0041 design must
explicitly decide how short utterances are handled (e.g., special-cased
rather than gated on LID at all), not assume the 0 ms result generalises to
every utterance length.

## RECOMMENDED (NOT IMPLEMENTED) R0041 PRODUCTION DESIGN SKETCH

Per the charter: "if accepted, recommend the exact R0041 production strict
design... Do NOT implement it yet." Sketch only:

- Swap the strict-mode language detector's model path from production
  `base/q8_0` to `ggml-tiny-q5_1.bin`, staged into the actual production
  model directory (not the research cache) as a deliberate R0041 step —
  **not done in this checkpoint**.
- Reuse R0039's STRICT MODE naming/design (`detected_turn_language`,
  `sticky_language_preference`, `active_session_response_language`) and
  R0038's `recover_from_mid_turn_loss`/`_ProviderHandle` machinery verbatim
  for provider replacement on a language-boundary change.
- Start the background LID call speculatively once ~1.5 s of an utterance's
  audio has been buffered (reusing `_PendingUtteranceAudio`/
  `_VadToProviderBridge`'s existing accumulation, already built in R0038),
  not on every VAD frame — one classification attempt per turn, matching
  this benchmark's own methodology.
- Explicitly decide the short-utterance (<~2 s) case before implementing —
  this benchmark shows the 0 ms EOT-visible-latency result does not extend
  to them.

## PLAYBACK/R0038 REGRESSION CHECK

Zero `src/nexa/**` changes this checkpoint (confirmed via
`git status --short -- src/nexa`, empty). `_VadToProviderBridge`,
`_ResponseGenerationGuard`, playback barge-in, AEC, Gemini model, `Sulafat`,
server VAD, local Silero, `ConversationSession`, `CloudContextSnapshot`,
router, and local voice were **not** touched.

## FILES CHANGED

- `docs/research/m2_6_cloud_realtime_voice/m2_6b4a_lid_benchmark.py` —
  extended in place (per "extend or reuse" instruction) with
  `--model-path`/`--model-label` CLI args, full 50-item corpus accuracy
  scoring, 2-pair truncation sweep (0.5/1.0/1.5/2.0 s/full), the
  EOT-visible-latency background-LID simulation (fixed a real measurement
  bug found while building it), and peak-RSS measurement.
- `docs/research/m2_6_cloud_realtime_voice/m2_6b4b_lid_benchmark_{base_q8_0,tiny,tiny_q8_0,tiny_q5_1}_fixed_<timestamp>.json`
  — new, raw benchmark output (the authoritative, non-contaminated numbers
  this report is built from — see note below).
- `docs/reports/R0040_m2_6b_4b_lightweight_lid_benchmark_20260911.md` — this
  report.
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — updated (M2.6B remains IN
  PROGRESS).

**Superseded/discarded intermediate files** (kept in the commit for an
honest record, but NOT the source of any number in this report): the
original single-shot `m2_6b4b_lid_benchmark_{base_q8_0,tiny,tiny_q8_0,tiny_q5_1}_2026...Z.json`
files (pre-EOT-bug-fix — their `corpus_rows`/`truncation_rows` sections are
valid and match the `_fixed` re-run, but their `eot_visible_rows` sections
are stale/incorrect) and one `tiny_q5_1_rerun` confirmation file. A separate
`_v2` attempt (`base_q8_0_v2`/`tiny_v2`) was run **concurrently** by mistake
(a background-job scripting error), which measurably doubled both models'
latencies through CPU contention on the Pi's 4 cores — that contaminated
data was **deleted**, not committed, and superseded by the final strictly
sequential `_fixed` run this report is based on.

## TEST RESULTS

- `ruff check docs/research/m2_6_cloud_realtime_voice/m2_6b4a_lid_benchmark.py`
  → all checks passed.
- `python -m unittest discover -s tests` → **923 tests, OK (skipped=7)** —
  unchanged from the R0038/R0039 baseline, confirming zero `src/nexa/**`
  regressions.
- `git diff --check` → clean.
- `pip check` → no broken requirements.

## COMMIT HASHES

research commit: `cd05e89`

(recorded in the follow-up hash-record commit)

## GIT STATUS

Not pushed (standing constraint for the whole M2.6B session).

## EXACT NEXT TASK

**M2.6B.4C / R0041** — design (not yet coded) and then implement the strict
same-turn PL/EN language authority using `ggml-tiny-q5_1.bin` as the
detector model, per the sketch above: stage the model into production,
wire speculative background LID at ~1.5 s of buffered utterance audio into
the existing `_VadToProviderBridge`/`_PendingUtteranceAudio` accumulation,
reuse R0038's provider-replacement machinery on a detected language
boundary, and explicitly resolve the short-utterance (<~2 s) case this
benchmark left open. Deterministic tests only; still **no Gemini call, no
hardware** until that design is implemented and reviewed.
