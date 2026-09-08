# R0024 — M2.4B.4: Bilingual Voice Input Research & Benchmark

- **Date:** 2026-09-08
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.4 — bilingual
  voice-input research + real-hardware benchmark + architecture decision**
- **Status:** **STAGE PAUSED — awaiting the operator's corpus recording.**
  The evaluation corpus and the operator recorder are built and verified;
  the benchmark, metrics, and architecture decision resume once the 50
  WAV fixtures exist. **No production change** — ADR-0003 D5 (explicit
  PL/EN language hint) remains in force.
- **Related:** `ADR-0003` D4 (`whisper.cpp base/q8_0` STT baseline, not
  frozen) + D5 (explicit PL/EN; naive auto-detect rejected), `R0006`
  (M2.0A voice feasibility spikes — the WER/latency baseline + the
  short-Polish→Japanese auto-detect failure), `R0008` (M2.2 whisper.cpp
  STT adapter), `R0016` (Polish STT error track), `R0022`/`R0023` (model
  A/B + serving freeze — `gemma4:e4b`, unchanged here).
- **Research dir:** `docs/research/m2_4b_bilingual_stt/`.

---

## TASK RESULT

**PARTIAL — research corpus + recorder ready; benchmark blocked on real
operator audio (by design).**

The task requires the benchmark to run on *real microphone/operator
speech*, and no reusable corpus of the required shape exists (the only
committed WAVs — `docs/research/m2_voice_spikes/asr_test_samples/`, 12
files — are legacy-copied, 3.5 s each, cover neither the short/ambiguous
nor the mixed/code-switch cases, and are a different sentence set). So per
the task's "prepare the capture harness first and STOP" instruction:

- **Built** `docs/research/m2_4b_bilingual_stt/corpus.py` — the fixed
  50-utterance ground-truth corpus: **15 PL, 15 EN, 10 short/ambiguous,
  10 mixed/code-switch**, each with `expected_language`
  (`pl`/`en`/`ambiguous`/`mixed` — the last two never scored as one
  "correct" language), a soft `leans` hint for shorts, a `primary`
  (matrix) language for mixed, the reference transcript, and
  casing/spelling `acceptable` variants.
- **Built** `docs/research/m2_4b_bilingual_stt/capture_corpus.py` — a
  resume-safe operator recorder that captures each utterance once from the
  already-known-good `"respeaker"` mic (resolved **by name** via
  `nexa.voice.device.find_device_index`, no ALSA config, no hand-typed
  index) as 16 kHz mono PCM16 WAV — exactly what `WhisperCppTranscriber`
  consumes. Verified end-to-end minus the operator's voice: device
  resolves (PyAudio index 3, 16 kHz), stream opens, reads, WAV writes,
  ground-truth manifest writes.
- **14 deterministic tests** (`tests/test_bilingual_stt_corpus.py`):
  corpus counts/labels, uid uniqueness/format, mixed lines really contain
  both languages, JSON round-trip, slug stability, and — the discipline
  guards — the research tooling imports nothing from `nexa` except the
  read-only device resolver and constructs no `ConversationSession` /
  provider / `VoiceRuntime` / config mutation.

**Next:** operator runs one command (below), then the benchmark +
architecture decision continue in this same report.

## CURRENT EXPLICIT-LANGUAGE BASELINE

The production path (ADR-0003 D5, `R0008`): `WhisperCppTranscriber` calls
`whisper-cli -m ggml-base-q8_0.bin -l {pl|en} -t 4 -oj -nt -np`, language
chosen **per run** (`--language pl` / `--language en`), never `auto`.
`R0006` §2 measured `base/q8_0` on the 12 legacy recordings: **0.354 avg
WER (EN 0.100, PL 0.536), ~1.69 s latency, ~221 MB peak RSS**. Polish is
the known-weak side (`R0016`, and the R0022 operator's Candidate-B Polish
STT misses). This report re-measures the baseline on the *new* corpus as
the reference, then compares automatic strategies against it.

## RESEARCH FINDINGS

*(Verified 2026-09-08; expanded during the benchmark.)*

- **whisper.cpp v1.9.3** (released 2026-08-20) is the current stable
  release and is exactly what NeXa pins
  (`nexa.stt.config.WHISPER_CPP_TAG = "v1.9.3"`). MIT-licensed, ggml-org,
  actively maintained — no upgrade needed for this stage.
