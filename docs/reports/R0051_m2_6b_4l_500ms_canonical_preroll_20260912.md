# R0051 — M2.6B.4L: Use Empirically Validated 500ms User-Audio Preroll

**Date:** 2026-09-12
**Milestone:** M2.6B.4L (final capacity correction, following R0050's
forensic audit)
**Status:** Architecture and capacity correction implemented, proven
deterministically, AND now confirmed on real hardware.
**REAL HARDWARE POST-FIX RESULT: PASS** (operator follow-up below,
same day). No Gemini call, not pushed. **`M2.6B` remains IN PROGRESS —
this is not "all of M2.6B COMPLETE."** A minimal live Gemini
conversational validation is still required after this local PCM fix,
and the previously-known proactive-reconnect-caller gap remains a
separate, not-yet-addressed completion item.

## OPERATOR REAL HARDWARE FOLLOW-UP — PASS

The operator re-ran the exact, unchanged capture command
(`.venv/bin/python docs/research/m2_6_cloud_realtime_voice/
m2_6b4i_audio_ingress_parity_probe.py`) on the real reSpeaker, no Gemini
involved. New session (the diagnostic-only timestamped-subdirectory
feature added this checkpoint, exercised for the first time):
`docs/research/m2_6_cloud_realtime_voice/ingress_captures/
session_20260912T200941Z/ingress_capture_20260912T201010Z.json`
(git-ignored, not committed). Production configuration printed by the
real probe, confirmed from the JSON itself:
`sample_rate=16000`, `vad_start_secs=0.2`, `vad_stop_secs=0.5`,
**`preroll_ms=500`** — this checkpoint's fix is what the operator's
real hardware actually ran. **10 utterances captured successfully.**

**Byte-exact alignment re-run against this exact session**
(`wav_alignment.find_alignment`, the same tool R0050 built): every one
of the 10 real `production_forwarded.wav` files is confirmed to start
at **byte offset 0** of its own `raw_with_context.wav` —
`prefix_ms = 0.0` for all 10 takes, `suffix_ms = 500.0` for all 10
(unchanged, expected, the diagnostic's own post-VAD-stop context
window, unrelated to onset). This is the mechanically-expected result
of `preroll_ms(500) == pre_context_secs(500ms)`: **zero** bytes of the
diagnostic's own pre-VAD-start capture window are now omitted from what
production forwards — a complete, quantified confirmation, not merely
consistent with the operator's subjective listening result but
byte-for-byte proof of it. (Cross-checked against the JSON's own
recorded `raw_minus_forwarded_ms`, which is exactly `500.0` for every
take — matching `postroll_never_forwarded_s` alone, with zero
contribution from a prefix omission, exactly as expected.)

The operator directly listened to RAW vs PRODUCTION_FORWARDED for this
new session. **Real result: PASS.** The previously observed onset
clipping is gone. "Czarna dziura" is now audibly complete in
`production_forwarded.wav` — no more "arna dziura," "carna dziura," or
"dziura." English onset is also now complete. No audible
duplication/corruption was reported. The operator's own words: "SUPER
JEST JUZ DOBRZE" ("great, it's good now").

**Local hardware post-fix acceptance: PASS.** Recorded exactly per the
required checklist: real reSpeaker ✓; 10 utterances ✓; `preroll_ms=500`
✓; RAW complete ✓; PRODUCTION_FORWARDED complete ✓; Polish onset
complete ✓; English onset complete ✓; no audible clipping ✓; no
audible duplicate/corruption ✓.

**This does NOT mean all of `M2.6B` is complete.** Two items remain
before hardware acceptance overall can be marked PASS: (1) a minimal
live Gemini conversational validation of this exact fix (this
checkpoint's evidence is entirely local — no Gemini call has been made
since R0048 first discovered the clipping symptom); (2) the previously
identified proactive-reconnect-caller gap (`ReconnectController`'s own
proactive age-timer reconnect path has no production caller yet,
tracked separately, unrelated to audio ingress) remains open. `M2.6B`
stays IN PROGRESS.

## R0050 EVIDENCE

