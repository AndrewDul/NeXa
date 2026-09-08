# R0023 — M2.4B.3.6: Production Local Model Serving Freeze

- **Date:** 2026-09-08
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.3.6 — production
  local model serving freeze**
- **Related:** `R0022` (M2.4B.3.5 — operator blind A/B, `gemma4:e4b`
  chosen), `R0021` (M2.4B.3.4 — the serving benchmark: `num_thread=2`,
  cold-prefix cost, `keep_alive`), `R0020` (B.3.3 `ResponseMode.VOICE`),
  `R0009` (KV-cache prefix discipline), `ADR-0002` Amendment 2
  (`gemma4:e4b` frozen). Evidence probe:
  `docs/research/m2_4b_llm_bench/b36_serving_freeze_probe.py`.

---

## TASK RESULT

**PASS.** The R0021/R0022 serving findings are productionised on the one
canonical NeXa conversation path, with `gemma4:e4b` kept as
`DEFAULT_LOCAL_MODEL`:

1. **`num_thread=2`** — canonical provider policy, resolved once in
   `nexa.config` (`DEFAULT_LOCAL_NUM_THREAD`), sent as an Ollama request
   option by `LocalModelProvider`. No `taskset`, no `renice` on the LLM,
   no realtime scheduler. Piper stays `nice +10`.
2. **`keep_alive = 30m`** — canonical provider policy
   (`DEFAULT_LOCAL_KEEP_ALIVE`), replacing the stock `5m`. Not infinite
   (a ~10 GB resident model on a 16 GB Pi is a separate resource-policy
   decision). Env-overridable.
3. **Startup warm-up** — `nexa.bootstrap.warm_up_session()`:
   infrastructure-only, sends the canonical persona + `ResponseMode.VOICE`
   prefix with **empty history** (no user text, `num_predict=1`) once
   through the session's own provider and discards the output. It never
   writes `ConversationSession` history, never creates a turn, never
   builds a second session/provider/persona, and raises
   `ModelUnavailableError` visibly if Ollama is down.

**Measured on the real Pi + real Ollama (`b36_serving_freeze_probe.py`):**
the warm-up converts a cold first real voice turn from **TTFT ~76.7 s**
(model load 33.4 s + ~507-token persona/VOICE prefix reprocess 43.3 s) to
**TTFT ~3.2 s** (`prompt_eval` 43.3 s → 3.2 s for the same 535 prompt
tokens — 507 served from the llama.cpp KV cache, only the ~28 new
user + language-directive tokens re-evaluated). **The warm-up genuinely
leaves a reusable prefix; it is not a no-op.**

Scope held: `ResponseMode.TEXT` byte-for-byte unchanged; `VOICE` policy
unchanged; PL/EN response-language mechanism unchanged; no `e2b` routing;
no per-language model authority; no second conversation authority; not
pushed.

## WHAT I DID

### R0022 closed first

- Revealed `operator_blind_ab_mapping_20260908.txt`:
  **Candidate A = `gemma4:e2b`, Candidate B = `gemma4:e4b`**.
- Updated `R0022` with the reveal, the operator's verbatim observations
  (no invented 1–5 scores), the objective per-turn metrics from
  `blind_results/*.jsonl`, the Candidate-B Polish Pi-reset (recorded, not
  attributed to the model), and the final decision **`gemma4:e4b`**.

### R0022 final A/B mapping + operator decision

| blind label | model | operator's words |
|---|---|---|
| Candidate A | `gemma4:e2b` | PL "good and conversational", one long stall, later queued/overlapping utterances (not a clean latency read); EN "very large" improvement after an initial language anomaly, "good to talk with" |
| Candidate B | `gemma4:e4b` | PL understood him better, some STT misses needing slower/repeated speech, overall good; EN *"idealny"* / ideal |

**Decision: `gemma4:e4b`** — reliability/quality over `e2b`'s ~1.6–2×
speed. English already sufficient. Polish slower but accepted; Polish
latency + Polish STT are separate later tracks. NeXa stays bilingual.
`gemma4:e2b` rejected as production model; not routed to.

### Code — one canonical path, `gemma4:e4b` kept

