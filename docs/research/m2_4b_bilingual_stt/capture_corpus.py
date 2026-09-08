"""M2.4B.4 — operator corpus recorder.

RESEARCH TOOLING ONLY (deletable with the rest of `docs/research/`). Records
the fixed `corpus.CORPUS` utterances once, from the already-known-good
microphone (`nexa.voice`'s `"respeaker"` ALSA alias, resolved by name — no
ALSA config, no device index typed by hand), as 16 kHz mono PCM16 WAV
fixtures under `fixtures/audio/<uid>.wav` — exactly the format
`WhisperCppTranscriber` consumes.

It does NOT touch production config, `ConversationSession`, the model /
provider, TTS, or the production `VoiceRuntime` language behaviour. It only
opens a PyAudio input stream and writes WAV files + a metadata manifest.

Usage (operator):

    ./.venv/bin/python docs/research/m2_4b_bilingual_stt/capture_corpus.py

For each numbered sentence: press Enter, say the sentence once clearly,
press Enter again to stop. `s`+Enter skips, `r`+Enter re-records the one
just done. Already-recorded sentences are skipped on re-run (resume-safe).

    --only pl|en|short|mixed   record just one group
    --redo 007,012             re-record specific numbers (or uids)
    --list                     print the numbered sentence list and exit
    --device NAME              override the input device name (default: respeaker)
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "src"))

from corpus import CORPUS, GROUPS, as_records  # noqa: E402

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # PCM16
CHUNK = 1024

AUDIO_DIR = HERE / "fixtures" / "audio"
MANIFEST = HERE / "fixtures" / "corpus_manifest.json"
RECORDED_JSONL = HERE / "fixtures" / "corpus.jsonl"

_MIN_DURATION_S = 0.35
_SILENCE_PEAK = 600  # abs int16; below this the take is almost certainly silent
_CLIP_PEAK = 32_200


def _resolve_input_index(name: str) -> tuple[object, int]:
    import pyaudio  # noqa: PLC0415 - optional heavy dep, only when actually recording

    from nexa.voice.device import find_device_index

    pa = pyaudio.PyAudio()
    try:
        idx = find_device_index(pa, name, require_input=True)
    except Exception:
        pa.terminate()
        raise
    return pa, idx


def _peak_rms(frames: bytes) -> tuple[int, float]:
    import array
    import math

    a = array.array("h")
    a.frombytes(frames)
    if not a:
        return 0, -120.0
    peak = max(abs(x) for x in a)
    rms = math.sqrt(sum(x * x for x in a) / len(a))
    dbfs = 20 * math.log10(rms / 32768.0) if rms > 0 else -120.0
    return peak, dbfs


class _Recorder:
    def __init__(self, pa: object, index: int) -> None:
        self._pa = pa
        self._index = index
        self._stream = None
        self._buf: list[bytes] = []
        self._run = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        assert self._stream is not None
        start = time.monotonic()
        last_meter = 0.0
        while self._run.is_set():
            try:
                data = self._stream.read(CHUNK, exception_on_overflow=False)
            except Exception as exc:  # noqa: BLE001
                print(f"\n  ! read error: {exc}", file=sys.stderr, flush=True)
                break
            self._buf.append(data)
            now = time.monotonic()
            if now - last_meter >= 0.2:
                last_meter = now
                peak, _ = _peak_rms(data)
                bars = min(40, peak * 40 // 32768)
                print(
                    f"\r  ● rec {now - start:4.1f}s  |{'#' * bars}{' ' * (40 - bars)}|",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
        print("\r" + " " * 64 + "\r", end="", file=sys.stderr, flush=True)

    def start(self) -> None:
        import pyaudio  # noqa: PLC0415

        self._buf = []
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=self._index,
            frames_per_buffer=CHUNK,
        )
        self._run.set()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> bytes:
        self._run.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        return b"".join(self._buf)

    def close(self) -> None:
        self._pa.terminate()


def _write_wav(path: Path, frames: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(frames)


def _write_manifest() -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(
            {
                "purpose": "M2.4B.4 bilingual voice-input evaluation corpus — ground truth",
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
                "sample_width_bytes": SAMPLE_WIDTH,
                "items": as_records(),
            },
            ensure_ascii=False,
            indent=1,
        )
    )


def _load_recorded() -> dict[str, dict]:
    if not RECORDED_JSONL.exists():
        return {}
    out: dict[str, dict] = {}
    for line in RECORDED_JSONL.read_text().splitlines():
        line = line.strip()
        if line:
            rec = json.loads(line)
            out[rec["uid"]] = rec
    return out


def _save_recorded(recorded: dict[str, dict]) -> None:
    RECORDED_JSONL.parent.mkdir(parents=True, exist_ok=True)
    ordered = [recorded[it.uid] for it in CORPUS if it.uid in recorded]
    RECORDED_JSONL.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in ordered) + "\n"
    )


def _prompt(msg: str) -> str:
    try:
        return input(msg).strip().lower()
    except EOFError:
        return "q"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--only", choices=GROUPS)
    ap.add_argument("--redo", default="", help="comma list of numbers or uids to re-record")
    ap.add_argument("--device", default="respeaker")
    ap.add_argument("--list", action="store_true", help="print the sentence list and exit")
    args = ap.parse_args()

    _write_manifest()  # ground truth is always (re)written, independent of audio

    items = list(CORPUS)
    if args.only:
        items = [it for it in items if it.group == args.only]

    if args.list:
        for n, it in enumerate(CORPUS, 1):
            lang = it.expected_language.upper()
            print(f"{n:3d}. [{lang:<9}] {it.text}")
        return 0

    redo = {tok.strip() for tok in args.redo.split(",") if tok.strip()}

    def is_redo(n: int, uid: str) -> bool:
        return str(n) in redo or f"{n:03d}" in redo or uid in redo

    recorded = _load_recorded()
    todo = []
    for n, it in enumerate(CORPUS, 1):
        if args.only and it.group != args.only:
            continue
        wav = AUDIO_DIR / f"{it.uid}.wav"
        if wav.exists() and it.uid in recorded and not is_redo(n, it.uid):
            continue
        todo.append((n, it))

    if not todo:
        print(f"All {len(items)} target utterances already recorded. "
              f"Use --redo <numbers> to re-record.")
        print(f"  manifest: {MANIFEST}")
        print(f"  recorded: {RECORDED_JSONL}")
        return 0

    print(f"NeXa M2.4B.4 corpus recorder — {len(todo)} utterance(s) to record")
    print(f"  device: {args.device!r}   format: {SAMPLE_RATE} Hz mono PCM16")
    print(f"  audio -> {AUDIO_DIR}")
    print("  For each: [Enter]=start, speak once, [Enter]=stop. "
          "'s'=skip, 'r'=redo last, 'q'=quit.\n")

    try:
        pa, index = _resolve_input_index(args.device)
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not open input device {args.device!r}: {exc}", file=sys.stderr)
        return 1

    rec = _Recorder(pa, index)
    try:
        i = 0
        while i < len(todo):
            n, it = todo[i]
            lang = it.expected_language.upper()
            hint = ""
            if it.group == "short":
                hint = f"  (ambiguous; leans {it.leans})"
            elif it.group == "mixed":
                hint = f"  (code-switch; matrix {it.primary})"
            print(f"[{n:3d}/50] [{lang:<9}]{hint}")
            print(f"         “{it.text}”")
            cmd = _prompt("         [Enter]=start  s=skip  q=quit > ")
            if cmd == "q":
                break
            if cmd == "s":
                print("         (skipped)\n")
                i += 1
                continue

            rec.start()
            _prompt("         ● recording…  [Enter]=stop > ")
            frames = rec.stop()

            dur = len(frames) / (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS)
            peak, dbfs = _peak_rms(frames)
            warn = []
            if dur < _MIN_DURATION_S:
                warn.append(f"very short ({dur:.2f}s)")
            if peak < _SILENCE_PEAK:
                warn.append(f"near-silent (peak {peak})")
            if peak >= _CLIP_PEAK:
                warn.append(f"clipping (peak {peak})")
            status = ("  ⚠ " + ", ".join(warn)) if warn else "  ok"
            print(f"         {dur:.2f}s  peak {peak}  {dbfs:.1f} dBFS{status}")

            keep = _prompt("         [Enter]=keep  r=redo  s=skip  q=quit > ")
            if keep == "q":
                break
            if keep == "r":
                print("         (redo)\n")
                continue
            if keep == "s":
                print("         (skipped)\n")
                i += 1
                continue

            wav = AUDIO_DIR / f"{it.uid}.wav"
            _write_wav(wav, frames)
            recorded[it.uid] = {
                **{k: v for k, v in as_records()[n - 1].items()},
                "wav": f"fixtures/audio/{it.uid}.wav",
                "duration_s": round(dur, 3),
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
                "peak_abs": peak,
                "rms_dbfs": round(dbfs, 1),
                "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            _save_recorded(recorded)
            print(f"         saved {wav.name}\n")
            i += 1
    finally:
        rec.close()

    done = sum(1 for it in CORPUS if it.uid in recorded)
    print(f"\nRecorded {done}/50 total. Manifest: {MANIFEST}")
    print(f"Recorded metadata: {RECORDED_JSONL}")
    if done < 50:
        print("Re-run the same command to continue the remaining ones.")
    else:
        print("Corpus complete — ready for the benchmark.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
