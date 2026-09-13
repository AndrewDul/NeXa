#!/usr/bin/env python3
"""R0061 -- stateful fake `amixer` for OFFLINE testing of
``run_r0057_gain_ab_condition.sh``. Never touches real hardware; the
wrapper's own tests put a directory containing this script (symlinked or
copied to the literal name ``amixer``) at the front of ``PATH`` so every
``amixer`` invocation the script makes resolves here instead of the real
binary.

State (current Array raw value, UACDemoV10's own two channel raw values,
and injectable failure switches) is persisted in a small JSON file named
by the required ``AMIXER_FAKE_STATE`` environment variable, so repeated
invocations within one test (precheck, then set, then readback, then
rollback, ...) see a consistent, evolving picture -- exactly like the
real, stateful ALSA mixer it stands in for.

Supported invocations (only what the wrapper script actually calls):
  amixer -c Array sget 'PCM',1
  amixer -c Array sset 'PCM',1 <raw>
  amixer -c UACDemoV10 sget PCM

Failure injection (all optional, read from the SAME state file so a
test can change them mid-scenario by rewriting it):
  fail_array_sget:   bool -- Array sget exits 1 with no useful output.
  fail_array_sset:   bool -- Array sset exits 1 (mixer NOT changed).
  fail_uac_sget:     bool -- UACDemoV10 sget exits 1.
  array_sset_no_op_raw: int -- if set, an `sset` to THIS raw value
      "succeeds" (exit 0) but silently does NOT change the stored value
      -- simulates a partial/ineffective write the wrapper's own
      post-write readback must catch.
"""
import json
import os
import sys

STATE_PATH = os.environ["AMIXER_FAKE_STATE"]


def load():
    with open(STATE_PATH) as f:
        return json.load(f)


def save(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)


def array_block(raw):
    db = raw - 60
    pct = round(raw / 60 * 100)
    return (
        "Simple mixer control 'PCM',1\n"
        "  Capabilities: pvolume pvolume-joined pswitch pswitch-joined\n"
        "  Playback channels: Mono\n"
        "  Limits: Playback 0 - 60\n"
        f"  Mono: Playback {raw} [{pct}%] [{db:.2f}dB] [on]\n"
    )


def uac_block(raw_l, raw_r):
    def chan(raw):
        db = round((raw / 147.0) * 28.37 - 28.37, 2) if raw else -28.37
        pct = round(raw / 147 * 100)
        return raw, pct, db

    rl, pl, dl = chan(raw_l)
    rr, pr, dr = chan(raw_r)
    return (
        "Simple mixer control 'PCM',0\n"
        "  Capabilities: pvolume pswitch pswitch-joined\n"
        "  Playback channels: Front Left - Front Right\n"
        "  Limits: Playback 0 - 147\n"
        "  Mono:\n"
        f"  Front Left: Playback {rl} [{pl}%] [{dl:.2f}dB] [on]\n"
        f"  Front Right: Playback {rr} [{pr}%] [{dr:.2f}dB] [on]\n"
    )


def main(argv):
    state = load()
    # argv shape: -c <card> sget|sset 'CTRL',N [value]
    if len(argv) < 4 or argv[0] != "-c":
        print("fake_amixer: unrecognized invocation", file=sys.stderr)
        return 1
    card = argv[1]
    verb = argv[2]

    if card == "Array":
        if verb == "sget":
            if state.get("fail_array_sget"):
                print("fake_amixer: simulated Array sget failure", file=sys.stderr)
                return 1
            print(array_block(state["array_raw"]), end="")
            return 0
        if verb == "sset":
            value = int(argv[-1])
            if state.get("fail_array_sset"):
                print("fake_amixer: simulated Array sset failure", file=sys.stderr)
                return 1
            no_op_raw = state.get("array_sset_no_op_raw")
            if no_op_raw is not None and value == no_op_raw:
                # "Succeeds" but does not actually change the value --
                # simulates a partial/ineffective write.
                return 0
            state["array_raw"] = value
            save(state)
            return 0
    elif card == "UACDemoV10":
        if verb == "sget":
            if state.get("fail_uac_sget"):
                print("fake_amixer: simulated UACDemoV10 sget failure", file=sys.stderr)
                return 1
            print(uac_block(state["uac_raw_l"], state["uac_raw_r"]), end="")
            return 0

    print(f"fake_amixer: unrecognized invocation: {argv}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