| file | change |
|---|---|
| `src/nexa/config.py` | `DEFAULT_LOCAL_NUM_THREAD = 2`, `DEFAULT_LOCAL_KEEP_ALIVE = "30m"` (documented, R0021/R0022-cited). `LocalProviderSettings` gains `num_thread` / `keep_alive`. `local_provider_settings_from_env()` reads `NEXA_LOCAL_NUM_THREAD` (int; `0`/invalid ⇒ "let Ollama pick", provider sends no `num_thread`) and `NEXA_LOCAL_KEEP_ALIVE`. `DEFAULT_LOCAL_MODEL` still `gemma4:e4b`. |
| `src/nexa/providers/ollama.py` | `LocalModelProvider.__init__` gains `num_thread: int \| None = None`; `generate()` adds `options["num_thread"]` **only when set**. Every other wire field byte-identical. Still a generic Ollama driver — the number's provenance lives in `nexa.config`. |
| `src/nexa/bootstrap.py` | `build_default_session()` passes `keep_alive` + `num_thread` from settings to the one `LocalModelProvider`. New `warm_up_session(session, *, response_mode=VOICE) -> WarmUpResult` (frozen telemetry dataclass) — reuses `session.build_context()` (empty history) + `to_provider_messages(VOICE)` + `session.provider` + `session.options` (with `num_predict=1`); discards output; never touches `_history`; `ModelUnavailableError` propagates. |
| `apps/nexa_voice_tts_probe.py`, `apps/nexa_voice_chat_probe.py` | after `build_default_session()`, `await warm_up_session(session)`; on `ModelUnavailableError` print to stderr and `sys.exit(1)` (fail visible). `apps/nexa_chat.py` (TEXT) unchanged. |
| `tests/test_production_serving_freeze.py` | **new**, 20 deterministic offline tests (fake recording Ollama + `FakeModelProvider`). |
| `tests/test_operator_blind_ab.py` | `TestProductionUntouched` updated: `build_default_session()`'s provider now legitimately carries `num_thread=2` / `keep_alive=30m` (the approved B.3.6 follow-up); everything else in that suite unchanged. |
| `docs/research/m2_4b_llm_bench/b36_serving_freeze_probe.py` | **new** research probe (deletable with `docs/research/`). |

## WHAT I VERIFIED

### num_thread=2 production result

`build_default_session()` → provider `_num_thread == 2`, `_keep_alive ==
"30m"`; the wire body's `options` is exactly the persona option set **plus
one key** `num_thread=2`, `keep_alive="30m"` alongside; `num_ctx /
temperature / top_p / top_k / repeat_penalty / num_predict / think`
unchanged (asserted against `load_persona().options`).

Piper-active re-confirm (`b36` probe §2, `nice +10` Piper synthesising
`pl_PL-gosia-medium` continuously throughout, 2 warm PL turns per
variant) — consistent with R0021 (+12–14 % uncontended) and R0022's
`nt2_piper_validation` (+68 %):

| `gemma4:e4b`, Piper active | default threads | `num_thread=2` |
|---|---|---|
| decode tok/s (mean) | 1.74 | **2.85** (**+64 %**) |
| generated chars/s (mean) | 5.73 | **9.67** |
| warm TTFT | ~4.4 s | ~3.5 s |

(The absolute chars/s here is depressed by the probe's deliberately
harsh *continuous* Piper synth loop — every 3 s regardless of playback;
a real voice turn only synthesises during the reply. R0022's live
full-pipeline blind numbers — PL ~11.2 / EN ~16.5 chars/s at
`num_thread=2` — are the representative figures.)

### keep_alive policy

`keep_alive` is one-time provider policy from `nexa.config`, not a
per-session knob; `build_default_session()` sends `30m`;
`NEXA_LOCAL_KEEP_ALIVE` overrides. Chosen value 30m (not infinite):
`gemma4:e4b` is ~10 GB resident on the 16 GB Pi — indefinite residency is
deferred to a resource-policy decision once NeXa runs more concurrent
capabilities (recorded, not done here).

### Model / prefix warm-up result — DOES WARM-UP REALLY REDUCE FIRST-TURN COST?

**Yes — measured, eviction-controlled, `b36` probe §1 (Piper idle):**

