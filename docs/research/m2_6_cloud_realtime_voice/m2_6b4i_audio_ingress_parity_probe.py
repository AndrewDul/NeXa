#!/usr/bin/env python3
# ruff: noqa: E501  (research spike — long strings, docstrings, wide lines)
"""M2.6B.4I / R0048 — real audio ingress parity probe.

DIAGNOSTIC ONLY. No Gemini, no cloud API, no credential, no assistant
playback. Reuses the REAL production classes verbatim
(``nexa.realtime.gemini.runtime._make_vad_bridge_class`` /
``_ProviderHandle``, ``nexa.voice.bargein.BargeInController``, the exact
``SileroVADAnalyzer``/``VADParams``/``VADProcessor`` construction
``build_gemini_voice_runtime`` uses) — never a reimplementation of the
bridge's own VAD-boundary logic — with a REAL reSpeaker mic input and a
FAKE provider stub in place of ``GeminiLiveProvider`` (records calls
instead of talking to Gemini).

Purpose: compare, for the SAME spoken utterance, (A) the continuous raw
microphone stream (with ~500 ms of context before/after the locally
detected VAD boundaries) against (B) exactly the PCM current production
would hand to ``provider.send_user_audio()`` — to determine whether the
production ingress path clips the beginning/end of an utterance relative
to what a continuously-streaming architecture (the accepted M2.6A spike)
would have available.

Usage::

    # config-only, no audio device:
    .venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py --dry

    # real hardware capture (OPERATOR, real reSpeaker, NO Gemini):
    .venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py

Speak each of the printed phrases (repeat "Czarna dziura" — the real
Attempt #3 symptom — several times), pausing briefly between them.
Ctrl+C ends the session; a 10-minute hard cap ends it automatically.
Every completed utterance writes two WAV files
(``<n>_raw_with_context.wav``, ``<n>_production_forwarded.wav``) plus one
running ``ingress_capture_<timestamp>.json`` summary under
``ingress_captures/`` next to this script.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUT_DIR = Path(__file__).resolve().parent / "ingress_captures"
HARD_SESSION_CAP_S = 10 * 60
SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM, matches production's InputAudioRawFrame

#: How much raw mic context to retain BEFORE the local VAD confirms speech
#: start, and AFTER it confirms speech stop. Chosen to comfortably bracket
#: the mechanically-proven ``VADParams.start_secs`` default (0.2 s,
#: ``pipecat.audio.vad.vad_analyzer.VAD_START_SECS`` — confirmed identical
#: in both the M2.6A spike and current production, neither of which
#: overrides it) with a safety margin, per the charter's own "~500 ms"
#: instruction.
PRE_CONTEXT_SECS = 0.5
POST_CONTEXT_SECS = 0.5
#: Rolling raw-audio ring buffer depth. Must exceed PRE_CONTEXT_SECS with
#: margin so a VAD start is never missing its own pre-context.
RING_BUFFER_SECS = 3.0

#: The charter's exact test phrases. "Czarna dziura" (repeated) is the
#: PRIMARY diagnostic — the real Attempt #3 failure symptom.
TEST_PHRASES = (
    "Czarna dziura",
    "Czarna dziura",
    "Czarna dziura powstaje...",
    "Czarna dziura powstaje...",
    "Black hole",
    "Black hole",
    "What is a black hole?",
    "What is a black hole?",
    "Gwiazdy składają się z wodoru i helu",
    "Gwiazdy składają się z wodoru i helu",
)


def _pcm_ms(nbytes: int, sample_rate: int) -> float:
    return 1000.0 * (nbytes / SAMPLE_WIDTH_BYTES) / max(1, sample_rate)


def _write_wav(path: Path, pcm: bytes, *, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(SAMPLE_WIDTH_BYTES)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


@dataclass
class _Utterance:
    """One captured local VAD turn's raw-vs-forwarded evidence."""

    index: int
    sample_rate: int
    raw_capture_window_start_t: float
    vad_start_t: float
    vad_stop_t: float | None = None
    forwarded_turn_start_t: float | None = None
    forwarded_first_pcm_t: float | None = None
    forwarded_last_pcm_t: float | None = None
    forwarded_turn_end_t: float | None = None
    forwarded_pcm: bytearray = field(default_factory=bytearray)
    raw_pcm: bytearray = field(default_factory=bytearray)

    def derive(self) -> dict[str, Any]:
        raw_ms = _pcm_ms(len(self.raw_pcm), self.sample_rate)
        fwd_ms = _pcm_ms(len(self.forwarded_pcm), self.sample_rate)
        raw_capture_end_t = self.raw_capture_window_start_t + raw_ms / 1000.0
        onset_gap_s = (
            round(self.forwarded_first_pcm_t - self.vad_start_t, 4)
            if self.forwarded_first_pcm_t is not None
            else None
        )
        # positive => real, captured mic audio exists in raw_with_context.wav
        # AFTER the last byte production ever forwarded -- the trailing
        # window the operator can listen to but production never sent.
        postroll_never_forwarded_s = (
            round(raw_capture_end_t - self.forwarded_last_pcm_t, 4)
            if self.forwarded_last_pcm_t is not None
            else None
        )
        return {
            "index": self.index,
            "raw_capture_window_start_t": round(self.raw_capture_window_start_t, 4),
            "vad_start_t": round(self.vad_start_t, 4),
            "vad_stop_t": round(self.vad_stop_t, 4) if self.vad_stop_t is not None else None,
            "forwarded_turn_start_t": (
                round(self.forwarded_turn_start_t, 4)
                if self.forwarded_turn_start_t is not None
                else None
            ),
            "forwarded_first_pcm_t": (
                round(self.forwarded_first_pcm_t, 4)
                if self.forwarded_first_pcm_t is not None
                else None
            ),
            "forwarded_last_pcm_t": (
                round(self.forwarded_last_pcm_t, 4)
                if self.forwarded_last_pcm_t is not None
                else None
            ),
            "forwarded_turn_end_t": (
                round(self.forwarded_turn_end_t, 4)
                if self.forwarded_turn_end_t is not None
                else None
            ),
            "raw_duration_ms": round(raw_ms, 1),
            "forwarded_duration_ms": round(fwd_ms, 1),
            "raw_minus_forwarded_ms": round(raw_ms - fwd_ms, 1),
            # positive => this much real, captured mic audio exists in
            # raw_with_context.wav strictly BEFORE the first byte production
            # ever forwarded to the provider -- the clipped window the
            # operator can listen to but NOT hear in
            # production_forwarded.wav.
            "ms_raw_audio_before_first_forwarded": round(
                (
                    (self.forwarded_first_pcm_t or self.vad_start_t)
                    - self.raw_capture_window_start_t
                )
                * 1000.0,
                1,
            ),
            # ~0 expected: the bridge starts forwarding on (essentially) the
            # same frame that flips its local _turn_open flag, i.e. right at
            # VAD start -- confirms the clipping boundary IS vad_start_t,
            # not some other later point.
            "onset_gap_vad_start_to_first_forwarded_s": onset_gap_s,
            "postroll_never_forwarded_s": postroll_never_forwarded_s,
        }


