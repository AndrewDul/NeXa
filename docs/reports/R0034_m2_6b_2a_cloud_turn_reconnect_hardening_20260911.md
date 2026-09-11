# R0034 — M2.6B.2A: Pre-Hardware Cloud-Turn / Reconnect Hardening

## TASK RESULT

**PASS (checkpoint).** A real, serious correctness gap in M2.6B.2's
(`R0033`) mid-turn readiness handling was found and fixed, the provider ->
NeXa event mapping was completed and verified against real Pipecat frame
types (including a second gap — error frames travel **upstream**, which
the original single downstream tap could never see), the "no manual
injection" concern was closed with a real event-driven integration test,
five turn-ordering/interruption permutations were tested (a sixth was
proven impossible from source, not assumed), the fresh-snapshot-on-
resumption-failure mechanism was made precise, and two stale-wording
issues in `R0033`/`CURRENT_STATE.md` were corrected. **No cloud call, no
hardware test, no reconnect-duration test.** Local voice remains frozen —
zero existing `src/nexa/**` files outside `src/nexa/realtime/**` and
`src/nexa/conversation/session.py`'s prior additive method were touched
(this checkpoint adds no new touch to `conversation/session.py`).

## MID-TURN READINESS LOSS RESULT

**Confirmed real bug, now fixed.** Before this pass,
`GeminiLiveProvider.user_turn_start()` checked `readiness` only once, at
call time, to decide "send live" vs. "buffer via `UtteranceFramer`" — it
never recorded that decision anywhere `send_user_audio`/`user_turn_end`
could see it later. Consequence: a turn that started live (readiness was
`READY`) and then lost readiness *mid-utterance* would have every
subsequent `send_user_audio` call reach the framer's `capture_audio`,
which rejected it as an **orphan** (no `activity_start` had ever been
recorded there) — **silent user-speech loss** — and the eventual
`user_turn_end()` would be silently ignored too (no open turn in the
framer), so Gemini would also never receive a matching `activity_end` for
the live-started utterance.

**Fix:** a new `self._live_turn_open` flag tracks whether the *current*
utterance's `activity_start` actually went out live. If readiness drops
before `user_turn_end()`, the **first** piece of audio that arrives while
not ready triggers a deterministic **abort-and-restart**: the live segment
is marked aborted (it will never receive a live `activity_end` — logged at
`WARNING`, not silent) and every frame from that point on becomes a
**new, self-contained buffered utterance** (its own `activity_start` /
audio / `activity_end`, flushed once `READY` returns). Chosen over trying
to "resume" the same live utterance because that would require either (a)
silently reusing a stale `activity_start` with no assurance the connection
state was ever consistent, or (b) risking replay/duplication of already-
sent audio — both rejected per the charter. The **production policy**,
stated explicitly: **a connectivity loss exactly mid-utterance may split
one utterance into two from Gemini's perspective, but never silently
drops user audio and never delivers the same audio twice.**

Proven with 4 new tests (`TestMidTurnReadinessLoss` in
`test_realtime_gemini_service.py`, against the real Pipecat pipeline):
1. **READY → outage mid-turn → READY**: audio sent before the drop
   reaches the fake service exactly once (live); audio sent during the
   drop is buffered, then flushed as a second, complete, correctly-ordered
   utterance; the byte set delivered is the union of both with **zero**
   duplicates; the aborted live segment gets a start but never a live end,
   the buffered segment gets its own start/audio/end.
2. **READY → immediate outage before first audio**: no data was ever
   produced, so none is lost; the framer stays empty; no stray frames are
   queued.
3. **Mid-turn outage → fresh session required**: `take_pending_audio()`
   (new method) extracts the not-yet-delivered PCM chunks from the
   about-to-be-discarded provider (destructively — a second call returns
   `[]`, so nothing is ever redelivered from the old instance), and
   replaying them into a brand-new provider (fresh `user_turn_start` /
   `send_user_audio` / `user_turn_end`) delivers them correctly with fresh
   framing — never the stale instance's markers.
4. **Stale provider context never seeds the fresh session** (finishes the
   FRESH-SNAPSHOT item below) — see that section.

## USER TRANSCRIPTION EVENT PATH

