"""R0062 — offline, hardware-free tests for
``docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh``.

Every test shadows the real `amixer` with a stateful fake
(``test_fakes/fake_amixer.py``) via a PATH-prepended temp directory, and
replaces the real probe with a controllable fake
(``test_fakes/fake_probe.py``) via ``R0057_AB_PROBE_LAUNCHER``. The real
``amixer`` binary is NEVER invoked by any test in this file — each test
asserts this indirectly (the fake's own distinctive output/behavior is
what every assertion is built on) and a dedicated test confirms `command
-v amixer` resolves to the fake, never the system binary.

No real audio hardware, no Gemini, no `src/nexa` import. Pure
subprocess-level testing of the shell wrapper's own control flow.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = REPO_ROOT / "docs" / "research" / "m2_6_cloud_realtime_voice"
SCRIPT = RESEARCH_DIR / "run_r0057_gain_ab_condition.sh"
FAKE_AMIXER_SRC = RESEARCH_DIR / "test_fakes" / "fake_amixer.py"
FAKE_PROBE = RESEARCH_DIR / "test_fakes" / "fake_probe.py"

BASELINE_RAW = 40
CONDITION_B_RAW = 60
UAC_RAW = 147

EXPECTED_WAV_NAMES = [
    f"max_trial{i}_{kind}.wav" for i in (1, 2, 3) for kind in ("mic", "ref")
]


class _WrapperTestCase(unittest.TestCase):
    """Shared fixture: a fresh fake-PATH dir, fake amixer state, and a
    fresh, isolated capture-root directory per test -- never the real
    ``self_echo_captures/`` tree."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp_path = Path(self.tmp.name)

        self.fake_bin_dir = tmp_path / "fakebin"
        self.fake_bin_dir.mkdir()
        fake_amixer_dst = self.fake_bin_dir / "amixer"
        shutil.copy(FAKE_AMIXER_SRC, fake_amixer_dst)
        fake_amixer_dst.chmod(0o755)

        self.state_path = tmp_path / "amixer_state.json"
        self.invocation_log_path = tmp_path / "amixer_invocations.log"
        self.capture_root = tmp_path / "self_echo_captures"

        self._write_state(array_raw=BASELINE_RAW, uac_raw_l=UAC_RAW, uac_raw_r=UAC_RAW)

    def _write_state(self, **fields) -> None:
        state = {}
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text())
        state.update(fields)
        self.state_path.write_text(json.dumps(state))

    def _read_state(self) -> dict:
        return json.loads(self.state_path.read_text())

    def _invocations(self) -> list[str]:
        if not self.invocation_log_path.exists():
            return []
        return [
            line for line in self.invocation_log_path.read_text().splitlines() if line.strip()
        ]

    def _base_env(self, *, condition_label: str = "fake") -> dict:
        env = dict(os.environ)
        env["PATH"] = f"{self.fake_bin_dir}:{env['PATH']}"
        env["AMIXER_FAKE_STATE"] = str(self.state_path)
        env["AMIXER_FAKE_INVOCATION_LOG"] = str(self.invocation_log_path)
        env["R0057_AB_CAPTURE_ROOT"] = str(self.capture_root)
        env["R0057_AB_PROBE_LAUNCHER"] = str(FAKE_PROBE)
        env["R0057_AB_TIMEOUT_BOUND_S"] = "3"
        env["R0057_AB_TIMEOUT_KILL_AFTER_S"] = "1"
        env["FAKE_PROBE_EXIT_CODE"] = "0"
        env["FAKE_PROBE_SLEEP_SECONDS"] = "0"
        env["FAKE_PROBE_WRITE_ARTIFACTS"] = "1"
        env["FAKE_PROBE_LABEL"] = condition_label
        env["FAKE_PROBE_LEVEL"] = "max"
        env["FAKE_PROBE_TRIAL_COUNT"] = "3"
        env.pop("FAKE_PROBE_IGNORE_SIGTERM", None)
        env.pop("FAKE_PROBE_SPAWN_CHILD_SLEEP_SECONDS", None)
        return env

    def _run(self, condition: str, env: dict, timeout: float = 20.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(SCRIPT), condition, "--i-have-explicit-operator-approval"],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _latest_log(self) -> str:
        logs = sorted((self.capture_root / "warmup_hang_logs").glob("gain_ab_condition_*.log"))
        self.assertTrue(logs, "expected at least one persisted log file")
        return logs[-1].read_text()

    def _archive_dirs(self) -> list[Path]:
        root = self.capture_root / "gain_ab_experiment"
        if not root.exists():
            return []
        return sorted(p for p in root.iterdir() if p.is_dir())


