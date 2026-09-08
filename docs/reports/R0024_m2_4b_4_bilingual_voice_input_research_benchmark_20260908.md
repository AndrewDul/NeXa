# R0024 — M2.4B.4: Bilingual Voice Input Research & Benchmark

- **Date:** 2026-09-08
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.4 — bilingual
  voice-input research + real-hardware benchmark + architecture decision**
- **Status:** **COMPLETE — benchmark run on the 50-utterance real-operator
  corpus; architecture chosen; ADR-0003 D5 verdict = PARTIALLY supersede.**
  **No production change** — this is a research + decision stage; the
  production `VoiceRuntime` explicit-language behaviour is untouched and
  ADR-0003 D5 is not edited here (only a proposed amendment is recorded).
- **Related:** `ADR-0003` D4 (`whisper.cpp base/q8_0` STT baseline, not
  frozen) + D5 (explicit PL/EN; naive auto-detect rejected), `R0006`
  (M2.0A voice feasibility spikes — the WER/latency baseline + the
  short-Polish→Japanese auto-detect failure), `R0008` (M2.2 whisper.cpp
  STT adapter), `R0016` (Polish STT error track), `R0022`/`R0023` (model
  A/B + serving freeze — `gemma4:e4b`, unchanged here).
- **Research dir:** `docs/research/m2_4b_bilingual_stt/`.

---

## TASK RESULT

**PASS.** The operator recorded the full 50-utterance real-voice corpus.
The benchmark ran the pinned production whisper.cpp (`v1.9.3`,
`ggml-base-q8_0`, `-t 4`, default beam) over all 50 fixtures under four
strategies (200 runs): explicit `-l pl` on all 50, explicit `-l en` on all
50, `-l auto` on all 50, and `-dl` (detect-only) → explicit decode on all
50. Headline evidence:

- **whisper.cpp v1.9.3 never confuses Polish with English.** Over the 30
  monolingual utterances: **0 PL→EN, 0 EN→PL.** Language-ID accuracy is
  **90 %** (27/30); all **3** misses are slips to a *third* language
  (`pl→ru`, `en→ko`, `en→he`), each at low-ish confidence
  (`p` 0.35 / 0.47 / 0.53 vs 0.70–0.99 for the confident correct ones).
- **When the language label is right, `-l auto` transcript quality ≈ the
  explicit baseline.** PL WER 0.205 vs baseline 0.139; EN WER 0.113 vs
  0.041 — the whole gap is the 1 PL + 1 EN third-language misdetect that
  turn into wrong-script garbage (S3). Remove those two and auto == baseline.
- **`-l auto` costs ~+1.0–1.2 s** over explicit STT (one folded
  language-detection pass). **`-dl` → explicit costs ~+1.3 s** (the `-dl`
  pass alone is ~1.37 s because it still runs the encoder) and buys
  **nothing** in detection over `-l auto` (identical probabilities) — its
  only lever is *substituting* the decode language, which a naive PL/EN
  fallback does crudely.
- **Short/ambiguous:** auto reliably gets the EN-leaning shorts
  (`p` 0.68–0.91) but **breaks 3 of the 4 hard-PL shorts** —
  "Tak."→`en`/"Talk.", "Nie."→`ru`/"Не.", "Dobra."→`ru`/"Доброе утро!" —
  all at low confidence (`p` 0.24–0.56). A confidence gate catches them.
- **Mixed / code-switch:** `-l auto` = **6 USABLE / 4 PARTIALLY_USABLE /
  0 BROKEN** — better than either forced language (which reliably destroys
  the non-forced half). Not "supported"; embedded 2–3-word fragments in
  the other language get phonetically mangled, though the semantic core
  usually survives for the LLM.
- **Resources:** identical across strategies — peak RSS **221 MB**, temp
  **≤ 70 °C**, `throttled 0x0` for all 200 runs, ~85 % of 4 cores while
  decoding. No stronger-model download needed to choose the architecture.

**Decision:** future bilingual input = **`-l auto` single pass + a
PL/EN-and-confidence guard on the label + inherit-previous-input-language
fallback** (details below). **ADR-0003 D5: PARTIALLY superseded** —
automatic per-utterance PL↔EN switching for normal-length turns is
evidence-backed; isolated shorts and true within-utterance code-switch
stay deferred. No production change in this stage; a proposed ADR-0003
amendment is recorded, not applied.

## CORPUS VALIDATION

`docs/research/m2_4b_bilingual_stt/fixtures/audio/` — **50 / 50 WAV present**,
all **16 000 Hz, mono, PCM16**, all readable/decodable, durations
1.34–7.23 s (no near-silent, no clipping, no <0.35 s). `corpus.jsonl` = 50
records, `corpus_manifest.json` = 50 items; per-file duration cross-checks
to within 50 ms. Group counts: **pl 15 · en 15 · short 10 · mixed 10**.
Peak levels 1805–4126 (RMS −37 to −45 dBFS — quiet-ish but well inside
usable range; whisper had no trouble). **No fixture missing or corrupt —
no re-record needed.**