Verified end-to-end against the real Pipecat frame classes: Gemini input
transcription arrives as a `TranscriptionFrame` (final, respecting its own
`finalized` attribute — previously hard-coded to always `True`, corrected)
or an `InterimTranscriptionFrame` (partial — previously not handled at
all, now mapped to `UserTranscriptionEvent(final=False)`). Both are
distinct Pipecat classes (siblings of `TextFrame`, not parent/child), so
`isinstance` checks needed both, in the right order (interim checked
first, since a broader/looser check could otherwise misclassify). The
full path `TranscriptionFrame → UserTranscriptionEvent(final=True) →
ConversationRouter.handle_provider_event → CloudTurnAccumulator.
on_user_transcription → (on GenerationCompleteEvent)
commit_cloud_turn → ConversationSession.record_external_exchange` is
proven by `TestNoManualInjectionFullCloudTurn` — see below.

## FULL PROVIDER EVENT MAP

| Pipecat/Gemini source frame | `GeminiLiveProvider` typed event | `ConversationRouter` handling | `CloudTurnAccumulator` effect | Canonical `ConversationSession` effect |
|---|---|---|---|---|
| (internal `_ready_for_realtime_input` poll, not a frame) | `ReadinessChangedEvent` | informational (M2.6B.3: reconnect policy input) | none | none |
| `InterimTranscriptionFrame` | `UserTranscriptionEvent(final=False)` | `on_user_transcription(final=False)` | records provisional text, does not close the turn | none yet |
| `TranscriptionFrame` (`finalized` attr respected) | `UserTranscriptionEvent(final=<frame.finalized>)` | `on_user_transcription(final=...)` | `final=True` → moves to `AWAITING_ASSISTANT` | none yet (commits at generation-complete) |
| `TTSTextFrame` | `AssistantTranscriptionEvent(final=False)` | `on_assistant_transcription` | accumulates delta (ignored once interrupted — new fix, see below) | none yet |
| `TTSAudioRawFrame` | `AssistantAudioEvent` | not router state — playback layer (M2.6B.3) | n/a | n/a |
| `InterruptionFrame` (server ACK) | `ProviderInterruptionEvent` | `on_interruption()` | marks `interrupted=True`; freezes further assistant-text accumulation | none yet |
| `LLMFullResponseEndFrame` | **`GenerationCompleteEvent`** (new — previously the ambiguous `AssistantTranscriptionEvent(text="", final=True)`) | `commit_cloud_turn()` | commits the turn — exactly once | one canonical exchange (or user-only) appended |
| `usage_metadata` attr (any message) | `ProviderUsageEvent` | telemetry, not turn state | n/a | n/a |
| `session_resumption_update` attr | (not queued — stored as `latest_resumption_handle` property, only `resumable=true`) | M2.6B.3 will read this on reconnect | n/a | n/a |
| `ErrorFrame` (recoverable) | `RealtimeProviderError` | M2.6B.3 policy | n/a | n/a |
| `FatalErrorFrame` / permanent `ErrorFrame` | `RealtimeProviderFailedError` + `readiness → FAILED` | `handle_cloud_session_lost()` | commits whatever was accumulated (spoken-prefix rule) | user-only or full exchange, then `active_provider → LOCAL` |
| (no Pipecat frame — feature absent) | — | — | — | `GoAway` **confirmed still unhandled** in installed Pipecat 1.8.1 (R0032 finding, unchanged) |

**Correction found while building this table:** `push_error()` (Pipecat's
own error-reporting call, used internally by `GeminiLiveLLMService`) pushes
its `ErrorFrame`/`FatalErrorFrame` **upstream**
(`frame_processor.py:push_error_frame` — "Push error upstream"), not
downstream. The original M2.6B.2 pipeline
(`[user_agg, llm, down_tap, asst_agg]`) placed its only event tap *after*
`llm` in the downstream direction — it could **never** have seen an error
frame. Fixed by adding a second tap, `up_tap`, placed *before* `user_agg`
(pipeline is now `[up_tap, user_agg, llm, down_tap, asst_agg]`), mirroring
the M2.6A research probe's own upstream/downstream dual-tap pattern.
Proven with a dedicated test that pushes a `FatalErrorFrame` **upstream**
from the fake service and confirms it reaches `provider.events()` as a
`RealtimeProviderFailedError` and flips `readiness` to `FAILED`.

