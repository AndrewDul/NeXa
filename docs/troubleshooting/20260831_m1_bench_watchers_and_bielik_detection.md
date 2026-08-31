# M1.0 benchmark: stuck watcher loops + false "Bielik pull failed"

- Date: 2026-08-31
- Author: Claude Code (agent)
- Related: `docs/reports/R0002_m1_natural_conversation_research_20260831.md`,
  `scripts/m1_bench/`

Two independent tooling bugs hit during the M1.0 benchmark run. Neither touched
product code; both are in the throwaway `scripts/m1_bench/` harness / ad-hoc
wait loops.

## Symptom 1 — background "wait for benchmark" loops never finished

A `Bash(run_in_background)` waiter of the form:

```bash
until ! pgrep -f 'm1_bench/run_focused.sh' >/dev/null; do sleep 30; done
```

kept running long after `run_focused.sh` had exited (`run_focused.log` ended with
`=== run_focused done ... ===`, no `bench.py` / `run_focused.sh` process alive).

### Root cause (VERIFIED)

`pgrep -f 'm1_bench/run_focused.sh'` matches against the **full command line of
every process**, including the shell that is running the `until` loop — whose
command line literally contains the string `m1_bench/run_focused.sh`. The waiter
matched **itself**, so `pgrep` always found ≥1 process and the loop could never
exit.

### Fix / prevention

- Match the interpreter + script explicitly and exclude self, e.g.
  `pgrep -af '[b]ash .*run_focused\.sh'` (the `[b]` trick stops the pattern
  matching its own `pgrep`/loop), or
- watch for the *worker* not the driver: `pgrep -f '[b]ench\.py'`, or
- have the driver write a sentinel (`touch done.flag`) and wait on that file.
- Simplest: check `run_focused.log` for the `=== run_focused done` line.

## Symptom 2 — "BIELIK PULL FAILED" logged although the pull succeeded

`run_focused.sh` logged `verifying sha256 digest / writing manifest / success`,
`ollama show` printed real model metadata, yet the script printed
`BIELIK PULL FAILED — qwen3:4b results stand.` and skipped all Bielik benchmarks.
`ollama list` afterwards clearly showed
`SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0  88067a36fa44  5.1 GB`.

### Root cause (VERIFIED)

The guard was:

```bash
set -o pipefail
...
if ollama list 2>/dev/null | grep -qi bielik; then
```

`grep -q` exits **on the first matching line** and closes the pipe. `ollama list`
then receives `SIGPIPE` and exits `141`. With `set -o pipefail`, the pipeline's
exit status becomes `141` (non-zero) even though the match *did* occur, so the
`if` took the `else` branch.

### Fix / prevention (applied to `scripts/m1_bench/run_focused.sh`)

Capture the producer's output first, then match — the producer runs to
completion and can't be SIGPIPE'd:

```bash
bielik_list="$(ollama list 2>/dev/null || true)"
if printf '%s\n' "$bielik_list" | grep -qi 'bielik'; then
```

General rule: under `pipefail`, never end a pipeline with `grep -q` / `head` /
any early-exiting consumer when you care about the pipeline's exit status.

## Verification

- Bielik confirmed registered: `ollama list` →
  `SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0`; `ollama show` → llama arch, 4.8B,
  ctx 8192, Q8_0. Bielik perf + PL + EN benchmarks then ran successfully via a
  corrected one-off script.
- Qwen3-4B PL/EN conversation transcripts were already complete and were **not**
  re-run.