## CURRENT EXPLICIT-LANGUAGE BASELINE

Production path (ADR-0003 D5, `R0008`): `WhisperCppTranscriber` calls
`whisper-cli -m ggml-base-q8_0.bin -l {pl|en} -t 4 -oj -nt -np`, language
chosen **per run**, never `auto`. Re-measured on this corpus:

| baseline | n | WER mean | WER median | S0 | S1 | S2 | S3 | latency median | RSS | temp |
|---|---|---|---|---|---|---|---|---|---|---|
| `-l pl` on the 15 PL | 15 | **0.139** | 0.0 | 8 | 4 | 2 | 0 | **1.79 s** | 221 MB | ≤69 °C |
| `-l en` on the 15 EN | 15 | **0.041** | 0.0 | 11 | 1 | 3 | 0 | **1.70 s** | 221 MB | ≤69 °C |

Consistent with `R0006` §2 (EN strong, PL weak). The 2 PL S2s and 3 EN S2s
are a **pre-existing `base/q8_0` quality issue** (ADR-0003 D4 / `R0016`),
independent of anything bilingual — see SEMANTIC ERROR RESULTS.

**Forced-wrong-language is catastrophic** (the reason D5 exists):
`-l en` on the 15 PL fixtures → WER **1.234**; `-l pl` on the 15 EN → WER
**1.041** (WER > 1 because whisper inserts phantom words). Any automatic
scheme must therefore be judged first on *never forcing the wrong
language*.

## RESEARCH FINDINGS

*(Verified 2026-09-08 — ecosystem check + this benchmark.)*

- **whisper.cpp `v1.9.3`** (2026-08-20) is the current stable release and
  exactly what NeXa pins (`nexa.stt.config.WHISPER_CPP_TAG`). MIT,
  ggml-org, actively maintained. **No upgrade and no new model download
  is needed to choose the architecture.**
