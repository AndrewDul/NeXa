# R0047 — Pre-Attempt #3 Launch Contract Audit

**Date:** 2026-09-12
**Milestone:** M2.6B.4H erratum (documentation/test-only correction, no
architecture change)
**Status:** Root cause confirmed. Documentation corrected. One new
deterministic entrypoint test file added. No Gemini call, no hardware, no
runtime/architecture change. **Hardware acceptance remains FAIL. `M2.6B`
remains IN PROGRESS.**

## ROOT CAUSE

R0045's own "EXACT ATTEMPT #3 COMMAND" section (and R0046's copy of it)
documented:

```
.venv/bin/python apps/nexa_cloud_voice_app.py --bargein
```

`--bargein` is not, and has never been, a flag `apps/nexa_cloud_voice_app.py`
defines. Its `argparse.ArgumentParser` has exactly one flag, `--dry`
(confirmed by direct source read, and by `git log --all -p --
apps/nexa_cloud_voice_app.py`, which shows `bargein` never appears
anywhere in this file's own history). The operator ran the documented
command twice and both times got argparse's own rejection:

```
usage: nexa_cloud_voice_app.py [-h] [--dry]
nexa_cloud_voice_app.py: error: unrecognized arguments: --bargein
```

Attempt #3 never started; no Gemini evidence was produced.

## WAS --bargein EVER A CLOUD APP FLAG?

**No, never.** `git log --all -p -- apps/nexa_cloud_voice_app.py` shows
two commits touching this file (`6977fcb` R0035, `80e229a` R0036); neither
diff contains the string `bargein` anywhere. Every prior report from
R0035 through R0044 correctly documented the bare command
(`.venv/bin/python apps/nexa_cloud_voice_app.py`, optionally `--dry`) —
grepped directly from each report's own "EXACT ... COMMAND" section. The
wrong command was introduced for the first time in R0045 (this
checkpoint's immediate predecessor, produced in the same session) and
copied forward, unverified, into R0046.

`--bargein` **does** exist — exclusively on
`apps/nexa_bilingual_voice_probe.py` (`argparse.BooleanOptionalAction`,
default `False`), the M2.5B **local** voice probe, which gates an entirely
different, genuinely opt-in mechanism: `LocalAudioConfig(bargein_enabled=...)`
→ `nexa.voice.gate.HalfDuplexGate(bargein_enabled=...)`. This is a
different app, a different audio pipeline, and a different barge-in
implementation from the cloud path's `BargeInController`. The most likely
proximate cause: while writing R0045 (a checkpoint about barge-in
architecture), the local-voice probe's own `--bargein` convention was
pattern-matched onto the cloud app's launch command without checking that
app's actual `argparse` definition — a documentation-only error, not a
code or architecture defect.

## IS CLOUD BARGE-IN ALWAYS ENABLED?

**Yes, unconditionally, with no flag of any kind.**
`build_gemini_voice_runtime` (`src/nexa/realtime/gemini/runtime.py`)
constructs `BargeInController(aec_health=aec_health, on_confirmed=_on_confirmed)`
unconditionally, in both the `dry=True` and `dry=False` paths, and — in
the `dry=False` path — includes it, unconditionally, in the hardware
pipeline: `Pipeline([transport.input(), vad_processor, bargein, bridge,
aec_feeder, transport.output()])`. There is no `bargein_enabled` parameter
anywhere on `build_gemini_voice_runtime`'s signature, and none on
`apps/nexa_cloud_voice_app.py`'s own `main()`/`parse_args()`. R0045's
atomic provider replacement and R0046's canonical cloud turn lifecycle are
both wired directly into this same, always-constructed `BargeInController`
+ `_VadToProviderBridge` + `_ProviderHandle` object graph — there is
nothing to opt into.

## ACTUAL PRODUCTION PATH

`apps/nexa_cloud_voice_app.py main()` (no flags) →
`load_gemini_credential()` → `build_gemini_voice_runtime(session=...,
api_key=..., policy=ConversationPolicy.CLOUD_PREFERRED, on_event=...,
on_aec_change=..., dry=False)` → constructs, in order: `RuntimeMetrics`,
`GeminiLiveProvider`, `_ProviderHandle`, `ConversationRouter` (cloud
factory injected), `AecReferenceHealth`, `_ResponseLifecycle`,
`_ResponseGenerationGuard`, the real `_on_confirmed` closure (R0045/R0046
logic), `BargeInController(on_confirmed=_on_confirmed)`, then (since
`dry=False`) `pyaudio.PyAudio()` + device lookup, `LocalAudioTransport`,
`SileroVADAnalyzer`/`VADProcessor`, `_VadToProviderBridge(router=router,
...)` (R0046's new required param), `AecReferenceFeeder`, and the full
`Pipeline`/`PipelineWorker`/`WorkerRunner`. Every one of these
construction calls is unconditional — reached by the bare command alone.

## WHY R0045/R0046 COMMAND WAS WRONG

Documentation drift, not a code defect: the command was typed from
pattern-matching the local-voice-probe convention rather than from
reading `apps/nexa_cloud_voice_app.py`'s own `argparse` definition, and
nothing in the test suite ever exercised that file's CLI contract
end-to-end — `tests/test_realtime_gemini_runtime.py` tests
`build_gemini_voice_runtime` directly (thoroughly), but no test file ever
imported `apps/nexa_cloud_voice_app.py` itself, so a false claim about its
command-line surface could not be caught by `git diff --check`, `ruff`,
or the full test suite — all of which passed cleanly on both R0045 and
R0046 despite the error. This is now closed by
`tests/test_cloud_voice_app_entrypoint.py` (new), which imports the app
module directly and:

1. Parses the bare command's argv and the `--dry` command's argv,
   proving both remain accepted.
2. Asserts `--bargein` is rejected (`SystemExit`) — a canary: if a real
   `--bargein` flag is ever legitimately added to this app, this specific
   assertion breaks immediately, forcing the launch-command docs to be
   checked in the same change.
3. Runs the REAL `main()` (not a reimplementation of its wiring) under
   `--dry`, spying on `build_gemini_voice_runtime` to capture the returned
   runtime, and asserts `isinstance(runtime.bargein, BargeInController)`
   — proving the object graph the actual entrypoint constructs includes
   the barge-in architecture, unconditionally, with no flag.
4. Runs the REAL `main()` under the bare (non-`--dry`) command with the
   credential loader forced to fail, proving the documented live command
   parses and proceeds all the way to the first real I/O boundary
   (credential load) before any Gemini/hardware call — never touching
   either.

## FILES CHANGED

```
docs/reports/R0045_m2_6b_4g_atomic_provider_replacement_20260912.md    | erratum + corrected command
docs/reports/R0046_m2_6b_4h_production_canonical_cloud_turn_lifecycle_20260912.md | erratum + corrected command
docs/reports/R0047_pre_attempt3_launch_contract_audit_20260912.md      | new (this report)
docs/CURRENT_STATE.md                                                  | corrected + new entry
docs/ROADMAP.md                                                        | new entry
tests/test_cloud_voice_app_entrypoint.py                               | new, 5 tests
```

No `src/nexa/**` change of any kind — this was a documentation error, not
an architecture or wiring defect; the "smallest correct fix" is the
correction itself plus the regression test, per the charter's own decision
tree (cloud barge-in was already always enabled).

## TEST RESULTS

```
$ .venv/bin/python -m pytest tests/test_cloud_voice_app_entrypoint.py -q
5 passed in 0.70s

$ .venv/bin/python -m pytest tests/test_realtime_gemini_runtime.py -q
59 passed, 1 warning

$ .venv/bin/python -m pytest tests/test_realtime_router.py tests/test_realtime_turn.py -q
34 passed

$ .venv/bin/python -m unittest discover -s tests -p "test_*.py"
Ran 964 tests, OK (skipped=7)
```

`ruff check src/ tests/ apps/` — All checks passed.
`.venv/bin/pip check` — No broken requirements found.
`git diff --check` — clean.
Local voice freeze: `git diff --stat -- src/nexa/voice src/nexa/voice_tts` —
empty.

## COMMIT HASHES

fix/report commit: `46251b8`

(recorded in the follow-up hash-record commit)

## GIT STATUS

Working tree changes at time of writing:

```
 M docs/CURRENT_STATE.md
 M docs/ROADMAP.md
 M docs/reports/R0045_m2_6b_4g_atomic_provider_replacement_20260912.md
 M docs/reports/R0046_m2_6b_4h_production_canonical_cloud_turn_lifecycle_20260912.md
?? docs/reports/R0047_pre_attempt3_launch_contract_audit_20260912.md
?? tests/test_cloud_voice_app_entrypoint.py
```

Not pushed.

## ONE EXACT VERIFIED ATTEMPT #3 COMMAND

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

Verified by `tests/test_cloud_voice_app_entrypoint.py`: parses with
`args.dry == False`, and the REAL `main()`/`build_gemini_voice_runtime`
call path it drives constructs `BargeInController` (plus R0045's atomic
provider replacement and R0046's canonical turn lifecycle, both wired
into the same object graph) unconditionally — no flag required, none
exists.
