# R0077 — M3.3 Runtime Integration + ContextRequest Derivation

**Date:** 2026-09-17
**Milestone:** M3.3 follow-up — Context Engine runtime integration
**Status:** Local typed path wired and exercised (stub + one real Ollama
turn). Cloud realtime path audited, adapter built and fully tested, but
**not wired into the frozen production entrypoint** — an audited finding,
not an oversight (see §6). Not pushed.

## TASK RESULT

**PASS**, with one explicit, honest scope boundary: cloud runtime wiring
is proven-ready but deliberately not flipped on (§6/§16 below).

## 0. R0076 test-count correction

R0076 said "170 new tests" in one place and "91 new/changed tests" in
another. Re-audited: **91 is correct** — verified against per-file
`pytest` counts (14+29+12+19+36+10, with the privacy/repository files
counted as their delta from the pre-M3.3 baseline: +6/+8) and against the
full-suite delta (1355 − 1264 = 91, exact match). Corrected the "170"
line in `docs/reports/R0076_...md` in place; no other historical content
in that report was altered.

## 1. Repository audit (before any runtime code changed)

### A. Local typed conversation path

```
apps/nexa_chat.py
  -> nexa.bootstrap.build_default_session()      -- identity+persona -> system_prompt (once)
  -> loop: ConversationSession.send(user_text)
       session.py:232  self._history.append(USER turn)         <- user turn appended HERE
       session.py:238  context = self.build_context()            <- ConversationContext built HERE
       session.py:239  messages = context.to_provider_messages()   <- system prompt composed HERE
       session.py:241  self.provider.generate(messages, ...)         <- model request issued HERE
```
Confirmed (re-verified, matches R0075/R0076's own earlier audit): the
user turn is appended **before** context is built, in the same method —
the exact fact `nexa.core.context.derivation.derive_context_request()`'s
precondition depends on.

### B. Cloud realtime conversation path

```
apps/nexa_cloud_voice_simple.py:92-97
  session = build_default_session()
  snapshot = build_cloud_context_snapshot(session, policy_name=..., active_provider_name="cloud",
                                           context_facts=[...from --context-fact CLI flags...])
  -- session.history is EMPTY here -- no conversation has happened yet.

  -> build_cloud_realtime_conversation_adapter(session=session, snapshot=snapshot, ...)
       simple_conversation.py:313  router = ConversationRouter(session, policy=policy)
       -- router.start_cloud() is NEVER called anywhere in this file.
       -- router is used ONLY via handle_provider_event() (the write-back path).
  -> adapter.start() -- opens the Gemini socket + hardware pipeline directly from `snapshot`
```

**Key finding, verified by direct source read, not assumption:** the
ConversationRouter `snapshot_builder` constructor seam exists
(`router.py:67`) but is **never exercised** by the accepted simplified
adapter — `snapshot` is built once, directly, by the app, before the
router is even constructed, and `router.start_cloud()` (the only method
that calls `self._snapshot_builder(...)`) is never invoked in this path.
Gemini's own native session handles all subsequent turns; no NeXa-owned
per-turn snapshot rebuild exists in the accepted architecture.

**Consequence:** `session.history` is structurally always empty at the
one point a snapshot is built in this path — `ContextEngine.build_context()`'s
precondition (non-empty history, last turn USER) can never be satisfied
there. This is not a limitation of my implementation; it is a fact about
the accepted M2.6A-derived design (Gemini's own native turn handling
replaces NeXa-owned per-turn context rebuilding by design, per ADR-0004
Amendment 2 / R0071).

### C. Typed cloud path

None exists — `nexa.realtime.gemini.simple_conversation` is realtime
voice only; the M2.6B dual-pipeline runtime (`nexa.realtime.gemini.runtime`,
paused) is a separate, unrelated, untouched codepath.

### KEEP / EXTEND / DO-NOT-TOUCH map

| Component | Classification |
|---|---|
| `ConversationSession.send()` | **EXTEND** — one new optional parameter (§2) |
| `ConversationSession.build_context()` | **EXTEND** — one new optional parameter (§2) |
| `apps/nexa_chat.py` | **EXTEND** — wired (§4) |
| `nexa.bootstrap` | **EXTEND** — one new, separate composition function (§7) |
| `apps/nexa_cloud_voice_simple.py` | **DO NOT TOUCH** — audited, no viable non-restructuring wiring point (§6) |
| `nexa.realtime.gemini.simple_conversation` | **DO NOT TOUCH** — same reason |
| Pipecat pipeline / VAD / AEC / barge-in / audio transport | **DO NOT TOUCH** — never read, never imported by anything in this checkpoint |
| `nexa.core.context`, `nexa.core.memory` | **DO NOT TOUCH** (ownership) — only additive (`derivation.py`, timing fields) |

## 2. `ConversationSession.send()` — the `context_provider` hook

```python
async def send(self, user_text, *, ..., context_provider: Callable[[ConversationSession], str | None] | None = None): ...
```

Invoked with `self` immediately **after** the internal history append (so
`self.history[-1]` is safely the current USER turn) and **before** the
provider call — the exact insertion point the audit in §1.A identified.
A plain callable, not a `nexa.core.context` import: `nexa.core.context`
already depends on `ConversationSession`, so the reverse import would be
circular. This keeps `ConversationSession` exactly what it already was —
canonical transcript authority — plus one optional hook, not a new
dependency (§20, unchanged ownership).

Fails safe: an exception from `context_provider` is caught, logged
(`logger.warning(..., exc_info=True)`), and the turn proceeds with
`extra_system_context=None` — never crashes, never leaves `_history` in a
partial state (verified by test, §12 below). Default (`None`) is
byte-for-byte the pre-R0077 path (verified by test).

Only honored on the non-`provider_window` path — `_render_provider_window`
(M2.5B.2, voice KV-cache path) is untouched, unaffected, not wired this
checkpoint (matches §1.A's audit: `apps/nexa_chat.py`'s
`build_default_session()` never sets `provider_window`).

## 3. `derive_context_request()` — `src/nexa/core/context/derivation.py`

Lives inside `nexa.core.context` (not a call-site concern) since it
operates only on `ConversationSession`/text — provider-neutral, reusable
identically by a future local or cloud caller (matches "same
`ContextEngine` regardless of provider," ADR-0006 D3).

```python
derive_subject_hints("What should I learn next in Python?")
# -> ("learn", "next", "python")   -- exact required example, verified by test
```

Algorithm: lowercase, strip punctuation, tokenize, drop tokens shorter
than 3 chars, drop a small general English-function-word stopword set
(articles/pronouns/prepositions/modal verbs/wh-words — NOT domain/
business-specific), deduplicate preserving order, cap at 10. Deterministic,
no model call, no network (verified by a 100-call tight-loop test with no
I/O). Source-scanned (test, not just described) to confirm no hardcoded
`domain_hint = "teacher..."`-shaped mapping exists in the module's code.

`derive_context_request(session, *, domain_hint=None, ...)` — `domain_hint`
stays `None` by default (§4: no Goals/Projects/UserModel state invented to
supply a "trustworthy scope"); passthrough only.

## 4. Local typed path — wired (`apps/nexa_chat.py`)

```python
session = build_default_session()
runtime = build_default_context_runtime()
context_provider = make_local_context_provider(runtime.context_engine)
...
async for chunk in session.send(user_text, context_provider=context_provider):
    ...
```

`nexa.conversation.context_projection.to_local_context_addendum()` renders
**only** `selected_context_items` (never trace/descriptors/gaps/payload —
verified by a source-scan test on the function body). Identity/persona
composition (`bootstrap.py`, unchanged) and turn-history rendering
(`ConversationContext`/`ProviderWindow`, unchanged) remain the sole
authorities for those — Context Engine only ever appends one bounded
addendum block (§7/§8, satisfied — see §8 proof below).

## 5. Object lifetime / composition (§15)

`nexa.bootstrap.build_default_context_runtime()` — a **separate**, opt-in
function from `build_default_session()` (constructed **once**, at
application lifetime, not per-turn):

```python
@dataclass(frozen=True, slots=True)
class ContextRuntime:
    connection: sqlite3.Connection
    memory_service: MemoryService
    context_engine: ContextEngine
```

Three named references, no behavior, no `NeXaCore` god-object. Verified
separate from `build_default_session()` (a test confirms calling
`build_default_session()` alone never touches the filesystem/opens a
database — no unwanted side-effect expansion for the many existing
callers — voice probes, other tests — that don't need Context Engine).
`apps/nexa_chat.py` calls both and composes them itself; closes
`runtime.connection` in a `finally` block at shutdown.

## 6. Cloud realtime integration status — audited, proven-ready, NOT wired

`nexa.realtime.context_projection.make_cloud_snapshot_builder(engine)`
was built and is fully tested:

- Matches `ConversationRouter`'s exact `snapshot_builder` seam signature.
- **Proven byte-identical to the current unwired behavior when
  `session.history` is empty** — the exact, real condition at
  `apps/nexa_cloud_voice_simple.py`'s actual call site (verified by test
  AND by direct interactive run, below).
- With a real current turn present (a scenario the current frozen app
  never reaches, but a future flow might), correctly injects `CLOUD_SAFE`
  content and excludes `LOCAL_ONLY`/`CLOUD_WITH_USER_APPROVAL` (verified
  by test).
- Falls back safely to the plain snapshot on any internal failure
  (verified by test).

```
>>> from nexa.realtime.context_projection import make_cloud_snapshot_builder
>>> from nexa.realtime.snapshot import build_cloud_context_snapshot
>>> via_builder = builder(empty_history_session, policy_name="cloud_preferred", active_provider_name="cloud")
>>> direct = build_cloud_context_snapshot(empty_history_session, policy_name="cloud_preferred", active_provider_name="cloud")
>>> via_builder.system_instruction == direct.system_instruction
True
```

**Decision: `apps/nexa_cloud_voice_simple.py` and
`nexa.realtime.gemini.simple_conversation` are left completely
untouched** — zero lines changed. The audit in §1.B found no call site in
the accepted, frozen architecture where a current turn exists at
snapshot-build time; wiring the builder in there would be provably inert
(confirmed above) while still touching the file the R0071 real-hardware
acceptance covers. Per the explicit instruction ("wire... IF the audit
confirms this can be done without restructuring R0071"), the audit found
it **cannot** be done with any observable effect without restructuring
*when* snapshots are built — which is out of scope. This is reported as
the correct outcome of the audit, not a shortfall.

## 7. Provider projection shape

Local: one bounded string block appended to `system_prompt` for one turn
(`"Relevant things you already know:\n- ...\n- ..."`), never stored.
Cloud: `context.selected_context_items` -> `(content, cloud_eligibility)`
tuples -> the existing, unmodified `build_cloud_context_snapshot(...,
context_facts=...)`. Neither path ever sends `knowledge_references`,
`trace`, `conflicts`, or `knowledge_gaps` anywhere.

## 8. Proof: current turn / history not duplicated (real test output)

```
messages sent to provider (turn 1, stub provider, real ContextEngine, real Memory):
  [0] system: "You are NeXa...\n\n<persona>\n\nRelevant things you already know:\n- ..."
  [1] user:   "What did we decide about NeXa memory?"
  [2] system: "Respond to this message in English."   <- pre-existing R0009 directive, unrelated

identity substring count in message[0]: 1
user-role message count: 1
```

Turn 2 (multi-turn): exactly 2 distinct `user`-role messages across the
whole wire payload — one for each REAL prior turn (turn 1's question,
turn 2's question) — never more, proving `CurrentTurnContext.conversation_window`
is never independently re-rendered on top of what `ConversationContext`
already produces from `session.history`.

## 9. Natural NeXa-project recall — WITHOUT explicit `domain_hint` (real test)

```python
memory_service.remember(namespace="projects.nexa",
    content="One MemoryService is the canonical memory authority.")
# user turn (no domain_hint anywhere in the test):
session.send("What did we decide about NeXa memory?", context_provider=context_provider)
# derive_subject_hints -> ("decide", "nexa", "memory")
# "nexa" substring-matches domain "projects.nexa" -> descriptor found -> retrieved
# => system prompt contains "One MemoryService is the canonical memory authority."
```

## 10. Teacher lexical recall — WITHOUT hardcoded mapping (real test)

```python
memory_service.remember(namespace="teacher.python", category=STATE,
    record_type="skill_mastery", content="Recursion mastery: 55%")
session.send("What should I learn next in Python?", context_provider=context_provider)
# derive_subject_hints -> ("learn", "next", "python")
# "python" substring-matches domain "teacher.python" -- pure generic matching,
# zero "python"->"teacher.python" code anywhere (source-scan test, §3)
# => system prompt contains "Recursion mastery: 55%"
```

## 11. Unrelated-domain exclusion (real test)

Same Python question as §10, with `lifeos.finance` also populated with
`"UNRELATED-FINANCE-FACT"` — the fact never appears in the rendered
system prompt (`derive_subject_hints` produces no hint matching
`lifeos.finance`'s domain/summary text).

## 12. Dynamic knowledge, same session (real test)

```
turn 1: session.send("What did we decide about NeXa?") -> no fact (nothing remembered yet)
  memory_service.remember(namespace="projects.nexa", content="Dynamically added mid-session fact.")
turn 2: SAME session, SAME ContextEngine/MemoryService instances, no restart, no reconstruction
  -> system prompt contains "Dynamically added mid-session fact."
```

## 13. Privacy proof (both local and cloud, real test output)

```
Local (full context, no cloud crossing here):
  LOCAL_ONLY fact "LOCAL-ONLY-FACT-FOR-LOCAL-USE-ONLY" -> present in local system prompt

Cloud (to_cloud_snapshot / make_cloud_snapshot_builder):
  CLOUD_SAFE fact "Cloud-safe project fact."            -> crosses (present in system_instruction)
  LOCAL_ONLY fact "LOCAL-SECRET-MUST-NOT-CROSS"           -> excluded
  CLOUD_WITH_USER_APPROVAL fact "NEEDS-APPROVAL-FACT"      -> excluded (never crosses automatically)
  knowledge_references (descriptor domains)                 -> never appear in system_instruction, even when populated
```

No privacy logic was reimplemented — `filter_cloud_safe()`/
`build_cloud_context_snapshot()` are called completely unmodified.

## 14. Latency measurements (§17, real measurements, not estimates)

Isolated `ContextEngine.build_context()` timing (excludes model/network
time entirely), 250 Memory records across 50 namespaces, 10-run average
after 3 warm-up calls:

```
discovery_ms avg: 1.29
retrieval_ms avg: 0.07
total_ms avg:     1.38
```

Context Engine's own contribution is sub-2ms at this scale — negligible
next to any local (seconds) or cloud (network round-trip) model response
time. `ContextBuildTrace` now carries `discovery_ms`/`retrieval_ms`/
`total_ms` permanently (small, additive fields — no telemetry framework);
`nexa.conversation.context_projection`/`nexa.realtime.context_projection`
log a one-line `logger.debug(...)` summary per call (discovery/retrieval/
projection/total ms + item count — never raw content). One real end-to-end
local run (§16) took **84.8s total**, entirely explained by Ollama's own
documented cold-model-load time (R0021: ~29-40s cold TTFT) plus real
generation — confirming Context Engine integration adds no material
latency to the actual conversation path.

For cloud voice specifically: since the accepted path never calls
`ContextEngine.build_context()` at all (§6), there is **zero** added
latency to the real cloud voice session-start path from this checkpoint.

## 15. Tests / full regression

```
tests/test_core_context_derivation.py             13 passed (new)
tests/test_conversation_context_projection.py      13 passed (new)
tests/test_realtime_context_projection.py           17 passed (+7 new)
tests/test_conversation_session.py                   24 passed (+6 new)
tests/test_bootstrap_context_runtime.py                5 passed (new)
```
= 44 new/changed tests (72 total across these 5 files).

§22 regression battery (Context Engine, Memory, ConversationSession,
snapshot, router, simple cloud voice, dependency-direction, privacy,
identity): **272 passed**, zero failures.

Full suite (`./.venv/bin/python3 -m pytest tests/ -q`, real run, sandbox
disabled): **1399 passed, 7 skipped, 1 pre-existing unrelated failure**
(`tests/test_voice_architecture.py` — the same paused R0068-R0070
`scheduled_aec_reference` issue present before this checkpoint, untouched
by this work). `ruff check` and `py_compile` clean on every changed file.
`git diff --check` clean (see commit gate below).

## 16. Real-hardware result

**Not performed.** This environment has no attached reSpeaker/audio
hardware — it is a development sandbox, not the physical Pi. Per
instruction, this is stated explicitly rather than claimed: **no
real-hardware cloud voice acceptance was run this checkpoint.** One real
LOCAL-provider turn WAS exercised end to end against a live Ollama
instance (§14), which is the closest available substitute for "real
inference" verification given this task's actual scope (the cloud path
was correctly found to need no code changes at all, §6).

## 17. Known limitations

- Cloud realtime path has **zero** observable Context Engine integration
  in the current frozen architecture (audited fact, §6) — a future,
  separately-scoped task would need to change *when* the cloud snapshot
  is built (e.g., a resumption/reconnect flow with real history) to make
  this adapter's cloud contribution ever actually fire; the adapter
  itself needs no further changes to support that when it exists.
- Subject-hint derivation remains a plain substring/stopword heuristic
  (unchanged limitation from R0075) — English-only stopword list; no
  Polish stopwords, matching this repo's bilingual PL/EN persona but not
  yet covered here (a real limitation worth a future look, not addressed
  in this checkpoint's scope).
- No real-hardware voice acceptance performed (§16).
- `provider_window` (M2.5B.2 voice KV-cache path) does not honor
  `context_provider` — deliberately out of scope (§1.A, §2).

## 18. Exact changed files

```
NEW:
  src/nexa/core/context/derivation.py
  tests/test_core_context_derivation.py
  tests/test_conversation_context_projection.py
  tests/test_bootstrap_context_runtime.py
  docs/reports/R0077_m3_3_runtime_integration_20260917.md (this file)

MODIFIED:
  src/nexa/core/context/models.py           (+ ContextBuildTrace timing fields)
  src/nexa/core/context/engine.py            (+ time.monotonic() instrumentation)
  src/nexa/core/context/__init__.py           (+ derive_context_request/derive_subject_hints exports)
  src/nexa/conversation/session.py             (+ send()'s context_provider hook,
                                                  build_context()'s system_prompt_override)
  src/nexa/conversation/context_projection.py    (NEW file, local rendering adapter)
  src/nexa/realtime/context_projection.py          (+ make_cloud_snapshot_builder())
  src/nexa/bootstrap.py                              (+ ContextRuntime, build_default_context_runtime())
  apps/nexa_chat.py                                    (wired: the first production entrypoint
                                                          to actually call ContextEngine)
  tests/test_realtime_context_projection.py             (+ 7 tests)
  tests/test_conversation_session.py                     (+ 6 tests)
  docs/reports/R0076_m3_3_context_engine_knowledge_awareness_implementation_20260917.md
                                                            (test-count correction, §0)
  docs/CURRENT_STATE.md, docs/ROADMAP.md                    (checkpoint update, below)

UNTOUCHED (confirmed by audit + source-scan tests):
  apps/nexa_cloud_voice_simple.py
  src/nexa/realtime/gemini/simple_conversation.py
  src/nexa/realtime/gemini/runtime.py (paused M2.6B)
  everything under Pipecat/VAD/AEC/barge-in/audio transport
```

## 19. Documentation updated

- This report.
- `docs/reports/R0076_...md` — test-count correction (§0).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — M3.3 now distinguishes
  "foundation implemented" from "runtime integration: local wired /
  cloud audited-not-wired" explicitly, not claimed as full production
  integration.

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO.

## NEXT RECOMMENDED ACTION (do not start without review)

M3.4 — Personality + Relationship, per the approved ROADMAP.md sequence.
Alternatively, a future scoped task could revisit cloud realtime Context
Engine integration specifically if/when the cloud path grows a real
per-turn or reconnect-time snapshot rebuild — not needed for M3.4 itself.
