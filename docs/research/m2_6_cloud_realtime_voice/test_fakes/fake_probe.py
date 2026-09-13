#!/usr/bin/env python3
"""R0061 -- fake probe launcher for OFFLINE testing of
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
                                 JSON and two fake WAV files into
                                 R0057_AB_RUN_JSON_DIR/R0057_AB_RUN_PCM_DIR
                                 (set by the wrapper); "0" to write
                                 nothing, exercising the wrapper's own
                                 "no new artifacts found" path.
  FAKE_PROBE_LABEL           -- a short label folded into the written
                                 filenames/JSON body so a test can tell
                                 condition A's vs condition B's own fake
                                 artifacts apart if it inspects them
                                 before the wrapper archives them.
"""
import json
import os
import time
from pathlib import Path


def main() -> int:
    sleep_s = float(os.environ.get("FAKE_PROBE_SLEEP_SECONDS", "0"))
    if sleep_s > 0:
        time.sleep(sleep_s)

    if os.environ.get("FAKE_PROBE_WRITE_ARTIFACTS", "1") == "1":
        json_dir = Path(os.environ["R0057_AB_RUN_JSON_DIR"])
        pcm_dir = Path(os.environ["R0057_AB_RUN_PCM_DIR"])
        json_dir.mkdir(parents=True, exist_ok=True)
        pcm_dir.mkdir(parents=True, exist_ok=True)
        label = os.environ.get("FAKE_PROBE_LABEL", "fake")
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        payload = {
            "report": "FAKE (test-only, R0061 offline wrapper test)",
            "label": label,
            "warmup_result": {"repeats_run": 18, "playback_start_count": 18,
                               "playback_stop_count": 18, "interrupt_confirmed_delta": 0},
            "trials": [],
        }
        (json_dir / f"self_echo_probe_{ts}.json").write_text(json.dumps(payload, indent=2))
        (pcm_dir / f"max_trial1_{label}_mic.wav").write_bytes(b"FAKEWAVMIC" + label.encode())
        (pcm_dir / f"max_trial1_{label}_ref.wav").write_bytes(b"FAKEWAVREF" + label.encode())

    return int(os.environ.get("FAKE_PROBE_EXIT_CODE", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
