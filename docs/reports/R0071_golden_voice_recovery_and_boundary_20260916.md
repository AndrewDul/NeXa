# R0071 — Golden Voice Recovery, M2.6B Freeze, NeXa Core Boundary

**Date:** 2026-09-16
**Milestone:** M2.6B closeout + ADR-0004 Amendment 2 (NeXa Core boundary)
**Status:** M2.6B dual-pipeline production path **PAUSED** (self-echo
unresolved, not fixed). R0031/M2.6A cloud conversation **GOLDEN / operator-
confirmed baseline, reconfirmed today**. Simplified, golden-derived
production path (`nexa.realtime.gemini.simple_conversation`) implemented,
tested, and **real-hardware ACCEPTED — Cloud realtime conversation
baseline = ACCEPTED / FROZEN** (2026-09-16, same day; see FINAL
REAL-HARDWARE ACCEPTANCE below). Known non-blocking issue: occasional
playback/stream continuity stutter during longer assistant speech.
Project priority moves to M3 — NeXa Core. Not committed, not pushed.

## TASK RESULT

**PASS — investigation concluded and documented; architectural pivot
implemented, unit-tested, and real-hardware ACCEPTED.**

## WHAT I DID

1. **Golden control.** Resolved the exact final accepted R0031/M2.6A
   revision (`7dd6b87`), ran it unmodified in an isolated git worktree
   against today's real hardware (current reSpeaker mic, current separate
   USB speaker, real Gemini, Sulafat). Operator result: natural
   conversation, natural interruption, **zero self-interruption** during a
   14s silent-operator window, clean recovery after repeated
   interruptions — **10/10, matching R0031's original 2026-09-10
   acceptance.**
2. **Differential audit (M2.6A vs. M2.6B).** Source-level comparison found
   a real architectural gap: `_VadToProviderBridge` (M2.6B's dual-pipeline
   bridge) forwarded raw local VAD candidates to the cloud provider
   *before* `BargeInController` decided confirm/reject — so a candidate
   `BargeInController` correctly rejected could still have already reached
   Gemini as a real user turn.
3. **Fixed and kept** that ownership gap (`candidate_pending` on
   `_ProviderHandle`, `on_candidate`/`on_candidate_rejected` wired) — 4
   new focused tests + full regression suite green. This is a genuine
   correctness fix, independent of which runtime is active, and is **not
   reverted**.
4. **Real acceptance, M2.6B (post-fix): FAIL.** Sustained self-echo/
   self-conversation during operator silence — the ownership fix could not
   prevent it, because self-echo VAD segments were *sustained* (survived
   the 300ms confirm-hold legitimately, per the state machine's own
   correct rules), not brief rejected blips.
5. **Controlled A/B: reference-gain scaling (`CoherentReferenceGain`) on
   vs. off.** Both FAILED with sustained self-conversation. This rules out
   gain-ownership incoherence as the *sole* explanation (it was already a
   confirmed, real, partial contributor per R0053–R0055) without
   identifying a proven alternative single cause. Reverted the A/B back to
   the original wiring (`gain_source=reference_gain.current_gain`) —
   unchanged from before this checkpoint.
6. **Root cause of the M2.6B self-echo remains UNPROVEN.** Leading
   candidates on record (R0053–R0056, R0071's own differential audit):
   hardware AEC residual correlation, the reSpeaker's own firmware
   `AEC_FAR_EXTGAIN` (confirmed -20dB, never touched by any NeXa software
   fix), and/or M2.6B's dual-pipeline/multi-hop timing relative to
   golden's single tight pipeline. **Investigation stops here per explicit
   product decision — see below.**
7. **Product decision (this checkpoint): stop trying to out-engineer
   Gemini Live's own realtime handling in a second, NeXa-owned interruption
   architecture.** Extracted golden M2.6A's *behavior* (not its
   diagnostics) into a new, simplified production adapter, wired to the
   SAME `ConversationSession`/`ConversationRouter`/`CloudContextSnapshot`
   boundary ADR-0004 already established. Formalized the NeXa-Core-owns-
   everything-local / provider-owns-only-the-realtime-session boundary
   explicitly (ADR-0004 Amendment 2), and introduced explicit cloud
   eligibility (`CloudEligibility`: `LOCAL_ONLY`/`CLOUD_SAFE`/
   `CLOUD_WITH_USER_APPROVAL`).

## WHAT I VERIFIED

- `git diff -u` of `AecReferenceFeeder` between `7dd6b87` and HEAD: the
  ONLY structural change is the (now-ruled-out-as-sole-cause) gain
  mechanism; with `gain_source=None` it is byte-identical to golden.
- `TTSAudioRawFrame` is not an `InputAudioRawFrame` subclass and Pipecat's
  `VADController.process_frame` filters on `isinstance(frame,
  InputAudioRawFrame)` — ruled out a software frame-routing bug (TTS audio
  is never analyzed as mic input in either architecture).
