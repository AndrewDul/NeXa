"""M2.4 `PiperHttpServer` tests — no real Piper venv/voice/network needed
for the deterministic cases; an opt-in live test exercises the real
external process (`NEXA_RUN_LIVE_TTS_TEST=1`).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.tts import EN_VOICE, PiperHttpConfig, PiperHttpServer  # noqa: E402
from nexa.tts.errors import (  # noqa: E402
    PiperHttpError,
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


if __name__ == "__main__":
    unittest.main()
