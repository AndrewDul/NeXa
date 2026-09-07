#!/usr/bin/env python3
"""M2.4B.3.2A — offline, deterministic speech production/consumption budget.

NO runtime component. Pure arithmetic over ALREADY-MEASURED data:
per-phrase (text_chars, http_wall_s, audio_s) and per-phrase ready-at
timestamps taken verbatim from R0013/R0017 and the accepted B.2/B.2A
operator run (`/tmp/nexa_m24b2_operator.jsonl`) + the B.3.1 scripted raw
comparison (`b31_compare_raw_20260907.txt`).

It answers: is continuous speech mathematically sustainable at the current
`gemma4:e4b` text-generation rate and `pl_PL-gosia-medium` speaking rate,
and what can a prebuffer / batching / a slightly slower voice actually do.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Phrase:
    chars: int          # spoken (post-speech-planner) characters
    ready_at: float     # s, turn-relative: when the phrase TEXT was available to synthesize
    http_wall: float    # s, measured Piper synth wall time for this phrase
    audio_s: float      # s, measured produced audio duration for this phrase


@dataclass
class Turn:
    name: str
    source: str
    llm_gen_chars: int      # total chars gemma4:e4b GENERATED (pre-planner)
    llm_gen_dur_s: float    # generation duration (first token -> complete)
    phrases: list[Phrase]

    # ---- rates -------------------------------------------------------------
    @property
    def spoken_chars(self) -> int:
        return sum(p.chars for p in self.phrases)

    @property
    def total_audio_s(self) -> float:
        return sum(p.audio_s for p in self.phrases)

    @property
    def llm_chars_per_s(self) -> float:
        return self.llm_gen_chars / self.llm_gen_dur_s

    @property
    def speech_chars_per_s(self) -> float:
        return self.spoken_chars / self.total_audio_s

    @property
    def realtime_text_ratio(self) -> float:
        # production of SPOKEN-equivalent text vs consumption.
        spoken_production = self.llm_chars_per_s * (self.spoken_chars / self.llm_gen_chars)
        return spoken_production / self.speech_chars_per_s

    # ---- serialized synthesis: when each phrase's AUDIO is fully ready ----
    def audio_available_at(self) -> list[float]:
        out: list[float] = []
        synth_end = 0.0
        for p in self.phrases:
            synth_start = max(p.ready_at, synth_end)
            synth_end = synth_start + p.http_wall
            out.append(synth_end)
        return out

    # ---- deterministic buffer-drain simulation ---------------------------
    def simulate(self, prebuffer_s: float = 0.0):
        """Playback starts `prebuffer_s` after the first phrase's audio is
        ready. Audio plays at 1x. Returns dict of gap stats."""
        avail = self.audio_available_at()
        aud = [p.audio_s for p in self.phrases]
        total = sum(aud)
        start = avail[0] + prebuffer_s
        wall = start
        played = 0.0
        gaps: list[float] = []
        first_underrun_after_start: float | None = None
        EPS = 1e-6

        def produced(t: float) -> float:
            return sum(a for a, av in zip(aud, avail, strict=True) if av <= t + EPS)

        while played < total - EPS:
            buf = produced(wall) - played
            future = [av for av in avail if av > wall + EPS]
            next_arr = min(future) if future else None
            if buf > EPS:
                step = buf if next_arr is None else min(buf, next_arr - wall)
                played += step
                wall += step
            else:
                if next_arr is None:
                    break
                gap = next_arr - wall
                gaps.append(gap)
                if first_underrun_after_start is None:
                    first_underrun_after_start = wall - start
                wall = next_arr
        return {
            "prebuffer_s": prebuffer_s,
            "first_audio_delay_added_s": prebuffer_s,
            "first_underrun_after_start_s": first_underrun_after_start,
            "underruns": len(gaps),
            "gaps_s": [round(g, 2) for g in gaps],
            "total_silence_s": round(sum(gaps), 2),
            "max_silence_s": round(max(gaps), 2) if gaps else 0.0,
            "wall_to_finish_s": round(wall - avail[0], 2),
        }

    def min_prebuffer_for_zero_underrun(self) -> float:
        """max_i ( audio_available_at[i] - sum(audio_s[0..i-1]) ), clamped >=0,
        minus the first phrase's own availability. i.e. how long you must
        delay playback so accumulated audio covers every future arrival gap."""
        avail = self.audio_available_at()
        aud = [p.audio_s for p in self.phrases]
        need = 0.0
        for i in range(len(self.phrases)):
            need = max(need, avail[i] - sum(aud[:i]))
        return max(0.0, need - avail[0])


# --------------------------------------------------------------------------- #
# MEASURED DATA (verbatim)
# --------------------------------------------------------------------------- #

# Accepted B.2/B.2A operator run, /tmp/nexa_m24b2_operator.jsonl.
# ready_at shifted so phrase 0 == 0.0 (raw base 69523.968659610).
OP_STAR = Turn(
    name="operator star question (turn 5)",
    source="/tmp/nexa_m24b2_operator.jsonl",
    llm_gen_chars=668, llm_gen_dur_s=78.28,
    phrases=[
        Phrase(82, 0.000, 1.43, 4.99),
        Phrase(33, 4.177, 0.80, 2.27),
        Phrase(98, 46.286, 1.84, 6.96),
        Phrase(132, 48.717, 2.43, 8.55),
        Phrase(49, 49.753, 1.03, 3.25),
        Phrase(59, 50.987, 1.24, 3.63),
        Phrase(128, 65.164, 2.35, 9.23),
        Phrase(61, 69.501, 0.56, 3.93),
    ],
)

OP_T2 = Turn(
    name="operator turn 2 (list reply)",
    source="/tmp/nexa_m24b2_operator.jsonl",
    llm_gen_chars=470, llm_gen_dur_s=55.23,
    phrases=[
        # ready_at base 69285.975665545
        Phrase(87, 0.000, 1.52, 5.52),
        Phrase(67, 8.115, 1.12, 4.08),
        Phrase(57, 13.752, 1.08, 3.40),
        Phrase(166, 36.785, 2.53, 9.68),
        Phrase(78, 45.529, 0.66, 4.69),
    ],
)

# B.3.1 scripted comparison, b31_compare_raw_20260907.txt. ready_at from the
# "Generating TTS [..]" log timestamps; audio_s / http_wall from the report.
B31_NICE10 = Turn(
    name="B.3.1 nice+10 measured turn",
    source="b31_compare_raw_20260907.txt",
    llm_gen_chars=182, llm_gen_dur_s=21.29,
    phrases=[
        Phrase(54, 0.000, 1.40, 3.86),   # 15:46:59.518
        Phrase(127, 15.109, 1.25, 8.47),  # 15:47:14.627
    ],
)

B31_CONTROL = Turn(
    name="B.3.1 CONTROL measured turn (nice 0 / 3 s)",
    source="b31_compare_raw_20260907.txt",
    llm_gen_chars=280, llm_gen_dur_s=34.17,
    phrases=[
        Phrase(52, 0.000, 1.24, 3.56),    # 15:49:15.694
        Phrase(110, 13.776, 1.89, 6.51),   # 15:49:29.470
        Phrase(116, 28.130, 1.08, 7.79),   # 15:49:43.824
    ],
)

TURNS = [OP_STAR, OP_T2, B31_CONTROL, B31_NICE10]
PREBUFFERS = [0.0, 1.0, 2.0, 3.0, 5.0]

# gosia weighted speech rate for the length_scale / answer-length analysis
_ALL_SPOKEN = sum(t.spoken_chars for t in TURNS)
_ALL_AUDIO = sum(t.total_audio_s for t in TURNS)
GOSIA_CHARS_PER_S = _ALL_SPOKEN / _ALL_AUDIO
_ALL_GEN = sum(t.llm_gen_chars for t in TURNS)
_ALL_GEN_DUR = sum(t.llm_gen_dur_s for t in TURNS)
LLM_CHARS_PER_S = _ALL_GEN / _ALL_GEN_DUR


def _self_check() -> None:
    """The sim is only trustworthy if it reproduces the MEASURED gaps.
    B.3.1 CONTROL reported silence_gaps_ms = [10888, 7032]; nice+10 had a
    real audible ~11 s gap (the metric's 8.1 s under-counts because
    BotStopped lags the true audio end by ~3 s). Operator turn 5 had one
    real ~39 s gap (gaps_ms = [0, 39073.6, 0])."""
    c = B31_CONTROL.simulate(0.0)
    assert c["underruns"] == 2 and abs(c["gaps_s"][0] - 10.87) < 0.2 \
        and abs(c["gaps_s"][1] - 7.03) < 0.2, c
    n = B31_NICE10.simulate(0.0)
    assert n["underruns"] == 1 and abs(n["gaps_s"][0] - 11.1) < 0.3, n
    s = OP_STAR.simulate(0.0)
    assert s["underruns"] == 1 and abs(s["gaps_s"][0] - 39.4) < 1.0, s


def main() -> None:
    _self_check()
    print("=" * 78)
    print("M2.4B.3.2A — SPEECH PRODUCTION / CONSUMPTION RATE BUDGET  (offline)")
    print("=" * 78)
    print("(self-check: simulated gaps match the measured B.3.1 / operator gaps)")

    print("\n### PER-TURN RATES")
    hdr = f"{'turn':<44}{'LLM ch/s':>9}{'speech ch/s':>12}{'ratio':>8}"
    print(hdr)
    print("-" * len(hdr))
    for t in TURNS:
        print(f"{t.name:<44}{t.llm_chars_per_s:>9.2f}"
              f"{t.speech_chars_per_s:>12.2f}{t.realtime_text_ratio:>8.3f}")
    print(f"\n  weighted gosia speech rate : {GOSIA_CHARS_PER_S:6.2f} spoken chars/s")
    print(f"  weighted gemma4:e4b rate   : {LLM_CHARS_PER_S:6.2f} generated chars/s")
    print(f"  weighted realtime_text_ratio (spoken basis) : "
          f"{(LLM_CHARS_PER_S * (_ALL_SPOKEN/_ALL_GEN)) / GOSIA_CHARS_PER_S:.3f}")

    print("\n### PER-PHRASE TABLE")
    for t in TURNS:
        print(f"\n  {t.name}   ({t.source})")
        print(f"    {'#':>2} {'chars':>6} {'ready@s':>9} {'http_wall':>10} "
              f"{'audio_s':>8} {'ch/s':>7} {'audio_ready@s':>13}")
        avail = t.audio_available_at()
        for i, p in enumerate(t.phrases):
            print(f"    {i:>2} {p.chars:>6} {p.ready_at:>9.2f} {p.http_wall:>10.2f} "
                  f"{p.audio_s:>8.2f} {p.chars / p.audio_s:>7.2f} {avail[i]:>13.2f}")
        print(f"    turn: spoken {t.spoken_chars} ch / {t.total_audio_s:.2f} audio-s "
              f"= {t.speech_chars_per_s:.2f} ch/s;  min prebuffer for 0 underrun = "
              f"{t.min_prebuffer_for_zero_underrun():.1f} s")

    print("\n### BUFFER-DRAIN SIMULATION  (prebuffer sweep A=0 B=1 C=2 D=3 E=5 s)")
    for t in TURNS:
        print(f"\n  {t.name}")
        print(f"    {'pb_s':>5} {'+1st_audio':>11} {'1st_underrun@':>14} "
              f"{'underruns':>10} {'tot_silence':>12} {'max_silence':>12} {'finish_wall':>12}")
        for pb in PREBUFFERS:
            r = t.simulate(pb)
            fu = r["first_underrun_after_start_s"]
            fu_str = f"{fu:.2f}" if fu is not None else "none"
            print(f"    {pb:>5.1f} {r['first_audio_delay_added_s']:>11.1f} "
                  f"{fu_str:>14} "
                  f"{r['underruns']:>10} {r['total_silence_s']:>12.2f} "
                  f"{r['max_silence_s']:>12.2f} {r['wall_to_finish_s']:>12.2f}")
        print(f"    -> min prebuffer for ZERO underruns this turn: "
              f"{t.min_prebuffer_for_zero_underrun():.1f} s "
              f"(first audio then at +{t.min_prebuffer_for_zero_underrun():.1f} s)")

    print("\n### STEADY-STATE DRAIN MODEL")
    ratio = (LLM_CHARS_PER_S * (_ALL_SPOKEN / _ALL_GEN)) / GOSIA_CHARS_PER_S
    drain = 1.0 - ratio
    print(f"  realtime_text_ratio r = {ratio:.3f}  =>  while speaking, the audio")
    print(f"  buffer drains at (1 - r) = {drain:.3f} audio-seconds per second of speech.")
    print("  continuous speech time before starve, given B0 audio-s of initial buffer")
    print(f"  (beyond the first phrase):  t_starve = B0 / {drain:.3f}")
    for b0 in (0, 1, 2, 3, 5):
        print(f"     B0 = {b0} s  ->  ~{b0 / drain:5.1f} s of extra continuous speech")
    print(f"  to sustain an N-second reply with 0 underruns: B0 >= N * {drain:.3f}")
    for n in (4, 8, 12, 20, 40):
        print(f"     N = {n:>2} s reply  ->  need B0 >= {n * drain:5.1f} s initial buffer")

    print("\n### MINIMUM REQUIRED LLM RATE  (to reach ratio targets)")
    spoken_frac = _ALL_SPOKEN / _ALL_GEN
    print(f"  spoken/generated char fraction = {spoken_frac:.3f} (planner strips ~"
          f"{100*(1-spoken_frac):.0f}%)")
    for label, target_ratio in [
        ("asymptotic / unbounded speech, 0 buffer", 1.00),
        ("15 s reply, 2 s initial buffer", 1 - 2 / 15),
        ("15 s reply, 3 s initial buffer", 1 - 3 / 15),
    ]:
        need_spoken = target_ratio * GOSIA_CHARS_PER_S
        need_gen = need_spoken / spoken_frac
        mult = need_gen / LLM_CHARS_PER_S
        print(f"  {label:<40}: LLM >= {need_gen:5.1f} gen ch/s  "
              f"(~{mult:.2f}x current {LLM_CHARS_PER_S:.1f})")

    print("\n### LENGTH_SCALE SENSITIVITY  (analysis only; Piper length_scale NOT changed)")
    print(f"  gosia baseline (length_scale 1.00): {GOSIA_CHARS_PER_S:.2f} spoken ch/s consumed")
    base_deficit = 1.0 - ratio
    for ls in (1.00, 1.05, 1.10):
        consumed = GOSIA_CHARS_PER_S / ls
        new_ratio = (LLM_CHARS_PER_S * spoken_frac) / consumed
        new_deficit = 1.0 - new_ratio
        closed_pct = 100 * (base_deficit - new_deficit) / base_deficit
        print(f"  length_scale {ls:.2f}: consume {consumed:5.2f} ch/s  ->  ratio "
              f"{new_ratio:.3f}  deficit {new_deficit:.3f}  (closes "
              f"{closed_pct:4.1f}% of the {base_deficit:.3f} deficit)")
    ls_needed = GOSIA_CHARS_PER_S / (LLM_CHARS_PER_S * spoken_frac)
    print(f"  length_scale needed to reach ratio 1.0 by itself: {ls_needed:.2f} "
          f"(operator ceiling is ~1.10 -> cannot close the deficit alone)")


if __name__ == "__main__":
    main()