class TestNormalCompletion(_WrapperTestCase):
    def test_condition_a_completes_cleanly_and_rolls_back(self) -> None:
        env = self._base_env(condition_label="condA")
        result = self._run("A", env)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = self._read_state()
        self.assertEqual(state["array_raw"], BASELINE_RAW)  # rolled back
        self.assertEqual(state["uac_raw_l"], UAC_RAW)
        self.assertEqual(state["uac_raw_r"], UAC_RAW)  # never written

        log = self._latest_log()
        self.assertIn("PRECHECK_OK", log)
        self.assertIn("SET_CONDITION_A", log)
        self.assertIn("EXIT_CODE=0", log)
        self.assertIn("ROLLBACK_OUTCOME=clean", log)
        self.assertIn("UAC_FINAL_STATUS=0", log)
        self.assertIn("ARCHIVE_COMPLETE=1", log)
        self.assertIn("FINAL_EXIT_CODE=0", log)

        archives = self._archive_dirs()
        self.assertEqual(len(archives), 1)
        jsons = list(archives[0].glob("self_echo_probe_*.json"))
        wavs = sorted(p.name for p in (archives[0] / "pcm").glob("*.wav"))
        self.assertEqual(len(jsons), 1)
        self.assertEqual(wavs, sorted(EXPECTED_WAV_NAMES))
        body = json.loads(jsons[0].read_text())
        self.assertEqual(len(body["trials"]), 3)

        manifest = (archives[0] / "MANIFEST.txt").read_text()
        self.assertIn("sha256=", manifest)
        self.assertIn(jsons[0].name, manifest)
        for w in wavs:
            self.assertIn(w, manifest)
        # Explicit original-JSON-path -> archived-file mapping present.
        self.assertIn("ORIGINAL JSON WAV PATH -> ARCHIVED FILE", manifest)
        self.assertIn("max_trial1_mic.wav -> pcm/max_trial1_mic.wav", manifest)

        # Shared, fixed-name locations must be empty afterward -- archived,
        # not left in place for a second condition to collide with.
        self.assertEqual(list(self.capture_root.glob("self_echo_probe_*.json")), [])
        self.assertEqual(list((self.capture_root / "pcm").glob("*.wav")), [])

        # Zero writes were ever issued to UACDemoV10.
        self.assertFalse(any("UACDemoV10 sset" in inv for inv in self._invocations()))


class TestNonzeroProbeStatus(_WrapperTestCase):
    def test_probe_failure_exit_code_is_preserved_and_rollback_still_verified(self) -> None:
        env = self._base_env(condition_label="condA")
        env["FAKE_PROBE_EXIT_CODE"] = "17"
        result = self._run("A", env)

        self.assertEqual(result.returncode, 17)
        state = self._read_state()
        self.assertEqual(state["array_raw"], BASELINE_RAW)

        log = self._latest_log()
        self.assertIn("EXIT_CODE=17", log)
        self.assertIn("INVALID RUN", log)
        self.assertIn("ROLLBACK_OUTCOME=clean", log)
        self.assertIn("FINAL_EXIT_CODE=17", log)


class TestSupervisorTimeout(_WrapperTestCase):
    def test_external_timeout_kills_probe_and_wrapper_preserves_124(self) -> None:
        env = self._base_env(condition_label="condA")
        env["R0057_AB_TIMEOUT_BOUND_S"] = "1"
        env["R0057_AB_TIMEOUT_KILL_AFTER_S"] = "1"
        env["FAKE_PROBE_SLEEP_SECONDS"] = "30"
        env["FAKE_PROBE_WRITE_ARTIFACTS"] = "0"

        result = self._run("A", env, timeout=20.0)

        self.assertEqual(result.returncode, 124)
        state = self._read_state()
        self.assertEqual(state["array_raw"], BASELINE_RAW)

        log = self._latest_log()
        self.assertIn("EXIT_CODE=124", log)
        self.assertIn("ROLLBACK_OUTCOME=clean", log)
        self.assertIn("FINAL_EXIT_CODE=124", log)

        # Confirm the whole process group was actually reaped, not just
        # that `timeout` itself finished -- parse the exact PGID this
        # run recorded and verify it, not a substring/marker guess.
        pgid = _parse_pgid(log)
        self.assertIsNotNone(pgid)
        self.assertFalse(_pgid_alive(pgid))


