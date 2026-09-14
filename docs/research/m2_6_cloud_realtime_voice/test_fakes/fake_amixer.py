#!/usr/bin/env python3
"""R0063 -- stateful fake `amixer` for OFFLINE testing of
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

Supported invocations (only what the wrapper script actually calls), each
now checked for EXACT arity and EXACT control-identifier argument -- R0063:
prior revisions only checked card+verb and used ``argv[-1]`` for the value,
so a wrong control identifier (extra/missing args, or a control other than
the one the wrapper actually uses) could slip through silently:
  amixer -c Array sget 'PCM',1
  amixer -c Array sset 'PCM',1 <raw, integer 0-60>
  amixer -c UACDemoV10 sget PCM

Every OTHER invocation (wrong card, wrong verb, wrong control identifier,
wrong arity, a non-integer value, an out-of-range value, or an attempted
`sset` on UACDemoV10 -- which the real wrapper must never issue) is
explicitly REJECTED (exit 1, a distinct diagnostic message) -- there is no
catch-all success path.

R0062: every invocation (recognized or not) is appended, verbatim, to
``AMIXER_FAKE_INVOCATION_LOG`` (one line per call, optional -- silently
skipped if unset) BEFORE it is processed, so a test can assert on the exact
sequence of calls made -- in particular, that zero `sset` calls occurred
when a precheck was expected to fail closed.

Failure injection (all optional, read from the SAME state file so a test
can change them mid-scenario by rewriting it):
  fail_array_sget:        bool -- Array sget exits 1, no useful output.
  fail_array_sset:        bool -- Array sset exits 1 (mixer NOT changed).
  fail_uac_sget:          bool -- UACDemoV10 sget exits 1.
  array_sset_no_op_raw:   int  -- an `sset` to THIS raw value "succeeds"
      (exit 0) but silently does NOT change the stored value -- simulates
      a partial/ineffective write the wrapper's own post-write readback
      must catch.
  array_sset_fail_for_raw: int -- (R0063) an `sset` to THIS raw value
      reports FAILURE (exit 1, mixer NOT changed) while an `sset` to any
      OTHER value succeeds normally -- isolates "the ROLLBACK's own write
      specifically fails (loudly)" from "the CONDITION's own write fails",
      the opposite failure mode from `array_sset_no_op_raw` (which
      reports success but silently does nothing).
  array_sset_fail_for_raw_after_first_success: int -- (R0063 round 2) the
      FIRST `sset` to THIS raw value succeeds normally; every SUBSEQUENT
      `sset` to the SAME raw value fails (state unchanged) -- lets a test
      construct "the condition's own write succeeds, but the LATER
      rollback write (targeting the same value, e.g. BASELINE_RAW) fails
      while the underlying value is already, genuinely, at that value" --
      proving a failed restoration command is never silently converted
      into a reported success merely because the readback happens to
      observe the right value anyway.
  array_wrong_identity:   bool -- Array sget returns a DIFFERENT control
      name/limits line than the real device does, so a test can prove the
      wrapper's own control-identity/limits validation (not just the raw
      integer) actually rejects a misidentified control.
  array_identity_prefix_collision: bool -- (R0063) Array sget reports
      control identity `'PCM',10` -- a SUPERSET string of the expected
      `'PCM',1` -- proving the wrapper's own EXACT (never substring) match
      rejects a coincidentally-matching prefix.
  array_limits_prefix_collision: bool -- (R0063) Array sget reports
      `Limits: Playback 0 - 600` -- a SUPERSET string of the expected
      `0 - 60` -- same purpose as above, for the Limits line.
  uac_wrong_identity:     bool -- same as array_wrong_identity, for
      UACDemoV10.
  uac_limits_prefix_collision: bool -- (R0063) UACDemoV10 sget reports
      `Limits: Playback 0 - 1470` instead of `0 - 147`.
  array_switch_off:       bool -- Array sget reports the switch as [off].
  uac_switch_off:         bool -- UACDemoV10 sget reports [off] on the
      Front Left channel and [on] on Front Right -- a genuinely MIXED
      state, never both channels off, so a test can prove the wrapper
      rejects it on the strength of the one remaining [on] channel no
      longer being sufficient.
  uac_switch_off_after_first_read: bool -- (R0063) like
      `uac_fail_after_first_read`/`uac_mismatch_after_first_read`: UAC
      sget succeeds normally (both channels on) on its FIRST call (the
      precheck) but reports the mixed off/on state starting on the SECOND
      call onward (the wrapper's own later final-state check) -- isolates
      "wrong only at the end" from "wrong from the start."
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


def reject(argv, reason):
    print(f"fake_amixer: unrecognized invocation ({reason}): {argv}", file=sys.stderr)
    return 1


def array_block(raw, *, wrong_identity=False, identity_prefix_collision=False,
                 limits_prefix_collision=False, switch_off=False):
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
    elif identity_prefix_collision:
        # R0063: 'PCM',10 is a SUPERSET string of the expected 'PCM',1 --
        # a substring-based check would wrongly accept this; an exact
        # whole-line check must not.
        header = (
            "Simple mixer control 'PCM',10\n"
            "  Capabilities: pvolume\n"
            "  Playback channels: Mono\n"
            "  Limits: Playback 0 - 60\n"
        )
    else:
        limits_line = (
            "  Limits: Playback 0 - 600\n"
            if limits_prefix_collision
            else "  Limits: Playback 0 - 60\n"
        )
        header = (
            "Simple mixer control 'PCM',1\n"
            "  Capabilities: pvolume pvolume-joined pswitch pswitch-joined\n"
            "  Playback channels: Mono\n"
            f"{limits_line}"
        )
    db = raw - 60
    pct = round(raw / 60 * 100)
    sw = "off" if switch_off else "on"
    return header + f"  Mono: Playback {raw} [{pct}%] [{db:.2f}dB] [{sw}]\n"


def uac_block(raw_l, raw_r, *, wrong_identity=False, limits_prefix_collision=False,
              switch_off=False):
    def chan_db(raw):
        # Linear between (raw=0 -> -28.37dB) and (raw=147 -> -0.94dB) --
        # matches the REAL UACDemoV10's own measured dB curve (verified in
        # R0062 via `amixer -c UACDemoV10 cget numid=3`: dBminmax
        # min=-28.37dB max=-0.94dB).
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
        limits_line = (
            "  Limits: Playback 0 - 1470\n"
            if limits_prefix_collision
            else "  Limits: Playback 0 - 147\n"
        )
        header = (
            "Simple mixer control 'PCM',0\n"
            "  Capabilities: pvolume pswitch pswitch-joined\n"
            "  Playback channels: Front Left - Front Right\n"
            f"{limits_line}"
            "  Mono:\n"
        )
    pl, pr = round(raw_l / 147 * 100), round(raw_r / 147 * 100)
    # switch_off produces a genuinely MIXED state (Front Left off, Front
    # Right on) -- never both off -- so a test can prove the wrapper's own
    # switch validation is not satisfied merely by the presence of ONE
    # [on] marker somewhere in the output.
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

    if len(argv) < 3 or argv[0] != "-c":
        return reject(argv, "expected -c <card> <verb> ...")
    card = argv[1]
    verb = argv[2]

    if card == "Array" and verb == "sget":
        if len(argv) != 4 or argv[3] != "PCM,1":
            return reject(argv, "Array sget requires exactly 'PCM',1 as its sole control argument")
        if state.get("fail_array_sget"):
            print("fake_amixer: simulated Array sget failure", file=sys.stderr)
            return 1
        print(
            array_block(
                state["array_raw"],
                wrong_identity=bool(state.get("array_wrong_identity")),
                identity_prefix_collision=bool(state.get("array_identity_prefix_collision")),
                limits_prefix_collision=bool(state.get("array_limits_prefix_collision")),
                switch_off=bool(state.get("array_switch_off")),
            ),
            end="",
        )
        return 0

    if card == "Array" and verb == "sset":
        if len(argv) != 5 or argv[3] != "PCM,1":
            return reject(argv, "Array sset requires exactly 'PCM',1 <value>")
        try:
            value = int(argv[4])
        except ValueError:
            print(f"fake_amixer: malformed Array sset value: {argv[4]!r}", file=sys.stderr)
            return 1
        if not (0 <= value <= 60):
            print(f"fake_amixer: Array sset value out of range (0-60): {value}", file=sys.stderr)
            return 1
        if state.get("fail_array_sset"):
            print("fake_amixer: simulated Array sset failure", file=sys.stderr)
            return 1
        fail_for_raw = state.get("array_sset_fail_for_raw")
        if fail_for_raw is not None and value == int(fail_for_raw):
            print(
                f"fake_amixer: simulated Array sset failure for raw={value} "
                "(array_sset_fail_for_raw)",
                file=sys.stderr,
            )
            return 1
        # R0063 round 2: the FIRST sset to this raw value succeeds
        # normally; every subsequent sset to the SAME raw value fails
        # (state unchanged) -- lets a test construct "the condition's own
        # write to raw X succeeds, but the LATER rollback write (also
        # targeting X, e.g. when X == BASELINE_RAW) fails while the
        # underlying value is already, genuinely, at X" -- the exact
        # "write failed but baseline is observed anyway" combination.
        fail_after_first_target = state.get("array_sset_fail_for_raw_after_first_success")
        if fail_after_first_target is not None and value == int(fail_after_first_target):
            count = state.get("_array_sset_fail_after_first_count", 0) + 1
            state["_array_sset_fail_after_first_count"] = count
            save(state)
            if count > 1:
                print(
                    f"fake_amixer: simulated Array sset failure for raw={value} "
                    "(2nd+ attempt, array_sset_fail_for_raw_after_first_success)",
                    file=sys.stderr,
                )
                return 1
        no_op_raw = state.get("array_sset_no_op_raw")
        if no_op_raw is not None and value == int(no_op_raw):
            # "Succeeds" but does not actually change the value --
            # simulates a partial/ineffective write.
            return 0
        state["array_raw"] = value
        save(state)
        return 0

    if card == "UACDemoV10" and verb == "sget":
        if len(argv) != 4 or argv[3] != "PCM":
            return reject(argv, "UACDemoV10 sget requires exactly PCM as its sole control argument")
        # R0062/R0063: call-counted drift, so a test can distinguish "wrong
        # at PRECHECK" from "correct at precheck, wrong ONLY at the
        # wrapper's own later final-state check" -- the two real UAC reads
        # a normal run performs are precheck (call 1) and the
        # post-rollback final check (call 2). The `*_after_first_read`
        # flags only take effect from call 2 onward, never call 1.
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
        switch_off = bool(state.get("uac_switch_off"))
        if count > 1 and state.get("uac_switch_off_after_first_read"):
            switch_off = True
        print(
            uac_block(
                state["uac_raw_l"],
                raw_r,
                wrong_identity=bool(state.get("uac_wrong_identity")),
                limits_prefix_collision=bool(state.get("uac_limits_prefix_collision")),
                switch_off=switch_off,
            ),
            end="",
        )
        return 0

    # Every other combination (wrong card, wrong verb, a UACDemoV10 `sset`
    # attempt in particular -- the wrapper must never issue one) is
    # explicitly rejected. No catch-all success path.
    return reject(argv, "no matching supported invocation shape")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
