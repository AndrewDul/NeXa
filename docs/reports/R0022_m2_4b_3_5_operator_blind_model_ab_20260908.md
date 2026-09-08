# R0022 — M2.4B.3.5: Operator-blind local voice model A/B

- **Date:** 2026-09-08
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.3.5 — operator
  blind A/B model decision** · **CLOSED 2026-09-08** (prepared + run +
  revealed + decided; serving productionisation is R0023)
- **Related:** `docs/reports/R0021_…` (**the objective benchmark** —
  `gemma4:e4b` PL ~9.4 chars/s / best PL quality vs `gemma4:e2b` PL
  ~18.7 chars/s / repeatable factual regressions; `num_thread=2` = +12–14 %
  decode), `docs/reports/R0020_…` (B.3.3 `ResponseMode.VOICE` — the policy
  both candidates run), `docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md`
  (the M1.0B blind-test instrument this reuses), `docs/research/m2_4b_llm_bench/`
  (harness, sealed mapping, results dir).

**CLOSED 2026-09-08.** The operator ran the blind A/B (Candidate A PL+EN,
Candidate B PL+EN) on the full NeXa realtime voice path, gave his
observations, and stated a preference. The sealed mapping is now revealed
below and the model decision is final: **`gemma4:e4b` is the canonical
local production conversation model.** The serving improvements
(`num_thread=2`, longer `keep_alive`, startup warm-up) are productionised
in the follow-up stage **M2.4B.3.6 — see R0023**; this report is the A/B
record only. Not pushed.

---

## TASK RESULT

**COMPLETE — BLIND A/B RUN, MAPPING REVEALED, MODEL DECIDED.**

The operator completed the listening test. Mapping: **Candidate A =
`gemma4:e2b`, Candidate B = `gemma4:e4b`** (see BLIND A/B CLOSED below).
**Operator decision: `gemma4:e4b`** — reliability / quality over the
~1.6–2× raw-speed advantage of `gemma4:e2b`; English with the stronger
model is already good enough for this milestone; Polish is slower but
Polish latency + STT are explicitly separate later tracks. NeXa stays
bilingual (PL + EN) — this is a *model* decision, not a language-removal
decision. `gemma4:e2b` is rejected as the production conversation model
and stays documented only as a possible future speed/fallback candidate;
normal NeXa conversations are not routed to it.

No 1–5 subjective scorecard numbers are recorded — the operator gave
qualitative observations, not axis scores, and none are invented on his
behalf.

Also done this stage:

- **B.3.3 operator evidence recorded** — the operator has now run real
  Polish *and* English voice sessions on the post-B.3.3 build (see B.3.3
  OPERATOR EVIDENCE). No subjective verdict is invented on his behalf; the
  live A/B session is where he gave it.
- **`num_thread=2` validated under a concurrent Piper load** (see that
  section) so it could be used for BOTH candidates without giving either
  an unfair serving advantage. Shipped to production in B.3.6 (R0023).

---

## BLIND A/B CLOSED — MAPPING REVEAL

The operator finished the four blind runs and stated a preference on
2026-09-08. Only then was `operator_blind_ab_mapping_20260908.txt`
opened. The sealed payload (`salt dc48ecc7…`, created `2026-09-08T16:29`):

| blind label | actual model |
|---|---|
| **Candidate A** | **`gemma4:e2b`** (the ~1.6–2× faster MatFormer sibling) |
| **Candidate B** | **`gemma4:e4b`** (the ADR-0002 frozen baseline) |

The operator **chose Candidate B → `gemma4:e4b`**.

### Why `gemma4:e4b` won

- **Reliability / quality over speed.** The operator judged the stronger
  model's answers good enough to keep, and did not consider `e2b`'s raw
  responsiveness advantage worth its known factual-reliability regression
  (R0021: relativity / iron-vs-feathers self-contradictions, a "pink Sun"
  hallucination, one EN→PL mirroring break, thinner reasoning).
- **English is already sufficient** with `e4b` for the current milestone —
  the operator's word for the `e4b` English run was *"idealny"* / ideal.
- **Polish is accepted as slower for now.** `e4b` Polish text throughput
  (~11 chars/s here) and local Polish STT accuracy are known deficits;
  both become dedicated later stages, not reasons to switch the model or
  drop the language.
- NeXa stays **bilingual PL + EN**. This is a model decision only.