Restated, not re-derived (R0050 stands unmodified in its own
conclusions): byte-exact alignment of 6 real post-R0049 captures showed
`production_forwarded.wav` began precisely 200.0ms into each
`raw_with_context.wav`'s 500ms pre-VAD-start window (500ms diagnostic
pre-context − 300ms retained preroll = 200ms omitted, identical across
every take). A plain PCM RMS energy analysis of that omitted 200ms
showed real, rising acoustic energy in 8 of 10 real captured takes
(both post- and pre-fix), beginning roughly 300–460ms before VAD
confirmation. Source re-audit confirmed `start_secs=0.2` is only a
192ms (6×32ms Silero chunks) confirmation window — excluding Pipecat's
own exponential volume-smoothing lag (~100–330ms, data-dependent) and
Silero's model-internal confidence timing, both additive. Crucially,
`src/nexa/stt/utterance_buffer.py`'s own docstring documents an
independent, pre-existing, R0006-era empirical measurement of REAL
Silero confirmation delay at 288–352ms against real speech fixtures for
this IDENTICAL VAD mechanism — which is exactly why that module's own
`PRE_ROLL_MS` default is 500, not 300.

## R0050 DOCUMENTATION CORRECTION

R0050's own report originally stated "Full-suite/regression re-run
deferred to R0051" — this was inaccurate; the full 991-test project
suite was actually run and passed during R0050's own validation, before
its commit. Corrected in `docs/reports/R0050_...md`'s own TEST RESULTS
section this checkpoint, with the actual full-suite output recorded.
No R0050 conclusion was changed.

## WHY 500 MS

Not a new, third derivation. **NeXa already has one empirically
validated answer to "how much preroll does this exact Silero/local-VAD
mechanism need against real speech": 500ms** — measured once, for the
LOCAL voice path, against 3 real speech fixtures with manually-verified
true onset (RMS profile inspection), landing at 288–352ms real
confirmation delay, then rounded up with a safety margin against
jitter. The cloud bridge already reuses `UtteranceBuffer` (R0049) for
its ring/capture state machine; reusing its `PRE_ROLL_MS` constant too
(rather than re-deriving a NeXa/Pipecat-specific "300" that turned out
to be wrong) is the smallest, most correct change — it removes a
duplicated, unvalidated-against-real-speech magic number instead of
adding a second one.

## CANONICAL PREROLL OWNERSHIP

`nexa.stt.utterance_buffer.PRE_ROLL_MS` (= 500, unchanged, untouched
this checkpoint — `git diff --stat -- src/nexa/stt` is empty) remains
the SINGLE canonical definition. `nexa.realtime.gemini.runtime` imports
it directly (`from ...stt.utterance_buffer import PRE_ROLL_MS,
UtteranceBuffer`) — the SAME dependency edge R0049 already introduced
for the `UtteranceBuffer` class itself, so this adds **zero new
layering** (no new module-to-module edge; one more name imported from
an already-imported module). No second "500" is defined anywhere;
`build_gemini_voice_runtime` sets `preroll_ms = PRE_ROLL_MS` directly
— no arithmetic, no derivation, no dependency on `vad_analyzer.params`
at all for this purpose (the analyzer is still constructed for
`vad_processor`, unrelated to preroll now).

## PRODUCTION CHANGE

`src/nexa/realtime/gemini/runtime.py`:

- Removed `AUTOSIZED_PREROLL_MARGIN_SECS = 0.1` (module constant) and
  its docstring entirely — the derivation it fed is proven insufficient
  and is not left behind as a dead or misleading constant.
- `build_gemini_voice_runtime`: replaced
  `preroll_ms = int((vad_analyzer.params.start_secs +
  AUTOSIZED_PREROLL_MARGIN_SECS) * 1000)` with `preroll_ms = PRE_ROLL_MS`.
- Import line updated: `from ...stt.utterance_buffer import PRE_ROLL_MS,
  UtteranceBuffer`.
- Module docstring's M2.6B.4J section's capacity-derivation paragraph
  replaced with a new M2.6B.4L section recording this correction and
  its evidence, without altering the (still-accurate) architecture
  description around it.