class TestPrecheckFailureCausesZeroWrites(_WrapperTestCase):
    """R0062 Correction 1: a failed precheck must never write ANYTHING to
    the mixer -- not the condition's own target, and not a "safety"
    restore to the baseline either. R0061's own test wrongly expected
    Array to be forced from 55 back to 40; that assumption is corrected
    here to its opposite: Array must stay at whatever the precheck itself
    observed, and the invocation log must show zero `sset` calls."""

    def test_wrong_array_baseline_causes_zero_writes(self) -> None:
        self._write_state(array_raw=55)  # not the accepted -20dB baseline
        env = self._base_env()
        result = self._run("A", env)

        self.assertEqual(result.returncode, 90)
        log = self._latest_log()
        self.assertIn("PRECHECK_FAIL", log)
        self.assertNotIn("SET_CONDITION_A", log)
        self.assertIn("ROLLBACK_SKIPPED", log)
        self.assertIn("ROLLBACK_OUTCOME=skipped_no_mutation", log)

        state = self._read_state()
        self.assertEqual(state["array_raw"], 55, "precheck failure must NEVER mutate Array")
        self.assertEqual(
            [inv for inv in self._invocations() if "sset" in inv],
            [],
            "zero sset calls must occur when precheck fails",
        )

    def test_wrong_uac_channel_causes_zero_writes(self) -> None:
        self._write_state(uac_raw_r=100)
        env = self._base_env()
        result = self._run("A", env)

        self.assertEqual(result.returncode, 90)
        log = self._latest_log()
        self.assertIn("PRECHECK_FAIL", log)
        self.assertNotIn("SET_CONDITION_A", log)
        self.assertEqual([inv for inv in self._invocations() if "sset" in inv], [])

    def test_unreadable_array_causes_zero_writes(self) -> None:
        self._write_state(fail_array_sget=True)
        env = self._base_env()
        result = self._run("A", env)

        self.assertEqual(result.returncode, 90)
        log = self._latest_log()
        self.assertIn("PRECHECK_FAIL", log)
        self.assertIn("unreadable", log)
        self.assertEqual([inv for inv in self._invocations() if "sset" in inv], [])

    def test_wrong_control_identity_causes_zero_writes(self) -> None:
        """R0062 Correction 2: a coincidentally-matching raw integer from
        the WRONG control must still be rejected -- not just a wrong
        number."""
        self._write_state(array_wrong_identity=True)
        env = self._base_env()
        result = self._run("A", env)

        self.assertEqual(result.returncode, 90)
        log = self._latest_log()
        self.assertIn("UNEXPECTED_CONTROL_IDENTITY", log)
        self.assertEqual([inv for inv in self._invocations() if "sset" in inv], [])

    def test_switch_off_causes_zero_writes(self) -> None:
        self._write_state(array_switch_off=True)
        env = self._base_env()
        result = self._run("A", env)

        self.assertEqual(result.returncode, 90)
        log = self._latest_log()
        self.assertIn("SWITCH_NOT_ON", log)
        self.assertEqual([inv for inv in self._invocations() if "sset" in inv], [])


class TestFailedSetReadback(_WrapperTestCase):
    def test_ineffective_condition_write_is_detected_and_aborts(self) -> None:
        # The write to CONDITION_B_RAW "succeeds" (exit 0) but silently
        # does not change the stored value -- the wrapper's own
        # post-write readback must catch this, not just trust the exit
        # code.
        self._write_state(array_sset_no_op_raw=CONDITION_B_RAW)
        env = self._base_env()
        result = self._run("B", env)

        self.assertEqual(result.returncode, 92)
        log = self._latest_log()
        self.assertIn("SET_CONDITION_B", log)
        self.assertIn("did not verify", log)
        # Rollback is still attempted after the partial/ineffective write
        # (mutation WAS attempted, unlike the precheck-failure case), and
        # succeeds here since BASELINE_RAW is not the no-op target.
        self.assertIn("ROLLBACK_OUTCOME=clean", log)
        state = self._read_state()
        self.assertEqual(state["array_raw"], BASELINE_RAW)