`gemma4:e2b` is **rejected** as the production conversation model. It may
stay documented as a future speed / fallback candidate, but normal NeXa
conversations are not routed to it, and no automatic `e2b` routing and no
separate per-language model authority are introduced.

## OPERATOR OBSERVATIONS (recorded verbatim, not scored)

The operator did not fill in the 1–5 ten-axis scorecard; he gave
free-form observations. Recorded as given, no numbers invented:

**Candidate A — `gemma4:e2b` — Polish**
- Generally good and conversational.
- One long apparent stall near the end.
- The later part of the session had queued / overlapping utterances, so it
  **must not** be treated as a clean model-latency measurement.
- The conversation felt good overall.

**Candidate A — `gemma4:e2b` — English**
- After an initial language anomaly at the start, the subsequent English
  conversation felt much smoother.
- The operator described the improvement as *very large* and said it was
  good to talk with.

**Candidate B — `gemma4:e4b` — Polish**
- The operator initially felt it understood him better.
- Still some STT misses; needed occasional slower / repeated speech.
- Overall quality good.
- The distinction between **STT accuracy** (whisper.cpp mishearing) and
  **model interpretation of an already-damaged transcript** must be kept
  separate — a Polish miss here is first an STT-track issue.

**Candidate B — `gemma4:e4b` — English**
- The operator described it as *"idealny"* / ideal.

**Final operator decision (verbatim intent):** choose `gemma4:e4b` as the
production model; accept current Polish latency for now; improve Polish
separately later.

## OBJECTIVE METRICS FROM `blind_results/`

From the completed per-turn files (`candidate_*_{pl,en}_2026*.jsonl`).
"Warm" = excluding each run's cold turn 1 (fresh-process prefix reprocess)
and, for A-PL, the operator-flagged queued/overlapping turns 11–14 (not a
clean latency measurement). `num_thread=2` + `keep_alive=30m` for both.

| metric (warm turns) | A = `e2b` PL | A = `e2b` EN | B = `e4b` PL | B = `e4b` EN |
|---|---|---|---|---|
| turns scored | 9 (t2–10) | 8 (t2–9) | 6 (t2–7) | 2 (t2–3)¹ |
| generated **chars/s** mean | **18.4** | **~29** | **11.2** | **16.5** |
| warm **TTFT** mean (STT-result → 1st token) | 1.71 s | 1.48 s | 3.50 s | 3.04 s |
| **END_OF_TURN → first audio** mean | 8.2 s | 7.3 s | 13.2 s | 10.6 s |
| generation duration mean | 5.6 s | 6.6 s | 12.9 s | 15.1 s |
| cold turn-1 TTFT (excluded above) | 21.6 s | 15.3 s | 43.2 s | 43.7 s |
| CPU total mean / peak | 49 % / 100 % | 43 % / 100 % | 50 % / 100 % | 51 % / 100 % |
| `llama-server` CPU mean | 137 % | 106 % | 152 % | 151 % |
| temp max | 67.2 °C | 63.4 °C | 65.0 °C | 64.5 °C |
| `throttled` | `0x0` | `0x0` | `0x0` | `0x0` |
| MemAvailable min | ~6.2 GB | ~6.2 GB | ~3.8 GB | ~3.9 GB |
| swap used max | 86 MB | 85 MB | 0 MB | 0 MB |

¹ The operator ended the Candidate B English block after two warm turns,
having already judged it *"idealny"*.

**Reading of the numbers.** `e2b` generated spoken text ~1.6× faster in
Polish (18.4 vs 11.2 chars/s) and ~1.7× faster in English (~29 vs 16.5),
with roughly half the warm TTFT (~1.7 s vs ~3.5 s PL). `e4b` holds ~2.4 GB
more RAM resident (≈3.8 GB free vs ≈6.2 GB during `e2b`). Neither model
throttled; both stayed thermally comfortable (≤ 67 °C). This is
consistent with R0021's directional finding; the operator weighed the
`e2b` speed against its reliability cost and chose `e4b`.

**The Candidate B Polish Pi reset.** The first Candidate B Polish attempt
(`candidate_B_pl_20260908_170608.txt`, 0 bytes, no `.jsonl`) produced no
completed turns and the Pi was reset before the successful run
(`…_171115`, used above). Nothing in the captured data isolates a cause.
The only recorded environmental difference from the `e2b` runs is the
~2.4 GB smaller free-RAM headroom while `e4b` (~10.2 GB) is resident.
**Not attributed to the model** — flagged as an observation only; the
successful B-PL run that followed showed no instability, no throttling and
`0` swap.