`FatalErrorFrame` itself is deprecated since Pipecat 1.8.0 (removal
targeted for 2.0.0; `ErrorFrame` + `push_error(...,
force_treat_as_permanent=True)` is the documented replacement) — still
present and usable in the installed 1.8.1, so the mapping is kept, with
this deprecation noted for whenever Pipecat is upgraded past 1.8.x.

## TURN ORDERING / INTERRUPTION RESULT

Five permutations proven with `TestTurnOrderingPermutations` (event-driven
via `ConversationRouter.handle_provider_event`, never manual injection):

- **A** (user final → assistant partial → assistant final → turnComplete):
  one exchange, assistant text is the full accumulated delta.
- **B** (interruption wins over a late server ACK): local
  `router.on_interruption()` + `set_spoken_prefix(...)` fire first (as
  they would in production, driven by NeXa's own local barge-in, never a
  provider event); the server's own `InterruptionFrame` ACK and a further,
  late `TTSTextFrame` delta arrive *after* — the late delta is now
  **discarded**, not appended (see the `CloudTurnAccumulator` fix below);
  the committed assistant text is exactly the spoken prefix, marked
  `interrupted=True`; a further `commit_cloud_turn()` call for the same
  (already-committed) turn commits nothing more.
- **C** (turnComplete before a delayed final transcription) — **proven
  IMPOSSIBLE from the installed Pipecat source, not assumed**: in
  `GeminiLiveLLMService._connection_task_handler`, messages are processed
  strictly in arrival order from one `async for message in turn:` loop,
  and — per that method's own comment ("server_content fields are NOT
  mutually exclusive — Gemini 3.x can bundle multiple content fields and
  turn_complete on the same message, so process the content-bearing
  fields before closing the turn") — even a message that bundles *both*
  fields together is still handled `input_transcription` first, then
  `turn_complete`. A later message's `turn_complete` therefore can never
  be processed before an earlier (or same-message) final transcription.
  The router is still proven safe in this test as defence in depth: a
  premature `GenerationCompleteEvent` with no user text yet commits
  nothing (the accumulator's turn has `user_text=None`), and the resulting
  no-op-committed turn cannot later be double-committed.
- **D** (provider dies after user transcription, before any assistant
  output): `handle_cloud_session_lost()` commits `COMMITTED_USER_ONLY` —
  the user turn is retained, no assistant turn appears.
- **E** (provider dies after some assistant audio was actually spoken):
  the playback layer's high-water mark (`set_spoken_prefix`, simulated
  here) is what gets committed — `COMMITTED_EXCHANGE` with exactly the
  spoken prefix, not the full transcript-so-far.

**Second `CloudTurnAccumulator` fix found while building scenario B:**
`on_assistant_transcription` previously only checked `is_terminal`
(`COMMITTED`/`ABANDONED`) before appending — a late transcription delta
arriving *after* `on_interruption()` but *before* commit would still be
appended, silently growing the stored text past the actually-spoken
prefix set by `set_spoken_prefix`. Fixed: `on_assistant_transcription` now
also refuses once `cur.interrupted` is `True`. A dedicated unit test
(`test_late_assistant_transcription_after_interruption_is_ignored`,
`test_realtime_turn.py`) proves this at the accumulator level, independent
of the provider/router integration test.

## FRESH SNAPSHOT RECONNECT MECHANISM

Made precise (the `R0033` "resolved explicitly" / "remains open" tension
is resolved — see R0033 DOC CORRECTIONS): the mechanism is **destroy and
recreate**, not in-place repair. A new `GeminiLiveProvider` instance is
constructed with a freshly built `CloudContextSnapshot`
(`ConversationRouter.request_fresh_snapshot_after_resumption_failure`,
unchanged from R0033); it builds its own brand-new `LLMContext` from that
snapshot in `start()` and holds no reference of any kind to any previous
instance's `self._llm` / `self._llm._context` — so a stale Pipecat-owned
context **cannot** structurally seed the new session; there was never a
code path by which it could. This is documented explicitly in
`gemini/service.py`'s module docstring now, and proven by a dedicated test
(`test_stale_provider_context_never_seeds_the_fresh_session`): an OLD
provider seeded with turns "A/OLD" is stopped; canonical
`ConversationSession` (independently, turns "A/B/C") produces a fresh
snapshot; a brand-new provider started from that fresh snapshot has "turn
C"/"reply C" in its seeded context and **never** "OLD stale reply".

