"""M2.4B.4 — bilingual STT strategy benchmark runner.

RESEARCH TOOLING ONLY (deletable with the rest of `docs/research/`). Runs the
pinned production whisper.cpp (`v1.9.3`, `ggml-base-q8_0`, `-t 4`, default
beam) over the 50-utterance real-operator corpus under four strategies and
records raw per-run evidence. It does NOT touch production config,
`ConversationSession`, the model/provider, TTS, or the production
`VoiceRuntime` language behaviour — it only shells out to `whisper-cli` and
writes JSON.

Strategies:

* ``b0_pl``  — explicit ``-l pl`` on ALL 50 (baseline + forced-PL effect)
* ``b0_en``  — explicit ``-l en`` on ALL 50 (baseline + forced-EN effect)
* ``auto``   — ``-l auto`` on ALL 50 (detect + decode, one pass)
* ``dl_then_explicit`` — ``-dl`` (detect only, exits) on ALL 50, then a
  second ``-l <detected>`` decode; latency = LID pass + decode pass.

Per run: transcript, detected language + probability (auto/dl, from
stderr), wall latency, RTF, and a coarse resource window (CPU %, child peak
RSS, temperature, throttled).

Output: ``bench_stt_raw_<ts>.jsonl`` (one row per strategy×uid) +
``bench_stt_meta_<ts>.json``.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "src"))

from corpus import CORPUS  # noqa: E402

from nexa.stt.config import (  # noqa: E402  (read-only: path + pin constants)
    DEFAULT_THREADS,
    default_model_path,
    default_whisper_cli_path,
)

CLI = default_whisper_cli_path()
MODEL = default_model_path()  # ggml-base-q8_0.bin
THREADS = DEFAULT_THREADS  # 4 — production value
AUDIO = HERE / "fixtures" / "audio"

_DETECT_RE = re.compile(r"auto-detected language:\s*(\w+)\s*\(p\s*=\s*([\d.]+)\)")


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def _temp_c() -> float:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000.0
    except Exception:
        return -1.0


def _throttled() -> str:
    try:
        out = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        return out.split("=")[-1] if "=" in out else out
    except Exception:
        return "n/a"


def _cpu_times() -> tuple[int, int]:
    parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    vals = [int(x) for x in parts]
    idle = vals[3] + vals[4]
    return sum(vals), idle


class _Sampler(threading.Thread):
    """Coarse resource window around one whisper-cli run."""

    def __init__(self, proc: subprocess.Popen) -> None:
        super().__init__(daemon=True)
        self._proc = proc
        self._stop = threading.Event()
        self.peak_rss_kb = 0
        self.temp_max = _temp_c()

    def run(self) -> None:
        pid = self._proc.pid
        status = Path(f"/proc/{pid}/status")
        while not self._stop.is_set():
            try:
                for line in status.read_text().splitlines():
                    if line.startswith("VmHWM:"):
                        self.peak_rss_kb = max(self.peak_rss_kb, int(line.split()[1]))
                        break
            except Exception:
                pass
            t = _temp_c()
            if t > 0:
                self.temp_max = max(self.temp_max, t)
            time.sleep(0.1)

    def stop(self) -> None:
        self._stop.set()


def _run_whisper(wav: Path, *, lang: str, detect_only: bool, out_dir: Path) -> dict:
    """One whisper-cli invocation. Returns transcript / detected lang+prob /
    latency / rtf / resources."""
    cmd = [
        str(CLI), "-m", str(MODEL), "-f", str(wav),
        "-l", lang, "-t", str(THREADS),
    ]
    if detect_only:
        cmd.append("-dl")
    else:
        cmd += ["-oj", "-of", str(out_dir / wav.stem), "-nt"]

    cpu0 = _cpu_times()
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    sampler = _Sampler(proc)
    sampler.start()
    stdout, stderr = proc.communicate(timeout=180)
    wall = time.perf_counter() - t0
    sampler.stop()
    sampler.join(timeout=1.0)
    cpu1 = _cpu_times()

    dtot = max(cpu1[0] - cpu0[0], 1)
    didle = cpu1[1] - cpu0[1]
    cpu_pct = round(100.0 * (1 - didle / dtot), 1)

    blob = stdout + "\n" + stderr
    det_lang, det_p = None, None
    m = _DETECT_RE.search(blob)
    if m:
        det_lang, det_p = m.group(1), float(m.group(2))

    transcript = None
    if not detect_only:
        jf = out_dir / f"{wav.stem}.json"
        if jf.is_file():
            data = json.loads(jf.read_text(encoding="utf-8"))
            transcript = "".join(s["text"] for s in data.get("transcription", [])).strip()
            if det_lang is None:
                det_lang = (data.get("result") or {}).get("language")

    audio_s = _wav_seconds(wav)
    return {
        "cmd_lang_arg": lang,
        "detect_only": detect_only,
        "returncode": proc.returncode,
        "transcript": transcript,
        "detected_language": det_lang,
        "detected_p": det_p,
        "wall_s": round(wall, 3),
        "audio_s": round(audio_s, 3),
        "rtf": round(wall / audio_s, 3) if audio_s else None,
        "cpu_pct_window": cpu_pct,
        "peak_rss_kb": sampler.peak_rss_kb,
        "temp_c_max": round(sampler.temp_max, 1),
        "throttled": _throttled(),
        "stderr_tail": stderr.strip().splitlines()[-1] if stderr.strip() else "",
    }


def main() -> int:
    if not CLI.is_file() or not MODEL.is_file():
        print(f"error: whisper-cli / model missing ({CLI}, {MODEL})", file=sys.stderr)
        return 1

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    raw_path = HERE / f"bench_stt_raw_{ts}.jsonl"
    meta_path = HERE / f"bench_stt_meta_{ts}.json"
    out_dir = HERE / "_bench_out"
    out_dir.mkdir(exist_ok=True)

    print(f"M2.4B.4 STT benchmark — whisper.cpp v1.9.3 base/q8_0 -t{THREADS}")
    print(f"  {len(CORPUS)} fixtures x 4 strategy passes")
    print(f"  raw -> {raw_path.name}\n")

    rows: list[dict] = []
    with raw_path.open("w", encoding="utf-8") as fh:
        for n, it in enumerate(CORPUS, 1):
            wav = AUDIO / f"{it.uid}.wav"
            base = {
                "uid": it.uid, "group": it.group,
                "expected_language": it.expected_language,
                "expected_text": it.text, "leans": it.leans, "primary": it.primary,
            }

            # --- b0_pl / b0_en: explicit, all fixtures ---
            for strat, larg in (("b0_pl", "pl"), ("b0_en", "en")):
                r = _run_whisper(wav, lang=larg, detect_only=False, out_dir=out_dir)
                row = {**base, "strategy": strat, **r}
                rows.append(row)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

            # --- strategy 1: auto direct ---
            r = _run_whisper(wav, lang="auto", detect_only=False, out_dir=out_dir)
            row_auto = {**base, "strategy": "auto", **r}
            rows.append(row_auto)
            fh.write(json.dumps(row_auto, ensure_ascii=False) + "\n")

            # --- strategy 2: -dl (detect only) then explicit decode ---
            r_dl = _run_whisper(wav, lang="auto", detect_only=True, out_dir=out_dir)
            _dld = r_dl["detected_language"]
            decided = _dld if _dld in ("pl", "en") else "en"
            r_dec = _run_whisper(wav, lang=decided, detect_only=False, out_dir=out_dir)
            row_dl = {
                **base, "strategy": "dl_then_explicit",
                "dl_detected_language": r_dl["detected_language"],
                "dl_detected_p": r_dl["detected_p"],
                "dl_wall_s": r_dl["wall_s"],
                "decode_lang": decided,
                "decode_wall_s": r_dec["wall_s"],
                "wall_s": round(r_dl["wall_s"] + r_dec["wall_s"], 3),
                "audio_s": r_dec["audio_s"],
                "rtf": round((r_dl["wall_s"] + r_dec["wall_s"]) / r_dec["audio_s"], 3)
                if r_dec["audio_s"] else None,
                "transcript": r_dec["transcript"],
                "detected_language": r_dl["detected_language"],
                "detected_p": r_dl["detected_p"],
                "cpu_pct_window": r_dec["cpu_pct_window"],
                "peak_rss_kb": max(r_dl["peak_rss_kb"], r_dec["peak_rss_kb"]),
                "temp_c_max": max(r_dl["temp_c_max"], r_dec["temp_c_max"]),
                "throttled": r_dec["throttled"],
            }
            rows.append(row_dl)
            fh.write(json.dumps(row_dl, ensure_ascii=False) + "\n")

            print(f"[{n:2d}/50] {it.uid:46} "
                  f"b0_pl/b0_en/auto/dl done  "
                  f"auto->{row_auto['detected_language']}"
                  f"({row_auto['detected_p']})  dl->{row_dl['dl_detected_language']}"
                  f"({row_dl['dl_detected_p']})", flush=True)

    meta = {
        "ts": ts,
        "whisper_cpp_tag": "v1.9.3",
        "model": "ggml-base-q8_0",
        "threads": THREADS,
        "cli": str(CLI),
        "n_fixtures": len(CORPUS),
        "strategies": ["b0_pl", "b0_en", "auto", "dl_then_explicit"],
        "n_rows": len(rows),
        "notes": (
            "Explicit b0_pl/b0_en run on ALL 50 fixtures (not just their own "
            "language) so forced-language effects on mixed/short are visible. "
            "detected_p is from whisper-cli stderr 'auto-detected language: X "
            "(p = Y)'; JSON does not expose it. -dl re-runs the encoder, so "
            "dl_then_explicit pays ~2x encode — reported, not hidden."
        ),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    # tidy the transient decode output
    for f in out_dir.glob("*.json"):
        f.unlink()
    out_dir.rmdir()
    print(f"\nDONE — {len(rows)} rows -> {raw_path.name} , {meta_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
