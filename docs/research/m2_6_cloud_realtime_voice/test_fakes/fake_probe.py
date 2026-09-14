#!/usr/bin/env python3
"""R0062 -- fake probe launcher for OFFLINE testing of
``run_r0057_gain_ab_condition.sh``. Never runs real audio hardware, never
imports Pipecat. A test points ``R0057_AB_PROBE_LAUNCHER`` at this file so
the wrapper script invokes it in place of the real
``m2_6b4m_self_echo_probe.py``.

Controlled entirely via environment variables (all optional):
  FAKE_PROBE_EXIT_CODE       -- exit code to return (default "0").
  FAKE_PROBE_SLEEP_SECONDS   -- sleep this long before exiting (default
                                 "0") -- used to exercise the wrapper's
                                 own external `timeout` supervisor.
  FAKE_PROBE_WRITE_ARTIFACTS -- "1" (default) to write one fake results
                                 JSON and its WAV pairs into
                                 R0057_AB_RUN_JSON_DIR/R0057_AB_RUN_PCM_DIR
                                 (set by the wrapper); "0" to write
                                 nothing.
  FAKE_PROBE_LEVEL           -- (default "max") -- must match the
                                 wrapper's own EXPECTED_LEVEL for its
                                 archive-completeness check to see a
                                 match; used ONLY to build the FIXED
                                 filenames the real probe itself would
                                 use (`{level}_trial{n}_mic.wav` etc.) --
                                 R0062 no longer embeds any per-run label
                                 in the filename (the real probe never
                                 does either), so two runs against the
                                 SAME shared location produce IDENTICALLY
                                 NAMED files -- exactly the collision risk
                                 the wrapper's own archiving must survive.
  FAKE_PROBE_TRIAL_COUNT     -- (default "3") how many trial WAV pairs to
                                 write -- set below the wrapper's own
                                 EXPECTED_TRIALS (3) to simulate missing/
                                 incomplete evidence.
  FAKE_PROBE_LABEL           -- a short label folded into the WAV file
                                 CONTENT (never the filename) and into the
                                 JSON body's own "label" field, so a test
                                 can distinguish condition A's vs B's own
                                 archived bytes even though the filenames
                                 are identical.
  FAKE_PROBE_IGNORE_SIGTERM  -- "1" to install a SIGTERM handler that does
                                 nothing (simulates a probe that does not
                                 die from a graceful terminate, forcing
                                 the wrapper's own SIGKILL escalation).
  FAKE_PROBE_SPAWN_CHILD_SLEEP_SECONDS -- if set, spawns a real child
                                 `sleep` subprocess for this many seconds
                                 (a descendant the wrapper's own
                                 process-group termination must also
                                 reach, not just this direct process).
  FAKE_PROBE_CHMOD_PCM_DIR_READONLY_AFTER_WRITE -- (R0063 round 2,
                                 optional) "1" makes the fake probe
                                 chmod its own PCM_DIR to 0555 right
                                 after writing all artifacts, so the
                                 wrapper's own LATER `rm -f` of each
                                 source WAV (after a successful copy)
                                 fails with a permission error --
                                 deterministically reproducing "copy
                                 succeeded, source removal failed".
  FAKE_PROBE_CORRUPT_MAPPING -- (R0063, optional) deliberately corrupts the
                                 JSON<->WAV relationship, for testing the
                                 wrapper's own structural mapping
                                 validation (never a real probe behavior):
                                   "wrong_path" -- trial 1's own mic_wav
                                     points at a path never actually
                                     written this run.
                                   "duplicate"  -- trial 2's own mic_wav is
                                     made identical to trial 1's.
                                   "empty_trial" -- (R0063 round 2) trial
                                     1's own mic_wav AND ref_wav fields
                                     are both wiped to "", while its own
                                     two real WAV files are still written
                                     normally (6 files on disk, 3 trial
                                     objects, only 2 trials populated).
                                   "swapped_roles" -- (R0063 round 2)
                                     trial 1's own mic_wav/ref_wav values
                                     are swapped -- both still point at
                                     real, owned files, but in the wrong
                                     role.
                                   "extra_wav"  -- one extra, genuinely
                                     written WAV file that no trial
                                     references at all.
"""
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def main() -> int:
    if os.environ.get("FAKE_PROBE_IGNORE_SIGTERM") == "1":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    child_sleep = os.environ.get("FAKE_PROBE_SPAWN_CHILD_SLEEP_SECONDS")
    child_proc = None
    if child_sleep:
        child_proc = subprocess.Popen(["sleep", child_sleep])

    # R0062: an explicit readiness marker -- printed only once the
    # signal disposition is installed and the child (if any) is spawned
    # -- so a test can synchronize on the fake probe's OWN actual
    # readiness instead of guessing from wrapper-side timing (the
    # wrapper's own "CHILD_PGID=" line only proves ITS OWN fork
    # happened, not that this script has reached this point yet).
    # `flush=True` matters: stdout is fully block-buffered once
    # redirected (as it always is here, via the wrapper's own
    # `exec > >(tee -a "$LOG")`).
    print("FAKE_PROBE_READY", flush=True)

    sleep_s = float(os.environ.get("FAKE_PROBE_SLEEP_SECONDS", "0"))
    if sleep_s > 0:
        time.sleep(sleep_s)

    if os.environ.get("FAKE_PROBE_WRITE_ARTIFACTS", "1") == "1":
        json_dir = Path(os.environ["R0057_AB_RUN_JSON_DIR"])
        pcm_dir = Path(os.environ["R0057_AB_RUN_PCM_DIR"])
        json_dir.mkdir(parents=True, exist_ok=True)
        pcm_dir.mkdir(parents=True, exist_ok=True)
        label = os.environ.get("FAKE_PROBE_LABEL", "fake")
        level = os.environ.get("FAKE_PROBE_LEVEL", "max")
        trial_count = int(os.environ.get("FAKE_PROBE_TRIAL_COUNT", "3"))
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

        trials = []
        for i in range(1, trial_count + 1):
            mic_path = pcm_dir / f"{level}_trial{i}_mic.wav"
            ref_path = pcm_dir / f"{level}_trial{i}_ref.wav"
            # FIXED filenames (matching the real probe exactly) but
            # content that embeds the label -- so condition A's and B's
            # own bytes at the SAME shared path are distinguishable, and
            # a later run overwriting them is detectable by content, not
            # merely by filename.
            mic_path.write_bytes(f"FAKEWAVMIC label={label} trial={i}".encode())
            ref_path.write_bytes(f"FAKEWAVREF label={label} trial={i}".encode())
            trials.append(
                {
                    "level": level,
                    "cross_correlation": {
                        "mic_wav": str(mic_path),
                        "ref_wav": str(ref_path),
                    },
                }
            )

        # R0063: deliberately corrupt the JSON<->WAV relationship, for
        # testing the wrapper's own new structural mapping validation
        # (never the production probe's own real behavior).
        corrupt = os.environ.get("FAKE_PROBE_CORRUPT_MAPPING", "")
        if corrupt == "wrong_path" and trials:
            # Trial 1's own mic_wav now points at a path with the CORRECT
            # expected basename (max_trial1_mic.wav -- passes the
            # wrapper's own per-trial/per-role basename check) but sitting
            # OUTSIDE this run's own PCM_DIR -- i.e. NOT one of the paths
            # this run actually discovered and archived. This isolates
            # "REJECTED: not owned" from "ROLE_MISMATCH: wrong basename"
            # (a different corruption mode a different mic/ref value would
            # trip; a plainly wrong basename is covered by
            # "swapped_roles" below instead).
            bogus_path = pcm_dir.parent / f"{level}_trial1_mic.wav"
            trials[0]["cross_correlation"]["mic_wav"] = str(bogus_path)
        elif corrupt == "duplicate" and len(trials) >= 2:
            # Trial 2's own mic_wav is made IDENTICAL to trial 1's --
            # the same real, archived file referenced by two different
            # trials.
            trials[1]["cross_correlation"]["mic_wav"] = trials[0]["cross_correlation"]["mic_wav"]
        elif corrupt == "empty_trial" and trials:
            # R0063 round 2: trial 1 exists structurally and its own TWO
            # real WAV files were still written normally (unlike the
            # other corruption modes) -- but its own JSON fields are
            # wiped. The EXACT "three trial objects, six real WAV files,
            # only two populated mapping rows" scenario the external
            # review named.
            trials[0]["cross_correlation"]["mic_wav"] = ""
            trials[0]["cross_correlation"]["ref_wav"] = ""
        elif corrupt == "swapped_roles" and trials:
            # R0063 round 2: trial 1's own mic_wav/ref_wav values are
            # swapped -- both still point at real, this-run-owned files,
            # but in the WRONG role, proving the wrapper's own per-role
            # basename check (not just "is this file owned") catches it.
            cc = trials[0]["cross_correlation"]
            cc["mic_wav"], cc["ref_wav"] = cc["ref_wav"], cc["mic_wav"]
        elif corrupt == "extra_wav":
            # An extra, genuinely-written WAV file that no trial
            # references at all -- proves the wrapper's own exact
            # ARCHIVE_WAV_COUNT check catches an untracked extra, not just
            # a missing expected file.
            stray_path = pcm_dir / f"{level}_trial{trial_count + 1}_mic.wav"
            stray_path.write_bytes(f"FAKEWAVMIC label={label} trial=stray".encode())

        payload = {
            "report": "FAKE (test-only, R0063 offline wrapper test)",
            "label": label,
            "warmup_result": {
                "repeats_run": 18,
                "playback_start_count": 18,
                "playback_stop_count": 18,
                "interrupt_confirmed_delta": 0,
            },
            "trials": trials,
        }
        (json_dir / f"self_echo_probe_{ts}.json").write_text(json.dumps(payload, indent=2))

        if os.environ.get("FAKE_PROBE_CHMOD_PCM_DIR_READONLY_AFTER_WRITE") == "1":
            # R0063 round 2: makes the wrapper's own LATER `rm -f "$src"`
            # (inside archive_one_file, after a successful copy) fail with
            # a permission error -- deterministically reproduces "copy
            # succeeded, source removal failed" without real hardware.
            # POSIX removal needs write access to the PARENT directory,
            # not the file itself.
            os.chmod(pcm_dir, 0o555)

    if child_proc is not None:
        child_proc.wait()

    return int(os.environ.get("FAKE_PROBE_EXIT_CODE", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