- `whisper-cli` language modes actually available and measured here:
  - **`-l auto`** — one invocation: a language-detection decode pass, then
    the full transcription in the detected language. Emits
    `auto-detected language: <lang> (p = <prob>)` on **stderr** (suppressed
    by `-np`); the JSON exposes only `result.language`, not `p`.
  - **`-dl` / `--detect-language`** — detect and exit. **Not free**: it
    still runs the encoder (~1.37 s mean here) — roughly a full `base`
    decode's cost.
  - Neither CLI mode exposes the **per-language** probability vector
    (`p_pl` vs `p_en`); only the top-1 language + its `p`. A
    PL/EN-*constrained* detector (which this benchmark shows would be
    ~100 % accurate) needs the library (`whisper_lang_id` /
    candidate-probability API, issue #3603) — an implementation detail for
    the next stage, not doable from the CLI.
- **The D5-original failure has shifted, not vanished.** `R0006` saw short
  Polish → Japanese script. v1.9.3 gives short Polish → **Russian**
  ("Nie."→"Не.", "Dobra."→"Доброе утро!") and → **English** ("Tak."→"Talk.").
  Still engine-level, still real — but now **always at low confidence**
  (`p` 0.24–0.56), which makes it gate-able.
- **PL and EN are not confused with each other.** 0/15 PL→EN, 0/15 EN→PL.
  Every LID error is a slip to a language NeXa doesn't support.
- No external audio/text language-ID library was pulled in: a lexical
  arbiter over dual explicit decodes was tried (post-hoc, from the
  existing `b0_pl`/`b0_en` transcripts) and scored **63 %** — *worse* than
  `-l auto` — because a forced-wrong-language decode still emits enough
  function words/diacritics of the forced language to fool a word counter.
  Rejected.

## CANDIDATE STRATEGIES

| # | strategy | mechanism | LID acc (mono) | PL WER | EN WER | added latency | verdict |
|---|---|---|---|---|---|---|---|
| B0 | explicit `-l pl` / `-l en` | production today | n/a (fixed) | 0.139 | 0.041 | — | reference |
| **1** | **auto direct** | `-l auto`, one pass | **90 %** | 0.205 | 0.113 | **+1.0–1.2 s** | **chosen (with a guard)** |
| 2 | detect → explicit | `-dl` then `-l <det>` | 90 % (identical) | 0.227 | 0.041¹ | +1.3 s | rejected — pure extra latency |
| 3 | dual-decode + lexical arbiter | `-l pl` + `-l en`, pick | 63 % | — | — | +2.0 s | rejected — worse than #1 |
| — | stronger model | #1 on a bigger `ggml-*` | — | — | — | — | not needed for the decision |

¹ `dl_then_explicit` EN WER matches baseline only because its crude
"third-language → decode as `en`" fallback happened to rescue the 2 EN
misdetects; the same fallback *broke* the 1 PL misdetect (`ru`→forced
`en`→"How to install it?"). It is not a real accuracy gain.

## PL RESULTS

15 Polish fixtures. `-l auto` transcript = `-l pl` transcript **verbatim
on 14/15**; the one difference is `002` where auto misdetects `ru`.

| fixture | `-l pl` (baseline) transcript | WER | auto label | note |
|---|---|---|---|---|
| 001 Co to jest czarna dziura? | "Co to jest, czarna dziura." | 0.0 | pl 0.31 | comma only (S0) |
| 002 Jak ona powstaje? | "Jak ona powstaje." | 0.0 | **ru 0.35** | auto → "как она повстает." (**S3**) |
| 003 Z czego składa się gwiazda? | exact | 0.0 | pl 0.98 | S0 |
| 004 Po co człowiekowi sen? | "Po co? Człowiekowi sam." | 0.5 | pl 0.27 | **"sen"→"sam"** (**S2**) |
| 005 …rzeczywisty kolor Słońca? | "Jakie jest rzeczywisty kolor słońca?" | 0.2 | pl 0.97 | gender "Jaki"→"Jakie" (S1) |
| 006 …kilogram żelaza czy kilogram piór? | "…cięższe kilogram żelaza…piór?" | 0.0 | pl 0.99 | comma (S0) |
| 007 Powiedz mi to trochę prościej. | exact | 0.0 | pl 0.78 | S0 |
| 008 Nie rozumiem, wyjaśnij jeszcze raz. | "Nie rozumiem. Wyjaśni jeszcze raz." | 0.2 | pl 0.998 | "wyjaśnij"→"wyjaśni" (S1) |
| 009 …mąkę pszenną i żytnią. | "…mąkę pszenną i rzytnią." | 0.14 | pl 0.995 | "żytnią"→"rzytnią" (S1) |
| 010 Która będzie godzina za dziewięćdziesiąt pięć minut? | "…za 95 minut." | 0.29 | pl 0.97 | number spelled→digits, meaning identical (S1) |
| 011 Co dzisiaj możemy zrobić? | "Co dzisiaj możemy zrobić." | 0.0 | pl 0.98 | S0 |
| 012 Powiedz mi coś ciekawego. | exact | 0.0 | pl 0.98 | S0 |
| 013 Jak działa komputer? | "Jak działa komputer." | 0.0 | pl 0.44 | S0 |
| 014 Dlaczego niebo jest niebieskie? | "Dlaczego nie bo jest nie pieskie?" | 1.0 | pl 0.98 | **"niebo"→"nie bo", "niebieskie"→"nie pieskie"** (**S2**) |
| 015 Wróćmy do poprzedniego tematu. | exact | 0.0 | pl 0.997 | S0 |

**PL: baseline S0 8 / S1 4 / S2 2 / S3 0, WER 0.139. `-l auto` adds one S3
(002) → S0 7 / S1 4 / S2 2 / S3 1, WER 0.205.** The 2 S2s (`004` sleep→alone,
`014` blue→"not doggy") are the pre-existing `base/q8_0` Polish weakness.

## EN RESULTS

15 English fixtures. `-l auto` transcript = `-l en` transcript **verbatim
on 14/15**; the one difference is `029` where auto misdetects `he`.
(`021` auto label is `ko` but its transcript is still English — same S2 as
baseline.)

| fixture | `-l en` (baseline) transcript | WER | auto label | note |
|---|---|---|---|---|
| 016 What is a black hole? | exact | 0.0 | en 0.85 | S0 |
| 017 How does it form? | exact | 0.0 | en 0.86 | S0 |
| 018 What is a star made of? | exact | 0.0 | en 0.34 | S0 |
| 019 Why do humans need sleep? | exact | 0.0 | en 0.28 | S0 |
| 020 …actual colour of the Sun? | "…actual color of the sun?" | 0.0 | en 0.73 | accepted "color" variant (S0) |
| 021 …one kilogram of iron or one kilogram of feathers? | "…1 kilogram of iron or kilogram of **features**?" | 0.42 | **ko 0.47** | **"feathers"→"features"** (**S2**); transcript still English |
| 022 Explain it more simply. | exact | 0.0 | en 0.70 | S0 |
| 023 I still don't understand, explain it again. | "…explain it **better**." | 0.29 | en 0.45 | **"again"→"better"** — intent changed (**S2**) |
| 024 …bought bread flour and rye flour. | "…bought **bread, flour** and rye flour." | 0.22 | en 0.64 | 2 items → 3; memory content wrong (**S2**) |
| 025 …in ninety-five minutes? | "…in 95 minutes?" | 0.22 | en 0.34 | number form, meaning identical (S1) |
| 026 What can we do today? | exact | 0.0 | en 0.59 | S0 |
| 027 Tell me something interesting. | exact | 0.0 | en 0.72 | S0 |
| 028 How does a computer work? | exact | 0.0 | en 0.74 | S0 |
| 029 Why is the sky blue? | exact | 0.0 | **he 0.53** | auto → "ומה זה איזו שקי בלו?" (**S3**) |
| 030 Let's go back to the previous topic. | exact | 0.0 | en 0.86 | S0 |

**EN: baseline S0 11 / S1 1 / S2 3 / S3 0, WER 0.041. `-l auto` adds one
S3 (029) → S0 10 / S1 1 / S2 3 / S3 1, WER 0.113.** The 3 S2s
(`021` feathers→features, `023` again→better, `024` flour grouping) are
pre-existing `base/q8_0` errors — and `023`/`024` are exactly the kind of
S2 that corrupts NeXa intent/memory.

## SHORT / AMBIGUOUS RESULTS

10 isolated one-word utterances. No conversational history → for the
genuinely neutral ones there is **no objectively correct language**; not
forced.

| fixture | spoke | leans | `-l pl` | `-l en` | `-l auto` (label, p) | verdict |
|---|---|---|---|---|---|---|
| 031 | Tak. | pl | **Tak.** | "Talk." | en 0.56 → "Talk." | **auto BROKEN** (PL word, EN garble) |
| 032 | Nie. | pl | **Nie.** | "Yeah." | ru 0.42 → "Не." | **auto BROKEN** |
| 033 | Dobra. | pl | **Dobra.** | "Good." | ru 0.24 → "Доброе утро!" | **auto BROKEN** (hallucinated) |
| 034 | Okej. | pl | "Ok." | "Okay." | en 0.58 → "Okay." | ok (semantically fine either way) |
| 035 | Super. | none | "Super." | "Super!" | en 0.37 → "Super!" | ok (word is bilingual) |
| 036 | Yeah. | en | "Ja." | **Yeah.** | en 0.91 → "Yeah." | auto ok |
| 037 | No. | none | "No." | "No." | en 0.89 → "No." | ok (bilingual) |
| 038 | Right. | en | "Właśnie." | **Right.** | en 0.83 → "Right." | auto ok |
| 039 | Okay. | none | "Ok." | "Okay." | en 0.86 → "Okay." | auto ok |
| 040 | Sure. | en | "Szó!" | **Sure.** | en 0.68 → "Sure." | auto ok |

**Pattern:** the EN-leaning shorts detect `en` at **p 0.68–0.91** and
transcribe correctly; the hard-PL shorts (031–033) detect the *wrong*
language at **p 0.24–0.56** and produce garbage. Confidence cleanly
separates the two groups. **Product rule (research, not implemented):**
for a short utterance where `p < ~0.6` **or** the detected language ∉
{pl, en}, do **not** trust the detector — set `InputSpeechLanguage` =
the `ConversationSession`'s last input language (session default on turn
1) and re-decode explicitly in that language. `b0_pl` shows this recovers
031–033 perfectly ("Tak.", "Nie.", "Dobra.").