- New adapter's dry construction, Sulafat voice default, seeded history,
  and `CLOUD_SAFE`-only context-fact filtering all verified directly
  (manual smoke + tests).

## TESTS

```
tests/test_realtime_privacy.py                 5 passed
tests/test_realtime_snapshot.py (+5 new)       15 passed
tests/test_simple_cloud_conversation.py        6 passed
```
Broader regression (untouched by this checkpoint's runtime code, confirming
no side effects from the `snapshot.py` extension):
```
tests/test_realtime_router.py, test_realtime_provider.py,
tests/test_realtime_gemini_runtime.py, test_realtime_gemini_service.py,
tests/test_cloud_voice_app_entrypoint.py        148 passed
```
`ruff check` / `py_compile` clean on every new/changed file.

## UNRESOLVED

- The M2.6B dual-pipeline self-echo root cause — paused, not solved (see
  point 6 above). Resuming this investigation is explicitly deferred
  unless a future real product requirement needs the dual-pipeline
  architecture specifically.
- Known non-blocking backlog item (not investigated this checkpoint):
  occasional short playback/stream continuity stutter during longer
  assistant speech on the simplified adapter, observed during final
  acceptance. Intermittent, did not block or degrade the accepted
  conversation quality.

## DOCUMENTATION / REPORTS UPDATED

- `docs/decisions/ADR-0004_cloud_realtime_voice_provider_boundary.md` —
  Amendment 2 (NeXa Core boundary, M2.6B pause, simplified baseline,
  `CloudEligibility`).
- This report.
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — updated separately, concise.

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO new external research this checkpoint (reused R0053–R0056's own
already-completed research).

## CURRENT VERIFIED STATE

- `M2.6B` dual-pipeline production runtime (`nexa.realtime.gemini.runtime`,
  `apps/nexa_cloud_voice_app.py`): **PAUSED**. Code preserved unchanged
  (candidate/reject ownership fix included and kept). Not deleted, not the
  production default going forward.
- `R0031/M2.6A` cloud conversation: **GOLDEN, operator-reconfirmed
  2026-09-16** on current hardware.
- New simplified production path (`nexa.realtime.gemini.
  simple_conversation.CloudRealtimeConversationAdapter`,
  `apps/nexa_cloud_voice_simple.py`): implemented, unit-tested (25 new
  tests), **real-hardware ACCEPTED 2026-09-16 — the CLOUD_PREFERRED
  production baseline, FROZEN.**
- `CloudEligibility` (`nexa.realtime.privacy`) and `CloudContextSnapshot.
  context_facts`: implemented, tested, privacy-filtering confirmed
  (`LOCAL_ONLY`/`CLOUD_WITH_USER_APPROVAL` facts never reach the snapshot).
- Working tree: all changes uncommitted. No push.

## FINAL REAL-HARDWARE ACCEPTANCE (2026-09-16, same day)

Ran `apps/nexa_cloud_voice_simple.py` for real (current reSpeaker mic,
current separate USB speaker, real Gemini, Sulafat, one `CLOUD_SAFE`
context fact: `"Current project name is NeXa IkiGai."`). Session log:
`var/r0071_final_acceptance_session.log` (194 lines, ~9 minutes,
multi-topic PL/EN conversation with ~40+ natural interruptions — black
holes, stars, the Polish national anthem's history, explicit PL↔EN
switching — no self-conversation/generic-greeting-loop pattern anywhere in
the log, unlike every M2.6B dual-pipeline failing session this same day).

**Operator verdict (Andrzej, 2026-09-16):** normal conversation works;
Polish works; English works; language switching works as expected; NeXa no
longer responds to herself; no self-conversation loop; natural
conversation works; interruption works; conversation continues correctly
after interruption; cloud context integration works (the injected
`CLOUD_SAFE` fact was used correctly). One **known, non-blocking** issue:
occasional short playback/stream continuity stutter during longer
assistant speech, intermittent, does not break the conversation.

| Test | Result |
|---|---|
| 1 — normal turn | PASS |
| 2 — silence during long answer (self-interruption check) | PASS |
| 3 — natural interruption ×2–3 | PASS |
| 4 — NeXa Core `CLOUD_SAFE` context fact | PASS |

## FINAL VERDICT

**Cloud realtime conversation baseline = ACCEPTED / FROZEN.**

`KNOWN NON-BLOCKING ISSUE: occasional playback/stream continuity stutter
during longer assistant speech.` Recorded as backlog, not investigated or
fixed in this checkpoint, not a re-opening of the M2.6B self-echo
investigation (a different symptom: continuity, not self-triggering).

The M2.6B self-echo investigation, AEC experiments, VAD/confirm-hold
tuning, scheduled-reference work, and PipeWire/WebRTC AEC research remain
explicitly PAUSED — not resumed by this acceptance, not needed to close it.

## NEXT RECOMMENDED ACTION

Project priority moves to **M3 — NeXa Core**. Voice is not the priority
until a concrete product requirement reopens it.