class TestFailedRollback(_WrapperTestCase):
    def test_rollback_that_does_not_verify_forces_nonzero_exit(self) -> None:
        # The condition's OWN write (to CONDITION_B_RAW) works normally;
        # only the LATER rollback write (to BASELINE_RAW) is made
        # ineffective -- isolating "failed rollback" from "failed set".
        self._write_state(array_sset_no_op_raw=BASELINE_RAW)
        env = self._base_env()
        result = self._run("B", env)

        self.assertEqual(result.returncode, 91)
        log = self._latest_log()
        self.assertIn("SET_CONDITION_B", log)
        self.assertIn("EXIT_CODE=0", log)  # the probe itself succeeded
        self.assertIn("ROLLBACK_OUTCOME=readback_mismatch", log)
        self.assertIn("UAC_FINAL_STATUS=0", log)  # UAC itself was fine
        self.assertIn("WRAPPER_FAILURE", log)
        self.assertIn("FINAL_EXIT_CODE=91", log)
        # The mixer is left at the condition's own value (60), NOT the
        # baseline -- the rollback genuinely did not take effect, and the
        # log must not claim otherwise.
        state = self._read_state()
        self.assertEqual(state["array_raw"], CONDITION_B_RAW)


class TestFinalUacCheck(_WrapperTestCase):
    """R0062 Correction 2: the FINAL UACDemoV10 state (after an otherwise
    fully successful run) is independently validated -- an unreadable or
    mismatched reading there must prevent a successful wrapper exit, and
    UACDemoV10 is never written to try to fix it."""

    def test_final_uac_read_failure_after_otherwise_successful_run(self) -> None:
        # Succeeds at precheck (call 1); fails starting at the final
        # check (call 2+) -- isolates "wrong only at the end".
        self._write_state(uac_fail_after_first_read=True)
        env = self._base_env(condition_label="condA")
        result = self._run("A", env)

        self.assertEqual(result.returncode, 91)
        log = self._latest_log()
        self.assertIn("EXIT_CODE=0", log)  # probe itself succeeded
        self.assertIn("ROLLBACK_OUTCOME=clean", log)  # Array itself restored fine
        self.assertIn("UAC_FINAL_CHECK_FAILED", log)
        self.assertIn("UAC_FINAL_STATUS=1", log)
        self.assertIn("FINAL_EXIT_CODE=91", log)
        # UACDemoV10 was never written, under any circumstance.
        self.assertEqual([inv for inv in self._invocations() if "UACDemoV10 sset" in inv], [])

    def test_final_uac_mismatch_after_otherwise_successful_run(self) -> None:
        self._write_state(uac_mismatch_after_first_read=True, uac_mismatch_raw_r=99)
        env = self._base_env(condition_label="condA")
        result = self._run("A", env)

        self.assertEqual(result.returncode, 91)
        log = self._latest_log()
        self.assertIn("EXIT_CODE=0", log)
        self.assertIn("ROLLBACK_OUTCOME=clean", log)
        self.assertIn("UAC_FINAL_STATUS=1", log)
        self.assertIn("FINAL_EXIT_CODE=91", log)
        self.assertEqual([inv for inv in self._invocations() if "UACDemoV10 sset" in inv], [])
        # UACDemoV10 is never written by this script -- the underlying
        # (fake) hardware value is unchanged; only the wrapper's own
        # LATER *reading* of it was made to mismatch, to isolate
        # "detected wrong" from "attempted to fix."
        state = self._read_state()
        self.assertEqual(state["uac_raw_r"], UAC_RAW)


def _parse_pgid(log: str) -> str | None:
    for line in log.splitlines():
        if line.startswith("CHILD_PGID=") or "CHILD_PGID=" in line:
            for token in line.split():
                if token.startswith("CHILD_PGID="):
                    return token.split("=", 1)[1]
    return None