## BLIND A/B STATUS

| item | state |
|---|---|
| sealed random mapping | **generated** (`secrets.SystemRandom`), stored, not printed |
| blind launcher | **built + self-tested** (`operator_blind_ab.py --selftest` → 18 checks pass) |
| fairness controls | **in place** (see FAIRNESS CONFIGURATION) |
| `num_thread=2` Piper-active check | **PASS** — +68 % decode under Piper load, RTF unchanged, no throttle |
| harness safety tests | **25 pass** (`tests/test_operator_blind_ab.py`) |
| operator listening test | **DONE 2026-09-08** — A pl+en, B pl+en |
| mapping | **REVEALED** — A = `gemma4:e2b`, B = `gemma4:e4b` |
| operator decision | **`gemma4:e4b`** (Candidate B) — quality over speed |
| production default | **`gemma4:e4b`** — confirmed; serving policy shipped in R0023 |

## CANDIDATES

Two models, one hidden random assignment to the labels **Candidate A** and
**Candidate B**:

- `gemma4:e4b` — the current frozen baseline. R0021: best Polish quality
  in the field, PL ~9.4 / EN ~14.7 generated chars/s, warm TTFT ~2–3 s,
  ~10.2 GB resident.
- `gemma4:e2b` — same MatFormer family. R0021: PL ~18.7 / EN ~27.4
  chars/s (~2×, clears the 15.9 chars/s target), warm TTFT ~1.0–1.4 s,
  ~7.5 GB resident — **but** repeatable factual regressions (relativity
  and iron/feathers self-contradictions, a "pink Sun" hallucination, one
  EN→PL language break, thinner reasoning working).

**The question the operator answers:** is `e2b`'s ~2× responsiveness worth
its measured drop in factual reliability / reasoning depth, *as
experienced live over voice*?

## FAIRNESS CONFIGURATION

The blind launcher (`docs/research/m2_4b_llm_bench/operator_blind_ab.py`)
runs the identical NeXa voice stack for both candidates. The **only**
intended independent variable is the model identity.

| held identical for A and B | how |
|---|---|
| `ConversationSession` implementation | `nexa.conversation.session.ConversationSession` — the production class, built directly |
| persona / system prompt | `configs/personas/nexa_persona_v1.json` via `nexa.config.load_persona()` |
| `GenerationOptions` | the persona's — `num_ctx 8192`, `temperature 0.7`, `top_p 0.8`, `top_k 20`, `repeat_penalty 1.05`, `num_predict 200`, `think false` |
| response policy | `ResponseMode.VOICE` (B.3.3), same directive |
| response-language mechanism | the canonical per-turn `language_directive` (R0009) — untouched |
| SpeechPlanner | `NexaSpeechPlanner`, `default_language` = the block's language |
| continuity controller | `NexaSpeechContinuityController`, `target_reserve_s` default (2.0) |
| Piper voices | `pl_PL-gosia-medium` / `en_GB-jenny_dioco-medium` |
| Piper priority | `nice +10` (B.3.1) |
| TTS context timeout | `DEFAULT_TTS_CONTEXT_TIMEOUT_S` (8 s, B.3.1) |
| STT / VAD / mic / speaker | real `WhisperCppTranscriber` + `VoiceRuntime` + `LocalAudioConfig` — the same devices |
| `num_thread` | **2** for BOTH (R0021 serving finding, applied equally) |
| `keep_alive` | **30m** for BOTH (so neither evicts mid-session) |
| warm-up | model load + persona/VOICE prefix prime + one discarded generation, per candidate, before scoring |

**Only difference:** the Ollama model tag the session's provider points
at, resolved from the sealed mapping and never printed.

Each candidate run starts a **fresh `ConversationSession`** (empty
history) so A cannot inherit B's context; within a run, one continuous
session accumulates history across all turns (so context / memory turns
are real).

## NUM_THREAD=2 PIPER-ACTIVE VALIDATION

R0021 measured `num_thread=2` = +12–14 % `gemma4:e4b` decode tok/s
*uncontended*. Before using it for the blind A/B, one focused check that
it is still safe under a **real concurrent Piper (`nice +10`) load** — the
realtime-voice condition — via
`docs/research/m2_4b_llm_bench/nt2_piper_validation.py`: a background loop
keeps Piper synthesising `pl_PL-gosia-medium` at a realistic sentence
cadence while 3 warm voice-policy `gemma4:e4b` turns run, default threads
then `num_thread=2`; records LLM tok/s, Piper true RTF, per-core CPU,
temperature, throttled.

