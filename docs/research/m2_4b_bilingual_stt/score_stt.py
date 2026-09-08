"""M2.4B.4 — score the STT benchmark raw rows.

RESEARCH TOOLING ONLY. Reads a ``bench_stt_raw_<ts>.jsonl`` (latest if not
given) + the corpus and computes, per strategy and per group:

* WER and normalised WER (lowercase, punctuation -> space, whitespace
  collapsed; best of the reference + its ``acceptable`` variants);
* an automatic S-class first pass (S0 exact-normalised, S1 tiny lexical,
  S2/S3 left for human review — printed with the diff so they can be set);
* language-ID confusion (monolingual PL/EN only) for ``auto`` and the
  ``-dl`` pass, with the probability distribution;
* short/ambiguous per-utterance detection + transcript;
* mixed/code-switch token-survival (both a PL and an EN content token
  present in the transcript) as a USABLE / PARTIALLY_USABLE / BROKEN hint.

Writes ``score_stt_<ts>.json`` and prints a summary.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from corpus import CORPUS  # noqa: E402

_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)
_WS = re.compile(r"\s+")


def norm(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s).lower().strip()
    s = _PUNCT.sub(" ", s)
    s = s.replace("’", "'")
    return _WS.sub(" ", s).strip()


def wer(ref: str, hyp: str) -> float:
    r, h = norm(ref).split(), norm(hyp).split()
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(
                d[j] + 1,
                d[j - 1] + 1,
                prev + (0 if r[i - 1] == h[j - 1] else 1),
            )
            prev = cur
    return d[len(h)] / len(r)


def best_wer(item, hyp: str) -> float:
    refs = [item.text, *item.acceptable]
    return min(wer(r, hyp) for r in refs)


def auto_sclass(item, hyp: str) -> str:
    """First-pass S-class. S2/S3 must be human-reviewed."""
    w = best_wer(item, hyp)
    nh, nr = norm(hyp), norm(item.text)
    if not nh:
        return "S3"
    if nh == nr or w == 0.0:
        return "S0"
    # non-latin script leaked in (the classic auto-detect failure)
    if re.search(r"[^\x00-\x7fÀ-ſ]", hyp):
        return "S3?"
    if w <= 0.20:
        return "S1"
    if w <= 0.50:
        return "S2?"
    return "S3?"


PL_MARK = re.compile(r"[ąćęłńóśźż]", re.I)
_EN_WORDS = {
    "tell", "me", "more", "okay", "let", "lets", "continue", "what", "black",
    "hole", "is", "a", "can", "you", "explain", "now", "the",
}
_PL_WORDS = {
    "dobra", "wróćmy", "wrocmy", "wrócić", "wrocic", "polskiego", "prościej",
    "prosciej", "dlaczego", "ale", "więcej", "wiecej", "gwiazdach", "powiedz",
    "polsku", "jeszcze", "raz", "to", "mi", "tego", "po",
}


def mixed_survival(item, hyp: str) -> tuple[str, str]:
    toks = set(norm(hyp).split())
    has_pl = bool(PL_MARK.search(hyp)) or bool(toks & _PL_WORDS)
    has_en = bool(toks & _EN_WORDS)
    if has_pl and has_en:
        return "USABLE?", "both PL+EN tokens present"
    if has_pl or has_en:
        side = "PL only" if has_pl else "EN only"
        return "PARTIALLY_USABLE?", f"{side} survived"
    return "BROKEN?", "neither side clearly recognisable"


def load_raw(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def summarise(rows_by_key: dict, by_item: dict) -> dict:
    out: dict = {}
    strategies = sorted({k[0] for k in rows_by_key})
    groups = ["pl", "en", "short", "mixed"]

    # ---- WER / latency per strategy x group ----
    wer_tbl: dict = {}
    for strat in strategies:
        wer_tbl[strat] = {}
        for g in groups:
            rs = [rows_by_key[(strat, it.uid)] for it in CORPUS
                  if it.group == g and (strat, it.uid) in rows_by_key]
            if not rs:
                continue
            wers = [best_wer(by_item[r["uid"]], r["transcript"] or "") for r in rs]
            lat = [r["wall_s"] for r in rs if r.get("wall_s")]
            rtf = [r["rtf"] for r in rs if r.get("rtf")]
            wer_tbl[strat][g] = {
                "n": len(rs),
                "wer_mean": round(mean(wers), 3),
                "wer_median": round(median(wers), 3),
                "lat_mean_s": round(mean(lat), 3) if lat else None,
                "lat_median_s": round(median(lat), 3) if lat else None,
                "lat_p90_s": round(sorted(lat)[int(0.9 * (len(lat) - 1))], 3) if lat else None,
                "lat_min_s": round(min(lat), 3) if lat else None,
                "lat_max_s": round(max(lat), 3) if lat else None,
                "rtf_mean": round(mean(rtf), 3) if rtf else None,
            }
    out["wer_latency"] = wer_tbl

    # ---- language-ID (monolingual only) ----
    lid: dict = {}
    for strat in ("auto", "dl_then_explicit"):
        conf = Counter()
        probs = defaultdict(list)
        errors = []
        for it in CORPUS:
            if it.group not in ("pl", "en"):
                continue
            r = rows_by_key.get((strat, it.uid))
            if not r:
                continue
            det = r.get("detected_language")
            p = r.get("detected_p")
            conf[(it.expected_language, det)] += 1
            if p is not None:
                probs[it.expected_language].append(p)
            if det != it.expected_language:
                errors.append({"uid": it.uid, "expected": it.expected_language,
                               "detected": det, "p": p, "audio_s": r.get("audio_s")})
        n = sum(conf.values())
        correct = sum(v for (exp, det), v in conf.items() if exp == det)
        lid[strat] = {
            "n": n,
            "accuracy": round(correct / n, 3) if n else None,
            "pl_acc": _acc(conf, "pl"),
            "en_acc": _acc(conf, "en"),
            "pl_to_en": conf[("pl", "en")],
            "en_to_pl": conf[("en", "pl")],
            "confusion": {f"{e}->{d}": v for (e, d), v in sorted(conf.items())},
            "prob_mean_pl": round(mean(probs["pl"]), 3) if probs["pl"] else None,
            "prob_mean_en": round(mean(probs["en"]), 3) if probs["en"] else None,
            "prob_min": round(min(sum(probs.values(), [])), 3) if any(probs.values()) else None,
            "errors": errors,
        }
    out["language_id"] = lid

    # ---- per-item S-class first pass (all strategies) ----
    sclass: dict = {}
    for strat in strategies:
        rows = []
        for it in CORPUS:
            r = rows_by_key.get((strat, it.uid))
            if not r:
                continue
            hyp = r.get("transcript") or ""
            rows.append({
                "uid": it.uid, "group": it.group, "expected": it.text,
                "hyp": hyp, "wer": round(best_wer(it, hyp), 3),
                "sclass_auto": auto_sclass(it, hyp),
                "detected": r.get("detected_language"),
                "p": r.get("detected_p"),
            })
        sclass[strat] = rows
    out["per_item"] = sclass

    # ---- short / ambiguous ----
    shorts = []
    for it in CORPUS:
        if it.group != "short":
            continue
        row = {"uid": it.uid, "text": it.text, "leans": it.leans}
        for strat in ("b0_pl", "b0_en", "auto", "dl_then_explicit"):
            r = rows_by_key.get((strat, it.uid))
            if r:
                row[strat] = {"t": r.get("transcript"), "det": r.get("detected_language"),
                              "p": r.get("detected_p")}
        shorts.append(row)
    out["short"] = shorts

    # ---- mixed ----
    mixed = []
    for it in CORPUS:
        if it.group != "mixed":
            continue
        row = {"uid": it.uid, "text": it.text, "primary": it.primary}
        for strat in ("b0_pl", "b0_en", "auto", "dl_then_explicit"):
            r = rows_by_key.get((strat, it.uid))
            if r:
                cls, why = mixed_survival(it, r.get("transcript") or "")
                row[strat] = {"t": r.get("transcript"), "det": r.get("detected_language"),
                              "class": cls, "why": why}
        mixed.append(row)
    out["mixed"] = mixed

    # ---- resources ----
    res_rows = list(rows_by_key.values())
    temps = [r["temp_c_max"] for r in res_rows
             if isinstance(r.get("temp_c_max"), (int, float)) and r["temp_c_max"] > 0]
    rss = [r["peak_rss_kb"] for r in res_rows if r.get("peak_rss_kb")]
    thr = Counter(r.get("throttled") for r in res_rows if r.get("throttled"))
    cpu = [r["cpu_pct_window"] for r in res_rows
           if isinstance(r.get("cpu_pct_window"), (int, float))]
    out["resources"] = {
        "temp_c_max": round(max(temps), 1) if temps else None,
        "peak_rss_kb_max": max(rss) if rss else None,
        "peak_rss_mb_max": round(max(rss) / 1024, 1) if rss else None,
        "cpu_pct_window_mean": round(mean(cpu), 1) if cpu else None,
        "throttled": dict(thr),
    }
    return out


def _acc(conf: Counter, lang: str) -> float | None:
    tot = sum(v for (e, _), v in conf.items() if e == lang)
    ok = conf[(lang, lang)]
    return round(ok / tot, 3) if tot else None


def main() -> int:
    args = sys.argv[1:]
    if args:
        raw_path = Path(args[0])
    else:
        cands = sorted(HERE.glob("bench_stt_raw_*.jsonl"))
        if not cands:
            print("no bench_stt_raw_*.jsonl found — run bench_stt.py first", file=sys.stderr)
            return 1
        raw_path = cands[-1]

    rows = load_raw(raw_path)
    by_item = {it.uid: it for it in CORPUS}
    rows_by_key = {(r["strategy"], r["uid"]): r for r in rows}
    result = summarise(rows_by_key, by_item)
    result["raw_file"] = raw_path.name
    result["n_rows"] = len(rows)

    out_name = raw_path.name.replace("bench_stt_raw_", "score_stt_").replace(".jsonl", ".json")
    out_path = HERE / out_name
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1))

    # ---- console summary ----
    print(f"scored {len(rows)} rows from {raw_path.name}\n")
    print("WER (best-of-ref) / median latency  by strategy x group")
    print(f"{'strategy':18} {'group':6} {'n':>3} {'wer_mean':>9} {'wer_med':>8} "
          f"{'lat_med_s':>10} {'lat_p90_s':>10}")
    for strat, gg in result["wer_latency"].items():
        for g, s in gg.items():
            print(f"{strat:18} {g:6} {s['n']:>3} {s['wer_mean']:>9} {s['wer_median']:>8} "
                  f"{str(s['lat_median_s']):>10} {str(s['lat_p90_s']):>10}")
    print("\nLanguage-ID (monolingual PL/EN, n=30):")
    for strat, s in result["language_id"].items():
        print(f"  {strat:18} acc={s['accuracy']}  PL={s['pl_acc']}  EN={s['en_acc']}  "
              f"PL->EN={s['pl_to_en']}  EN->PL={s['en_to_pl']}  "
              f"p(min)={s['prob_min']}  confusion={s['confusion']}")
        for e in s["errors"]:
            print(f"      MISS {e['uid']}  {e['expected']}->{e['detected']} "
                  f"(p={e['p']}, {e['audio_s']}s)")
    print(f"\nresources: {result['resources']}")
    print(f"\n-> {out_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
