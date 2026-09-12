"""M2.6B.4I / R0048 — deterministic tests for the real audio ingress
parity probe (``docs/research/m2_6_cloud_realtime_voice/
m2_6b4i_audio_ingress_parity_probe.py``).

Offline, no real audio device, no Gemini, no network. Covers (1) the pure
``IngressCapture`` windowing/finalize/WAV-writing logic in isolation, and
(2) the REAL processor chain (``build_processor_chain`` — the SAME
``SileroVADAnalyzer``/``VADProcessor``/``BargeInController``/
``_VadToProviderBridge`` construction the probe's own live-hardware path
uses) driven with synthetic PCM + hand-injected VAD marker frames through
a real ``Pipeline``/``PipelineWorker``/``WorkerRunner`` — the same
established convention ``TestVadBridgeQuarantine`` already uses for the
bridge itself (Silero's own neural voice-activity detection cannot be
relied on to fire deterministically on synthetic tone/silence PCM in a
fast unit test; the marker frames it WOULD have emitted are injected
directly, exactly mirroring every other test in this codebase that
exercises ``_VadToProviderBridge``).
"""

from __future__ import annotations

import asyncio
import sys
import unittest
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
PROBE_DIR = REPO_ROOT / "docs" / "research" / "m2_6_cloud_realtime_voice"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(PROBE_DIR) not in sys.path:
    sys.path.insert(0, str(PROBE_DIR))

import m2_6b4i_audio_ingress_parity_probe as probe  # noqa: E402

try:
    from nexa.realtime.gemini.runtime import _pipecat_hw_imports

    _PIPECAT_AVAILABLE = True
except Exception:  # pragma: no cover - only if pipecat-ai isn't importable
    _PIPECAT_AVAILABLE = False


async def _wait_until(predicate, *, timeout: float = 2.0, step: float = 0.01) -> None:
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return
        await asyncio.sleep(step)
        elapsed += step
    raise AssertionError(f"condition not met within {timeout}s")


