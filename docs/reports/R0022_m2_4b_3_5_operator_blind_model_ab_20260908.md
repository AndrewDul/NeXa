# R0022 — M2.4B.3.5: Operator-blind local voice model A/B

- **Date:** 2026-09-08
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.3.5 — operator
  blind A/B decision (preparation)**
- **Related:** `docs/reports/R0021_…` (**the objective benchmark** —
  `gemma4:e4b` PL ~9.4 chars/s / best PL quality vs `gemma4:e2b` PL
  ~18.7 chars/s / repeatable factual regressions; `num_thread=2` = +12–14 %
  decode), `docs/reports/R0020_…` (B.3.3 `ResponseMode.VOICE` — the policy
  both candidates run), `docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md`
  (the M1.0B blind-test instrument this reuses), `docs/research/m2_4b_llm_bench/`
  (harness, sealed mapping, results dir).

**Preparation only. No production default changed: `gemma4:e4b` remains
the ADR-0002 frozen model, `num_thread` / `keep_alive` unchanged in
production config, bilingual auto-STT not implemented, M2.5 not started.
Not pushed. The A/B mapping is NOT in this report and NOT revealed.**

---

## TASK RESULT

**PREPARED — AWAITING OPERATOR BLIND TEST.**

A real, operator-blind A/B of `gemma4:e4b` vs `gemma4:e2b` on the full
NeXa realtime voice path is built, self-tested, and ready to run. The
operator has not yet done the listening test, so there is **no verdict,
no winner, and no model decision** in this report. Once the operator
returns Candidate A / Candidate B scorecards and a preference, this report
is updated with the reveal, the objective comparison, and the final call.

Also done this stage:

- **B.3.3 operator evidence recorded** — the operator has now run real
  Polish *and* English voice sessions on the post-B.3.3 build (see B.3.3
  OPERATOR EVIDENCE). No subjective verdict is invented on his behalf; the
  live A/B session is where he gives it.
- **`num_thread=2` validated under a concurrent Piper load** (see that
  section) so it can be used for BOTH candidates without giving either an
  unfair serving advantage. Still not shipped to production.

## BLIND A/B STATUS

| item | state |
|---|---|
| sealed random mapping | **generated** (`secrets.SystemRandom`), stored, not printed |
| blind launcher | **built + self-tested** (`operator_blind_ab.py --selftest` → 18 checks pass) |
| fairness controls | **in place** (see FAIRNESS CONFIGURATION) |
| `num_thread=2` Piper-active check | **PASS** — +68 % decode under Piper load, RTF unchanged, no throttle |
| harness safety tests | **25 pass** (`tests/test_operator_blind_ab.py`) |
| operator listening test | **NOT STARTED** — awaiting the operator |
| production default | **unchanged** (`gemma4:e4b`) |

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

**`gemma4:e4b` — unchanged.** `nexa.config.DEFAULT_LOCAL_MODEL` is
`gemma4:e4b`; `build_default_session()` builds it with the stock provider
(no `num_thread`, `keep_alive` `5m`). Nothing in this stage edits the
canonical model, `num_thread`, or `keep_alive` in production config. The
blind harness builds a throwaway session only.

## NEXT STEP

Operator runs the four commands above (Candidate A pl/en, Candidate B
pl/en), scores each candidate on the ten axes, states a preference. Then
this report is updated with: A scores, B scores, preference, the mapping
reveal, the objective per-turn comparison from `blind_results/`, and the
final model decision under R0021's decision rule (switch only on a
meaningful product improvement; otherwise keep `e4b`, optionally take the
`num_thread=2` + warm-keep serving wins). Only if the operator approves
does a production model / config change follow — as its own stage.

**Do not switch the production default. Do not ship `num_thread=2` /
`keep_alive`. Do not implement bilingual auto-STT. Do not start M2.5. Do
not push. Do not reveal the mapping.**

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

The decision is handed to the operator, not pre-empted: no winner is
declared, the `e2b` quality caveat from R0021 is carried in full, the
comparison is genuinely fair (one variable), the blind is real
(`secrets`-random, never printed, tested), and the production model is
untouched. No gap exposed.
