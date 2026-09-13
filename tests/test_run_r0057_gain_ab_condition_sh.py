"""R0061 — offline, hardware-free tests for
``docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh``.

Every test shadows the real `amixer` with a stateful fake
(``test_fakes/fake_amixer.py``) via a PATH-prepended temp directory, and
replaces the real probe with a controllable fake
(``test_fakes/fake_probe.py``) via ``R0057_AB_PROBE_LAUNCHER``. The real
``amixer`` binary is NEVER invoked by any test in this file — each test
asserts this indirectly (the fake's own distinctive output/behavior is
what every assertion is built on) and the wrapper's own PATH is
constructed so the fake is the only ``amixer`` it can find.

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

    def _base_env(self, *, condition_label: str = "fake") -> dict:
        env = dict(os.environ)
        env["PATH"] = f"{self.fake_bin_dir}:{env['PATH']}"
        env["AMIXER_FAKE_STATE"] = str(self.state_path)
        env["R0057_AB_CAPTURE_ROOT"] = str(self.capture_root)
        env["R0057_AB_PROBE_LAUNCHER"] = str(FAKE_PROBE)
        env["R0057_AB_TIMEOUT_BOUND_S"] = "3"
        env["R0057_AB_TIMEOUT_KILL_AFTER_S"] = "1"
        env["FAKE_PROBE_EXIT_CODE"] = "0"
        env["FAKE_PROBE_SLEEP_SECONDS"] = "0"
        env["FAKE_PROBE_WRITE_ARTIFACTS"] = "1"
        env["FAKE_PROBE_LABEL"] = condition_label
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
        self.assertIn("FINAL_EXIT_CODE=0", log)

        archives = self._archive_dirs()
        self.assertEqual(len(archives), 1)
        jsons = list(archives[0].glob("self_echo_probe_*.json"))
        wavs = list((archives[0] / "pcm").glob("*.wav"))
        self.assertEqual(len(jsons), 1)
        self.assertEqual(len(wavs), 2)
        manifest = (archives[0] / "MANIFEST.txt").read_text()
        self.assertIn("sha256=", manifest)
        self.assertIn(jsons[0].name, manifest)
        for w in wavs:
            self.assertIn(w.name, manifest)

        # Shared, fixed-name locations must be empty afterward -- archived,
        # not left in place for a second condition to collide with.
        self.assertEqual(list(self.capture_root.glob("self_echo_probe_*.json")), [])
        self.assertEqual(list((self.capture_root / "pcm").glob("*.wav")), [])


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


class TestPrecheckMismatch(_WrapperTestCase):
    def test_wrong_array_baseline_aborts_before_any_mutation(self) -> None:
        self._write_state(array_raw=55)  # not the accepted -20dB baseline
        env = self._base_env()
        result = self._run("A", env)

        self.assertEqual(result.returncode, 90)
        log = self._latest_log()
        self.assertIn("PRECHECK_FAIL", log)
        self.assertNotIn("SET_CONDITION_A", log)
        # The safety rollback still runs even though nothing was
        # intentionally mutated -- forcing the known-good baseline.
        self.assertIn("ROLLBACK_BEGIN", log)
        state = self._read_state()
        self.assertEqual(state["array_raw"], BASELINE_RAW)

    def test_wrong_uac_channel_aborts_before_any_mutation(self) -> None:
        self._write_state(uac_raw_r=100)
        env = self._base_env()
        result = self._run("A", env)

        self.assertEqual(result.returncode, 90)
        log = self._latest_log()
        self.assertIn("PRECHECK_FAIL", log)
        self.assertNotIn("SET_CONDITION_A", log)

    def test_unreadable_array_aborts_before_any_mutation(self) -> None:
        self._write_state(fail_array_sget=True)
        env = self._base_env()
        result = self._run("A", env)

        self.assertEqual(result.returncode, 90)
        log = self._latest_log()
        self.assertIn("PRECHECK_FAIL", log)
        self.assertIn("unreadable", log)


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
        # Rollback is still attempted after the partial/ineffective write,
        # and succeeds here since BASELINE_RAW is not the no-op target.
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
        self.assertIn("WRAPPER_FAILURE", log)
        self.assertIn("FINAL_EXIT_CODE=91", log)
        # The mixer is left at the condition's own value (60), NOT the
        # baseline -- the rollback genuinely did not take effect, and the
        # log must not claim otherwise.
        state = self._read_state()
        self.assertEqual(state["array_raw"], CONDITION_B_RAW)


class TestInterruption(_WrapperTestCase):
    def _run_and_signal(self, sig: int) -> subprocess.CompletedProcess:
        env = self._base_env(condition_label="condA")
        env["FAKE_PROBE_SLEEP_SECONDS"] = "10"
        env["FAKE_PROBE_WRITE_ARTIFACTS"] = "0"
        proc = subprocess.Popen(
            ["bash", str(SCRIPT), "A", "--i-have-explicit-operator-approval"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            # Poll for the wrapper to actually reach the probe-launch
            # line before signaling -- never a fixed timing guess.
            deadline = time.monotonic() + 10.0
            log_dir = self.capture_root / "warmup_hang_logs"
            saw_running = False
            while time.monotonic() < deadline:
                logs = list(log_dir.glob("gain_ab_condition_*.log")) if log_dir.exists() else []
                if logs and "RUNNING PROBE" in logs[0].read_text():
                    saw_running = True
                    break
                time.sleep(0.05)
            self.assertTrue(saw_running, "wrapper never reached RUNNING PROBE before deadline")

            proc.send_signal(sig)
            stdout, _ = proc.communicate(timeout=15.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
            self.fail(f"wrapper did not exit after signal {sig}; output:\n{stdout}")
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, "")

    def test_sigint_yields_130_and_rolls_back(self) -> None:
        result = self._run_and_signal(signal.SIGINT)
        self.assertEqual(result.returncode, 130)
        self.assertIn("INTERRUPTED: received SIGINT", result.stdout)
        self.assertIn("ROLLBACK_OUTCOME=clean", result.stdout)
        state = self._read_state()
        self.assertEqual(state["array_raw"], BASELINE_RAW)

    def test_sigterm_yields_143_and_rolls_back(self) -> None:
        result = self._run_and_signal(signal.SIGTERM)
        self.assertEqual(result.returncode, 143)
        self.assertIn("INTERRUPTED: received SIGTERM", result.stdout)
        self.assertIn("ROLLBACK_OUTCOME=clean", result.stdout)
        state = self._read_state()
        self.assertEqual(state["array_raw"], BASELINE_RAW)

    def test_interrupted_child_process_is_actually_terminated(self) -> None:
        """Not just a clean wrapper exit -- the fake probe's own process
        must actually be gone afterward, not orphaned."""
        env = self._base_env(condition_label="condA")
        env["FAKE_PROBE_SLEEP_SECONDS"] = "10"
        env["FAKE_PROBE_WRITE_ARTIFACTS"] = "0"
        marker = f"fake_probe_marker_{os.getpid()}_{id(self)}"
        env["FAKE_PROBE_LABEL"] = marker

        proc = subprocess.Popen(
            ["bash", str(SCRIPT), "A", "--i-have-explicit-operator-approval"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10.0
            log_dir = self.capture_root / "warmup_hang_logs"
            while time.monotonic() < deadline:
                logs = list(log_dir.glob("gain_ab_condition_*.log")) if log_dir.exists() else []
                if logs and "RUNNING PROBE" in logs[0].read_text():
                    break
                time.sleep(0.05)
            proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=15.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            self.fail("wrapper did not exit after SIGTERM")

        # Poll briefly for the child to be reaped from the process table.
        deadline = time.monotonic() + 5.0
        leftover = "1"
        while time.monotonic() < deadline:
            check = subprocess.run(
                ["pgrep", "-f", marker], capture_output=True, text=True
            )
            leftover = check.stdout.strip()
            if not leftover:
                break
            time.sleep(0.1)
        self.assertEqual(leftover, "", f"leftover fake_probe process(es): {leftover}")


class TestDistinctArtifactsAcrossConditions(_WrapperTestCase):
    def test_condition_a_then_b_preserve_distinct_artifacts(self) -> None:
        env_a = self._base_env(condition_label="condA")
        result_a = self._run("A", env_a)
        self.assertEqual(result_a.returncode, 0, result_a.stdout + result_a.stderr)

        env_b = self._base_env(condition_label="condB")
        result_b = self._run("B", env_b)
        self.assertEqual(result_b.returncode, 0, result_b.stdout + result_b.stderr)

        archives = self._archive_dirs()
        self.assertEqual(len(archives), 2)

        contents = {}
        for archive in archives:
            jsons = list(archive.glob("self_echo_probe_*.json"))
            self.assertEqual(len(jsons), 1)
            body = json.loads(jsons[0].read_text())
            contents[body["label"]] = archive

        self.assertIn("condA", contents)
        self.assertIn("condB", contents)
        self.assertNotEqual(contents["condA"], contents["condB"])

        wavs_a = {p.name for p in (contents["condA"] / "pcm").glob("*.wav")}
        wavs_b = {p.name for p in (contents["condB"] / "pcm").glob("*.wav")}
        self.assertEqual(len(wavs_a), 2)
        self.assertEqual(len(wavs_b), 2)
        self.assertTrue(all("condA" in w for w in wavs_a))
        self.assertTrue(all("condB" in w for w in wavs_b))
        self.assertEqual(wavs_a & wavs_b, set())

        # Shared fixed-name locations are empty after BOTH runs -- neither
        # condition's own artifacts were ever left where the other could
        # collide with them.
        self.assertEqual(list(self.capture_root.glob("self_echo_probe_*.json")), [])
        self.assertEqual(list((self.capture_root / "pcm").glob("*.wav")), [])


class TestNeverInvokesRealAmixer(_WrapperTestCase):
    def test_resolved_amixer_is_the_fake(self) -> None:
        """Positive confirmation that the fake, not any real amixer on
        this machine, is what the wrapper actually resolves via PATH."""
        env = self._base_env()
        check = subprocess.run(
            ["bash", "-c", "command -v amixer"], env=env, capture_output=True, text=True
        )
        self.assertEqual(check.stdout.strip(), str(self.fake_bin_dir / "amixer"))


if __name__ == "__main__":
    unittest.main()