## MIXED / CODE-SWITCH RESULTS

10 within-utterance code-switch fixtures. Classified on the `-l auto`
transcript (the best of the four).

| fixture | spoke | `-l auto` transcript | class | what was lost |
|---|---|---|---|---|
| 041 | Dobra, tell me more. | "Dobra, tell me more!" | **USABLE** | — |
| 042 | Okay, wróćmy do polskiego. | "Ok, wróćmy do **pluskiego**." | PARTIALLY_USABLE | "polskiego"→"pluskiego" (the word "Polish" garbled) |
| 043 | Powiedz mi what a black hole is. | "**Hope it's me.** What a black hole is." | PARTIALLY_USABLE | "Powiedz mi"→"Hope it's me"; English question fully intact |
| 044 | Can you wyjaśnić to prościej? | "**Keniu** wyjaśnić to prostiej." | PARTIALLY_USABLE | "Can you"→"Keniu"; Polish core intact |
| 045 | No dobra, let's continue. | "No dobra, let's continue." | **USABLE** | — |
| 046 | Tell me więcej o gwiazdach. | "Tell me więcej o gwiazdach." | **USABLE** | — |
| 047 | Okay, ale dlaczego? | "Ok, ale dlaczego?" | **USABLE** | — |
| 048 | Let's wrócić do tego. | "Let's wrócić do tego." | **USABLE** | — |
| 049 | Explain mi to jeszcze raz. | "**Explenuj** mi to jeszcze raz." | PARTIALLY_USABLE | "Explain"→"Explenuj" (verb hybridised); rest intact |
| 050 | Super, now powiedz to po polsku. | "Super! Now Powiedz to po Polsku!" | **USABLE** | — |