| first real voice turn (PL "Co to jest czarna dziura?") | TTFT | `load_duration` | `prompt_eval` (count) | decode tok/s |
|---|---|---|---|---|
| **COLD** — evicted model, no warm-up | **76.7 s** | 33.4 s | 43.3 s (535 tok) | 3.65 |
| production warm-up call (system-only prefix, `num_predict=1`) | 72.6 s | 31.7 s | 41.0 s (507 tok) | — (1 tok discarded) |
| **WARM** — evicted model, warm-up first, then the real turn | **3.2 s** | 0.002 s | **3.2 s** (535 tok) | 3.45 |

The real turn after warm-up still reports `prompt_eval_count = 535`, but
`prompt_eval_duration` is **3.2 s not 43.3 s** — the 507-token persona +
VOICE prefix is served from the llama.cpp KV cache (the warm-up call put
it there) and only the ~28 new user + language-directive tokens are
actually evaluated. **First-turn cost saved: ~73 s.** This is the same
mechanism R0009 relies on for warm turns; the warm-up just pays turn 0's
prefix cost during startup instead of on the user's first question.

The **production function itself** was also exercised end-to-end against
real Ollama: `build_default_session()` → `await warm_up_session(session)`
(`WarmUpResult(model='gemma4:e4b', prefix_message_count=2,
discarded_chars=1)`, `session.history == ()`) → first real VOICE turn
**TTFT 3.10 s**, then `history` correctly holds exactly the 2 real turns
(user + assistant). Same result as the probe's reconstruction.

Warm-up safety (offline tests):
- after `warm_up_session()`, `session.history == ()` — no turn created;
- wire messages are exactly `[system=persona, system=voice_directive]`,
  no `user` role, `num_predict` clamped to 1, all other options unchanged;
- the warm-up message list is a strict prefix of the first real VOICE
  turn's message list;
- same `session.provider` object reused — no second provider/persona;
- `ModelUnavailableError` propagates (no silent alternate model);
- `bootstrap.py` contains no `_history` / `.send(` reference; AST check:
  exactly one `LocalModelProvider(...)` and one `ConversationSession(...)`
  construction.

### Real Pi acceptance (headless LLM + serving + Piper; Piper `nice +10` active)

`num_thread=2`, `keep_alive=30m`, warm prefix, `pl_PL-gosia-medium`
synthesising continuously. Per-turn through the real B.3.3 wire path
(persona + `voice_response_directive()` + per-turn `language_directive`),
accumulating context.

**REAL PI PL RESULTS** — "Co to jest czarna dziura?" → "Jak ona
powstaje?" → "Po co człowiekowi sen?" (accumulating context):

| turn | warm TTFT | chars | gen chars/s | tok/s | `num_predict` hit? | temp | MemAvail | throttled |
|---|---|---|---|---|---|---|---|---|
| 1 | 3.52 s | 137 | 8.37 | 2.57 | no | 65.0 °C | 3497 MB | `0x0` |
| 2 | 3.59 s | 200 | 9.44 | 2.79 | no | 65.6 °C | 3517 MB | `0x0` |
| 3 | 4.19 s | 157 | 10.72 | 2.80 | no | 65.6 °C | 3447 MB | `0x0` |
| **mean** | **3.77 s** | 165 | **9.5** | 2.72 | — | ≤ 65.6 °C | — | `0x0` |

**REAL PI EN RESULTS** — "What is a black hole?" → "How does it form?" →
"Why do humans need sleep?" (accumulating context):

| turn | warm TTFT | chars | gen chars/s | tok/s | `num_predict` hit? | temp | MemAvail | throttled |
|---|---|---|---|---|---|---|---|---|
| 1 | 3.59 s | 178 | 12.66 | 2.85 | no | 65.6 °C | 3484 MB | `0x0` |
| 2 | 4.05 s | 217 | 17.09 | 2.76 | no | 67.8 °C | 3436 MB | `0x0` |
| 3 | 2.89 s | 160 | 15.05 | 2.63 | no | 65.6 °C | 3439 MB | `0x0` |
| **mean** | **3.47 s** | 185 | **14.9** | 2.75 | — | ≤ 67.8 °C | — | `0x0` |

These are under the harsh continuous-synth contention; the live blind
figures (R0022) for this same serving config are PL ~11 / EN ~16.5
chars/s, warm TTFT ~3.0–3.5 s, END_OF_TURN→first-audio ~10–13 s.