class TestIngressCaptureWindowing(unittest.TestCase):
    """Pure logic, no Pipecat, no asyncio -- the ring-buffer/finalize
    bookkeeping in isolation."""

    def setUp(self) -> None:
        self.tmp = Path(self.id().split(".")[-1] + "_capture_tmp")
        self.tmp.mkdir(exist_ok=True)
        self.capture = probe.IngressCapture(sample_rate=100, out_dir=self.tmp)

    def tearDown(self) -> None:
        for p in self.tmp.glob("*"):
            p.unlink()
        self.tmp.rmdir()

    def _pcm(self, n_samples: int, value: int = 100) -> bytes:
        return (value.to_bytes(2, "little", signed=True)) * n_samples

    def test_pre_context_window_contains_frames_before_vad_start(self) -> None:
        """Invariant 1: the raw pre-context buffer contains frames before
        VAD START, at the exact PRE_CONTEXT_SECS boundary."""
        c = self.capture
        t0 = 100.0
        # feed 1.0s of raw audio (10 frames of 0.1s each) ending at t0 + 1.0
        for i in range(10):
            c.record_raw(self._pcm(10), t0 + i * 0.1)
        # VAD start fires 1.0s in -- PRE_CONTEXT_SECS (0.5s) of that raw
        # audio must be captured as pre-context.
        vad_start_t = t0 + 1.0
        c.mark_vad_start(vad_start_t)
        self.assertIsNotNone(c._current)  # noqa: SLF001
        self.assertLessEqual(c._current.raw_capture_window_start_t, vad_start_t - 0.45)  # noqa: SLF001
        self.assertGreater(len(c._current.raw_pcm), 0)  # noqa: SLF001

    def test_production_forwarded_contains_exactly_what_bridge_would_send(self) -> None:
        """Invariant 2: the forwarded capture is exactly the PCM the
        stub provider's send_user_audio() received, nothing more, nothing
        less."""
        c = self.capture
        c.mark_vad_start(10.0)
        c.mark_forwarded_turn_start(10.01)
        chunk_a = self._pcm(5, 1)
        chunk_b = self._pcm(5, 2)
        c.record_forwarded(chunk_a, 10.02)
        c.record_forwarded(chunk_b, 10.05)
        c.mark_vad_stop(10.1)
        c.mark_forwarded_turn_end(10.1)
        derived = c.finalize(c._generation)  # noqa: SLF001
        self.assertIsNotNone(derived)
        raw_wav = self.tmp / derived["forwarded_wav"]
        with wave.open(str(raw_wav), "rb") as wf:
            self.assertEqual(wf.readframes(wf.getnframes()), chunk_a + chunk_b)

    def test_timestamps_are_monotonic(self) -> None:
        """Invariant 3: every recorded timestamp for one utterance is
        monotonically non-decreasing in the order production would
        actually observe them."""
        c = self.capture
        c.record_raw(self._pcm(5), 0.0)
        c.mark_vad_start(0.5)
        c.mark_forwarded_turn_start(0.51)
        c.record_forwarded(self._pcm(5), 0.52)
        c.record_forwarded(self._pcm(5), 0.6)
        c.mark_vad_stop(0.9)
        c.mark_forwarded_turn_end(0.9)
        derived = c.finalize(c._generation)  # noqa: SLF001
        ordered = [
            derived["raw_capture_window_start_t"],
            derived["vad_start_t"],
            derived["forwarded_turn_start_t"],
            derived["forwarded_first_pcm_t"],
            derived["forwarded_last_pcm_t"],
            derived["vad_stop_t"],
        ]
        self.assertEqual(ordered, sorted(ordered))

    def test_fast_followup_utterance_never_silently_dropped(self) -> None:
        """A second VAD start arriving before the first utterance's own
        POST_CONTEXT_SECS finalize delay elapsed finalizes the first one
        immediately (best-effort) instead of orphaning it."""
        c = self.capture
        c.mark_vad_start(0.0)
        c.mark_forwarded_turn_start(0.01)
        c.record_forwarded(self._pcm(5), 0.02)
        c.mark_vad_stop(0.3)
        c.mark_forwarded_turn_end(0.3)
        # a second utterance starts before any finalize-after-delay task
        # would have fired.
        c.mark_vad_start(0.35)
        self.assertEqual(len(c.completed), 1)
        self.assertEqual(c.completed[0]["index"], 1)

    def test_wav_files_are_written_with_correct_sample_rate(self) -> None:
        c = self.capture
        c.mark_vad_start(0.0)
        c.record_raw(self._pcm(3), 0.0)
        c.mark_forwarded_turn_start(0.01)
        c.record_forwarded(self._pcm(3), 0.02)
        c.mark_vad_stop(0.1)
        c.mark_forwarded_turn_end(0.1)
        derived = c.finalize(c._generation)  # noqa: SLF001
        for key in ("raw_wav", "forwarded_wav"):
            with wave.open(str(self.tmp / derived[key]), "rb") as wf:
                self.assertEqual(wf.getframerate(), 100)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getnchannels(), 1)


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestRealProcessorChainPreVadStartAudioParity(unittest.IsolatedAsyncioTestCase):
    """M2.6B.4J (R0049) POST-FIX A/B PROOF. Drives synthetic PCM +
    hand-injected VAD marker frames through the REAL processor chain
    (real ``VADProcessor``/``BargeInController``/``_VadToProviderBridge``
    construction, not a reimplementation).

    BEFORE R0049 (the exact test this replaces, from R0048): the
    production-forwarded capture was missing the pre-VAD-start window the
    raw capture retained -- proving the clipping hypothesis.

    AFTER R0049 (this test): the production-forwarded capture now
    contains that SAME pre-VAD-start window too -- exactly once, in
    order, ahead of the live audio -- proving the fix restores parity
    without duplicating or reordering anything."""

    async def test_bridge_now_forwards_the_preroll_before_vad_start_exactly_once(
        self,
    ) -> None:
        P = _pipecat_hw_imports()
        tmp = Path("test_ingress_chain_tmp")
        tmp.mkdir(exist_ok=True)
        try:
            capture = probe.IngressCapture(sample_rate=16000, out_dir=tmp)
            processors, meta = probe.build_processor_chain(capture=capture)
            self.assertEqual(meta["vad_start_secs"], 0.2)  # unmodified Pipecat default
            self.assertEqual(meta["preroll_ms"], 300)  # (0.2 + 0.1) * 1000

            pipeline = P["Pipeline"](processors)
            worker = P["PipelineWorker"](
                pipeline,
                params=P["PipelineParams"](audio_in_sample_rate=16000, audio_out_sample_rate=16000),
                enable_rtvi=False,
                idle_timeout_secs=None,
            )
            runner = P["WorkerRunner"]()
            await runner.add_workers(worker)
            run_task = asyncio.create_task(runner.run())
            await asyncio.sleep(0.1)

            pre_onset = b"\x01\x00" * 400  # "speech onset" audio, BEFORE VAD confirms
            spoken = b"\x02\x00" * 400
            await worker.queue_frames(
                [
                    P["InputAudioRawFrame"](audio=pre_onset, sample_rate=16000, num_channels=1),
                    P["VADUserStartedSpeakingFrame"](),  # hand-injected -- see module docstring
                    P["InputAudioRawFrame"](audio=spoken, sample_rate=16000, num_channels=1),
                    P["VADUserStoppedSpeakingFrame"](),
                ]
            )
            await _wait_until(lambda: len(capture.completed) >= 1, timeout=3.0)

            derived = capture.completed[0]
            with wave.open(str(tmp / derived["raw_wav"]), "rb") as wf:
                raw_pcm = wf.readframes(wf.getnframes())
            with wave.open(str(tmp / derived["forwarded_wav"]), "rb") as wf:
                forwarded_pcm = wf.readframes(wf.getnframes())

            # the raw capture retains the pre-VAD-start onset audio
            # (unchanged from R0048)...
            self.assertIn(pre_onset, raw_pcm)
            self.assertIn(spoken, raw_pcm)
            # ...and, AFTER R0049, so does the production-forwarded
            # stream -- exactly once, preroll first, then the live
            # audio, never duplicated or reordered.
            self.assertEqual(forwarded_pcm, pre_onset + spoken)

            await runner.end(reason="test done")
            await asyncio.wait_for(run_task, timeout=5.0)
        finally:
            for p in tmp.glob("*"):
                p.unlink()
            tmp.rmdir()