**`-l auto`: 6 USABLE / 4 PARTIALLY_USABLE / 0 BROKEN.** Forced single
language is worse — `-l en` broke 042/047 outright ("let's go back to the
other side", "I'll let you go"); `-l pl` broke 043 ("Poprzez mnie, co
blakuje to."). **Code-switch is NOT "supported":** whenever one language
appears as a 2–3-word embedded fragment, that fragment is phonetically
mangled. But the **semantic core survives often enough** that
`gemma4:e4b` would still answer correctly in 8–10 of 10. The `-l auto`
**label** for mixed is unreliable (it picks whichever side has the
stronger phonetic signal); only the transcript matters here.

## LANGUAGE-ID CONFUSION MATRIX

30 monolingual fixtures. `-l auto` and `-dl` produce **identical**
detection (same language, same `p`).

|  | detected pl | detected en | detected other |
|---|---|---|---|
| **spoke pl** (15) | **14** | **0** | 1 (`ru`) |
| **spoke en** (15) | **0** | **13** | 2 (`ko`, `he`) |

- Overall accuracy **90.0 %** (27/30). PL **93.3 %**, EN **86.7 %**.
- **PL↔EN confusion: 0.** Every error is a third language.
- The 3 misses: `002` "Jak ona powstaje?" → `ru` (p 0.35, 3.5 s);
  `021` "What is heavier, one kilogram…feathers?" → `ko` (p 0.47, 6.3 s);
  `029` "Why is the sky blue?" → `he` (p 0.53, 2.6 s). Two of three are
  *not* short (3.5 s, 6.3 s) — length alone doesn't predict the failure;
  low confidence does (all three ≤ 0.53; every correct detection ≥ 0.27
  but the confident mass is 0.6–0.99).
- Confidence: mean `p` over the PL fixtures 0.80, over the EN fixtures
  0.61; min over all monolingual 0.265. Every one of the 3 misses has
  `p` ≤ 0.53; the correct-detection mass sits at 0.6–0.99.
- **A PL/EN-*constrained* detector** (argmax over `{p_pl, p_en}` only)
  would score **100 %** on these 30 — the correct language always
  out-scores the other target language; only exotic languages sneak
  ahead. This is the single biggest lever and it needs the library API.

## WER RESULTS

Best-of-reference WER (reference + accepted variants; punctuation/casing
normalised out for the WER number itself).

| strategy | PL (15) | EN (15) | short (10) | mixed (10) |
|---|---|---|---|---|
| `-l pl` (baseline PL) | **0.139** | 1.041 | 0.5 | 0.263 |
| `-l en` (baseline EN) | 1.234 | **0.041** | 0.4 | 0.675 |
| **`-l auto`** | 0.205 | 0.113 | 0.5 | **0.206** |
| `-dl` → explicit | 0.227 | 0.041¹ | 0.4 | 0.231 |

- On its own language, `-l auto` costs **+0.066 PL / +0.072 EN** WER vs
  the explicit baseline — and that entire delta is the single
  third-language misdetect per language (S3). No other regression.
- `-l auto` is the **best** strategy on mixed (0.206).
- Short WER is high for everything (one-word refs; a single wrong token =
  WER 1.0) — read the SHORT table, not this number.

## SEMANTIC ERROR RESULTS

Human-reviewed S-class (`docs/research/m2_4b_bilingual_stt/sclass_review.json`).
S2/S3 weighted over raw WER, per the task.

| set | S0 | S1 | S2 | S3 | S2+S3 rate |
|---|---|---|---|---|---|
| **PL baseline** (`-l pl`, 15) | 8 | 4 | 2 | 0 | **13 %** |
| **PL `-l auto`** (15) | 7 | 4 | 2 | **1** | **20 %** |
| **EN baseline** (`-l en`, 15) | 11 | 1 | 3 | 0 | **20 %** |
| **EN `-l auto`** (15) | 10 | 1 | 3 | **1** | **27 %** |
| **EN `-dl`→explicit** (15) | 11 | 1 | 3 | 0 | 20 % |

- **Pre-existing `base/q8_0` S2s (independent of bilingual work):** PL —
  `004` "sen" (sleep) → "sam" (alone); `014` "niebieskie" (blue) → "nie
  pieskie" (not doggy). EN — `021` "feathers" → "features"; `023` "again"
  → "better"; `024` "bread flour and rye flour" → "bread, flour and rye
  flour" (memory content wrong). These are ADR-0003 D4 / `R0016`
  territory, **not** something automatic language handling causes or fixes.
- **The only S-class damage `-l auto` itself adds:** exactly **one S3 per
  language** — the third-language misdetect turning into wrong-script
  output (`002` Cyrillic, `029` Hebrew). Both are gate-able by confidence
  and by a PL/EN constraint.

## LATENCY RESULTS

Wall time per utterance (whisper-cli subprocess, `-t 4`), n = 50 per
strategy.

| strategy | mean | median | p90 | min | max | RTF | Δ vs explicit |
|---|---|---|---|---|---|---|---|
| `-l pl` | 1.95 s | 1.77 s | 2.54 s | — | 3.19 s | 0.65 | — |
| `-l en` | 1.74 s | 1.70 s | 1.89 s | — | 2.67 s | 0.59 | — |
| **`-l auto`** | **2.89 s** | **2.86 s** | **3.07 s** | — | 3.58 s | 0.99 | **+1.0–1.2 s** |
| `-dl` → explicit | 3.08 s | 3.07 s | 3.30 s | — | 3.73 s | 1.05 | +1.3 s (`-dl` 1.37 s + decode 1.72 s) |

Per group, `-l auto` median: PL 2.88 s, EN 2.95 s, short 2.72 s, mixed
2.89 s (vs explicit ~1.5–1.8 s).

**Trade-off, stated plainly:** automatic language handling adds **~1.1 s**
to every voice turn's STT with `-l auto`, or **~1.3 s** with the two-pass
`-dl` route. On the current pipeline (STT ~1.7 s → END_OF_TURN→first-audio
~10–13 s, R0022/R0023) that pushes first-audio to ~11–14 s. `-dl`→explicit
is **strictly worse** — same detection, more latency, no accuracy gain —
so if automatic handling ships, it ships as single-pass `-l auto`, with
the confidence-gated re-decode paid **only on the ~10 % of turns** the
guard rejects, not on every turn.

## CPU / RAM / THERMAL RESULTS

Identical across all four strategies (same model, same threads):

| metric | value |
|---|---|
| peak RSS (child) | **221 MB** (matches `R0006`) |
| CPU during decode | ~85 % of 4 cores (~3.4 cores) |
| temperature (max over the whole 200-run, ~40-min session) | **70 °C** |
| `vcgencmd get_throttled` | **`0x0`** on all 200 runs |
| RTF | `-l auto` ~0.99, explicit ~0.6, `-dl`→explicit ~1.05 |

Measurement resolution is coarse (CPU is a `/proc/stat` window around the
subprocess; RSS is `VmHWM` polled at 10 Hz) — treat ±1 core / ±10 MB as
noise. No thermal or memory concern at `base/q8_0`; a *stronger* model
would change this picture (see below).

## STRONGER WHISPER CANDIDATE DECISION

**No download. Architecture chosen on `base/q8_0` evidence.** The
bilingual-input decision does not depend on transcript quality — it
depends on language-ID behaviour (PL↔EN never confused; errors gate-able),
which `base/q8_0` already shows clearly.

The PL/EN **transcript-quality** deficit (PL S2 13 %, EN S2 20 %,
pre-existing) is a **separate track** (ADR-0003 D4 / `R0016`), not opened
here. *If* the operator chooses to pursue it later, the one candidate
worth a controlled benchmark — **stop and get approval first** — is:

| field | value |
|---|---|
| model | `ggml-large-v3-turbo` (q5_0 quant) |
| source | `ggml-org/whisper.cpp` → `models/download-ggml-model.sh large-v3-turbo-q5_0` (weights originate from OpenAI `large-v3-turbo`) |
| license | MIT (model weights + whisper.cpp) |
| download size | ~547 MB (q5_0) / ~834 MB (q8_0) |
| expected RAM | ~1.0–1.5 GB peak RSS |
| estimated Pi 5 latency | ~4–8 s / utterance CPU-only (turbo has 4 decoder layers vs `large`'s 32 — far faster than `large-v3`, still ~3–5× `base`) |
| what it should address | the PL S2s (`004`, `014`) and EN S2s (`021`, `023`, `024`) — wrong content words / intent |
| why it's the right candidate | only multilingual `ggml` model that is meaningfully more accurate than `base` *and* plausibly real-time-ish on Pi 5 CPU; `small` is ~0 WER but ~7 s (`R0006`), `large-v3` is far too slow |
| risk | competes with `gemma4:e4b` for CPU during a turn (ADR-0003 D7); may still blow the latency budget — hence "benchmark, then decide", not "adopt" |

## BEST LANGUAGE-ID STRATEGY

**`-l auto`'s built-in detector, guarded.** Raw it is 90 % (0 PL↔EN
confusion; 3 low-confidence third-language slips). Add:

1. **PL/EN constraint** — if the detected language ∉ {pl, en}, discard it.
2. **Confidence gate** — if `p < ~0.6`, discard it.
3. **Fallback** — on discard, `InputSpeechLanguage` = the
   `ConversationSession`'s last input language (session default on turn 1).

On this corpus that guard converts 90 % → an effective **100 %** for the
monolingual set and recovers the 3 broken PL shorts, at the cost of one
extra explicit decode on the ~10 % of turns it fires. `-dl` adds nothing
(same detector). A dual-decode lexical arbiter is *worse* (63 %). The
theoretically clean fix — a true `{p_pl, p_en}` argmax — needs the
whisper.cpp **library** API and is the recommended implementation, not a
CLI option.

## BEST STT STRATEGY

**Strategy 1 — `-l auto`, single pass** — for the transcript, with the
language-ID guard above deciding `InputSpeechLanguage`. Rationale:

- transcript quality == explicit baseline whenever the label is right
  (14/15 PL, 14/15 EN verbatim-identical);
- one pass, ~+1.1 s — the cheapest way to get LID *and* transcript
  together;
- `-dl`→explicit is the same detector + a second full decode = +1.3 s for
  no gain;
- the guard's conditional re-decode only pays the second pass on the
  minority of low-confidence turns.

Keep `ggml-base-q8_0` and `-t 4` (production config) — unchanged.

## RECOMMENDED FUTURE ARCHITECTURE

**Outcome D (hybrid): automatic per-utterance PL↔EN switching now; true
within-utterance code-switch explicitly deferred.**

Pipeline (all five concepts kept distinct — none collapse into one
variable):

```
audio
  → whisper.cpp  -l auto   (one pass)                    ── STTDecodeLanguage = detected
  → detected language + p (from the decoder)
  → LanguageIdGuard:
        detected ∈ {pl,en} AND p ≥ ~0.6   →  InputSpeechLanguage = detected
        else                              →  InputSpeechLanguage = session.last_input_language
                                             (+ optional single explicit re-decode in that language)
  → CanonicalTranscript   = the auto transcript (or the re-decode when the guard fired)
  → ConversationSession   (one session, one history, gemma4:e4b — unchanged)
  → ResponseLanguageResolver:
        default          →  ResponseLanguage = InputSpeechLanguage      (spoken PL → answer PL, spoken EN → answer EN)
        explicit request →  ResponseLanguage = requested, and set a sticky session preference
                            ("Odpowiedz po angielsku." / "Answer in Polish." /
                             "Od teraz mów po angielsku." / "Wracamy do polskiego.")
  → TTSVoice = voice_for_language(ResponseLanguage)      (existing pl_PL-gosia / en_GB-jenny)
```

- **`InputSpeechLanguage` ≠ `STTDecodeLanguage` ≠ `CanonicalTranscript` ≠
  `ResponseLanguage` ≠ `TTSVoice`.** The resolver is a separate authority
  from STT; the LLM's response language is *derived from*
  `InputSpeechLanguage` by default but overridden by an explicit user
  request (which also sets a sticky per-session preference). **None of
  that response-language machinery is part of this STT benchmark** and
  none of it is built here.
- **Implementation note for the next stage:** replace the guard's
  CLI-level "detected ∉ {pl,en}" check with a real `{p_pl, p_en}` argmax
  via the whisper.cpp library (`whisper_lang_id` / candidate-probability
  API) — the benchmark shows that alone gets monolingual LID to ~100 %.
- **Deferred (harder track):** true within-utterance code-switch. `-l
  auto` already gives 6/10 USABLE, 0 BROKEN, so it is *tolerable* as a
  side-effect of shipping outcome D, but it is not a solved problem and
  gets its own stage if/when it matters.
- **Deferred (separate track):** PL transcript quality (`R0016`) — the
  stronger-model benchmark above.

## DO WE HAVE ENOUGH EVIDENCE TO SUPERSEDE ADR-0003 D5?

**PARTIALLY.**

**Safe to supersede now (evidence-backed):**
- Automatic per-utterance PL↔EN language selection for **normal-length
  monolingual turns**, via `-l auto` + a PL/EN + confidence guard +
  inherit-previous-input-language fallback. Evidence: 0/30 PL↔EN
  confusion; `-l auto` transcript == explicit baseline when the label is
  right (28/30 verbatim); the residual errors are low-confidence
  third-language slips the guard removes. D5's absolute "M2 must not call
  STT in `auto` as its normal path" can become "`auto` **guarded** is the
  normal path; the guard never lets a non-{pl,en} or low-confidence label
  through."

**NOT safe to supersede (stays deferred):**
- **Isolated short/ambiguous utterances.** No correct language exists in
  isolation; needs the inherit-previous-language product rule, which is
  not designed or built. Until it is, a short utterance must fall back to
  the session language, not to `auto`.
- **True within-utterance code-switch.** `-l auto` is *tolerable*
  (0 BROKEN) but embedded fragments are mangled; not "supported".
- **Full elimination of third-language slips.** Needs the library-level
  `{p_pl,p_en}` detector; the CLI guard is a mitigation, not a fix.

**Recommended ADR-0003 action (proposal, not applied here):** a D5
*amendment* — "explicit per-session language remains the safe default;
**guarded `-l auto`** (PL/EN-constrained + confidence-gated + session
fallback) is an accepted automatic path for normal turns, adopted in a
dedicated implementation stage; isolated-short and within-utterance
code-switch handling remain open." No ADR file is edited in this research
stage.

## RISKS

- **+~1.1 s per turn** from `-l auto`. On today's ~10–13 s
  END_OF_TURN→first-audio that is noticeable; must be weighed against the
  UX win of not needing `--language` per session. The guard's conditional
  re-decode adds a further ~1.8 s on the minority of low-confidence turns.
- **Third-language slips** (`ru`/`ko`/`he`) still occur (~10 % here) and
  produce wrong-script S3 output if unguarded. The CLI guard mitigates;
  only the library `{p_pl,p_en}` detector eliminates.
- **Short utterances** have no language alone — a wrong guess flips the
  whole turn's language. The fallback rule must exist *before* `-l auto`
  ships for shorts.
- **Code-switch** transcripts lose embedded fragments; an LLM usually
  recovers, but "Powiedz mi …"→"Hope it's me …" class errors can mislead.
- **Pre-existing PL/EN S2 rate** (13 % / 20 %) is unchanged by any of
  this — a separate quality debt (`R0016`).
- A **stronger model** to address that debt would raise RAM to ~1–1.5 GB
  and latency to ~4–8 s, competing with `gemma4:e4b` for CPU (ADR-0003
  D7).

## FILES CHANGED

- `docs/research/m2_4b_bilingual_stt/corpus.py`, `capture_corpus.py`,
  `README.md` (from the prep commit `d633f11`).
- `docs/research/m2_4b_bilingual_stt/fixtures/audio/*.wav` (**50**, the
  operator's real-voice corpus), `fixtures/corpus.jsonl`,
  `fixtures/corpus_manifest.json`.
- `docs/research/m2_4b_bilingual_stt/bench_stt.py` (benchmark runner),
  `score_stt.py` (scorer), `sclass_review.json` (manual S-class review),
  `bench_stt_raw_20260908_174742.jsonl`, `bench_stt_meta_20260908_174742.json`,
  `score_stt_20260908_174742.json` (raw + scored evidence).
- `tests/test_bilingual_stt_corpus.py` (14 tests, prep commit),
  `tests/test_bilingual_stt_bench.py` (**new**, 19 tests — scoring logic +
  no-production-impact guards).
- `docs/reports/R0024_…md` (this — all sections filled).
- **No `src/`, `apps/`, `configs/`, or ADR file changed.** Production
  `VoiceRuntime` explicit-language behaviour untouched.

## COMMIT HASH

`badab98` — `research: bilingual voice-input benchmark + architecture
decision (M2.4B.4 / R0024)` (tip of `main`). Preceded by `d633f11` /
`787b4b4` (corpus + recorder prep). Research + docs only; not pushed.
(This hash-record edit lands in the next commit.)

## GIT STATUS

Branch `main`, not pushed. `pytest` 513 passed / 7 skipped / 14 subtests;
`python -m unittest discover -s tests` 520 OK; `ruff check src tests apps
docs/research/m2_4b_bilingual_stt` clean; `git diff --check` clean. No
`src/`, `apps/`, `configs/`, or ADR file changed. Pre-existing unrelated
`ruff` E501s in `docs/research/m2_1_vad_calibration/vad_offline_calibration.py`
(commit `e2201ca`) left untouched.

## NEXT STEP

1. **Operator review** of this recommendation (outcome D + the guarded
   `-l auto` architecture + the proposed ADR-0003 D5 amendment).
2. If accepted: a dedicated **implementation stage** — add a
   library-level `{p_pl, p_en}` constrained detector to the
   `nexa.stt` boundary, the `LanguageIdGuard` + session
   `last_input_language`, and a `ResponseLanguageResolver` (default =
   input language; explicit override = sticky session preference), behind
   the ADR-0003 D5 amendment. Keep `ggml-base-q8_0` / `-t 4`.
3. Separately and optionally: the **PL transcript-quality** track —
   benchmark `large-v3-turbo-q5_0` (approval required before download).
4. `M2.5 — barge-in` remains the next *voice* milestone and is unaffected.
