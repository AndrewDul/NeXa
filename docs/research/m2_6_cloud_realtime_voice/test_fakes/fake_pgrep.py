#!/usr/bin/env python3
"""R0063 -- fake `pgrep` for OFFLINE testing of a genuine process-
INSPECTION ERROR in ``run_r0057_gain_ab_condition.sh``'s own
``pgid_liveness`` helper (used inside ``terminate_process_group``).

The wrapper only ever invokes `pgrep -g <pgid>`. This shim delegates to the
REAL system `pgrep` binary for every PGID EXCEPT:
  - one specific value named by `PGREP_FAKE_ERROR_PGID` (optional), for
    which it deterministically simulates a fatal inspection error (exit 3,
    matching real pgrep's own documented "fatal error" exit code family,
    distinct from its exit 1 "no processes matched") instead of either
    "alive" (0) or "confirmed empty" (1); or
  - every invocation, when `PGREP_FAKE_ALWAYS_ERROR=1` is set (a blanket
    mode, used when the test does not know the real pgid in advance and
    only needs to prove the wrapper never treats an inspection error as
    verified emptiness, regardless of which pgid it was checking).

Every other invocation is passed through to the REAL, absolute-path
`pgrep` binary unchanged -- this shim never fabricates "confirmed empty"
for anything it was not specifically asked to intercept, and it locates
the real binary by an ABSOLUTE path (never a bare `pgrep` lookup through
PATH), since PATH is shadowed by this shim's own directory during a test
and a bare lookup would recurse into itself.
"""
import os
import subprocess
import sys

_CANDIDATE_REAL_PGREP_PATHS = ("/usr/bin/pgrep", "/bin/pgrep", "/usr/local/bin/pgrep")


def _real_pgrep_path() -> str:
    override = os.environ.get("PGREP_REAL_BINARY")
    if override and os.path.exists(override):
        return override
    for candidate in _CANDIDATE_REAL_PGREP_PATHS:
        if os.path.exists(candidate):
            return candidate
    # Last resort: this should not happen on the systems this suite is
    # designed for, but fail loudly rather than silently recursing.
    print("fake_pgrep: could not locate a real pgrep binary", file=sys.stderr)
    sys.exit(127)


def main(argv):
    if os.environ.get("PGREP_FAKE_ALWAYS_ERROR") == "1":
        print("fake_pgrep: simulated fatal inspection error (blanket mode)", file=sys.stderr)
        return 3
    error_pgid = os.environ.get("PGREP_FAKE_ERROR_PGID")
    if error_pgid and len(argv) == 2 and argv[0] == "-g" and argv[1] == error_pgid:
        print("fake_pgrep: simulated fatal inspection error", file=sys.stderr)
        return 3
    result = subprocess.run([_real_pgrep_path(), *argv])
    return result.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
