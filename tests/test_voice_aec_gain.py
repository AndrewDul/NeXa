"""M2.6B.4N / R0053 — deterministic tests for
``nexa.voice.aec_gain`` (coherent AEC reference gain).

Offline, no audio hardware, no real ``amixer`` call, no Gemini. The
``runner``/``time_source`` injection points make ``CoherentReferenceGain``
fully deterministic; ``parse_amixer_db_gain``/``apply_gain`` are pure
functions tested directly against captured real ``amixer`` output shapes
(the exact text this checkpoint's own system audit read from this
Pi's two independent ALSA mixers).
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.voice.aec_gain import (  # noqa: E402
    CoherentReferenceGain,
    _run_amixer,
    apply_gain,
    parse_amixer_db_gain,
)

#: Real `amixer -c UACDemoV10 get PCM` output captured during R0053's
#: own system audit (2026-09-12) — the USB speaker's own mixer.
_USB_SPEAKER_OUTPUT = """Simple mixer control 'PCM',0
  Capabilities: pvolume pswitch pswitch-joined
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 147
  Mono:
  Front Left: Playback 100 [68%] [-9.72dB] [on]
  Front Right: Playback 100 [68%] [-9.72dB] [on]
"""

#: Real `amixer -c Array get PCM` output captured during the same audit
#: — the reSpeaker's own, separate, always-observed-at-max mixer.
_RESPEAKER_OUTPUT = """Simple mixer control 'PCM',0
  Capabilities: pvolume pswitch
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 60
  Mono:
  Front Left: Playback 60 [100%] [0.00dB] [on]
  Front Right: Playback 60 [100%] [0.00dB] [on]
"""

_MUTED_OUTPUT = """Simple mixer control 'PCM',0
  Front Left: Playback 0 [0%] [-60.00dB] [off]
"""


class TestParseAmixerDbGain(unittest.TestCase):
    def test_real_usb_speaker_output_at_minus_9_72db(self) -> None:
        gain = parse_amixer_db_gain(_USB_SPEAKER_OUTPUT)
        self.assertIsNotNone(gain)
        self.assertAlmostEqual(gain, 10.0 ** (-9.72 / 20.0), places=6)

    def test_real_respeaker_output_at_0db_is_unity(self) -> None:
        gain = parse_amixer_db_gain(_RESPEAKER_OUTPUT)
        self.assertEqual(gain, 1.0)

    def test_muted_channel_reports_zero_not_none(self) -> None:
        gain = parse_amixer_db_gain(_MUTED_OUTPUT)
        self.assertEqual(gain, 0.0)

    def test_garbled_output_is_unreadable(self) -> None:
        self.assertIsNone(parse_amixer_db_gain("no card, no such device"))

    def test_empty_output_is_unreadable(self) -> None:
        self.assertIsNone(parse_amixer_db_gain(""))

    def test_gain_is_clamped_to_0_1_even_for_a_positive_db_value(self) -> None:
        # Defensive: a hypothetical boost-capable control reporting +6dB
        # must never scale the reference ABOVE the original signal.
        gain = parse_amixer_db_gain("Front Left: Playback 100 [100%] [6.00dB] [on]\n")
        self.assertEqual(gain, 1.0)


class TestApplyGain(unittest.TestCase):
    def test_gain_1_0_is_a_true_identity_noop(self) -> None:
        pcm = b"\x01\x02\x03\x04"
        self.assertIs(apply_gain(pcm, 1.0), pcm)

    def test_empty_pcm_is_a_noop_regardless_of_gain(self) -> None:
        self.assertEqual(apply_gain(b"", 0.5), b"")

    def test_gain_0_5_halves_sample_amplitude(self) -> None:
        pcm = struct.pack("<4h", 1000, -1000, 20000, -20000)
        out = apply_gain(pcm, 0.5)
        self.assertEqual(struct.unpack("<4h", out), (500, -500, 10000, -10000))

    def test_gain_0_0_silences(self) -> None:
        pcm = struct.pack("<2h", 12345, -12345)
        out = apply_gain(pcm, 0.0)
        self.assertEqual(struct.unpack("<2h", out), (0, 0))


class TestCoherentReferenceGain(unittest.TestCase):
    def _fake(self, output: str, *, refresh_secs: float = 2.0):
        calls: list[list[str]] = []

        def runner(args: list[str]) -> str:
            calls.append(args)
            return output

        clock = {"t": 0.0}

        def time_source() -> float:
            return clock["t"]

        crg = CoherentReferenceGain(
            card="UACDemoV10",
            runner=runner,
            time_source=time_source,
            refresh_secs=refresh_secs,
        )
        return crg, calls, clock

    def test_reads_real_captured_usb_speaker_shape_correctly(self) -> None:
        crg, calls, _clock = self._fake(_USB_SPEAKER_OUTPUT)
        gain = crg.current_gain()
        self.assertAlmostEqual(gain, 10.0 ** (-9.72 / 20.0), places=6)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ["amixer", "-c", "UACDemoV10", "get", "PCM"])

    def test_caches_within_refresh_window(self) -> None:
        crg, calls, clock = self._fake(_USB_SPEAKER_OUTPUT, refresh_secs=2.0)
        crg.current_gain()
        clock["t"] = 1.0
        crg.current_gain()
        self.assertEqual(len(calls), 1)  # no second amixer call yet

    def test_rereads_after_refresh_window_elapses(self) -> None:
        crg, calls, clock = self._fake(_USB_SPEAKER_OUTPUT, refresh_secs=2.0)
        crg.current_gain()
        clock["t"] = 5.0
        crg.current_gain()
        self.assertEqual(len(calls), 2)

    def test_defaults_to_1_0_before_any_successful_read(self) -> None:
        crg, _calls, _clock = self._fake("garbled, unreadable")
        self.assertEqual(crg.current_gain(), 1.0)

    def test_falls_back_to_last_known_good_on_a_later_failed_read(self) -> None:
        outputs = [_USB_SPEAKER_OUTPUT, "garbled now"]
        calls: list[list[str]] = []

        def runner(args: list[str]) -> str:
            calls.append(args)
            return outputs[len(calls) - 1] if len(calls) <= len(outputs) else "garbled"

        clock = {"t": 0.0}
        crg = CoherentReferenceGain(
            card="UACDemoV10",
            runner=runner,
            time_source=lambda: clock["t"],
            refresh_secs=1.0,
        )
        first = crg.current_gain()
        self.assertAlmostEqual(first, 10.0 ** (-9.72 / 20.0), places=6)
        clock["t"] = 10.0
        second = crg.current_gain()  # this read is garbled -- keep first
        self.assertEqual(second, first)

    def test_a_well_behaved_runner_returning_empty_output_is_safe(self) -> None:
        # The real default runner (`_run_amixer`) never raises -- any
        # subprocess failure becomes "" output, which this must treat as
        # unreadable (fall back to 1.0), never crash.
        crg = CoherentReferenceGain(card="UACDemoV10", runner=lambda a: "")
        self.assertEqual(crg.current_gain(), 1.0)


class TestRunAmixerNeverRaises(unittest.TestCase):
    def test_a_nonexistent_binary_returns_empty_string_not_an_exception(self) -> None:
        out = _run_amixer(["definitely-not-a-real-binary-xyz", "get", "PCM"])
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