class TestNoHiddenMutationOfNormalProductionPath(unittest.TestCase):
    """Invariant 4: nothing in this diagnostic module imports, patches, or
    monkeypatches any ``src/nexa`` production class — it only constructs
    real, unmodified production objects and reads their frames/calls."""

    def test_probe_module_does_not_import_mock_or_patch(self) -> None:
        import ast

        source = (PROBE_DIR / "m2_6b4i_audio_ingress_parity_probe.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "unittest.mock":
                self.fail("diagnostic probe must never monkeypatch production code")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "unittest.mock")

    def test_probe_module_never_imports_google_genai_or_gemini_service(self) -> None:
        """Invariant 5 (structural half): no Gemini/`google.genai` import
        statement anywhere in this file (prose mentioning it in a
        docstring is fine and expected) — it CANNOT connect to Gemini
        even if misused, only ``INPUT_SAMPLE_RATE_HZ`` (a plain int
        constant) is imported from the gemini service module."""
        import ast

        source = (PROBE_DIR / "m2_6b4i_audio_ingress_parity_probe.py").read_text()
        tree = ast.parse(source)
        imported_modules: set[str] = set()
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
                imported_names.update(alias.name for alias in node.names)
        self.assertFalse(any(m.startswith("google.genai") for m in imported_modules))
        self.assertNotIn("GeminiLiveProvider", imported_names)
        self.assertNotIn("load_gemini_credential", imported_names)
        # the one, deliberate, credential-free exception: a plain int
        # constant, not the provider or any network-capable symbol.
        self.assertIn("nexa.realtime.gemini.service", imported_modules)
        self.assertIn("INPUT_SAMPLE_RATE_HZ", imported_names)


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestDiagnosticModeCannotConnectToGemini(unittest.TestCase):
    """Invariant 5 (behavioural half): --dry constructs the full object
    graph (real VAD/bridge/router) with no network, no credential, no
    device -- and the LIVE path's own provider role is always the local
    ``_StubProvider``, never anything Gemini-capable."""

    def test_dry_build_constructs_without_network_or_credential(self) -> None:
        tmp = Path("test_ingress_dry_tmp")
        tmp.mkdir(exist_ok=True)
        try:
            capture = probe.IngressCapture(sample_rate=16000, out_dir=tmp)
            worker, runner_cls, meta = probe.build_diagnostic_pipeline(capture=capture, dry=True)
            self.assertIsNone(worker)
            self.assertIsNone(runner_cls)
            self.assertEqual(meta["mode"], "dry")
            self.assertEqual(meta["vad_start_secs"], 0.2)
            self.assertEqual(meta["vad_stop_secs"], 0.5)
        finally:
            tmp.rmdir()

    def test_processor_chain_provider_role_is_always_the_local_stub(self) -> None:
        tmp = Path("test_ingress_stub_tmp")
        tmp.mkdir(exist_ok=True)
        try:
            capture = probe.IngressCapture(sample_rate=16000, out_dir=tmp)
            processors, _meta = probe.build_processor_chain(capture=capture)
            bridge = processors[-1]
            self.assertIsInstance(bridge._handle.current, probe._StubProvider)  # noqa: SLF001
        finally:
            tmp.rmdir()


if __name__ == "__main__":
    unittest.main()
