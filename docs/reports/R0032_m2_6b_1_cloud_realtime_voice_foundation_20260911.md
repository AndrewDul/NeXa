# R0032 — M2.6B.1: Production Cloud Realtime Voice — Provider-Agnostic Foundation

## TASK RESULT

**PASS (checkpoint).** The provider-agnostic M2.6B foundation defined by
ADR-0004 (Accepted, Amendment 1 Accepted) is implemented, tested, and
committed. No `GeminiLiveProvider`, no live Gemini connection, no
`ConversationRouter`, no canonical cloud write-path — those are M2.6B.2+.
The frozen local voice path (M2.5B, `R0029`) is unmodified: zero existing
`src/nexa/**` files were touched, only new files under `src/nexa/realtime/`
were added.

## WHAT I DID

1. Re-verified repo state (`git status`, `git log -5`, HEAD/`origin/main`
   relation), re-read `AGENTS.md`, `CURRENT_STATE.md`, `ROADMAP.md`,
   `ADR-0004` (incl. Amendment 1), `R0030`, `R0031`, and the existing
   `src/nexa/conversation/`, `src/nexa/providers/`, `src/nexa/voice*/`,
   `src/nexa/config.py` sources before writing anything.
2. Performed the **mandatory Gemini/Pipecat startup-sequencing source
   audit** against the *installed* `pipecat-ai==1.8.1`
   (`services/google/gemini_live/llm.py`, 2180 lines) and
   `google-genai==2.22.0` — see
   `docs/research/m2_6_cloud_realtime_voice/m2_6b_gemini_startup_sequencing_source_audit_20260911.md`
   for the full nine-question audit. **No cloud call.**
3. Implemented the provider-agnostic M2.6B foundation in a new
   `src/nexa/realtime/` package (+ `src/nexa/realtime/gemini/` for the two
   pieces that need no cloud SDK):
   - `provider.py` — `RealtimeVoiceProvider` ABC (peer of `ModelProvider`,
     never a subtype), `ProviderReadiness`, `RealtimeProviderCapabilities`,
     the typed provider->NeXa event stream, `RealtimeProviderError` /
     `RealtimeProviderFailedError`.
   - `policy.py` — `ConversationPolicy` (`LOCAL_ONLY` default,
     `CLOUD_PREFERRED`, `AUTO` provisional), `ActiveProvider`,
     `DistributionMode`, `ProviderEligibilityPolicy` (Amendment 1 §1 —
     `distribution_mode` / `billing_verified`, **not** a key property),
     env-var-only fail-closed loaders.
   - `snapshot.py` — `CloudContextSnapshot` + `build_cloud_context_snapshot`:
     a pure, bounded, allow-listed projection of `ConversationSession`
     (role card + language preference + last N turns + policy metadata;
     never persona verbatim, memory, credentials, or device internals).
   - `inbound_audio_buffer.py` — `InboundAudioBuffer`, the NeXa-owned
     protection for Pipecat issue #5465 (bounded by time *and* bytes,
     drop-oldest overflow with metrics/logging, per-frame sequence id so a
     reconnect never re-delivers already-flushed audio).
   - `usage.py` — `ProviderUsageEvent` / `SessionUsageAggregate` /
     `UsagePriceTable` (authoritative counts only, no raw audio, cost
     estimate always labelled and price-table-driven).
   - `reconnect.py` — `ReconnectController`: deterministic reconnect
     policy state machine (age-timer, `GoAway` deadline, only-keep-a-
     `resumable=true`-handle, bounded backoff+jitter, `RESUMED` /
     `FRESH_SESSION_REQUIRED` / `FAILED_RETRY` / `FAILED_TERMINAL`
     outcomes) — **no socket, no Gemini-specific choreography**.
   - `gemini/credentials.py` — env-var-first + XDG-secrets-file
     `CredentialSource`, redaction, unsafe-file-permission warning, **no**
     paid/unpaid inference from the key.
   - `gemini/voice.py` — `warm_female -> Sulafat` provider-agnostic voice
     preference mapping (+ the 3 recorded, untested alternatives).
   - `realtime/__init__.py` / `realtime/gemini/__init__.py` — re-exports
     only; neither imports anything cloud-specific.
4. Added 9 new focused test modules (`tests/test_realtime_*.py`, 73 tests)
   and one import-isolation subprocess test proving no cloud dependency is
   pulled in.
5. Wrote the source-audit engineering note (item 2) and this report.
6. Updated `docs/CURRENT_STATE.md` and `docs/ROADMAP.md`:
   `M2.6B: IN PROGRESS`, `M2.6B.1: provider-agnostic foundation
   implemented`, next = `M2.6B.2`. Also fixed the stale `ROADMAP.md` header
   `## M0 — Foundation ✅ (current)` (M0 has not been the active milestone
   since M1; nothing else in that history was rewritten).