def _pgid_alive(pgid: str) -> bool:
    check = subprocess.run(["pgrep", "-g", pgid], capture_output=True, text=True)
    return check.returncode == 0 and bool(check.stdout.strip())


class TestInterruption(_WrapperTestCase):
    """R0062 Correction 3: signal handling terminates the WHOLE recorded
    process group, verified via the exact PGID this run itself reported
    in its own log -- never a fuzzy `pgrep -f <env-var marker>` guess."""

    def _start_and_wait_for_pgid(
        self, env: dict, *, wait_for_probe_ready: bool = False
    ) -> tuple[subprocess.Popen, str]:
        """Starts the wrapper and returns once its own log shows a
        recorded `CHILD_PGID=`. When `wait_for_probe_ready` is set, ALSO
        waits for the fake probe's own `FAKE_PROBE_READY` marker -- i.e.
        that it has actually installed its signal disposition and
        spawned its own child -- before returning, so a test that
        signals immediately afterward cannot race a probe that has not
        yet reached its steady state (the wrapper's own CHILD_PGID line
        only proves ITS OWN fork happened, not that the exec'd probe has
        run any of its own Python yet)."""
        proc = subprocess.Popen(
            ["bash", str(SCRIPT), "A", "--i-have-explicit-operator-approval"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log_dir = self.capture_root / "warmup_hang_logs"
        deadline = time.monotonic() + 10.0
        pgid = None
        while time.monotonic() < deadline:
            logs = list(log_dir.glob("gain_ab_condition_*.log")) if log_dir.exists() else []
            if logs:
                text = logs[0].read_text()
                candidate = _parse_pgid(text)
                ready_enough = not wait_for_probe_ready or "FAKE_PROBE_READY" in text
                if candidate and ready_enough:
                    pgid = candidate
                    break
            time.sleep(0.05)
        self.assertIsNotNone(pgid, "wrapper never recorded a CHILD_PGID before the deadline")
        return proc, pgid

    def _signal_and_collect(self, proc: subprocess.Popen, sig: int) -> subprocess.CompletedProcess:
        try:
            proc.send_signal(sig)
            stdout, _ = proc.communicate(timeout=15.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
            self.fail(f"wrapper did not exit after signal {sig}; output:\n{stdout}")
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, "")

    def test_sigint_yields_130_rolls_back_and_group_is_empty(self) -> None:
        env = self._base_env(condition_label="condA")
        env["FAKE_PROBE_SLEEP_SECONDS"] = "10"
        env["FAKE_PROBE_WRITE_ARTIFACTS"] = "0"
        proc, pgid = self._start_and_wait_for_pgid(env)
        result = self._signal_and_collect(proc, signal.SIGINT)

        self.assertEqual(result.returncode, 130)
        self.assertIn("INTERRUPTED: received SIGINT", result.stdout)
        self.assertIn("ROLLBACK_OUTCOME=clean", result.stdout)
        self.assertFalse(_pgid_alive(pgid), "no process may remain in the owned group")
        state = self._read_state()
        self.assertEqual(state["array_raw"], BASELINE_RAW)

    def test_sigterm_yields_143_rolls_back_and_group_is_empty(self) -> None:
        env = self._base_env(condition_label="condA")
        env["FAKE_PROBE_SLEEP_SECONDS"] = "10"
        env["FAKE_PROBE_WRITE_ARTIFACTS"] = "0"
        proc, pgid = self._start_and_wait_for_pgid(env)
        result = self._signal_and_collect(proc, signal.SIGTERM)

        self.assertEqual(result.returncode, 143)
        self.assertIn("INTERRUPTED: received SIGTERM", result.stdout)
        self.assertIn("ROLLBACK_OUTCOME=clean", result.stdout)
        self.assertFalse(_pgid_alive(pgid))
        state = self._read_state()
        self.assertEqual(state["array_raw"], BASELINE_RAW)

    def test_sigterm_resistant_descendant_requires_sigkill_escalation(self) -> None:
        """The exact scenario a normally-terminating `time.sleep` process
        cannot exercise: the fake probe IGNORES SIGTERM and has spawned
        its own real child process. The wrapper's own group-wide TERM
        must fail to end it, triggering the documented SIGKILL
        escalation, and the group must be confirmed empty afterward --
        never merely that the wrapper itself exited.

        Both `R0057_AB_TIMEOUT_BOUND_S` and `R0057_AB_TIMEOUT_KILL_AFTER_S`
        are deliberately set LARGE here: the base env's own small
        defaults (3s bound, 1s kill-after) are tuned for the OTHER,
        genuinely-timeout-driven tests in this file, but `timeout(1)`
        would otherwise fire its OWN independent alarm here too (at 3s)
        and forward its OWN TERM/KILL to its child on its OWN schedule,
        racing with and masking THIS wrapper's own
        `terminate_process_group` escalation (confirmed directly this
        checkpoint via `DEBUG_TPG_*` tracing: with the small defaults,
        `timeout`'s own alarm fired mid-test and the group emptied
        before this script's own 5s poll loop ever logged
        `PROCESS_GROUP_STILL_ALIVE_AFTER_TERM`). Setting both well
        beyond this test's own runtime isolates and proves the
        wrapper's OWN signal-path mechanism specifically, independent
        of `timeout`'s own separate, unrelated one."""
        env = self._base_env(condition_label="condA")
        env["R0057_AB_TIMEOUT_BOUND_S"] = "120"
        env["R0057_AB_TIMEOUT_KILL_AFTER_S"] = "30"
        env["FAKE_PROBE_SLEEP_SECONDS"] = "30"
        env["FAKE_PROBE_WRITE_ARTIFACTS"] = "0"
        env["FAKE_PROBE_IGNORE_SIGTERM"] = "1"
        env["FAKE_PROBE_SPAWN_CHILD_SLEEP_SECONDS"] = "987.65"
        proc, pgid = self._start_and_wait_for_pgid(env, wait_for_probe_ready=True)

        # Confirm the group actually has multiple members (probe + its
        # spawned child) before signaling, so the escalation is
        # meaningfully exercised, not accidentally already-empty.
        before = subprocess.run(["pgrep", "-g", pgid], capture_output=True, text=True)
        self.assertGreaterEqual(len(before.stdout.split()), 2)

        result = self._signal_and_collect(proc, signal.SIGTERM)

        self.assertEqual(result.returncode, 143)
        self.assertIn("PROCESS_GROUP_STILL_ALIVE_AFTER_TERM", result.stdout)
        self.assertIn("PROCESS_GROUP_TERMINATED", result.stdout)
        self.assertFalse(
            _pgid_alive(pgid), "the SIGTERM-ignoring probe and its child must both be gone"
        )
        state = self._read_state()
        self.assertEqual(state["array_raw"], BASELINE_RAW)


class TestIncompleteArchive(_WrapperTestCase):
    """R0062 Correction 4: artifact acceptance requires EXACTLY the
    expected evidence for a claimed-successful 3-trial condition --
    missing WAVs (or a JSON reporting fewer than 3 trials) must not
    produce a successful archive status, even though the probe itself
    exited 0."""

    def test_missing_wav_pairs_prevent_successful_exit(self) -> None:
        env = self._base_env(condition_label="condA")
        env["FAKE_PROBE_TRIAL_COUNT"] = "2"  # only 2 of the expected 3
        result = self._run("A", env)

        self.assertEqual(result.returncode, 93)
        log = self._latest_log()
        self.assertIn("EXIT_CODE=0", log)  # the probe itself "succeeded"
        self.assertIn("ARCHIVE_MISSING_EXPECTED_WAV: max_trial3_mic.wav", log)
        self.assertIn("ARCHIVE_MISSING_EXPECTED_WAV: max_trial3_ref.wav", log)
        self.assertIn("ARCHIVE_COMPLETE=0", log)
        self.assertIn("FINAL_EXIT_CODE=93", log)
        # Rollback still ran and verified despite the incomplete evidence.
        self.assertIn("ROLLBACK_OUTCOME=clean", log)

        # The partial evidence that DOES exist is still preserved
        # (archived), not discarded, even though the run is not accepted
        # as a successful condition.
        archives = self._archive_dirs()
        self.assertEqual(len(archives), 1)
        wavs = {p.name for p in (archives[0] / "pcm").glob("*.wav")}
        self.assertEqual(wavs, {"max_trial1_mic.wav", "max_trial1_ref.wav",
                                 "max_trial2_mic.wav", "max_trial2_ref.wav"})

    def test_no_artifacts_at_all_prevent_successful_exit(self) -> None:
        env = self._base_env(condition_label="condA")
        env["FAKE_PROBE_WRITE_ARTIFACTS"] = "0"
        result = self._run("A", env)

        self.assertEqual(result.returncode, 93)
        log = self._latest_log()
        self.assertIn("ARCHIVE_JSON_COUNT=0", log)
        self.assertIn("ARCHIVE_COMPLETE=0", log)
        self.assertIn("FINAL_EXIT_CODE=93", log)


class TestDistinctArtifactsAcrossConditions(_WrapperTestCase):
    """R0062 Correction 4: the real probe always writes the SAME fixed
    WAV filenames regardless of condition -- this is the actual
    collision risk, not something a per-condition-labeled filename (as
    R0061's own fake used) could ever expose. Both runs write IDENTICAL
    filenames with DIFFERENT content; condition A's own archived bytes
    and hashes must be provably unchanged after condition B runs."""

    def test_identical_filenames_different_contents_a_survives_b(self) -> None:
        env_a = self._base_env(condition_label="condA")
        result_a = self._run("A", env_a)
        self.assertEqual(result_a.returncode, 0, result_a.stdout + result_a.stderr)

        archives_after_a = self._archive_dirs()
        self.assertEqual(len(archives_after_a), 1)
        archive_a = archives_after_a[0]
        hashes_a_before = {
            p.name: _sha256(p) for p in (archive_a / "pcm").glob("*.wav")
        }
        json_a_before = next(archive_a.glob("self_echo_probe_*.json")).read_text()

        # A brand-new run needs a distinguishable timestamp-based archive
        # directory name -- ensure B's own timestamp differs from A's.
        time.sleep(1.1)

        env_b = self._base_env(condition_label="condB")
        result_b = self._run("B", env_b)
        self.assertEqual(result_b.returncode, 0, result_b.stdout + result_b.stderr)

        archives_after_b = self._archive_dirs()
        self.assertEqual(len(archives_after_b), 2)
        archive_b = next(a for a in archives_after_b if a != archive_a)

        # A's own archived bytes and hashes are BYTE-FOR-BYTE unchanged.
        hashes_a_after = {p.name: _sha256(p) for p in (archive_a / "pcm").glob("*.wav")}
        self.assertEqual(hashes_a_before, hashes_a_after)
        self.assertEqual(next(archive_a.glob("self_echo_probe_*.json")).read_text(), json_a_before)

        # Both archives use the IDENTICAL fixed filenames (the real
        # collision risk) but distinguishable CONTENT.
        names_a = {p.name for p in (archive_a / "pcm").glob("*.wav")}
        names_b = {p.name for p in (archive_b / "pcm").glob("*.wav")}
        self.assertEqual(names_a, set(EXPECTED_WAV_NAMES))
        self.assertEqual(names_b, set(EXPECTED_WAV_NAMES))

        content_a = (archive_a / "pcm" / "max_trial1_mic.wav").read_bytes()
        content_b = (archive_b / "pcm" / "max_trial1_mic.wav").read_bytes()
        self.assertIn(b"condA", content_a)
        self.assertIn(b"condB", content_b)
        self.assertNotEqual(content_a, content_b)

        # Shared fixed-name locations are empty after BOTH runs.
        self.assertEqual(list(self.capture_root.glob("self_echo_probe_*.json")), [])
        self.assertEqual(list((self.capture_root / "pcm").glob("*.wav")), [])


class TestStorageFailure(_WrapperTestCase):
    """R0062 Correction 5: a failure to create the required
    evidence-storage directories must abort BEFORE any precheck or
    mutation -- never silently fall through."""

    def test_unwritable_capture_root_parent_aborts_before_any_write(self) -> None:
        tmp_path = Path(self.tmp.name)
        readonly_parent = tmp_path / "readonly_parent"
        readonly_parent.mkdir()
        readonly_parent.chmod(0o555)
        self.addCleanup(readonly_parent.chmod, 0o755)

        env = self._base_env()
        env["R0057_AB_CAPTURE_ROOT"] = str(readonly_parent / "self_echo_captures")
        result = self._run("A", env)

        self.assertEqual(result.returncode, 95)
        # The FATAL line is written to stderr (`>&2`) and, since this
        # failure happens BEFORE the log redirection is even set up, it
        # is never captured in any persisted log file either -- check
        # both real output streams directly, not a log.
        combined = result.stdout + result.stderr
        self.assertIn("could not create required evidence-storage directories", combined)
        self.assertFalse((readonly_parent / "self_echo_captures").exists())
        # No hardware WRITE occurred (the safety-net UAC read-only final
        # check in rollback() still runs even here -- harmless, since it
        # never mutates anything; only `sset` calls are prohibited).
        self.assertEqual([inv for inv in self._invocations() if "sset" in inv], [])


class TestConcurrencyLock(_WrapperTestCase):
    """R0062 Correction 4/5: `find -newer` alone is not proof of run
    ownership -- a concurrency lock prevents two wrapper invocations from
    ever sharing the same capture locations at all."""

    def test_second_concurrent_invocation_is_refused(self) -> None:
        env = self._base_env(condition_label="condA")
        env["FAKE_PROBE_SLEEP_SECONDS"] = "5"
        # Must comfortably exceed the fake probe's own sleep -- otherwise
        # the wrapper's own external `timeout` (base env default 3s)
        # kills it before it can finish, which would test a timeout, not
        # the concurrency lock.
        env["R0057_AB_TIMEOUT_BOUND_S"] = "15"
        first = subprocess.Popen(
            ["bash", str(SCRIPT), "A", "--i-have-explicit-operator-approval"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            lock_dir = self.capture_root / ".gain_ab_experiment.lock"
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not lock_dir.exists():
                time.sleep(0.05)
            self.assertTrue(lock_dir.exists(), "first invocation never acquired the lock")

            second_env = self._base_env(condition_label="condB")
            second = self._run("B", second_env, timeout=10.0)
            self.assertEqual(second.returncode, 94)
            self.assertIn("already holds the concurrency lock", second.stdout + second.stderr)

            stdout, _ = first.communicate(timeout=15.0)
            self.assertEqual(first.returncode, 0, stdout)
        finally:
            if first.poll() is None:
                first.kill()
                first.communicate()

        # The lock is released after the first run finishes.
        self.assertFalse((self.capture_root / ".gain_ab_experiment.lock").exists())


class TestFakeAmixerRejectsUnsupportedInvocations(unittest.TestCase):
    """R0062 Correction 6: the fake itself must reject anything the real
    wrapper should never issue -- confirms there is no silent catch-all
    success path in the test double."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "state.json"
        self.state_path.write_text(
            json.dumps({"array_raw": 40, "uac_raw_l": 147, "uac_raw_r": 147})
        )

    def _call(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["AMIXER_FAKE_STATE"] = str(self.state_path)
        return subprocess.run(
            ["python3", str(FAKE_AMIXER_SRC), *args], env=env, capture_output=True, text=True
        )

    def test_unsupported_verb_is_rejected(self) -> None:
        result = self._call("-c", "Array", "cget", "numid=6")
        self.assertEqual(result.returncode, 1)

    def test_uac_sset_is_rejected(self) -> None:
        """The wrapper must never attempt this; the fake must not let it
        silently succeed if it ever did."""
        result = self._call("-c", "UACDemoV10", "sset", "PCM", "100")
        self.assertEqual(result.returncode, 1)

    def test_unknown_card_is_rejected(self) -> None:
        result = self._call("-c", "SomeOtherCard", "sget", "PCM")
        self.assertEqual(result.returncode, 1)


class TestNeverInvokesRealAmixer(_WrapperTestCase):
    def test_resolved_amixer_is_the_fake(self) -> None:
        """Positive confirmation that the fake, not any real amixer on
        this machine, is what the wrapper actually resolves via PATH."""
        env = self._base_env()
        check = subprocess.run(
            ["bash", "-c", "command -v amixer"], env=env, capture_output=True, text=True
        )
        self.assertEqual(check.stdout.strip(), str(self.fake_bin_dir / "amixer"))


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
