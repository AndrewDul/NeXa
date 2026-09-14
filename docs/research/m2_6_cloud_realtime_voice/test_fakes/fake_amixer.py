#!/usr/bin/env python3
"""R0062 -- stateful fake `amixer` for OFFLINE testing of
``run_r0057_gain_ab_condition.sh``. Never touches real hardware; the
wrapper's own tests put a directory containing this script (copied to the
literal name ``amixer``) at the front of ``PATH`` so every ``amixer``
invocation the script makes resolves here instead of the real binary.

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

Every OTHER invocation (wrong card, wrong verb, e.g. `cget`, or an
attempted `sset` on UACDemoV10 -- which the real wrapper must never issue)
is explicitly REJECTED (exit 1, "unrecognized invocation") -- there is no
catch-all success path.

R0062: every invocation (recognized or not) is appended, verbatim, to
``AMIXER_FAKE_INVOCATION_LOG`` (one line per call, optional -- silently
skipped if unset) BEFORE it is processed, so a test can assert on the
exact sequence of calls made -- in particular, that zero `sset` calls
occurred when a precheck was expected to fail closed.

Failure injection (all optional, read from the SAME state file so a test
can change them mid-scenario by rewriting it):
  fail_array_sget:        bool -- Array sget exits 1, no useful output.
  fail_array_sset:        bool -- Array sset exits 1 (mixer NOT changed).
  fail_uac_sget:          bool -- UACDemoV10 sget exits 1.
  array_sset_no_op_raw:   int  -- an `sset` to THIS raw value "succeeds"
      (exit 0) but silently does NOT change the stored value -- simulates
      a partial/ineffective write the wrapper's own post-write readback
      must catch.
  array_wrong_identity:   bool -- Array sget returns a DIFFERENT control
      name/limits line than the real device does, so a test can prove
      the wrapper's own control-identity/limits validation (not just the
      raw integer) actually rejects a misidentified control.
  uac_wrong_identity:     bool -- same, for UACDemoV10.
  array_switch_off:       bool -- Array sget reports the switch as [off].
  uac_switch_off:         bool -- UACDemoV10 sget reports [off] on one
      channel.
  uac_fail_after_first_read:     bool -- UACDemoV10 sget succeeds on its
      FIRST call (the precheck) but fails on every call after that (the
      wrapper's own later final-state check) -- isolates "wrong only at
      the end" from "wrong from the start."
  uac_mismatch_after_first_read: bool -- same, but the second-and-later
      reads report a mismatched Front Right raw value
      (`uac_mismatch_raw_r`, default current value + 1) instead of
      failing outright.
  uac_mismatch_raw_r:     int -- the Front Right raw value reported once
      `uac_mismatch_after_first_read` is active.
"""
import json
import os
import sys

STATE_PATH = os.environ["AMIXER_FAKE_STATE"]
INVOCATION_LOG_PATH = os.environ.get("AMIXER_FAKE_INVOCATION_LOG")


def load():
    with open(STATE_PATH) as f:
        return json.load(f)


def save(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)


def log_invocation(argv):
    if not INVOCATION_LOG_PATH:
        return
    with open(INVOCATION_LOG_PATH, "a") as f:
        f.write(" ".join(argv) + "\n")


def array_block(raw, *, wrong_identity=False, switch_off=False):
    if wrong_identity:
        # A deliberately WRONG control identity/limits line -- same raw
        # integer, different control -- to prove the wrapper's own
        # identity/limits check (not just the number) is what catches
        # this, not a coincidental "Playback N [" text match.
        header = (
            "Simple mixer control 'PCM',9\n"
            "  Capabilities: pvolume\n"
            "  Playback channels: Mono\n"
            "  Limits: Playback 0 - 100\n"
        )
    else:
        header = (
            "Simple mixer control 'PCM',1\n"
            "  Capabilities: pvolume pvolume-joined pswitch pswitch-joined\n"
            "  Playback channels: Mono\n"
            "  Limits: Playback 0 - 60\n"
        )
    db = raw - 60
    pct = round(raw / 60 * 100)
    sw = "off" if switch_off else "on"
    return header + f"  Mono: Playback {raw} [{pct}%] [{db:.2f}dB] [{sw}]\n"