- The pinned `whisper-cli` already supports both automatic modes this
  stage must benchmark:
  - `-l auto` — detect language on the first ~30 s window, then decode in
    that language (single pass);
  - `-dl` / `--detect-language` — detect and **exit** (a fast LID-only
    pass, no decode) → then a second explicit `-l <lang>` decode.
  Recent releases exposed `whisper_lang_id()` and candidate/probability
  language detection (issue #3603, "Proper Language detection fixed"),
  which the `-dl` pass surfaces as a per-language probability — usable for
  confidence-based routing.
- `R0006` §2.5 / ADR-0003 D5: naive `auto` reproduced a short-Polish
  utterance decoded as Japanese script — **engine-independent**, the
  reason D5 exists. Whether v1.9.3's improved detector still fails this
  way on *real operator shorts* is one of the questions this benchmark
  answers.
- Stronger Polish model candidates (to be pinned precisely, sized, and
  RAM/latency-estimated **before** any download; operator approval if
  large): `ggml-small-q8_0` (~250 MB, `R0006` spot-check ~0 WER but ~7 s /
  utterance — likely too slow), `ggml-large-v3-turbo-q5_0` (~550 MB,
  faster than `large` but unproven on Pi 5 CPU), possibly a distil / PL
  fine-tune if one is practical and licensed. Not downloaded yet.
- External language-ID libraries (e.g. fastText `lid.176`, `lingua`):
  candidate strategy C — evaluated only if A/B leave the question open.
  Likely unnecessary weight for a two-language decision if `-dl` is good
  enough; measured, not assumed.

## CANDIDATE STRATEGIES

| # | strategy | mechanism | expected cost |
|---|---|---|---|
| **B0** | explicit PL (baseline) | `-l pl` | 1 pass |
| **B0** | explicit EN (baseline) | `-l en` | 1 pass |
| 1 | auto direct | `-l auto` (detect + decode, one pass) | ~1 pass + detect on 1st window |
| 2 | detect → explicit | `-dl` (LID only) → `-l <detected>` decode | LID pass + 1 decode pass |
| 3 | *(if needed)* external LID → explicit | lightweight text/audio LID → `-l <lang>` | LID lib + 1 decode pass |
| — | stronger model | best strategy above, on a stronger `ggml-*` model | model-dependent |

"Do not build five pipelines if the first two answer the question."

## OPERATOR CORPUS

50 utterances, `docs/research/m2_4b_bilingual_stt/corpus.py`:

| group | n | `expected_language` | notes |
|---|---|---|---|
| `pl` | 15 | `pl` | the task's Polish list, verbatim |
| `en` | 15 | `en` | the task's English list, verbatim |
| `short` | 10 | `ambiguous` | Tak/Nie/Dobra/Okej/Super/Yeah/No/Right/Okay/Sure — `leans` hint only |
| `mixed` | 10 | `mixed` | within-utterance code-switch; `primary` = matrix language |

Ground truth in `fixtures/corpus_manifest.json`. Punctuation/casing
differences are class **S0** and never a semantic failure.

**Recorded so far:** _0 / 50 — awaiting the operator._

## PL RESULTS
_Pending corpus._

## EN RESULTS
_Pending corpus._

## SHORT / AMBIGUOUS RESULTS
_Pending corpus. Scored on: can the strategy inherit recent conversation
language safely, or mark low-confidence — NOT forced to a fake "correct"
language._

## MIXED / CODE-SWITCH RESULTS
_Pending corpus. Treated as the separate harder case: does any strategy
produce a genuinely usable canonical transcript, or does a single
"primary language" lose too much?_

## LANGUAGE-ID CONFUSION MATRIX
_Pending corpus (PL/EN only; PL→EN and EN→PL error counts)._

## WER RESULTS
_Pending corpus._

## SEMANTIC ERROR RESULTS
_Pending corpus. Classes: S0 harmless (punct/case) · S1 minor lexical ·
S2 meaning partially changed · S3 critical (wrong entity/intent/nonsense).
S2/S3 weighted over raw WER._

## LATENCY RESULTS
_Pending corpus. Reported as: added cost of (LID + STT) vs current
explicit-language STT._

## CPU / RAM / THERMAL RESULTS
_Pending corpus._

## STRONGER WHISPER CANDIDATE
_Pending: exact candidate + size + RAM + Pi latency estimate + license,
then operator approval before any large download._

## BEST LANGUAGE-ID STRATEGY
_Pending benchmark._

## BEST STT STRATEGY
_Pending benchmark._

## RECOMMENDED FUTURE ARCHITECTURE
_Pending benchmark. Will keep the five concepts separate: input speech
language ≠ STT decoding language ≠ transcript ≠ response language ≠ TTS
voice._

## DO WE HAVE ENOUGH EVIDENCE TO SUPERSEDE ADR-0003?
**Not yet.** ADR-0003 D5 (explicit PL/EN) stands until this benchmark
produces the evidence. No production change in this stage.

## RISKS
- Auto/LID adds latency to every turn — must be quantified against the
  conversational budget before it can ship.
- Short/ambiguous utterances may have no correct language in isolation —
  needs a product rule (inherit recent language / mark low-confidence),
  not a silent guess.
- True within-utterance code-switch may not be solvable by the same
  mechanism as per-turn switching — explicitly a separate, harder track.
- A stronger Polish model may fix accuracy but blow the latency budget or
  compete with `gemma4:e4b` for CPU (ADR-0003 D7).

## FILES CHANGED
- `docs/research/m2_4b_bilingual_stt/{corpus.py,capture_corpus.py,README.md}`
  (new).
- `docs/research/m2_4b_bilingual_stt/fixtures/corpus_manifest.json` (new,
  generated).
- `tests/test_bilingual_stt_corpus.py` (new, 14 tests).
- `docs/reports/R0024_…md` (this).
- No `src/`, `apps/`, `configs/`, or ADR change.

## COMMIT HASH
_To be recorded on commit._

## GIT STATUS
Branch `main`, not pushed.

## NEXT STEP
1. Operator records the corpus (one command — see RETURN / the research
   README).
2. Benchmark B0 baseline, then strategies 1 & 2 (then 3 / stronger model
   only if needed), fill every `_Pending_` section with per-group results.
3. Produce the recommended architecture + the explicit ADR-0003 D5
   verdict + the exact next implementation plan.
4. Only after that does any production auto-STT work begin — as its own
   stage, behind a superseding ADR revision if the evidence supports it.