## WHAT I VERIFIED

- **Source audit (VERIFIED FACT, direct read of the installed package,
  2026-09-11):** `_get_history_config()` already returns
  `HistoryConfig(initial_history_in_client_content=True)` unconditionally
  (Vertex subclass excepted); `_handle_context` triggers `_reconnect()` if
  a later context's effective `system_instruction` differs from the
  init-provided one (structural proof that config is immutable on an open
  connection — confirms Amendment 1 §2); `_handle_msg_resumption_update`
  only stores a handle `if update.resumable and update.new_handle`
  (confirms Amendment 1 §4's own rule already exists in the library); no
  `GoAway` handling exists anywhere in the file (case-insensitive search,
  confirms the existing ADR-0004/R0030/R0031 finding, unchanged);
  `LLMRunFrame` -> `LLMUserAggregator._handle_llm_run` ->
  `push_context_frame()` -> `LLMContextFrame` -> `_handle_context` is the
  exact, only path that makes `GeminiLiveLLMService` aware of any context
  (confirms the R0031 M2.6A root-cause finding at the source level).
  **No ADR-0004 architectural decision was contradicted** — see the audit
  note's "Conclusion".
- **Local voice freeze:** `git diff --name-only -- src/nexa` before vs.
  after this task shows **zero existing files modified** — every change
  under `src/nexa/` is a new file under `src/nexa/realtime/`.
- **No cloud dependency on import:** a genuinely isolated subprocess
  (`python -I`, so `PYTHONPATH`/site dirs are ignored — `nexa` is found
  only via its editable install) importing `nexa.realtime` and
  `nexa.realtime.gemini.{credentials,voice}` shows `'google.genai' not in
  sys.modules` and `'google' not in sys.modules`; `import nexa` alone does
  not import `nexa.realtime`; `nexa.realtime.gemini.service` (the future
  `GeminiLiveProvider`) does not exist yet, so no path can import it.
  `google-genai 2.22.0` **is** installed in this venv (from the M2.6A
  research spike) but this proves it is not *imported*, not that it is
  absent (`pyproject.toml` still lists only `pipecat-ai[local]==1.8.1`).
- **Ruff, whole repo:** 82 pre-existing errors, all in
  `scripts/m1_bench/` and unrelated research scripts — confirmed
  pre-existing by `git stash`-ing this task's new files and re-running
  ruff on the base commit (`986e65a`): identical 82-error count, none in
  `src/nexa/realtime/` or the new tests (`ruff check
  src/nexa/realtime tests/test_realtime_*.py` alone: **All checks
  passed**).
- **`pip check`:** clean, no broken requirements.

## TESTS

New: 9 files, **73 tests**, all real behaviour (no network, real
`ConversationSession` + `FakeModelProvider` for the snapshot tests, a real
temp-file-backed credential source, an injected deterministic clock for the
buffer/reconnect tests, a seeded `random.Random` for backoff determinism, a
genuinely isolated subprocess for the import-graph proof):

- `test_realtime_provider.py` (7) — ABC is a peer of `ModelProvider`
  (neither is a subclass of the other); a minimal fake implementation
  proves the contract is usable end-to-end; `ProviderReadiness` covers the
  documented lifecycle.
- `test_realtime_policy.py` (14) — `LOCAL_ONLY` default; invalid
  `NEXA_CONVERSATION_POLICY` fails closed; `ActiveProvider` has no
  `CLOUD_ONLY`; `ProviderEligibilityPolicy.cloud_allowed_for` matrix
  (`DEVELOPMENT` always allowed; `DISTRIBUTED` + EEA/CH/UK requires
  `billing_verified`; `DISTRIBUTED` elsewhere unrestricted); invalid env
  values fail closed; structural proof the eligibility type has **no**
  key/credential/paid/tier-named field.
- `test_realtime_snapshot.py` (9) — pure/deterministic; never contains the
  real persona or a planted "credential" marker string; bounded by turn
  count *and* char budget; never truncates a single turn's own text; empty
  history -> empty snapshot; language preference optional; role/order
  preserved.
- `test_realtime_inbound_audio_buffer.py` (9) — construction rejects
  non-positive bounds; all-frames-flushed-in-order on the READY
  transition; byte overflow drops oldest and preserves freshest; time
  overflow evicts stale frames; overflow always logs a `WARNING` (never
  silent); buffered bytes never exceed the bound across 1000 captures;
  repeated flush never re-delivers already-delivered frames; a simulated
  reconnect-mid-flush sequence delivers every frame exactly once.
- `test_realtime_reconnect.py` (16) — age-timer trigger math; `GoAway`
  deadline math (margin, never negative); only a `resumable=true` +
  non-empty handle is ever retained, a later bad update never erases the
  last good one; the full `attempt()` outcome matrix
  (`FAILED_RETRY`->`FAILED_TERMINAL`, `FRESH_SESSION_REQUIRED` with no
  handle, `RESUMED` / `FRESH_SESSION_REQUIRED` with a handle);
  `on_connected` resets the attempt counter; backoff is bounded and
  reproducible with a seeded RNG.
- `test_realtime_usage.py` (7) — aggregation accumulates correctly and
  rejects a mismatched provider/session id; cost estimate is computed only
  from a supplied `UsagePriceTable` (never hard-wired); structural proof
  neither `ProviderUsageEvent` nor `SessionUsageAggregate` has an
  audio/pcm-shaped field.
- `test_realtime_gemini_credentials.py` (9) — env beats file; file
  fallback works; missing credential raises a typed error carrying no
  value; unsafe (`0o644`) file permissions warn, safe (`0o600`) do not;
  `repr()`/`str()` never leak the raw value, only `reveal()` does;
  structural proof `GeminiCredential` has no paid/tier/billing field.
  **Uses a fake key throughout — the real operator key was never touched
  or copied into a test.**
- `test_realtime_gemini_voice.py` (4) — default maps to `Sulafat`; the 3
  recorded alternatives map correctly; an unknown preference raises rather
  than silently defaulting; the mapping is a function of the preference,
  not a hard-coded constant.
- `test_realtime_no_cloud_dependency.py` (4) — the four import-graph gates
  described above, each run in a genuinely isolated subprocess.

Full repository suite: `unittest discover -s tests` = **812 tests, OK
(skipped=7)** (739 baseline + 73 new; zero regressions). `ruff check
src/nexa/realtime tests/test_realtime_*.py` clean; whole-repo ruff
unchanged pre-existing 82 (none touch this task's files). `pip check`
clean. `git diff --check` clean (see CHECKS below).

## UNRESOLVED

- **`GeminiLiveProvider`, `ConversationRouter`, the canonical cloud
  write-path (`ConversationSession.record_external_exchange`), HYBRID audio
  wiring, cloud speaker/AEC playback, production barge-in on the cloud
  path, the operator cloud probe app, the `cloud-gemini` `pyproject.toml`
  extra, function calling/tools, memory, identity, UI, and the real `AUTO`
  classifier — all explicitly M2.6B.2+ (per ADR-0004's ordered plan and per
  this task's charter).**
- One design nuance flagged (not blocking, not a contradiction) in the
  source-audit note: on a resumption-failure reconnect *without* a
  resumable handle, Pipecat's own `_handle_session_ready` already re-seeds
  from its internally-tracked `self._context`; M2.6B.2 must decide whether
  to let that run as-is or intercept it with a NeXa-rebuilt
  `CloudContextSnapshot` (ADR-0004 Decision I).
- No real Gemini connection, no real reconnect, no real hardware test —
  none were in scope for this checkpoint.

## DOCUMENTATION / REPORTS UPDATED

- `docs/research/m2_6_cloud_realtime_voice/m2_6b_gemini_startup_sequencing_source_audit_20260911.md`
  (new).
- `docs/reports/R0032_m2_6b_1_cloud_realtime_voice_foundation_20260911.md`
  (this report).
- `docs/CURRENT_STATE.md` — `M2.6B: IN PROGRESS`; `M2.6B.1` recorded as
  the provider-agnostic foundation, implemented; next = `M2.6B.2`.
- `docs/ROADMAP.md` — same, plus the stale `## M0 — Foundation ✅
  (current)` header corrected (M0 is COMPLETE and no longer the active
  milestone; no other roadmap history rewritten).

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

YES — direct inspection of the installed `pipecat-ai==1.8.1` source (no
network fetch; static read of already-installed files) for the mandatory
startup-sequencing audit. No new official-docs fetch was needed beyond
what ADR-0004 Amendment 1 already verified on 2026-09-10.

## CURRENT VERIFIED STATE

- Local realtime voice (M2.5B, `R0029`) — unchanged, byte-for-byte,
  `OPERATOR-CONFIRMED`.
- M2.6A feasibility — PASS / OPERATOR-CONFIRMED, `Sulafat` OPERATOR-
  CONFIRMED (unchanged).
- `ADR-0004` + **Amendment 1** — Accepted (unchanged; no reopening — this
  checkpoint's source audit confirmed, not contradicted, the architecture).
- **M2.6B — IN PROGRESS.** `M2.6B.1` (this report): the provider-agnostic
  `RealtimeVoiceProvider` boundary, `ConversationPolicy` /
  `ProviderEligibilityPolicy`, `CloudContextSnapshot`, the #5465 inbound
  audio buffer, usage telemetry types, a deterministic
  `ReconnectController`, and the Gemini credential/voice-mapping pieces
  that need no cloud SDK are implemented and tested (73 new tests, 812
  total, 0 regressions). No live Gemini connection exists yet.
- `pip install .` (no extra) remains Google-cloud-free; `pyproject.toml`
  unchanged (no dependency-file edit in this checkpoint).

## FILES CHANGED

- **New:** `src/nexa/realtime/{__init__,provider,policy,snapshot,
  inbound_audio_buffer,usage,reconnect}.py`,
  `src/nexa/realtime/gemini/{__init__,credentials,voice}.py`.
- **New tests:** `tests/test_realtime_{provider,policy,snapshot,
  inbound_audio_buffer,reconnect,usage,gemini_credentials,gemini_voice,
  no_cloud_dependency}.py` (9 files, 73 tests).
- **New:**
  `docs/research/m2_6_cloud_realtime_voice/m2_6b_gemini_startup_sequencing_source_audit_20260911.md`,
  this report.
- **Modified (docs only):** `docs/CURRENT_STATE.md`, `docs/ROADMAP.md`
  (M2.6B IN PROGRESS / M2.6B.1 recorded / next M2.6B.2; stale
  `## M0 — Foundation ✅ (current)` header corrected).
- **Unmodified:** every existing file under `src/nexa/**` (verified via
  `git diff --name-only -- src/nexa` showing only new paths under
  `src/nexa/realtime/`); `pyproject.toml`; every existing `tests/**` file.

## CHECKS (this commit)

- `git diff --check` — clean (working tree and staged).
- Secret scan — clean. (One coincidental false-positive during drafting: a
  test fixture originally named `SECRET_CREDENTIAL = "AQ.SECRET-KEY-
  MARKER-DO-NOT-LEAK"` matched a scan pattern by coincidence of shape, not
  content — renamed to `FAKE_CREDENTIAL_MARKER` before commit; no real key
  material anywhere in this change.)
- `ruff check src/nexa/realtime tests/test_realtime_*.py` — all checks
  passed. Whole-repo `ruff check .` — 82 pre-existing errors, confirmed
  identical (via `git stash` back to the base commit `986e65a`) and all in
  `scripts/m1_bench/`/unrelated research scripts — none in this task's
  files.
- `pip check` — clean, no broken requirements.
- `python -m unittest discover -s tests` — **812 tests, OK (skipped=7)**
  (739 baseline + 73 new; zero regressions).
- Import isolation (`python -I` subprocess, 4 gates) — `nexa.realtime` and
  `nexa.realtime.gemini.{credentials,voice}` never import `google.genai`
  or `google`; `import nexa` alone never imports `nexa.realtime`;
  `nexa.realtime.gemini.service` does not exist yet.
- `git diff -- src/nexa` — only new files under `src/nexa/realtime/`, zero
  existing files touched. `git diff -- pyproject.toml` — empty. No
  dependency file changed. No Gemini call. No hardware test.

## COMMIT HASH

`e4b84ec` — `feat(m2.6b.1): provider-agnostic Cloud Realtime Voice
foundation (R0032)`.

Prior tip: `986e65a` (ADR-0004 Amendment 1, cumulative doc-consistency
commit).

## GIT STATUS

Branch `main`, ahead of `origin/main` (`505627f`) by 3 commits
(`986e65a`, `e4b84ec`, and this hash-record follow-up). Working tree clean
after commit. Not pushed.

## NEXT RECOMMENDED ACTION

**M2.6B.2 — `GeminiLiveProvider` + canonical cloud-turn integration**,
using the exact sequencing this report's source audit established:
construct `GeminiLiveLLMService` with `CloudContextSnapshot.system_instruction`
supplied at construction time (never mutated on an open connection), an
initial `LLMContext` seeded from `snapshot.recent_turns`,
`inference_on_context_initialization=False`, the one-time `LLMRunFrame`
kickoff, then wire the additive
`ConversationSession.record_external_exchange` and `ConversationRouter` on
top. Add the `cloud-gemini` optional `pyproject.toml` extra only when
M2.6B.2 actually needs `google-genai` at runtime (not merely for the tests
this checkpoint added, which needed none). Local voice stays frozen; no
push.