class IngressCapture:
    """Shared recorder between the raw-frame tap (upstream of VAD) and the
    fake provider stub (standing in for ``GeminiLiveProvider``). Owns the
    rolling pre-context ring buffer and one WAV pair per finalized
    utterance. No Pipecat/asyncio import here — testable in isolation."""

    def __init__(self, *, sample_rate: int, out_dir: Path) -> None:
        self.sample_rate = sample_rate
        self.out_dir = out_dir
        self._ring: list[tuple[float, bytes]] = []
        self._current: _Utterance | None = None
        self._next_index = 1
        self.completed: list[dict[str, Any]] = []
        #: monotonic generation counter -- guards the finalize-after-delay
        #: task against acting on a stale (already-finalized/superseded)
        #: utterance, matching the established
        #: ``nexa.voice.bargein`` capture-generation pattern.
        self._generation = 0

    # -- raw mic side (upstream of VAD; sees EVERY InputAudioRawFrame) --
    def record_raw(self, pcm: bytes, ts: float) -> None:
        self._ring.append((ts, pcm))
        cutoff = ts - RING_BUFFER_SECS
        while self._ring and self._ring[0][0] < cutoff:
            self._ring.pop(0)
        if self._current is not None:
            self._current.raw_pcm.extend(pcm)

    def mark_vad_start(self, ts: float) -> int:
        """Opens a new utterance, seeded with every ring-buffered frame
        from ``ts - PRE_CONTEXT_SECS`` onward. Returns the new
        generation id (for the caller's finalize-after-delay task).

        If a PREVIOUS utterance is still open (a fast follow-up utterance
        arriving before its own ``POST_CONTEXT_SECS`` finalize delay
        elapsed — normal speech has pauses, but never assume it), it is
        finalized (best-effort, with whatever post-context it managed to
        accumulate) right now rather than silently orphaned/dropped."""
        if self._current is not None:
            self._finalize_current()
        self._generation += 1
        gen = self._generation
        window_start = ts - PRE_CONTEXT_SECS
        seed = bytearray()
        actual_window_start = ts
        for frame_ts, pcm in self._ring:
            if frame_ts >= window_start:
                seed.extend(pcm)
                actual_window_start = min(actual_window_start, frame_ts)
        self._current = _Utterance(
            index=self._next_index,
            sample_rate=self.sample_rate,
            raw_capture_window_start_t=actual_window_start if seed else ts,
            vad_start_t=ts,
        )
        self._current.raw_pcm.extend(seed)
        self._next_index += 1
        return gen

    def mark_vad_stop(self, ts: float) -> None:
        if self._current is not None:
            self._current.vad_stop_t = ts

    # -- fake-provider side (exactly what production would forward) -----
    def mark_forwarded_turn_start(self, ts: float) -> None:
        if self._current is not None:
            self._current.forwarded_turn_start_t = ts

    def record_forwarded(self, pcm: bytes, ts: float) -> None:
        if self._current is None:
            return
        if self._current.forwarded_first_pcm_t is None:
            self._current.forwarded_first_pcm_t = ts
        self._current.forwarded_last_pcm_t = ts
        self._current.forwarded_pcm.extend(pcm)

    def mark_forwarded_turn_end(self, ts: float) -> None:
        if self._current is not None:
            self._current.forwarded_turn_end_t = ts

    # -- finalize (called after POST_CONTEXT_SECS of further raw frames) #
    def is_current_generation(self, gen: int) -> bool:
        return gen == self._generation

    def finalize(self, gen: int) -> dict[str, Any] | None:
        """Called by the caller's finalize-after-delay task, ``POST_CONTEXT_SECS``
        after VAD stop. A stale generation (superseded by a fast follow-up
        utterance via ``mark_vad_start``, which already finalized this one
        itself) is a silent no-op — never a double-write."""
        if not self.is_current_generation(gen) or self._current is None:
            return None
        return self._finalize_current()

    def _finalize_current(self) -> dict[str, Any] | None:
        if self._current is None:
            return None
        utt = self._current
        self._current = None
        raw_path = self.out_dir / f"{utt.index:03d}_raw_with_context.wav"
        fwd_path = self.out_dir / f"{utt.index:03d}_production_forwarded.wav"
        _write_wav(raw_path, bytes(utt.raw_pcm), sample_rate=self.sample_rate)
        _write_wav(fwd_path, bytes(utt.forwarded_pcm), sample_rate=self.sample_rate)
        derived = utt.derive()
        derived["raw_wav"] = raw_path.name
        derived["forwarded_wav"] = fwd_path.name
        self.completed.append(derived)
        return derived

    def write_summary(self, *, note: str) -> Path:
        payload = {
            "report": "M2.6B.4I / R0048 real audio ingress parity probe",
            "generated_utc": datetime.now(UTC).isoformat(),
            "note": note,
            "sample_rate": self.sample_rate,
            "pre_context_secs": PRE_CONTEXT_SECS,
            "post_context_secs": POST_CONTEXT_SECS,
            "vad_start_secs_source_fact": (
                "pipecat.audio.vad.vad_analyzer.VAD_START_SECS = 0.2 (default, "
                "unmodified by either the M2.6A spike or current production) "
                "-- the minimum duration of confirmed voice activity Silero "
                "requires before VADUserStartedSpeakingFrame fires; every "
                "byte of audio during that window necessarily precedes the "
                "marker."
            ),
            "utterances": self.completed,
        }
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = self.out_dir / f"ingress_capture_{ts}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path