No new Pipecat wrapper/subclass seam was needed — the existing
`service_class` injection point and `CloudContextSnapshot` boundary were
already sufficient; this checkpoint only had to state the mechanism
precisely and prove it, not build anything new for it.

**Still explicitly deferred to M2.6B.3** (unchanged from R0033): the
*live* trigger for this path — `GeminiLiveProvider` does not yet call into
`ReconnectController` on a real connection error, and there is no
GoAway/age-timer-driven reconnect wired up. This checkpoint hardens what
happens *once* NeXa has decided a fresh session is required and *while*
readiness is degraded mid-turn; it does not yet decide *when* that
happens from a real socket.

## FILES CHANGED

- **Modified:** `src/nexa/realtime/gemini/service.py` (mid-turn
  readiness fix — `_live_turn_open`, `take_pending_audio()`; `up_tap`
  added; `InterimTranscriptionFrame`/`ErrorFrame`/`FatalErrorFrame`
  handling; `GenerationCompleteEvent` replaces the ambiguous empty-text
  `AssistantTranscriptionEvent`; module docstring records the
  destroy-and-recreate fresh-session mechanism).
- **Modified:** `src/nexa/realtime/provider.py` (+`GenerationCompleteEvent`;
  `RealtimeProviderFailedError` added to the `ProviderEvent` union — it
  was usable as an exception before but not actually part of the declared
  event-stream type).
- **Modified:** `src/nexa/realtime/router.py` (+`handle_provider_event` —
  the single path by which a provider's event stream may reach the
  router/session; closes the "manual injection" gap).
- **Modified:** `src/nexa/realtime/turn.py` (`on_assistant_transcription`
  now also refuses once `cur.interrupted` — the second fix above).
- **Modified:** `src/nexa/realtime/__init__.py` (+`GenerationCompleteEvent`
  export).
- **Modified (tests):** `tests/test_realtime_gemini_service.py` (+16
  tests across 4 classes — `TestEventTranslation` extended,
  `TestMidTurnReadinessLoss` and `TestNoManualInjectionFullCloudTurn` and
  `TestTurnOrderingPermutations` new; fake service extended with
  `received_audio`/`turn_starts`/`turn_ends` counters and
  `emit_user_transcription`/`emit_generation_complete`/
  `emit_interruption`/`emit_fatal_error` helpers).
- **Modified (tests):** `tests/test_realtime_turn.py` (+1 test).
- **Docs corrected:** `docs/reports/R0033_m2_6b_2_gemini_provider_canonical_cloud_turn_20260911.md`
  (stale "88 new tests across 6 new files" corrected to the actual 57+1=58
  in WHAT I DID, matching what TEST RESULTS already said); this report;
  `docs/CURRENT_STATE.md`; `docs/ROADMAP.md`.
