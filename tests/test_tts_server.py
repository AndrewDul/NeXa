"""M2.4 `PiperHttpServer` tests — no real Piper venv/voice/network needed
for the deterministic cases; an opt-in live test exercises the real
external process (`NEXA_RUN_LIVE_TTS_TEST=1`).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.tts import (  # noqa: E402
    DEFAULT_PIPER_NICE,
    EN_VOICE,
    PiperHttpConfig,
    PiperHttpServer,
)
from nexa.tts.config import default_piper_nice  # noqa: E402
from nexa.tts.errors import (  # noqa: E402
    PiperHttpError,
    PiperServerStartError,
    PiperVenvNotFoundError,
    PiperVoiceNotFoundError,
)


def _fake_config() -> PiperHttpConfig:
    tmp = tempfile.mkdtemp()
    venv_python = Path(tmp) / "bin" / "python3"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n")
    venv_python.chmod(0o755)
    voices_dir = Path(tmp) / "voices"
    voices_dir.mkdir()
    (voices_dir / f"{EN_VOICE}.onnx").write_bytes(b"")
    return PiperHttpConfig(venv_python=venv_python, voices_dir=voices_dir)


class _FakeResponse:
    def __init__(self, status: int, text: str = "") -> None:
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._text

    async def read(self):
        return b"RIFF....WAVEfmt "


class TestPiperHttpServerValidatesAtConstruction(unittest.TestCase):
    def test_missing_venv_raises_explicit_error(self) -> None:
        config = PiperHttpConfig(venv_python=Path("/nonexistent/piper-venv/bin/python3"))
        with self.assertRaises(PiperVenvNotFoundError):
            PiperHttpServer(config)

    def test_missing_voice_model_raises_explicit_error(self) -> None:
        config = PiperHttpConfig(voices_dir=Path("/nonexistent/voices"))
        with self.assertRaises(PiperVoiceNotFoundError):
            PiperHttpServer(config)


class TestPiperHttpServerStopWithoutStart(unittest.IsolatedAsyncioTestCase):
    async def test_stop_without_start_is_a_safe_no_op(self) -> None:
        # Construct with a fake-but-present venv/voice layout so __init__
        # validation passes, without ever starting a real process.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            venv_python = Path(tmp) / "bin" / "python3"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("#!/bin/sh\n")
            venv_python.chmod(0o755)
            voices_dir = Path(tmp) / "voices"
            voices_dir.mkdir()
            (voices_dir / f"{EN_VOICE}.onnx").write_bytes(b"")

            config = PiperHttpConfig(venv_python=venv_python, voices_dir=voices_dir)
            server = PiperHttpServer(config)
            await server.stop()  # must not raise


class TestSynthesizeErrorHandling(unittest.IsolatedAsyncioTestCase):
    """Fakes the true external boundary (the HTTP call itself) — never
    mocks `PiperHttpServer` internals — matching this repo's established
    testing philosophy."""

    async def test_non_200_response_raises_explicit_error_no_silent_fallback(self) -> None:
        server = PiperHttpServer(_fake_config())
        with mock.patch(
            "aiohttp.ClientSession.post",
            return_value=_FakeResponse(500, "internal error"),
        ):
            with self.assertRaises(PiperHttpError):
                await server.synthesize("hello", voice=EN_VOICE)
        await server.stop()

    async def test_successful_response_returns_raw_audio_bytes(self) -> None:
        server = PiperHttpServer(_fake_config())
        with mock.patch("aiohttp.ClientSession.post", return_value=_FakeResponse(200)):
            audio = await server.synthesize("hello", voice=EN_VOICE)
        self.assertTrue(audio.startswith(b"RIFF"))
        await server.stop()


class _CapturingExec:
    """Records every ``asyncio.create_subprocess_exec`` call, then makes the
    server's readiness loop give up fast by returning a process that reports
    it exited (so ``start()`` raises ``PiperServerStartError`` — we only care
    about the argv it was launched with)."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))

        class _Proc:
            returncode = 1

            class stdout:  # noqa: N801
                @staticmethod
                async def read():
                    return b"stubbed exit"

            def terminate(self):
                pass

            def kill(self):
                pass

            async def wait(self):
                return 1

        return _Proc()


