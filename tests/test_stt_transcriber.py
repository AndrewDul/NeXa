"""M2.2 `WhisperCppTranscriber` tests using a fake `subprocess.run` boundary
— no real whisper.cpp binary, model, network, or microphone required by
default (matches the M1.1 `FakeModelProvider` pattern: the *runtime* is
tested deterministically, not the upstream binary's own accuracy).

An opt-in *live* test against the real pinned binary + model lives in
``test_stt_transcriber_live.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.stt.config import Language, WhisperCppConfig  # noqa: E402
from nexa.stt.errors import (  # noqa: E402
    NoUsableAudioError,
    SttBinaryNotFoundError,
    SttMalformedOutputError,
    SttModelNotFoundError,
    SttSubprocessError,
    SttTimeoutError,
)
from nexa.stt.transcriber import WhisperCppTranscriber  # noqa: E402

FAKE_AUDIO = b"\x00\x01" * 16_000  # 1.0s of 16kHz mono PCM16


def _fake_config(tmp_path: Path) -> WhisperCppConfig:
    binary = tmp_path / "whisper-cli"
    model = tmp_path / "ggml-base-q8_0.bin"
    binary.write_bytes(b"fake binary")
    model.write_bytes(b"fake model")
    return WhisperCppConfig(binary_path=binary, model_path=model, threads=4, timeout_s=5.0)


def _write_json_result(out_prefix: str, text: str) -> None:
    Path(out_prefix + ".json").write_text(
        json.dumps({"transcription": [{"text": text}]}), encoding="utf-8"
    )


class _TmpPathMixin:
    def setUp(self) -> None:
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory(prefix="nexa-stt-test-")
        self.tmp_path = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)


class TestBinaryAndModelValidation(_TmpPathMixin, unittest.TestCase):
    def test_missing_binary_raises_explicit_error(self) -> None:
        config = _fake_config(self.tmp_path)
        config.binary_path.unlink()
        with self.assertRaises(SttBinaryNotFoundError):
            WhisperCppTranscriber(config)

    def test_missing_model_raises_explicit_error(self) -> None:
        config = _fake_config(self.tmp_path)
        config.model_path.unlink()
        with self.assertRaises(SttModelNotFoundError):
            WhisperCppTranscriber(config)


class TestCommandConstruction(_TmpPathMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config = _fake_config(self.tmp_path)
        self.transcriber = WhisperCppTranscriber(self.config)

    async def test_command_contains_explicit_language(self) -> None:
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            out_prefix = cmd[cmd.index("-of") + 1]
            _write_json_result(out_prefix, "hello")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        with mock.patch("nexa.stt.transcriber.subprocess.run", side_effect=fake_run):
            await self.transcriber.transcribe(FAKE_AUDIO, language=Language.PL)

        cmd = captured["cmd"]
        self.assertIn("-l", cmd)
        self.assertEqual(cmd[cmd.index("-l") + 1], "pl")

    async def test_command_contains_explicit_thread_count(self) -> None:
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            out_prefix = cmd[cmd.index("-of") + 1]
            _write_json_result(out_prefix, "hello")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        with mock.patch("nexa.stt.transcriber.subprocess.run", side_effect=fake_run):
            await self.transcriber.transcribe(FAKE_AUDIO, language=Language.EN)

        cmd = captured["cmd"]
        self.assertIn("-t", cmd)
        self.assertEqual(cmd[cmd.index("-t") + 1], "4")

    async def test_command_does_not_use_shell(self) -> None:
        with mock.patch("nexa.stt.transcriber.subprocess.run") as fake_run:
            fake_run.side_effect = lambda cmd, **kwargs: (
                _write_json_result(cmd[cmd.index("-of") + 1], "hi"),
                subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr=""),
            )[1]
            await self.transcriber.transcribe(FAKE_AUDIO, language=Language.EN)

        _, kwargs = fake_run.call_args
        self.assertNotIn("shell", kwargs)
        args, _ = fake_run.call_args
        self.assertIsInstance(args[0], list)


class TestParsingAndResult(_TmpPathMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config = _fake_config(self.tmp_path)
        self.transcriber = WhisperCppTranscriber(self.config)

    async def test_valid_output_is_parsed_into_transcription_result(self) -> None:
        def fake_run(cmd, **kwargs):
            out_prefix = cmd[cmd.index("-of") + 1]
            _write_json_result(out_prefix, "Cześć NeXa.")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        with mock.patch("nexa.stt.transcriber.subprocess.run", side_effect=fake_run):
            result = await self.transcriber.transcribe(FAKE_AUDIO, language=Language.PL)

        self.assertEqual(result.text, "Cześć NeXa.")
        self.assertEqual(result.language, Language.PL)
        self.assertAlmostEqual(result.audio_duration_s, 1.0, places=3)

    async def test_malformed_json_raises_explicit_error(self) -> None:
        def fake_run(cmd, **kwargs):
            out_prefix = cmd[cmd.index("-of") + 1]
            Path(out_prefix + ".json").write_text("{not valid json", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        with mock.patch("nexa.stt.transcriber.subprocess.run", side_effect=fake_run):
            with self.assertRaises(SttMalformedOutputError):
                await self.transcriber.transcribe(FAKE_AUDIO, language=Language.EN)

    async def test_missing_json_output_raises_explicit_error(self) -> None:
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        with mock.patch("nexa.stt.transcriber.subprocess.run", side_effect=fake_run):
            with self.assertRaises(SttMalformedOutputError):
                await self.transcriber.transcribe(FAKE_AUDIO, language=Language.EN)

    async def test_subprocess_nonzero_exit_raises_explicit_error(self) -> None:
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, returncode=3, stdout="", stderr="init failed")

        with mock.patch("nexa.stt.transcriber.subprocess.run", side_effect=fake_run):
            with self.assertRaises(SttSubprocessError):
                await self.transcriber.transcribe(FAKE_AUDIO, language=Language.EN)

    async def test_subprocess_timeout_raises_explicit_error(self) -> None:
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 5.0))

        with mock.patch("nexa.stt.transcriber.subprocess.run", side_effect=fake_run):
            with self.assertRaises(SttTimeoutError):
                await self.transcriber.transcribe(FAKE_AUDIO, language=Language.EN)

    async def test_empty_audio_raises_no_usable_audio_error_without_subprocess(self) -> None:
        with mock.patch("nexa.stt.transcriber.subprocess.run") as fake_run:
            with self.assertRaises(NoUsableAudioError):
                await self.transcriber.transcribe(b"", language=Language.EN)
        fake_run.assert_not_called()

    async def test_non_language_enum_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            await self.transcriber.transcribe(FAKE_AUDIO, language="en")  # type: ignore[arg-type]

    async def test_temp_wav_and_json_are_removed_after_transcription(self) -> None:
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            wav_path = Path(cmd[cmd.index("-f") + 1])
            out_prefix = cmd[cmd.index("-of") + 1]
            captured["wav_path"] = wav_path
            captured["json_path"] = Path(out_prefix + ".json")
            self.assertTrue(wav_path.is_file(), "wav must exist while whisper-cli runs")
            _write_json_result(out_prefix, "hi")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        with mock.patch("nexa.stt.transcriber.subprocess.run", side_effect=fake_run):
            await self.transcriber.transcribe(FAKE_AUDIO, language=Language.EN)

        self.assertFalse(captured["wav_path"].exists(), "temp WAV must be deleted after use")
        self.assertFalse(captured["json_path"].exists(), "temp JSON must be deleted after use")
        self.assertFalse(captured["wav_path"].parent.exists(), "temp directory must be deleted")


if __name__ == "__main__":
    unittest.main()