- **Unmodified:** every other existing `src/nexa/**` file, including
  `conversation/session.py` (no change needed this checkpoint — the
  canonical write path itself was correct; only the provider-side event
  production and the accumulator's interruption handling needed fixing).

## TEST RESULTS

+16 new tests this checkpoint (886 total — 870 M2.6B.2 baseline + 16, **0
regressions**). `ruff check` on every modified file: clean. Whole-repo
`ruff check .`: 82 pre-existing errors, unchanged (confirmed via the same
`git stash`-to-base-commit method as R0032/R0033). `pip check`: clean.
`git diff --check`: clean. Secret scan: clean.

## R0033 DOC CORRECTIONS

- **Stale test count** (WHAT I DID, item 5): "Added 88 new deterministic
  tests across 6 new files + 1 extended file" corrected to the actual
  count — 57 new tests across 5 new files, +1 net in one extended file
  (58 net new) — matching what R0033's own TEST RESULTS section already
  stated correctly. No evidence was changed, only the stale prose.
- **Fresh-snapshot wording tension**: R0033 said both "resolved
  explicitly" and, later, "the stale Pipecat context vs. fresh NeXa
  snapshot design question remains open" for the same topic. Resolved
  here: the *architecture* decision (successful resumption may use
  Pipecat's restored context; failed/unsafe resumption uses a fresh NeXa
  snapshot) was indeed already decided in R0033/ADR-0004. What was
  genuinely open — and is now closed by this report — was the *mechanism*:
  destroy-and-recreate, proven never to leak stale context, as detailed
  above.

## LOCAL VOICE FREEZE CHECK

`git diff --name-only -- src/nexa` shows only files under
`src/nexa/realtime/**` — no local-voice file (`voice/**`, `voice_tts/**`,
`voice_conversation/**`, `stt/**`, `tts/**`, `providers/**`, `config.py`,
`conversation/session.py`) was touched this checkpoint. Full existing
local-voice suites pass unmodified within the 886-test full run.

## KNOWN RISKS (carried + new)

- Reconnect is still not driven end-to-end from a real connection error
  (unchanged from R0033 — explicit, deferred to M2.6B.3).
- The mid-turn abort-and-restart policy means a real connectivity hiccup
  exactly mid-utterance can present to Gemini as two separate utterances
  instead of one continuous one — a semantic degradation, not data loss,
  and not yet observed on real hardware (no live test performed).
- `FatalErrorFrame` is deprecated in installed Pipecat 1.8.1; a future
  Pipecat upgrade past 1.8.x may require switching the mapping to
  `ErrorFrame` + a permanence signal instead.
- None of this has been exercised against a real Gemini connection or
  real hardware yet.

## WHAT REMAINS FOR M2.6B

Unchanged from R0033: wire `ReconnectController` into `GeminiLiveProvider`
for a real connection-error path; HYBRID audio wiring; real LOCAL↔CLOUD
spoken switch; operator probe app; function calling/tools; memory;
identity; UI; real `AUTO` classifier; minimum real-hardware operator
acceptance (required before M2.6B COMPLETE).

## DOCUMENTATION / REPORTS UPDATED

This report; `R0033` (stale count corrected); `docs/CURRENT_STATE.md`
(M2.6B.2A recorded, two more stale "M2.6B NOT STARTED" occurrences
labelled historical/corrected); `docs/ROADMAP.md`.

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO new external fetch — the `push_error()` upstream-direction finding and
the message-ordering proof (scenario C) both came from reading the
already-installed Pipecat 1.8.1 source in this sandbox; no network call.

## CURRENT VERIFIED STATE

- Local realtime voice (M2.5B, `R0029`) — unchanged, `OPERATOR-CONFIRMED`.
- M2.6A feasibility — PASS / OPERATOR-CONFIRMED; `Sulafat` OPERATOR-
  CONFIRMED (unchanged).
- `ADR-0004` + Amendment 1 — Accepted (unchanged).
- **M2.6B — IN PROGRESS.** `M2.6B.1` (`R0032`) DONE. `M2.6B.2` (`R0033`)
  IMPLEMENTED. **`M2.6B.2A` (this report, `R0034`)** — pre-hardware
  cloud-turn / reconnect hardening — IMPLEMENTED: mid-turn readiness-loss
  fix (no silent audio loss, no duplication), complete provider event map
  (incl. the upstream-error-tap fix), no-manual-injection integration
  test, 5 turn-ordering permutations tested (1 proven impossible from
  source), fresh-snapshot mechanism made precise and tested against a
  deliberately stale context. 886 tests total, 0 regressions. **Still no
  live Gemini connection, no hardware test, no reconnect wired to a real
  socket.**

## NEXT RECOMMENDED ACTION

**M2.6B.3 — HYBRID cloud audio + real Gemini / Raspberry Pi operator
acceptance.** Local voice stays frozen. No push.

## COMMIT HASH

`c334ccd` — `fix(m2.6b.2a): mid-turn readiness loss, event map, reconnect
hardening (R0034)`.

Prior tip: `4547e59` (R0033 M2.6B.2 hash-record commit).

## GIT STATUS

Branch `main`, ahead of `origin/main` (`505627f`) by 7 commits
(`986e65a`, `e4b84ec`, `eeb3724`, `4d85820`, `4547e59`, `c334ccd`, and this
hash-record follow-up). Working tree clean after commit. Not pushed.