**Result — clean PASS** (`nt2_piper_validation_20260908.json`, 3 warm
turns per variant, Piper synthesising `pl_PL-gosia-medium` at a realistic
cadence throughout):

| | default threads | `num_thread=2` |
|---|---|---|
| `gemma4:e4b` decode, under Piper load | **1.69 tok/s** | **2.85 tok/s** (**+68 %**) |
| Piper true HTTP RTF (mean / max) | 0.342 / 0.462 | 0.329 / 0.452 |
| Piper synthesis errors | 0 | 0 |
| CPU temperature | ≤ 63 °C | ≤ 62 °C |
| `throttled` | `0x0` | `0x0` |

`num_thread=2` **keeps and in fact widens** the decode gain under
concurrent Piper (uncontended it was +12–14 %; here +68 %, because fewer
LLM threads leave more room for Piper's `nice +10` threads and the
scheduler arbitrates better). **Piper RTF is unchanged and ~2.2× faster
than real time** — no TTS regression. No throttling, no synthesis errors,
no instability. → `num_thread=2` is used for **both** candidates in the
blind A/B. It is still **not** shipped to production config (that is a
separate operator-approved follow-up).

## BLINDING METHOD

- **Assignment.** `make_mapping.py` uses `secrets.SystemRandom().shuffle`
  to map the two allowed models onto `("A", "B")`, writes a sealed JSON
  payload (`purpose`, `created`, a 16-byte random `salt`, `mapping`) to
  `docs/research/m2_4b_llm_bench/operator_blind_ab_mapping_20260908.txt`,
  and prints **only** a neutral confirmation plus the file's SHA-256 (for
  later audit) — never the assignment. It refuses to overwrite an
  existing mapping without `--force`, so a stray re-run cannot reshuffle
  the test.
- **Resolution.** The launcher reads the mapping, validates it (exactly
  `{"A","B"}` → exactly `{gemma4:e4b, gemma4:e2b}`, A ≠ B), and binds the
  session's provider to the resolved tag. `resolve_model()`'s result in
  the live path is assigned to `_` and never printed or logged to an
  operator-visible stream.
- **Operator-facing output.** The terminal shows only `Candidate A` /
  `Candidate B`, the language, the voice-state / transcript / TTS lines of
  the actual conversation, and a neutral `· turn N recorded` marker. No
  model tag, no tok/s, no `model:` line. Every per-turn metric and
  resource sample is written to `blind_results/candidate_<X>_<lang>_<ts>.txt`
  / `.jsonl` — **not** the terminal.
- **The mapping is not in this report.** It stays only in the sealed local
  file until the operator has given both scorecards and a preference.

## OPERATOR COMMANDS

Run from the repo root. Each command runs one candidate, one language, as
a fresh session; speak the warm-up first (excluded), then the turns
naturally, `Ctrl+C` when done. Do the two languages for one candidate,
score it, then the other candidate.

```
# ---- Candidate A ----
./.venv/bin/python docs/research/m2_4b_llm_bench/operator_blind_ab.py --candidate A --language pl
./.venv/bin/python docs/research/m2_4b_llm_bench/operator_blind_ab.py --candidate A --language en

# ---- Candidate B ----
./.venv/bin/python docs/research/m2_4b_llm_bench/operator_blind_ab.py --candidate B --language pl
./.venv/bin/python docs/research/m2_4b_llm_bench/operator_blind_ab.py --candidate B --language en
```

Order is the operator's choice; A-before-B is fine. The commands carry no
model identity.

After **Candidate A** (PL + EN) — score A 1–5 on the ten axes below, note
what was liked / what felt worse. Then the same for **Candidate B**.
Finally: *"Which do you choose for NeXa: A or B?"* — and only then is the
mapping revealed.

**Subjective scorecard (per candidate):** 1 speed/responsiveness ·
2 natural Polish · 3 natural English · 4 intelligence · 5 factual
trustworthiness · 6 context/memory · 7 conversational naturalness ·
8 voice continuity / lack of awkward gaps · 9 detail when requested ·
10 overall "this feels like NeXa". Plus: *what specifically did you like?*
/ *what specifically felt worse or wrong?*

## POLISH TEST PROTOCOL

STT language `pl`. Warm-up (excluded): **"Ile jest osiem razy siedem?"**
Then, naturally (short follow-ups welcome):