**PIPER / CPU / RAM / THERMAL RESULTS** (whole Piper-active phase, `b36`):

- **Piper** `pl_PL-gosia-medium`, `nice +10`: true HTTP RTF **mean 0.336 /
  max 0.521** over 66 synths, **0 errors** — ~3× faster than real time,
  no TTS regression from `num_thread=2`.
- **CPU:** `llama-server` ~2 cores while decoding (`num_thread=2` caps it);
  per R0022 `blind_results` for the same config, CPU total mean ~50 % /
  peak ~100 %, `llama-server` mean ~150 %.
- **RAM:** MemAvailable held ~3.4–3.5 GB with `gemma4:e4b` (~10 GB)
  resident + Piper + probe; **swap 0 MB**; no OOM, no instability across
  the whole run.
- **Thermal:** ≤ 67.8 °C; **`throttled` `0x0`** throughout (start 52.9 °C,
  peak during decode ~67.8 °C).

**STT latency and END_OF_TURN → first-audio** require a live mic and are
**not** in this headless probe — they are collected in the operator's
live voice confirmation session (same pattern as B.3.3). R0022's blind
`blind_results/` already has real full-pipeline numbers for `gemma4:e4b`
under this exact serving config (`num_thread=2` / `keep_alive=30m`):
warm TTFT ~3.0–3.5 s, END_OF_TURN→first-audio ~10–13 s, PL ~11 chars/s /
EN ~16.5 chars/s, no throttling, ≤ 65 °C.

### Tests

- `pytest` — **480 passed / 7 skipped / 14 subtests** (was 460; +20 new
  `test_production_serving_freeze.py`; `test_operator_blind_ab.py`
  updated, not grown).
- `python -m unittest discover -s tests` — **487 OK / 7 skipped** (was 467).
- `ruff check src tests apps docs/research/m2_4b_llm_bench` — clean.
- `git diff --check` — clean.

New tests (`tests/test_production_serving_freeze.py`, offline, fake
recording Ollama + `FakeModelProvider`):

- `DEFAULT_LOCAL_MODEL == "gemma4:e4b"`; `DEFAULT_LOCAL_NUM_THREAD == 2`;
  `DEFAULT_LOCAL_KEEP_ALIVE == "30m"`; settings carry them.
- canonical provider wire body: `num_thread=2` in `options`,
  `keep_alive="30m"`, and `options` is exactly the persona set **+ one**
  key; every sampling option equals `load_persona().options`.
- `NEXA_LOCAL_NUM_THREAD=0` ⇒ no `num_thread` on the wire (let Ollama
  pick); `NEXA_MODEL_PROVIDER=cloud` still raises (no silent local
  fallback).
- TEXT and VOICE hit the same provider authority; the only wire diff is
  the transient VOICE directive `system` message.
- TEXT mode messages byte-identical to a direct `to_provider_messages(
  response_mode=TEXT)`; PL/EN `language_directive` still injected.
- warm-up: `session.history == ()` after; messages are exactly
  `[system=persona, system=voice_directive]`, no `user` role;
  `num_predict` clamped to 1, other options unchanged; the warm-up
  message list is a strict prefix of the first real VOICE turn; same
  `session.provider` object; `ModelUnavailableError` propagates.
- AST: `bootstrap.py` has no `_history` / `.send(`; exactly one
  `LocalModelProvider(...)` and one `ConversationSession(...)`
  construction; only one `*_MODEL` string constant in `nexa.config`; no
  `gemma4:e2b` string literal / `*_MODEL` per-language name anywhere in
  `src/nexa`.

## POLISH PERFORMANCE STILL OWED

`gemma4:e4b` Polish generated-text throughput is ~11 chars/s (R0022
blind, `num_thread=2`) vs R0018's 15.9 chars/s target — Polish long
replies still gap. This is **not** solved in B.3.6 (no speed-up, no
planner/continuity redesign, no model switch). It is a later dedicated
optimisation track. `realtime_text_ratio` for Polish is unchanged.

## POLISH STT TRACK (recorded, deferred)