class TestPiperNicePriority(unittest.IsolatedAsyncioTestCase):
    """M2.4B.3.1 — the external Piper process is started at ``config.nice``.
    No sudo, no taskset, NeXa's own process untouched, ``llama-server``
    untouched, policy survives a lifecycle restart."""

    def test_default_nice_is_the_benchmark_backed_10(self) -> None:
        self.assertEqual(DEFAULT_PIPER_NICE, 10)
        self.assertEqual(PiperHttpConfig(venv_python=_fake_config().venv_python,
                                         voices_dir=_fake_config().voices_dir).nice, 10)

    def test_nice_is_configurable_and_negative_is_clamped(self) -> None:
        self.assertEqual(PiperHttpConfig(nice=6).nice, 6)
        self.assertEqual(PiperHttpConfig(nice=-4).nice, 0)  # never needs privilege

    def test_env_override_but_explicit_arg_wins(self) -> None:
        with mock.patch.dict(os.environ, {"NEXA_PIPER_NICE": "13"}):
            self.assertEqual(default_piper_nice(), 13)
            self.assertEqual(PiperHttpConfig().nice, 13)  # from the factory default
            self.assertEqual(PiperHttpConfig(nice=2).nice, 2)  # explicit still wins
        with mock.patch.dict(os.environ, {"NEXA_PIPER_NICE": "-5"}):
            self.assertEqual(default_piper_nice(), 0)

    async def test_child_is_launched_through_nice_wrapper(self) -> None:
        server = PiperHttpServer(PiperHttpConfig(**_fake_config_kwargs(), nice=10))
        cap = _CapturingExec()
        with mock.patch("asyncio.create_subprocess_exec", cap):
            with self.assertRaises(PiperServerStartError):
                await server.start()
        argv, kwargs = cap.calls[0]
        self.assertIn("nice", argv[0])  # /usr/bin/nice (or wherever it lives)
        self.assertEqual(list(argv[1:3]), ["-n", "10"])
        self.assertIn("piper.http_server", argv)
        # the real target python is still in the argv, after the nice prefix
        self.assertTrue(str(server.config.venv_python) in argv)
        self.assertIsNone(kwargs.get("preexec_fn"))

    async def test_nice_zero_launches_python_directly_no_wrapper(self) -> None:
        server = PiperHttpServer(PiperHttpConfig(**_fake_config_kwargs(), nice=0))
        cap = _CapturingExec()
        with mock.patch("asyncio.create_subprocess_exec", cap):
            with self.assertRaises(PiperServerStartError):
                await server.start()
        argv, kwargs = cap.calls[0]
        self.assertEqual(argv[0], str(server.config.venv_python))
        self.assertNotIn("nice", " ".join(str(a) for a in argv[:1]))
        self.assertIsNone(kwargs.get("preexec_fn"))

    async def test_lifecycle_restart_reapplies_the_nice_policy(self) -> None:
        server = PiperHttpServer(PiperHttpConfig(**_fake_config_kwargs(), nice=10))
        cap = _CapturingExec()
        with mock.patch("asyncio.create_subprocess_exec", cap):
            for _ in range(2):
                with self.assertRaises(PiperServerStartError):
                    await server.start()
                await server.stop()
        self.assertEqual(len(cap.calls), 2)
        for argv, _kw in cap.calls:
            self.assertIn("nice", argv[0])
            self.assertEqual(list(argv[1:3]), ["-n", "10"])

    def test_preexec_fallback_when_nice_binary_missing(self) -> None:
        server = PiperHttpServer(PiperHttpConfig(**_fake_config_kwargs(), nice=10))
        with mock.patch("shutil.which", return_value=None):
            prefix, preexec = server._launch_prefix()
        self.assertEqual(prefix, [])
        self.assertTrue(callable(preexec))  # os.setpriority in the forked child

    def test_server_module_has_no_sudo_taskset_or_affinity(self) -> None:
        src = (SRC / "nexa" / "tts" / "server.py").read_text(encoding="utf-8")
        for forbidden in ("sudo", "taskset", "sched_setaffinity", "setuid", "SCHED_FIFO",
                          "SCHED_RR", "CAP_SYS_NICE", "pkexec"):
            self.assertNotIn(forbidden, src, f"server.py must not use {forbidden}")

    def test_nexa_parent_process_priority_is_never_changed(self) -> None:
        before = os.getpriority(os.PRIO_PROCESS, 0)
        server = PiperHttpServer(PiperHttpConfig(**_fake_config_kwargs(), nice=10))
        server._launch_prefix()  # building the prefix must not renice this process
        self.assertEqual(os.getpriority(os.PRIO_PROCESS, 0), before)


class TestPiperNiceRealChildPriority(unittest.IsolatedAsyncioTestCase):
    """Integration proof of the mechanism itself — launches a trivial child
    through the exact same ``nice -n N`` wrapper the server uses and reads
    the child's real priority back. Uses NeXa's own python, not Piper."""

    async def test_nice_wrapper_actually_sets_child_priority(self) -> None:
        import shutil

        nice_bin = shutil.which("nice")
        if nice_bin is None:  # pragma: no cover - always present on Linux
            self.skipTest("coreutils 'nice' not on PATH")
        code = "import os; print(os.getpriority(os.PRIO_PROCESS, 0))"
        proc = await asyncio.create_subprocess_exec(
            nice_bin, "-n", "10", sys.executable, "-c", code,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        self.assertEqual(out.decode().strip(), "10")
        self.assertEqual(os.getpriority(os.PRIO_PROCESS, 0), 0)  # parent unchanged


def _fake_config_kwargs() -> dict:
    tmp = tempfile.mkdtemp()
    vp = Path(tmp) / "bin" / "python3"
    vp.parent.mkdir(parents=True)
    vp.write_text("#!/bin/sh\n")
    vp.chmod(0o755)
    vd = Path(tmp) / "voices"
    vd.mkdir()
    (vd / f"{EN_VOICE}.onnx").write_bytes(b"")
    return {"venv_python": vp, "voices_dir": vd}


if __name__ == "__main__":
    unittest.main()