- **No changes** to `start_secs`, `stop_secs`, `confidence`,
  `min_volume`, barge-in confirmation threshold, `_VadToProviderBridge`'s
  own frame-handling logic (the VAD_START/audio/VAD_STOP handlers are
  byte-for-byte unchanged from R0049 — only the CAPACITY passed into
  `UtteranceBuffer`'s constructor changed).
- `docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py`
  (diagnostic, not production): mirrors the same correction — imports
  `PRE_ROLL_MS` directly instead of re-deriving it, so the probe
  automatically exercises whatever production actually does. Verified
  live: `--dry` now prints `preroll_ms 500`.
- Diagnostic-only, optional (per the charter): each real (non-`--dry`)
  probe session now writes into its own timestamped
  `ingress_captures/session_<UTC timestamp>/` subdirectory, so a later
  run's `001..NNN` WAVs never silently overwrite an earlier run's (the
  exact file-name-reuse confusion R0050 had to explicitly flag and work
  around). `--dry` is unaffected (writes nothing). No production file
  touched for this.

## NORMAL TURN PCM RESULT

Proven directly (`TestVadBridgePrerollParity`, real bridge + real
Pipecat pipeline, unchanged test bodies from R0049 — only the
`preroll_ms` value driving them changed, now via the canonical
`PRE_ROLL_MS` import rather than a hardcoded 300): before VAD START,
the rolling buffer retains the last ≤500ms of PCM; at VAD START, the
provider receives, exactly once, `[retained preroll] + [live post-start
PCM]`, in that order — no duplicate boundary frame, no stale
previous-turn PCM, no reordering.

## BARGE-IN PCM RESULT

Unchanged mechanism, now exercised at 500ms capacity: the charter's own
worked example (`[PREE][POST]` → fresh provider receives `[PREEPOST]`
exactly once, `PREE` possibly up to ~500ms before VAD confirmation) is
still a literal passing test
(`test_7_8_confirmed_bargein_replay_includes_preroll_exactly_once`).
`UtteranceBuffer` (`self._pcm`) remains the single coherent PCM owner —
zero changes to `_replace_provider_after_bargein`, `_ProviderHandle`, or
quarantine/`sealed_utterances` logic.

## BUFFER BOUND RESULT

`test_2_idle_rolling_buffer_stays_bounded` (unchanged from R0049, uses
its own local 20ms/640-byte capacity to test the BOUNDING mechanism in
isolation, independent of the canonical 500ms value) confirms the ring
never grows unbounded regardless of how much idle audio arrives.
`test_11_two_consecutive_turns_do_not_leak_preroll_between_them`
confirms no stale PCM crosses a turn boundary — both re-verified green
at the new capacity.

## R0044/R0045 REGRESSION RESULT

**PASS, unmodified.** All 5 `TestAtomicProviderReplacement` tests
(including R0044 CASE 2) and both `TestVadBridgeQuarantine` tests
remain green — zero changes to `_replace_provider_after_bargein`,
`_ProviderHandle`, or the quarantine flag logic this checkpoint either.

## R0046 HISTORY REGRESSION RESULT

**PASS, unmodified.** Zero changes to `src/nexa/realtime/router.py` or
`src/nexa/realtime/turn.py` this checkpoint (confirmed by `git diff
--stat`). All 5 `TestProductionCanonicalTurnLifecycle` tests remain
green, including the one that exercises the REAL bridge directly.

## LOCAL VOICE REGRESSION RESULT

**PASS, unmodified.** `git diff --stat -- src/nexa/stt` (and `src/nexa/
voice`, `src/nexa/voice_tts`) is empty — `PRE_ROLL_MS`/`UtteranceBuffer`
were imported, never edited. `tests/test_stt_utterance_buffer.py` (9
tests, local voice's own suite for this exact class) re-run and green.
Local voice still resolves exactly `PRE_ROLL_MS = 500` from the same,
single, untouched definition.

## DETERMINISTIC POST-FIX RESULT

All offline validation green:

```
$ .venv/bin/python -m pytest tests/test_realtime_gemini_runtime.py -q
66 passed, 1 warning

$ .venv/bin/python -m pytest tests/test_m2_6b4i_audio_ingress_parity_probe.py tests/test_m2_6b4k_wav_alignment.py tests/test_realtime_router.py tests/test_realtime_turn.py tests/test_realtime_gemini_service.py tests/test_cloud_voice_app_entrypoint.py -q
94 passed, 2 warnings

$ .venv/bin/python -m pytest tests/test_stt_utterance_buffer.py -q
9 passed

$ .venv/bin/python -m unittest discover -s tests -p "test_*.py"
Ran 991 tests in 65.028s
OK (skipped=7)
```

`ruff check src/ tests/ apps/ docs/research/m2_6_cloud_realtime_voice/`
— All checks passed. `.venv/bin/pip check` — No broken requirements
found. `git diff --check` — clean. Local voice freeze — empty.
`docs/research/.../m2_6b4i_audio_ingress_parity_probe.py --dry` live
output confirms `preroll_ms 500`.

## REAL HARDWARE POST-FIX RESULT

**PENDING.** Not run this checkpoint (no Gemini, no hardware touched by
this session, per the charter). See EXACT LOCAL HARDWARE CAPTURE
COMMAND below — the operator must re-run it, speak at minimum "Czarna
dziura" (×2), "Czarna dziura powstaje," "Black hole" (×2), "What is a
black hole?", and listen to RAW vs PRODUCTION_FORWARDED before this
section can be updated to PASS or FAIL. `M2.6B` remains IN PROGRESS
until then.

## FILES CHANGED

```
docs/reports/R0050_m2_6b_4k_residual_onset_clipping_after_r0049_20260912.md | TEST RESULTS section corrected
docs/reports/R0051_m2_6b_4l_500ms_canonical_preroll_20260912.md             | new (this report)
docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py | reuse PRE_ROLL_MS; timestamped session subdirs (optional, diagnostic-only)
src/nexa/realtime/gemini/runtime.py                                         | remove AUTOSIZED_PREROLL_MARGIN_SECS; preroll_ms = PRE_ROLL_MS
tests/test_m2_6b4i_audio_ingress_parity_probe.py                            | assert preroll_ms == 500
tests/test_realtime_gemini_runtime.py                                       | import PRE_ROLL_MS; preroll_ms=300 -> canonical value at every call site
```

**Zero changes to `src/nexa/stt`, `src/nexa/voice`, `src/nexa/
voice_tts`, `src/nexa/realtime/router.py`, `src/nexa/realtime/turn.py`.**

## TEST RESULTS

See DETERMINISTIC POST-FIX RESULT above (all figures repeated there are
the actual, just-run numbers, not estimates).

## COMMIT HASHES

fix/report commit: `5b31cf5`

(recorded in the follow-up hash-record commit)

## GIT STATUS

At time of writing (before this checkpoint's commit):

```
 M docs/reports/R0050_m2_6b_4k_residual_onset_clipping_after_r0049_20260912.md
 M docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py
 M src/nexa/realtime/gemini/runtime.py
 M tests/test_m2_6b4i_audio_ingress_parity_probe.py
 M tests/test_realtime_gemini_runtime.py
?? docs/reports/R0051_m2_6b_4l_500ms_canonical_preroll_20260912.md
```

Not pushed.

## EXACT LOCAL HARDWARE CAPTURE COMMAND

**STOP — operator action required.** No Gemini, no credential:

```
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py
```

Speak at minimum: "Czarna dziura" (×2), "Czarna dziura powstaje,"
"Black hole" (×2), "What is a black hole?" — then listen to each
`raw_with_context.wav` vs `production_forwarded.wav` pair (now under a
fresh `ingress_captures/session_<timestamp>/` directory). Required for
real acceptance: `production_forwarded` contains the complete audible
onset (no "arna dziura," no "carna dziura," no missing first English
phoneme), forwarded PCM aligns at/near the start of the 500ms
diagnostic pre-context (consistent with 500ms retention), and no new
corruption/duplication. Report PASS or FAIL — do not assume; this
session will not fabricate a hardware PASS.

## EXACT NEXT GEMINI COMMAND

**Not yet.** No Gemini call should be made until the operator confirms
the local forwarded WAVs contain the full speech onset. Once confirmed:

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

Unchanged from R0047's corrected command — no new flag was added this
checkpoint either.