Operator sessions repeatedly show whisper.cpp `base`/`q8_0` Polish
errors, including malformed ordinary phrases (R0016, and the R0022
operator note about Candidate B Polish STT misses). Confirmed as its own
quality track: later, benchmark current `base`/`q8_0` vs stronger
whisper.cpp models / quantisations practical on the Pi — PL + EN
accuracy, latency, CPU/RAM — on real operator-style speech. **STT
unchanged in B.3.6.**

## BILINGUAL FUTURE (recorded, deferred)

Next planned language stage after B.3.6: **Bilingual / code-switch voice
input** — PL utterance → PL STT → PL answer; EN → EN → EN; dynamic
PL↔EN inside one `ConversationSession`; explicit language requests
("Odpowiedz po angielsku.", "Wracamy do polskiego.") override mirroring.
**Not implemented in B.3.6.**

## UNRESOLVED

- STT latency / END_OF_TURN→first-audio for B.3.6 config need the operator
  live-voice session (headless probe cannot produce them).
- Polish throughput deficit (~11 vs 15.9 chars/s) — later track.
- Polish STT accuracy — later track.
- `keep_alive` = infinite residency — deferred resource-policy decision.

## DOCUMENTATION / REPORTS UPDATED

- `docs/reports/R0022_…md` — blind A/B closed (reveal, operator
  observations, objective metrics, decision).
- `docs/reports/R0023_…md` — this report.
- `docs/CURRENT_STATE.md` — B.3.5 closed, B.3.6 done, serving config now
  `num_thread=2` / `keep_alive=30m` / startup warm-up.
- Memory: `m2-4b-progress.md` updated.

## FILES CHANGED

Production (`src/`): `nexa/config.py`, `nexa/providers/ollama.py`,
`nexa/bootstrap.py`. Apps: `nexa_voice_tts_probe.py`,
`nexa_voice_chat_probe.py` (`nexa_chat.py` TEXT untouched). Tests:
`tests/test_production_serving_freeze.py` (new),
`tests/test_operator_blind_ab.py` (one class updated). Docs:
`docs/reports/R0022_…md` (A/B closed), `docs/reports/R0023_…md` (this),
`docs/CURRENT_STATE.md`. Research (deletable): `b36_serving_freeze_probe.py`
+ its `_20260908_175830.json` / `_raw.txt` outputs; the operator's
`blind_results/candidate_*_{pl,en}_2026*.{jsonl,txt}` from the R0022 A/B.

## COMMIT HASH

`0dc98a6` — `feat: freeze production local model serving (B.3.6) + close
model A/B` (tip of `main`). One coherent local commit closing R0022 +
implementing B.3.6. Not pushed. (This report's own text-only tweak of the
hash lands in the next commit.)

## GIT STATUS

Branch `main`, not pushed. `ruff check src tests apps
docs/research/m2_4b_llm_bench` clean; `git diff --check` clean. No model
weights / `~/.ollama` blobs staged. The sealed A/B mapping file's
contents are reproduced only in R0022 now that the test is over.

## LEGACY NEXA USED — NO
## EXTERNAL RESEARCH USED — NO

(llama.cpp KV-prefix reuse behaviour was verified directly against the
running Ollama, not from docs.)

## CURRENT VERIFIED STATE

`gemma4:e4b` is the canonical local production conversation model
(`DEFAULT_LOCAL_MODEL`), operator-confirmed via the R0022 blind A/B. The
one canonical `LocalModelProvider` sends `num_thread=2` and
`keep_alive=30m` as one-time policy from `nexa.config`; `build_default_
session()` is the single TEXT+VOICE authority. `warm_up_session()` primes
the persona/VOICE KV prefix at startup (measured ~73 s saved on the first
real turn) without touching history. No `e2b` routing, no per-language
model, no fallback model, no second conversation authority.
`ResponseMode.TEXT` / `VOICE` and the PL/EN language mechanism are
unchanged. Not pushed.

## NEXT RECOMMENDED ACTION

Operator runs a short live voice session on the B.3.6 build (PL + EN, a
"rozwiń" / "tell me more" follow-up) to (a) confirm B.3.3 `VOICE` live
and (b) capture STT latency + END_OF_TURN→first-audio for this serving
config. Then **M2.5 — barge-in / interruption** (replaces the temporary
half-duplex gate). Recorded-not-started: bilingual auto-STT / code-switch;
Polish throughput track; Polish STT-model track.