# ---- pipecat wiring (deferred import; --dry works without pipecat too) --- #
def _pipecat_hw_imports() -> dict[str, Any]:
    from nexa.realtime.gemini.runtime import _pipecat_hw_imports as _prod_imports

    return _prod_imports()


class _StubProvider:
    """Stands in for ``GeminiLiveProvider``: records calls into
    ``IngressCapture`` instead of talking to Gemini. No network, no
    credential, no ``google.genai`` import anywhere in this file."""

    def __init__(self, capture: IngressCapture) -> None:
        self._capture = capture

    async def user_turn_start(self) -> None:
        self._capture.mark_forwarded_turn_start(time.monotonic())

    async def send_user_audio(self, pcm: bytes) -> None:
        self._capture.record_forwarded(pcm, time.monotonic())

    async def user_turn_end(self) -> None:
        self._capture.mark_forwarded_turn_end(time.monotonic())

    async def cancel(self) -> None:  # pragma: no cover - never called here
        return None


def build_processor_chain(*, capture: IngressCapture) -> tuple[list[Any], dict[str, Any]]:
    """Builds the diagnostic PROCESSOR CHAIN (no transport, no device, no
    ``WorkerRunner``): ``[raw_tap, vad_processor, bargein, marker_tap,
    bridge]`` — the SAME ``SileroVADAnalyzer``/``VADParams``/
    ``VADProcessor`` construction ``build_gemini_voice_runtime`` uses, the
    REAL ``BargeInController`` (dummy ``on_confirmed`` — never fires here,
    no response is ever "in flight"), and the REAL, unmodified
    ``_VadToProviderBridge`` (imported from production, never
    reimplemented) wired to a ``_StubProvider`` that records forwarded PCM
    into ``capture`` instead of talking to a real provider. Two
    diagnostic-only taps (raw-frame + VAD-marker) bracket the VAD stage;
    neither mutates or drops a single frame — both call ``self.push_frame``
    unconditionally for everything they see.

    Returned standalone (not yet wrapped in a ``Pipeline``/``PipelineWorker``)
    so both the real hardware run (which prepends ``transport.input()``)
    and the deterministic offline tests (which drive it directly via
    ``queue_frames`` — the SAME already-established test convention this
    whole codebase uses for ``_VadToProviderBridge``, e.g.
    ``TestVadBridgeQuarantine``) can build the identical processor chain.
    """
    from nexa.conversation.session import ConversationSession
    from nexa.realtime.gemini.runtime import (
        RuntimeMetrics,
        _make_vad_bridge_class,
        _ProviderHandle,
        _ResponseLifecycle,
    )
    from nexa.realtime.gemini.service import INPUT_SAMPLE_RATE_HZ
    from nexa.realtime.policy import ConversationPolicy
    from nexa.realtime.router import ConversationRouter
    from nexa.voice.aec import AecReferenceHealth
    from nexa.voice.bargein import BargeInController

    class _FakeModelProvider:
        async def generate(self, *_a, **_kw):  # pragma: no cover - never called
            raise NotImplementedError

    session = ConversationSession(provider=_FakeModelProvider(), system_prompt="diagnostic")
    router = ConversationRouter(session, policy=ConversationPolicy.LOCAL_ONLY)

    stub = _StubProvider(capture)
    provider_handle = _ProviderHandle(stub)

    P = _pipecat_hw_imports()
    bridge_cls = _make_vad_bridge_class(P)
    metrics = RuntimeMetrics()
    lifecycle = _ResponseLifecycle(on_finished=lambda: None)
    bridge = bridge_cls(
        provider_handle=provider_handle, metrics=metrics, lifecycle=lifecycle, router=router
    )

    aec_health = AecReferenceHealth()
    bargein = BargeInController(aec_health=aec_health, on_confirmed=lambda ctx: None)

    class _RawCaptureTap(P["FrameProcessor"]):
        """DIAGNOSTIC ONLY -- sits BEFORE the VAD stage. Records every
        ``InputAudioRawFrame`` verbatim (never mutates/drops it) so
        ``capture``'s rolling pre-context buffer sees exactly what
        production's own ``VADProcessor`` would see."""

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["InputAudioRawFrame"]):
                capture.record_raw(frame.audio, time.monotonic())
            await self.push_frame(frame, direction)

    class _VadMarkerTap(P["FrameProcessor"]):
        """DIAGNOSTIC ONLY -- sits AFTER the VAD stage, BEFORE the bridge.
        Marks the exact local monotonic time each VAD boundary frame
        reaches this point in the pipeline (the same instant the REAL
        bridge reacts to it, since it is the very next processor)."""

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["VADUserStartedSpeakingFrame"]):
                self._last_gen = capture.mark_vad_start(time.monotonic())
            elif isinstance(frame, P["VADUserStoppedSpeakingFrame"]):
                capture.mark_vad_stop(time.monotonic())
                gen = getattr(self, "_last_gen", None)
                if gen is not None:
                    asyncio.create_task(_finalize_after_delay(capture, gen))
            await self.push_frame(frame, direction)

    async def _finalize_after_delay(cap: IngressCapture, gen: int) -> None:
        await asyncio.sleep(POST_CONTEXT_SECS)
        derived = cap.finalize(gen)
        if derived is not None:
            print(
                f"  utterance {derived['index']:>3}: raw={derived['raw_duration_ms']}ms "
                f"forwarded={derived['forwarded_duration_ms']}ms  "
                f"ms_raw_before_first_forwarded={derived['ms_raw_audio_before_first_forwarded']}"
            )

    vad_analyzer = P["SileroVADAnalyzer"](
        sample_rate=INPUT_SAMPLE_RATE_HZ, params=P["VADParams"](stop_secs=0.5)
    )
    vad_processor = P["VADProcessor"](vad_analyzer=vad_analyzer)
    raw_tap = _RawCaptureTap()
    marker_tap = _VadMarkerTap()

    processors = [raw_tap, vad_processor, bargein, marker_tap, bridge]
    meta = {
        "sample_rate": INPUT_SAMPLE_RATE_HZ,
        "vad_start_secs": vad_analyzer.params.start_secs,
        "vad_stop_secs": vad_analyzer.params.stop_secs,
    }
    return processors, meta


