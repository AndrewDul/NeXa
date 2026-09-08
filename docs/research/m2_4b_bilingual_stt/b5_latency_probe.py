"""M2.4B.5 — headless latency + behaviour probe for the real
``BilingualSpeechTranscriber`` (library LID + guard + one explicit decode).

RESEARCH TOOLING ONLY. Runs the operator corpus WAVs through the *real*
production path (ctypes ``WhisperCppLanguageDetector`` +
``WhisperCppTranscriber`` CLI + ``LanguageIdGuard``) in a realistic
conversational order so ``last_input_language`` is meaningful, and records
per-turn: detect latency, decode latency, total, guard decision, whether a
re-decode fired, selected language, transcript.

The full voice-pipeline acceptance (END_OF_TURN → first audio, live sticky
preferences, PL→EN→PL in one ConversationSession) needs the operator mic —
see ``apps/nexa_bilingual_voice_probe.py``. This probe measures the STT
boundary only.

Writes ``b5_latency_probe_<ts>.json``.
"""
from __future__ import annotations

import asyncio
import json
import statistics as st
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))

from nexa.stt import (  # noqa: E402
    BilingualSpeechTranscriber,
    Language,
    LanguageIdGuard,
    WhisperCppLanguageDetector,
    WhisperCppTranscriber,
)

AUDIO = HERE / "fixtures" / "audio"

# scenario -> ordered list of fixture uids (as if spoken one after another)
SCENARIOS = {
    "confident_pl": ["001_pl_co_to_jest_czarna_dziura", "003_pl_z_czego_sklada_sie_gwiazda",
                     "004_pl_po_co_czlowiekowi_sen", "014_pl_dlaczego_niebo_jest_niebieskie"],
    "confident_en": ["016_en_what_is_a_black_hole", "018_en_what_is_a_star_made_of",
                     "019_en_why_do_humans_need_sleep", "029_en_why_is_the_sky_blue"],
    "low_conf_pl_short": ["001_pl_co_to_jest_czarna_dziura",  # establish PL context
                          "031_short_tak", "032_short_nie", "033_short_dobra"],
    "switch_pl_to_en": ["001_pl_co_to_jest_czarna_dziura", "016_en_what_is_a_black_hole",
                        "003_pl_z_czego_sklada_sie_gwiazda", "018_en_what_is_a_star_made_of"],
    "switch_en_to_pl": ["016_en_what_is_a_black_hole", "001_pl_co_to_jest_czarna_dziura",
                        "018_en_what_is_a_star_made_of", "004_pl_po_co_czlowiekowi_sen"],
    "third_language_recovery": ["001_pl_co_to_jest_czarna_dziura",  # PL context
                                "002_pl_jak_ona_powstaje",   # raw=ru, must still decode pl
                                "021_en_what_is_heavier_one_kilogram_of_iron_or_",  # raw=ko
                                "029_en_why_is_the_sky_blue"],  # raw=he
}


def _audio(uid: str) -> bytes:
    import wave

    w = wave.open(str(AUDIO / f"{uid}.wav"), "rb")
    d = w.readframes(w.getnframes())
    w.close()
    return d


async def main() -> None:
    base = WhisperCppTranscriber()
    detector = WhisperCppLanguageDetector()
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    out: dict = {"ts": ts, "explicit_baseline_note": "R0024: explicit -l pl/-l en ~1.7s/utt",
                 "scenarios": {}}
    all_totals: list[float] = []

    for name, uids in SCENARIOS.items():
        # fresh session state per scenario
        bt = BilingualSpeechTranscriber(
            base, detector, guard=LanguageIdGuard(), session_default_language=Language.PL
        )
        turns = []
        for uid in uids:
            audio = _audio(uid)
            t0 = time.perf_counter()
            r = await bt.transcribe(audio)
            wall = time.perf_counter() - t0
            d = r.language_decision
            turns.append({
                "uid": uid,
                "selected_language": d.selected_language,
                "guard": d.guard_decision.value,
                "redecoded": d.redecoded,
                "raw": d.raw_detected_language, "p_pl": d.p_pl, "p_en": d.p_en,
                "detect_s": d.detect_latency_s, "decode_s": d.decode_latency_s,
                "total_s": round(wall, 3),
                "transcript": r.text,
                "last_input_language_after": d.last_input_language_after,
            })
            all_totals.append(wall)
            print(f"[{name:22}] {uid:44} {d.guard_decision.value:17} "
                  f"-> {d.selected_language}  detect {d.detect_latency_s:.2f}s "
                  f"decode {d.decode_latency_s:.2f}s total {wall:.2f}s  "
                  f"redecode={d.redecoded}  {r.text!r}")
        det_l = [t["detect_s"] for t in turns]
        dec_l = [t["decode_s"] for t in turns]
        tot_l = [t["total_s"] for t in turns]
        out["scenarios"][name] = {
            "turns": turns,
            "detect_mean_s": round(st.mean(det_l), 3),
            "decode_mean_s": round(st.mean(dec_l), 3),
            "total_mean_s": round(st.mean(tot_l), 3),
            "total_median_s": round(st.median(tot_l), 3),
            "total_max_s": round(max(tot_l), 3),
        }

    out["overall"] = {
        "n": len(all_totals),
        "total_mean_s": round(st.mean(all_totals), 3),
        "total_median_s": round(st.median(all_totals), 3),
        "total_p90_s": round(sorted(all_totals)[int(0.9 * (len(all_totals) - 1))], 3),
        "total_min_s": round(min(all_totals), 3),
        "total_max_s": round(max(all_totals), 3),
        "added_vs_explicit_s_approx": round(st.mean(all_totals) - 1.7, 2),
    }
    detector.close()
    (HERE / f"b5_latency_probe_{ts}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("\nOVERALL:", json.dumps(out["overall"], ensure_ascii=False))
    print(f"-> b5_latency_probe_{ts}.json")


if __name__ == "__main__":
    asyncio.run(main())