1. "Co to jest czarna dziura?"
2. "Jak ona powstaje?"
3. "Z czego składa się gwiazda?"
4. "Dlaczego nic mającego masę nie może przekroczyć prędkości światła?"
5. "Wyjaśnij mi to trochę dokładniej." *(detail-override check)*
6. "Jeżeli jest godzina czternasta pięćdziesiąt i dodamy dziewięćdziesiąt
   pięć minut, która będzie godzina?" *(multi-step reasoning; answer 16:25)*
7. "Zapamiętaj: kupiłem mąkę pszenną chlebową i żytnią." *(memory set-up)*
8. "Po co człowiekowi sen?" *(unrelated turn)*
9. "Co powoduje, że niebo jest niebieskie?" *(unrelated turn + science)*
10. "Jakie dwie mąki powiedziałem wcześniej, że kupiłem?" *(memory recall)*

**Science spot-checks** to weave in naturally (do not tell the operator
which model previously failed which): the speed-of-light / rest-mass
question (already turn 4–5), **"Jaki jest rzeczywisty kolor Słońca?"**,
and the trick **"Co jest cięższe: kilogram żelaza czy kilogram piór?"**

## ENGLISH TEST PROTOCOL

Fresh session, STT language `en`. Warm-up (excluded): **"What is eight
times seven?"** Then:

1. "What is a black hole?"
2. "How does it form?"
3. "What is a star mainly made of?"
4. "Why can't an object with mass exceed the speed of light?"
5. "Tell me more about that." *(detail-override check)*
6. "If it is 2:50 PM and we add 95 minutes, what time will it be?"
   *(answer 4:25 PM)*
7. "Remember this: I bought bread flour and rye flour."
8. "Why do humans need sleep?"
9. "Why is the sky blue?"
10. "Which two types of flour did I tell you I bought earlier?"

**Science spot-check:** "What is the actual colour of the Sun?" and the
iron/feathers trick in English.

## METRICS CAPTURED

Per turn, to `blind_results/…` files only (never the operator terminal):
STT text + STT wall latency; TTFT (STT-result → first token); generation
duration; generated chars + chars/s; tok/s where the stream exposes it;
first TTS phrase ready; END_OF_TURN → first audio; total spoken audio;
max / mean intra-response playback gap; `BotStarted` / `BotStopped`
counts; buffer-underrun estimate; true Piper HTTP RTF
(`TimedPiperHttpTTSService`); per-sample CPU total + `llama-server` CPU;
temperature; throttled flag; MemAvailable. These are the
`render_turn_report` / JSONL outputs plus the `ResourceSampler` window —
the same instrumentation R0013/R0014 defined.

## B.3.3 OPERATOR EVIDENCE

M2.4B.3.3 (`ResponseMode.VOICE`, R0020) was PASS on automated + scripted
hardware but not yet `OPERATOR-CONFIRMED`. The operator has now run
**real Polish and English voice sessions** on the post-B.3.3 build. This
report records that that live PL + EN operator evidence exists; the
operator's subjective verdict on B.3.3 (and on the model trade-off) is
what the blind A/B session below collects — it is **not** pre-supplied
here.

## TEST RESULTS

`tests/test_operator_blind_ab.py` — **25 deterministic offline tests**
(no Ollama, no audio), all green, `ruff` clean:

- sealed mapping: file exists; labels exactly `{A,B}`; values exactly
  `{gemma4:e4b, gemma4:e2b}`; A ≠ B; `resolve_model` round-trips and
  rejects a bad label; payload carries a ≥16-byte random salt.
- blinding: `--selftest` stdout contains no model tag; no `print(...)`
  literal in the harness contains a model tag; `resolve_model()`'s live
  result is discarded to `_`; the usage/commands use `--candidate {A,B}`
  only, never `--model`.
- fairness: both are real `ConversationSession`s; fresh + separate
  (distinct `session_id`, empty history); identical persona system prompt
  and identical `GenerationOptions` (asserted against `load_persona()`);
  `num_thread=2` and `keep_alive=30m` identical for both; only the model
  tag differs (∈ the allowed pair).
- provider wire: `_NumThreadProvider.generate` puts `num_thread` in
  `options` and otherwise sends exactly the production option set +
  `keep_alive` + `stream`.
- policy / language: `ResponseMode.VOICE` still injects the voice
  directive; the per-turn `language_directive` still present (PL);
  history persists within a session and holds only real user/assistant
  turns; no mapping salt / model tag / filename leaks into the wire
  messages, history, or system prompt.