def build_diagnostic_pipeline(
    *, capture: IngressCapture, dry: bool, input_device_name: str = "respeaker"
):
    """Wraps ``build_processor_chain`` in a real ``Pipeline``/
    ``PipelineWorker``. ``dry=True`` builds every NeXa/router/Pipecat
    object but skips the real ``LocalAudioTransport``/PyAudio device open
    — mirrors every other ``--dry`` mode in this codebase."""
    P = _pipecat_hw_imports()
    processors, meta = build_processor_chain(capture=capture)
    sample_rate = meta["sample_rate"]

    if dry:
        return None, None, {"mode": "dry", **meta}

    import pyaudio

    from nexa.voice.device import find_device_index

    pa = pyaudio.PyAudio()
    in_idx = find_device_index(pa, input_device_name, require_input=True)

    transport = P["LocalAudioTransport"](
        P["LocalAudioTransportParams"](
            audio_in_enabled=True,
            audio_out_enabled=False,  # no assistant playback whatsoever
            audio_in_sample_rate=sample_rate,
            audio_in_channels=1,
            input_device_index=in_idx,
        )
    )
    pipeline = P["Pipeline"]([transport.input(), *processors])
    worker = P["PipelineWorker"](
        pipeline,
        params=P["PipelineParams"](audio_in_sample_rate=sample_rate),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    return worker, P["WorkerRunner"], {
        "mode": "live",
        "input_device_index": in_idx,
        **meta,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--dry", action="store_true",
        help="validate object construction only; no audio device, no Gemini, no network",
    )
    p.add_argument(
        "--note", default="", help="free-text note stored in the results JSON",
    )
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    capture = IngressCapture(sample_rate=16000, out_dir=OUT_DIR)
    worker, runner_cls, meta = build_diagnostic_pipeline(capture=capture, dry=args.dry)
    capture.sample_rate = meta["sample_rate"]

    print("=" * 74)
    print("M2.6B.4I / R0048 -- real audio ingress parity probe (NO GEMINI)")
    print("=" * 74)
    for k, v in meta.items():
        print(f"  {k:24} {v}")
    print("-" * 74)

    if args.dry:
        print("DRY RUN: pipeline objects constructed (real SileroVADAnalyzer/")
        print("VADProcessor/BargeInController/_VadToProviderBridge, fake")
        print("provider stub). No audio device opened, no results written.")
        return 0

    print("\nSpeak each phrase below, pausing briefly between them (repeat")
    print('"Czarna dziura" several times -- the real Attempt #3 symptom):\n')
    for i, phrase in enumerate(TEST_PHRASES, start=1):
        print(f"  {i:>2}. {phrase}")
    print("\nCtrl+C ends the session and writes the summary JSON.\n")

    runner = runner_cls()
    await runner.add_workers(worker)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    import signal

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async def _cap() -> None:
        await asyncio.sleep(HARD_SESSION_CAP_S)
        print(f"\n[hard {HARD_SESSION_CAP_S // 60}-min session cap reached]")
        stop.set()

    cap_task = asyncio.create_task(_cap())
    run_task = asyncio.create_task(runner.run())
    await asyncio.wait(
        {run_task, asyncio.create_task(stop.wait())}, return_when=asyncio.FIRST_COMPLETED
    )
    cap_task.cancel()
    with contextlib.suppress(Exception):
        await runner.stop_workers()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(run_task, timeout=10)

    path = capture.write_summary(note=args.note or "operator ingress-parity session")
    print(f"\n{len(capture.completed)} utterance(s) captured.")
    print(f"WAV files + summary JSON under: {OUT_DIR}")
    print(f"summary JSON: {path}")
    print("This probe does NOT judge audio quality -- listen to both WAVs")
    print("per utterance and compare (that is the operator's call).")
    return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
