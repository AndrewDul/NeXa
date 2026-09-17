# R0080 — Voice Context Parity Runtime Activation + Live Acceptance

**Date:** 2026-09-17
**Type:** Implementation (narrow runtime activation)
**Status:** Production wiring COMPLETE and independently verified (including one genuinely live, real-hardware-adjacent local voice run); cloud LIVE round-trip/compliance/barge-in still NOT TESTED — no Gemini API key or audio hardware in this environment
**Depends on:** R0079 (accepted local commit `476a7ec`), R0078 Revision 2, R0075-R0077, R0071

No M3.4. No AEC/VAD/audio-transport/barge-in redesign. No Learning/FTS/vectors. Not pushed.

---

## 0. What this milestone closes

R0079 built and unit-tested the mechanism (`ContextEngine.recall()`, local
`context_provider` seam, the `recall_context` Gemini tool adapter) but
explicitly left the real production entrypoints untouched. This report
closes exactly that gap: the mechanism is now **activated** in the real,
accepted composition roots, not just constructible by a test. Distinguishing
these two claims precisely — "mechanism implemented" vs. "production
runtime actually activates it" — is this report's central discipline,
per the explicit instruction.

---

## 1. Audit: actual production entrypoints

| Entrypoint | Role | Before R0080 | After R0080 |
|---|---|---|---|
| `apps/nexa_chat.py` | Typed local (accepted, R0077) | `ContextRuntime` + `make_local_context_provider` wired, one runtime per process | **Unchanged** — already correct |
| `apps/nexa_voice_chat_probe.py` | Older local voice (M2.3 scope: no TTS, no barge-in) | No Context Engine wiring | **Not touched** — superseded by the bilingual probe below; not the accepted production path (confirmed by its own docstring: "no TTS, no Piper, no speaker playback... no barge-in (ADR-0003 M2.3 scope)") |
| `apps/nexa_bilingual_voice_probe.py` | **Accepted local voice production entrypoint** (M2.4B/M2.5B — full TTS, bilingual STT, barge-in; its own docstring: "M2.4B.5 — automatic bilingual PL/EN voice probe (live acceptance)"; the "Live probe" referenced in this project's own M2.5B acceptance history) | No `ContextRuntime`, no `context_provider` — `VoiceConversationAdapter` constructed without it | **Activated** (§2) — one `ContextRuntime`, `context_provider` wired into the real adapter construction |
| `apps/nexa_cloud_voice_app.py` | **PAUSED** M2.6B dual-pipeline (own docstring: uses `nexa.realtime.gemini.runtime.build_gemini_voice_runtime`, the dual-pipeline architecture R0071 paused) | — | **Not touched** — remains paused, not production |
| `apps/nexa_cloud_voice_simple.py` | **Accepted cloud voice production entrypoint** (R0071, "the accepted, golden-M2.6A-derived production entry point") | No `RecallExecutor`, Pipecat's implicit default model | **Activated** (§3/§5) — one `RecallExecutor`, explicit model pin, `--no-core-recall` rollback |

**Distinguishing mechanism-implemented from runtime-activated, concretely:**
before this report, every R0079 test constructed `ContextEngine`/
`RecallExecutor`/`context_provider` **by hand, inside a test file** — none
of the five real entrypoints above ever called
`make_local_context_provider()` or `RecallExecutor()` themselves. That is
now false for the two accepted production entrypoints (bilingual voice
probe, simple cloud voice) and was already true for typed chat (R0077).
The two non-production files (`nexa_voice_chat_probe.py`,
`nexa_cloud_voice_app.py`) are correctly left untouched — activating
Core Context in a paused or superseded entrypoint would be wasted, risky
work with no product benefit.

---

## 2. Local voice production activation

**File:** `apps/nexa_bilingual_voice_probe.py`.

```python
session = build_default_session()
session.provider_window = ProviderWindow()
...
context_runtime = build_default_context_runtime()          # ONE, app-lifetime
context_provider = make_local_context_provider(context_runtime.context_engine)
...
adapter = VoiceConversationAdapter(
    session,
    ...,
    context_provider=context_provider,                       # NEW
    ...,
)
...
finally:
    await adapter.shutdown()
    await aiohttp_session.close()
    detector.close()
    context_runtime.connection.close()                        # NEW
    ...
```

No STT/TTS/VAD/AEC/barge-in code touched — the diff is exactly: two new
imports, three new lines around `session` construction, one new kwarg on
the existing `VoiceConversationAdapter(...)` call, one new `close()` call
in the existing `finally:` block. `make_local_context_provider()` itself
is unchanged (R0077) — it never filters `cloud_eligibility`, so local
voice legitimately gets `LOCAL_ONLY` content, exactly as R0079 already
proved for the synthetic-adapter case.

**Verified live**, not just by construction inspection: `tests/test_local_voice_context_activation.py`
(gated `NEXA_RUN_LIVE_TESTS=1`, matching this repo's existing
`test_live_ollama_integration.py` convention — real Piper HTTP server +
real local Ollama model are too heavy for every unit-test run) runs the
**real** `main()`, patching only `VoiceRuntime.run()` (to stop before real
microphone access) and spying on the real `VoiceConversationAdapter`
constructor call. Confirmed in this environment (Ollama + `gemma4:e4b`
loaded, Piper venv/voices present on disk): **2 passed in ~11-13s**,
proving:
1. `context_provider` is non-`None` in the real constructor call.
2. It is genuinely bound to a real `ContextEngine` instance (via closure
   introspection on `make_local_context_provider()`'s returned callable),
   not a stub.

This app has no `--dry` mode (unlike the cloud entrypoint) — real
hardware/model access is unavoidable to exercise it at all, which is why
this test is live-gated rather than always-on.

---

## 3. Cloud voice production activation

**File:** `apps/nexa_cloud_voice_simple.py`.

```python
recall_executor: RecallExecutor | None = None
if args.core_recall:                              # default True
    recall_executor = RecallExecutor()
    await recall_executor.start()                  # ONE, session-lifetime

adapter = build_cloud_realtime_conversation_adapter(
    ...,
    recall_executor=recall_executor,                # None when disabled
)
_print_core_recall_diagnostics(enabled=args.core_recall, executor=recall_executor)
...
finally:
    await adapter.stop(reason="operator shutdown")
    if recall_executor is not None:
        recall_executor.close()
```

`recall_executor.close()` is called on **every** exit path this report
touches: normal shutdown, kickoff-timeout failure, and missing-credential
early exit — no leaked worker thread/connection on any path (proven by
`test_executor_closed_when_credential_missing`).

No audio/VAD/AEC/barge-in code touched — `build_cloud_realtime_conversation_adapter()`'s
own signature already had the optional `recall_executor` parameter
(R0079); this activation is entirely: construct→start→pass→close, plus
the new CLI flag and diagnostics print.

**Verified**, not just by inspection: `tests/test_cloud_voice_simple_entrypoint.py`
runs the **real** `main()` in `--dry` mode (no network, no audio — the
existing, unmodified `--dry` contract), proving, against the real,
offline-constructed `GeminiLiveLLMService`:
- exactly ONE `RecallExecutor` is constructed per run (never per tool call);
- `start()` is awaited **before** the builder is called (the exact
  required lifecycle order);
- `recall_context` is actually present in `llm._functions` with
  `cancel_on_interruption=True`;
- the pinned model string is what the real `GeminiLiveLLMSettings.model`
  ends up holding;
- `--no-core-recall` reproduces byte-for-byte pre-R0079 construction (no
  executor constructed at all, no tool registered, no instruction text
  appended).

13 tests, all passing (§17).

---

## 4. Rollback path

`--core-recall` / `--no-core-recall` (`argparse.BooleanOptionalAction`,
default `True`), on `apps/nexa_cloud_voice_simple.py` only — matching the
instruction's own scoping ("Core recall" throughout R0078/R0079/R0080
names the cloud tool-calling mechanism specifically). Local voice's
`context_provider` gets no separate flag, by design: it already fails
safe through the exact same try/except `ConversationSession`/
`_render_provider_window()` already established in R0077/R0079 (a Context
Engine exception never crashes a turn, it just proceeds without the
addendum) — the same discipline `apps/nexa_chat.py`'s typed-chat wiring
already relies on unconditionally, with no rollback flag either. The
asymmetry is intentional: the *novel, not-yet-live-tested* risk R0080
must hedge against is specifically the cloud tool-calling path sitting on
top of the hardware-accepted R0071 baseline — not the local mechanism,
which R0079 already integration-tested end-to-end and R0080 has now
additionally verified live.

`--no-core-recall` is a **diagnostics/regression** path, not the intended
normal state — default is `True` (enabled), per the explicit instruction
("Preferred normal product state after acceptance: Core recall enabled").
**This default has not been live-hardware-verified in this environment**
— an operator running this for the first time on real hardware should
know `--no-core-recall` exists for instant rollback if anything looks
wrong, without needing to revert any code.

---

## 5. Explicit Gemini model pin

```python
# src/nexa/realtime/gemini/simple_conversation.py
GEMINI_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
...
settings=P["GeminiLiveLLMService"].Settings(model=GEMINI_MODEL, ...)
```

This is the exact model R0079 discovered was already running (as
Pipecat's own implicit SDK default) — **reproducibility, not migration**.
Verified: `adapter.llm._settings.model == GEMINI_MODEL` in the real,
offline-constructed object graph (`test_gemini_model_is_explicitly_pinned`).

---

## 6. Confirmed: the paused `gemini-3.1-flash-live-preview` string stays unrelated

Source-level regression tests (`TestPausedModelStringUnrelated`, 4 tests),
checking the actual **code** (comments/docstrings stripped, so R0080's own
explanatory comment about *why* the two strings are unrelated doesn't
false-positive the check): `gemini-3.1-flash-live-preview` never appears
as a used value in `simple_conversation.py`, `core_recall_tool.py`, or
`apps/nexa_cloud_voice_simple.py`; the app never imports
`nexa.realtime.gemini.runtime` or `nexa.realtime.gemini.service` (the
paused M2.6B modules where that string actually lives). This is now an
enforced regression, not just a documentation claim.

---

## 7. Production-entrypoint regression tests

`tests/test_cloud_voice_simple_entrypoint.py` (13 tests): CLI parsing
defaults, dry-run activation (executor lifecycle order, real tool
registration, model pin), rollback-flag construction, executor-closed-on-
early-exit, paused-model-string unrelation.

`tests/test_local_voice_context_activation.py` (2 tests, live-gated):
real `main()` wiring proof, real `ContextEngine` closure-identity proof.

---

## 8. One canonical state, not necessarily one Python object across processes

Stated precisely, per the explicit instruction: `apps/nexa_chat.py`,
`apps/nexa_bilingual_voice_probe.py`, and `apps/nexa_cloud_voice_simple.py`
are **three separate OS processes** when run as separate CLI invocations —
they cannot, and this report does not claim they do, share one literal
Python `ContextEngine` object across process boundaries. What IS true,
and is the actual canonical requirement:

- Each process's `MemoryService`/`ContextEngine` opens its own
  `sqlite3.Connection` (via `nexa.core.storage.sqlite.connect()`/
  `default_db_path()`) pointing at the **same canonical on-disk file**.
- All three apply the **same** Memory/Context/privacy semantics (same
  `MemoryService`/`MemoryRetriever`/`ContextEngine` code, same
  `CloudEligibility` rules) — not three different implementations that
  happen to agree.
- **Within** one process, there is exactly one `ContextRuntime`/
  `ContextEngine`, constructed once, reused for the process's whole
  lifetime — never rebuilt per turn or per tool call. This is what §2/§3
  verify directly (object-identity / call-count assertions), and it is
  the correct, narrower claim: object identity within a process, semantic
  identity across processes via the shared canonical store.

---

## 9. Official Gemini Live function-calling semantics — re-verified

**Cross-checked against live official Google documentation** (WebFetch,
accessed 2026-09-17 — not relied on Pipecat internal comments alone,
per the explicit instruction):

- `https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-native-audio-preview-12-2025`
  — Live API: **Supported**. Function calling: **Supported**, for this
  exact pinned model.
- `https://ai.google.dev/gemini-api/docs/live-api/tools` — *"Function
  calling executes sequentially by default, meaning execution pauses
  until the results of each function call are available."* Model
  coverage explicitly lists **"Gemini 2.5 Flash Live"** as supporting
  both synchronous and asynchronous function calling (asynchronous is
  opt-in via `behavior: NON_BLOCKING`). The exact dated variant string
  `gemini-2.5-flash-native-audio-preview-12-2025` is not spelled out on
  this particular page (it references the model family), but is
  confirmed to exist and support both Live API + function calling on its
  own model-card page above.
- `https://ai.google.dev/gemini-api/docs/live-guide` — corroborating
  context: **`gemini-3.1-flash-live-preview`** (the unrelated, paused
  string, §6) is documented as *"Function calling is sequential only. The
  model will not start responding until you've sent the tool response"* —
  interesting confirmation that blocking-by-default is the norm across
  this model family generally, not just Pipecat's own registration
  default.

**Conclusion:** the official documentation's stated default ("sequential/
blocking") matches exactly what R0079's Pipecat-level audit found
(`register_function(..., cancel_on_interruption=True)` = blocking = the
library's own default) — two independent sources agree. `register_recall_tool()`
registers explicitly with `cancel_on_interruption=True` (not relying on
the implicit default), matching this confirmed behavior. **This is not a
live API test** — it is documentation cross-verification, the strongest
evidence obtainable without a real Gemini session in this environment.

---

## 10. Live smoke-test command

For the operator, on the actual NeXa hardware, with the accepted audio
configuration and Core recall enabled (the default):

```bash
.venv/bin/python apps/nexa_cloud_voice_simple.py
```

Expect, before `>>> CLOUD_READY <<<`:

```
CORE_RECALL enabled
CORE_RECALL executor ready: True
CORE_RECALL tool registered: recall_context
GEMINI_MODEL models/gemini-2.5-flash-native-audio-preview-12-2025
```

To roll back to pre-R0079 cloud behavior without touching code:

```bash
.venv/bin/python apps/nexa_cloud_voice_simple.py --no-core-recall
```

Expect: `CORE_RECALL disabled (--no-core-recall)` and no `CORE_RECALL
executor`/`tool registered` lines. No Memory content is ever printed by
either diagnostics path (verified by direct reading of
`_print_core_recall_diagnostics()` — it prints only booleans and the
static model string).

For local voice (already-default-on, no flag — §4):

```bash
.venv/bin/python apps/nexa_bilingual_voice_probe.py --bargein
```

Expect: `Core Context: ContextRuntime ready, context_provider wired into
local voice (R0080)` printed once, before warm-up.

---

## 11. Live acceptance corpus (A-E) — DESIGNED, NOT RUN

No API key/hardware in this environment. Recorded here exactly as
specified, ready for the operator to execute:

**Seed:** `namespace=projects.nexa`, `content="NeXa test architecture code
is ORBIT-47."`, `cloud_eligibility=CLOUD_SAFE` (e.g. via
`apps/nexa_chat.py` + an explicit remember flow, or a direct
`MemoryService.remember()` call against the canonical DB before starting
the cloud session).

| # | Turn | Expected |
|---|---|---|
| A | "What is the NeXa test architecture code?" | `recall_context` called; Core returns ORBIT-47; Gemini answers correctly |
| B | "What is the capital of France?" | No `recall_context` call needed |
| C | Ask for a nonexistent personal/project fact | Tool may be called; `no_match`; Gemini must not invent a stored answer |
| D | Write a NEW `CLOUD_SAFE` fact into canonical Memory while the session stays open (e.g. a second process/`nexa_chat.py` remember, or direct `MemoryService.remember()`), then ask for it | No reconnect; same `RecallExecutor`; newly written fact found (mechanism proven at the executor level in R0079's `test_dynamic_fact_written_mid_session_becomes_recallable`; NOT proven through an actual live Gemini session here) |
| E | Seed `LOCAL_ONLY` content with a lexically matching namespace, ask for it through cloud | No private content crosses; provider sees `no_match`-equivalent behavior (unit-proven at the wire-mapping level in R0079; NOT proven live) |

**Status: NOT RUN.** This is a script for the operator, not a claim of
execution.

---

## 12. Live interruption acceptance — DESIGNED, NOT RUN

During a Core-recall-requiring answer: interrupt Gemini, immediately ask
a different question. Verify: audio stops; old answer does not resume;
old recall result is not applied to the new turn; no self-conversation;
no duplicate user/assistant history. **Mechanism verified by source audit**
in R0079 (Pipecat's `_handle_interruptions()`/`_cancel_function_call_tasks()`
— `settled = True` before cancellation, so a stale handler result cannot
be applied) — this report does not repeat that audit, only restates that
it remains the basis for confidence, pending an actual live test. **Status:
NOT RUN.** No interruption architecture was changed to make this claim.

---

## 13. Real latency — NOT MEASURED

Requires a live Gemini session; this environment cannot produce it. R0079's
`RecallExecutor` thread-hop measurement (~0.13ms mean) is explicitly NOT
a substitute and is not cited here as if it were — it measures only the
local executor overhead, never the Gemini function-call protocol
round-trip (transcript → tool-call decision → handler → response →
first audio), which is the number this section actually asks for.
**Status: NOT MEASURED.**

---

## 14. Cloud tool-compliance corpus — metrics kept separate, NOT RUN

Per the explicit instruction, two distinct, never-merged metrics for when
the corpus (§11/§14 of R0078 Revision 2 and R0079) is eventually run live:

- **TOOL DECISION**: did Gemini call `recall_context` when the query
  actually needed NeXa-specific knowledge (and correctly NOT call it for
  generic world knowledge)?
- **RETRIEVAL HIT**: given the tool WAS called, did V1's lexical
  discovery (plain substring domain/summary matching, no semantic search)
  actually find the intended record?

A failure on TOOL DECISION says nothing about RETRIEVAL HIT and vice
versa — conflating them would hide which of the two needs work. **Status:
NOT RUN** in this environment.

---

## 15. Model evaluation — scoped follow-up, not this milestone

No migration in R0080. Recorded here as a clearly scoped recommendation
for a future, dedicated report: compare the pinned
`gemini-2.5-flash-native-audio-preview-12-2025` baseline against the
current recommended Gemini Live model (per §9's search, official docs
mention **Gemini 3.8 Live** as the newer generation, with its own
`BLOCKING`/`NON_BLOCKING` explicit scheduling parameter and an Extended
Thinking variant that supports `NON_BLOCKING` only) across: latency,
voice naturalness, tool compliance, barge-in, PL, EN, PL↔EN switching,
cost, stability. That evaluation needs a dedicated milestone with real
hardware/API access — out of scope here.

---

## 16. Acceptance gate (honest, per-component)

| Component | Status |
|---|---|
| LOCAL VOICE PRODUCTION WIRING | **PASS** — real `main()`, live-run-verified (§2) |
| CLOUD VOICE PRODUCTION WIRING | **PASS** — real `main()` (`--dry`), 13 tests, real offline `GeminiLiveLLMService` (§3) |
| CLOUD LIVE RECALL ROUND-TRIP | **NOT TESTED** — no API key/hardware |
| CLOUD TOOL COMPLIANCE | **NOT TESTED** — requires live model access |
| DYNAMIC SAME-SESSION MEMORY | **PARTIAL** — mechanism proven at the `RecallExecutor` level (R0079), not through an actual live Gemini session |
| CLOUD PRIVACY | **PASS** at the unit/wire-mapping level (R0079); **NOT TESTED** live |
| CLOUD BARGE-IN WITH TOOL | **NOT TESTED** live; mechanism **PASS** by source audit (R0079 §11/§18) |
| MODEL PINNING | **PASS** — explicitly pinned, tested, cross-verified against live official docs (§5/§9) |

**Voice Context Parity is NOT fully accepted by this report** — production
wiring is complete and independently verified (including one genuine live
local-voice run), consistent with the instruction's own fallback: real
live cloud checks remain NOT TESTED, stated honestly, not fabricated.

---

## 17. Regression

New tests this milestone: **15** (`test_cloud_voice_simple_entrypoint.py`
13 + `test_local_voice_context_activation.py` 2, the latter live-gated and
skipped by default).

```
pytest tests/ -q
```

**Result:** 1481 collected (15 new vs. the R0079 baseline of 1466); 1471
passed, 9 skipped (the 7 pre-existing environment-gated live tests plus 2
new — this milestone's own `test_local_voice_context_activation.py`,
gated `NEXA_RUN_LIVE_TESTS=1`, which independently passed 2/2 in a live
run, §2), **1 pre-existing failure** —
`tests/test_voice_architecture.py::TestConfigIsExplicitAndTyped::test_local_audio_config_fields_are_typed_and_explicit`,
the same paused R0068-R0070 `scheduled_aec_reference` issue documented
since R0076, still untouched by this work. **Zero new failures.**

`ruff check` on every file this milestone touched: clean. (One pre-existing
`ruff` finding in the untouched, paused `apps/nexa_cloud_voice_app.py` —
confirmed via `git log`/`git diff --stat` to predate this session
entirely, part of the already-known uncommitted R0064-R0070 paused work;
not touched, not fixed, out of scope.)

`py_compile`: clean. `git diff --check`: clean.

---

## 18. Known limitations, restated

- No semantic search/FTS/vectors — unchanged from M3.3/R0079.
- Cloud live round-trip, tool compliance, barge-in-with-tool, and real
  latency are all NOT TESTED — this environment has no Gemini API key and
  no audio hardware. Nothing in this report claims otherwise.
- `--no-core-recall`'s counterpart, "Core recall enabled" as the default,
  has not itself been live-verified — it is the instructed target state,
  not a verified-safe one yet.
- Local voice has no `--dry` mode; its live-gated test is the only way to
  exercise its real `main()` without a human operator physically present
  (and still cannot reach actual microphone capture without real
  hardware).

---

## 19. Commit gate

Production wiring is complete for both accepted entrypoints; regression
tests pass (see numbers below, captured from the actual run); ruff/
py_compile/git diff --check are clean. Per the explicit fallback: this
environment still has no Gemini API key or audio hardware, so live cloud
checks are marked NOT TESTED per §16, not fabricated as PASS.