- production untouched: `nexa.config.DEFAULT_LOCAL_MODEL == "gemma4:e4b"`;
  `build_default_session()` still yields a `gemma4:e4b` provider with **no**
  `num_thread` override; the harness adds no file under `src/` or `apps/`
  and importing it does not monkeypatch the production provider;
  `Language` choices are exactly `{pl, en}` and the launcher routes the
  `--language` arg into both the STT (`VoiceRuntime(language=…)`) and the
  planner (`default_language=…`).

Full suite: **`pytest` — 460 passed / 7 skipped / 14 subtests** (was 435,
`+25` new); **`unittest discover` — 467 OK / 7 skipped**; `ruff check
src tests apps docs/research/m2_4b_llm_bench scripts/setup_piper_http.py`
clean; `git diff --check` clean. No `src/` file touched.

## FILES CHANGED

| file | change |
|---|---|
| `docs/research/m2_4b_llm_bench/blind_ab_common.py` | **new** — sealed-mapping loader + `_NumThreadProvider` (`LocalModelProvider` + `num_thread`) + `build_blind_session()` (fair, fresh session) |
| `docs/research/m2_4b_llm_bench/make_mapping.py` | **new** — `secrets.SystemRandom` sealed A/B mapping generator; prints only a neutral line + SHA-256 |
| `docs/research/m2_4b_llm_bench/operator_blind_ab.py` | **new** — the blind launcher: real voice path, model tag never printed, metrics → files, `--selftest` |
| `docs/research/m2_4b_llm_bench/nt2_piper_validation.py` | **new** — `num_thread=2` under a concurrent Piper load |
| `docs/research/m2_4b_llm_bench/operator_blind_ab_mapping_20260908.txt` | **new** — the sealed mapping (auditable; **do not open before the verdict**) |
| `docs/research/m2_4b_llm_bench/blind_results/` | **new (empty)** — per-candidate metrics land here |
| `docs/research/m2_4b_llm_bench/README.md` | B.3.5 section |
| `tests/test_operator_blind_ab.py` | **new** — 25 harness-safety tests |
| `docs/reports/R0022_…md` | **new** — this report |
| `docs/CURRENT_STATE.md` | B.3.5 prepared; B.3.3 operator PL+EN evidence recorded |

No `src/` change. No production default touched.

## COMMIT HASH

`test: prepare blind voice model comparison` — tip of `main` (see
`git log -1`).

## GIT STATUS

Branch `main`, not pushed. `git diff --check` clean. No model weights /
`~/.ollama` blobs staged. The sealed mapping file **is** committed (small,
auditable) — its contents are not reproduced anywhere in the tracked
reports.

## PRODUCTION MODEL

**`gemma4:e4b` — confirmed by operator blind A/B, remains
`nexa.config.DEFAULT_LOCAL_MODEL`.** As of this report the production
config was still the stock provider (no `num_thread`, `keep_alive` `5m`).
The serving changes the operator approved alongside the model decision —
`num_thread=2`, `keep_alive` 30m, a startup persona-prefix warm-up — are
implemented in **M2.4B.3.6 (R0023)**, still on this one canonical model
and one provider. `gemma4:e2b` is **not** wired anywhere: no automatic
routing, no per-language model authority, no fallback model.

## NEXT STEP

**Done in R0023 (M2.4B.3.6 — Production Local Model Serving Freeze):**
`num_thread=2` + `keep_alive` 30m productionised in the canonical
`LocalModelProvider` via `nexa.config`; an infrastructure-only startup
warm-up that primes the persona / `ResponseMode.VOICE` KV prefix without
writing `ConversationSession` history; real-Pi + Piper acceptance
measurement. Explicitly deferred: Polish latency, Polish STT quality
(R0016), bilingual auto-STT / code-switch. `gemma4:e4b` frozen; no `e2b`
routing.

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

The decision was handed to the operator, not pre-empted: no winner was
declared before the run, the `e2b` quality caveat from R0021 was carried
in full, the comparison was genuinely fair (one variable — the model tag),
the blind was real (`secrets`-random, never printed, tested), and the
mapping stayed sealed until the operator stated a preference. The reveal,
the verbatim operator observations (no invented scores), the objective
`blind_results/` metrics, and the Candidate B Polish Pi-reset (recorded,
not attributed to the model) are all above. No gap exposed.