def uac_block(raw_l, raw_r, *, wrong_identity=False, switch_off=False):
    def chan_db(raw):
        # Linear between (raw=0 -> -28.37dB) and (raw=147 -> -0.94dB) --
        # matches the REAL UACDemoV10's own measured dB curve (verified
        # this checkpoint via `amixer -c UACDemoV10 cget numid=3`:
        # dBminmax min=-28.37dB max=-0.94dB) -- NOT a naive
        # 0dB-at-max/linear-to-0 assumption (R0061's own fake had raw=147
        # reporting 0.00dB, which the real device never does).
        db_min, db_max = -28.37, -0.94
        return round(db_min + (raw / 147.0) * (db_max - db_min), 2)

    if wrong_identity:
        header = (
            "Simple mixer control 'PCM',9\n"
            "  Capabilities: pvolume\n"
            "  Playback channels: Front Left - Front Right\n"
            "  Limits: Playback 0 - 200\n"
            "  Mono:\n"
        )
    else:
        header = (
            "Simple mixer control 'PCM',0\n"
            "  Capabilities: pvolume pswitch pswitch-joined\n"
            "  Playback channels: Front Left - Front Right\n"
            "  Limits: Playback 0 - 147\n"
            "  Mono:\n"
        )
    pl, pr = round(raw_l / 147 * 100), round(raw_r / 147 * 100)
    sw_l = "off" if switch_off else "on"
    sw_r = "on"
    return (
        header
        + f"  Front Left: Playback {raw_l} [{pl}%] [{chan_db(raw_l):.2f}dB] [{sw_l}]\n"
        + f"  Front Right: Playback {raw_r} [{pr}%] [{chan_db(raw_r):.2f}dB] [{sw_r}]\n"
    )


def main(argv):
    log_invocation(argv)
    state = load()
    # argv shape: -c <card> sget|sset 'CTRL',N [value]
    if len(argv) < 4 or argv[0] != "-c":
        print("fake_amixer: unrecognized invocation", file=sys.stderr)
        return 1
    card = argv[1]
    verb = argv[2]

    if card == "Array" and verb == "sget":
        if state.get("fail_array_sget"):
            print("fake_amixer: simulated Array sget failure", file=sys.stderr)
            return 1
        print(
            array_block(
                state["array_raw"],
                wrong_identity=bool(state.get("array_wrong_identity")),
                switch_off=bool(state.get("array_switch_off")),
            ),
            end="",
        )
        return 0

    if card == "Array" and verb == "sset":
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

    if card == "UACDemoV10" and verb == "sget":
        # R0062: call-counted drift, so a test can distinguish "wrong at
        # PRECHECK" (fail_uac_sget / uac_wrong_identity, above) from
        # "correct at precheck, wrong ONLY at the wrapper's own later
        # final-state check" -- the two real UAC reads a normal run
        # performs are precheck (call 1) and the post-rollback final
        # check (call 2). `uac_fail_after_first_read`/
        # `uac_mismatch_after_first_read` only take effect from call 2
        # onward, never call 1.
        count = state.get("_uac_sget_count", 0) + 1
        state["_uac_sget_count"] = count
        save(state)
        if count > 1 and state.get("uac_fail_after_first_read"):
            print(
                "fake_amixer: simulated UACDemoV10 sget failure (after first read)",
                file=sys.stderr,
            )
            return 1
        if state.get("fail_uac_sget"):
            print("fake_amixer: simulated UACDemoV10 sget failure", file=sys.stderr)
            return 1
        raw_r = state["uac_raw_r"]
        if count > 1 and state.get("uac_mismatch_after_first_read"):
            raw_r = int(state.get("uac_mismatch_raw_r", raw_r + 1))
        print(
            uac_block(
                state["uac_raw_l"],
                raw_r,
                wrong_identity=bool(state.get("uac_wrong_identity")),
                switch_off=bool(state.get("uac_switch_off")),
            ),
            end="",
        )
        return 0

    # Every other combination (wrong card, wrong verb, a UACDemoV10
    # `sset` attempt in particular -- the wrapper must never issue one)
    # is explicitly rejected. No catch-all success path.
    print(f"fake_amixer: unrecognized invocation: {argv}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
