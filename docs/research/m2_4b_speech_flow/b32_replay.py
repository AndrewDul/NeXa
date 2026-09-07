#!/usr/bin/env python3
"""M2.4B.3.2 — offline replay of the R0018 phrase-ready timelines through the
REAL ``NexaSpeechContinuityController`` policy (``decide_release`` + the
bounded hold).

No runtime component, no Pipecat pipeline. It reuses the measured per-phrase
(text_ready_at, http_wall, audio_s) data from ``rate_budget_sim.py`` and the
exact ``decide_release`` policy from ``nexa.voice_tts.continuity``. It answers
the B.3.2 replay questions:

  * does the controller make first audio later?          (must be: no)
  * does it make the first underrun WORSE?               (must be: no)
  * for a short reply where sentence 2 arrives fast, does a candidate target
    reduce a small gap / avoid churn without adding latency?
  * can it eliminate the 39 s operator-star LLM stall?   (honest answer: no)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3] / "src"))

from rate_budget_sim import B31_CONTROL, B31_NICE10, OP_STAR, Phrase, Turn  # noqa: E402

from nexa.voice_tts.continuity import (  # noqa: E402
    DEFAULT_CONTINUITY_MAX_HOLD_S,
    DEFAULT_CONTINUITY_NEAR_EMPTY_S,
    PLAN_HOLD,
    decide_release,
)

TARGETS = (1.5, 2.0, 2.5)


@dataclass
class ReplayResult:
    target_s: float | None            # None = baseline (no controller)
    release_at: list[float]           # when each phrase was released to TTS
    audio_ready_at: list[float]       # serialized-synthesis completion per phrase
    first_audio_s: float
    first_underrun_after_start_s: float | None
    underruns: int
    gaps_s: list[float]
    total_silence_s: float
    max_silence_s: float
    holds_s: list[float]              # per phrase (0 if not held)


def _serialized_synth(release_at: list[float], phrases: list[Phrase]) -> list[float]:
    out: list[float] = []
    synth_end = 0.0
    for r, p in zip(release_at, phrases, strict=True):
        synth_end = max(r, synth_end) + p.http_wall
        out.append(synth_end)
    return out


def _drain(avail: list[float], aud: list[float]) -> tuple[float | None, int, list[float]]:
    """Playback starts at avail[0] (phrase 0 immediate, no prebuffer)."""
    EPS = 1e-6
    start = avail[0]
    wall = start
    played = 0.0
    total = sum(aud)
    gaps: list[float] = []
    first_underrun: float | None = None

    def produced(t: float) -> float:
        return sum(a for a, av in zip(aud, avail, strict=True) if av <= t + EPS)

    while played < total - EPS:
        buf = produced(wall) - played
        future = [av for av in avail if av > wall + EPS]
        nxt = min(future) if future else None
        if buf > EPS:
            step = buf if nxt is None else min(buf, nxt - wall)
            played += step
            wall += step
        else:
            if nxt is None:
                break
            gaps.append(nxt - wall)
            if first_underrun is None:
                first_underrun = wall - start
            wall = nxt
    return first_underrun, len(gaps), gaps


def replay(turn: Turn, target_s: float | None) -> ReplayResult:
    """Forward-simulate the controller against the measured text-ready
    timeline. ``target_s=None`` = baseline (release every phrase at ready)."""
    ph = turn.phrases
    n = len(ph)
    release_at = [0.0] * n
    released = [False] * n
    holds_s = [0.0] * n
    synth_end_so_far = 0.0
    audio_ready: list[float] = [0.0] * n

    def _commit_release(i: int, t: float) -> None:
        nonlocal synth_end_so_far
        release_at[i] = t
        released[i] = True
        synth_end_so_far = max(t, synth_end_so_far) + ph[i].http_wall
        audio_ready[i] = synth_end_so_far

    def _reserve_estimate(t: float) -> float | None:
        # ESTIMATE, exactly as the runtime controller: produced audio seconds
        # (synthesis complete) minus wall since first audio.
        done = [k for k in range(n) if released[k] and audio_ready[k] <= t + 1e-9]
        if not done:
            return None
        first_audio = min(audio_ready[k] for k in range(n) if released[k])
        if first_audio > t:
            return None
        produced = sum(ph[k].audio_s for k in done)
        return produced - (t - first_audio)

    # event-driven forward sim: PHRASE_READY(i) at ready_at[i], plus a
    # HOLD_DEADLINE(i) the controller's bounded-hold timer would schedule.
    events: list[tuple[float, int, int]] = [(p.ready_at, 0, i) for i, p in enumerate(ph)]
    held: int | None = None
    ei = 0

    def _release_held(t: float) -> None:
        nonlocal held
        if held is None:
            return
        holds_s[held] = max(0.0, t - ph[held].ready_at)
        _commit_release(held, t)
        held = None

    while ei < len(events):
        events.sort()
        t, kind, i = events[ei]
        ei += 1
        if kind == 1:  # HOLD_DEADLINE
            if held == i:
                _release_held(t)
            continue
        # PHRASE_READY(i): a newly ready phrase flushes any held one first
        _release_held(t)
        if target_s is None:
            _commit_release(i, t)
            continue
        reason, hold = decide_release(
            _reserve_estimate(t), is_first=(i == 0), target_s=target_s,
            near_empty_s=DEFAULT_CONTINUITY_NEAR_EMPTY_S,
            max_hold_s=DEFAULT_CONTINUITY_MAX_HOLD_S,
        )
        if reason != PLAN_HOLD:
            _commit_release(i, t)
        else:
            held = i
            events.append((t + hold, 1, i))  # bounded-hold timer

    _release_held(max(p.ready_at for p in ph) + 1e-6)  # end-of-turn flush

    avail = _serialized_synth(release_at, ph)
    aud = [p.audio_s for p in ph]
    fu, u, gaps = _drain(avail, aud)
    return ReplayResult(
        target_s=target_s, release_at=[round(x, 2) for x in release_at],
        audio_ready_at=[round(x, 2) for x in avail],
        first_audio_s=round(avail[0], 2),
        first_underrun_after_start_s=(round(fu, 2) if fu is not None else None),
        underruns=u, gaps_s=[round(g, 2) for g in gaps],
        total_silence_s=round(sum(gaps), 2), max_silence_s=round(max(gaps), 2) if gaps else 0.0,
        holds_s=[round(h, 2) for h in holds_s],
    )


# A synthetic short reply where gemma4:e4b KEEPS UP: sentence 2 ready 2 s
# after sentence 1 (the case where a hold can actually engage). Rates are the
# measured gosia/gemma numbers from R0018.
SHORT_KEEPS_UP = Turn(
    name="synthetic 2-sentence reply, LLM keeps up",
    source="synthetic (rates from R0018)",
    llm_gen_chars=150, llm_gen_dur_s=9.0,
    phrases=[
        Phrase(chars=70, ready_at=0.0, http_wall=1.30, audio_s=4.6),
        Phrase(chars=78, ready_at=2.0, http_wall=1.35, audio_s=5.1),
    ],
)

REPLAY_TURNS = [B31_NICE10, B31_CONTROL, OP_STAR, SHORT_KEEPS_UP]


def main() -> None:
    print("=" * 82)
    print("M2.4B.3.2 — CONTROLLER OFFLINE REPLAY  (real decide_release policy)")
    print(f"  near_empty={DEFAULT_CONTINUITY_NEAR_EMPTY_S}s  "
          f"max_hold={DEFAULT_CONTINUITY_MAX_HOLD_S}s  targets={TARGETS}")
    print("=" * 82)
    for turn in REPLAY_TURNS:
        print(f"\n### {turn.name}   ({turn.source})")
        base = replay(turn, None)
        print(f"  baseline (no controller): first_audio={base.first_audio_s}s  "
              f"1st_underrun@={base.first_underrun_after_start_s}  underruns={base.underruns}  "
              f"gaps={base.gaps_s}  total_silence={base.total_silence_s}s")
        for tgt in TARGETS:
            r = replay(turn, tgt)
            worse_first = r.first_audio_s > base.first_audio_s + 1e-6
            fu_b = base.first_underrun_after_start_s
            fu_r = r.first_underrun_after_start_s
            worse_underrun = (fu_b is not None and fu_r is not None and fu_r < fu_b - 1e-6)
            print(f"  target {tgt}s: first_audio={r.first_audio_s}s"
                  f"{'  !! LATER' if worse_first else ''}  "
                  f"1st_underrun@={fu_r}{'  !! WORSE' if worse_underrun else ''}  "
                  f"underruns={r.underruns}  gaps={r.gaps_s}  "
                  f"total_silence={r.total_silence_s}s  holds={[h for h in r.holds_s if h]}")
        # honesty check on the operator-star stall
        if turn is OP_STAR:
            r2 = replay(turn, 2.0)
            print(f"  -> operator-star 42 s LLM stall before phrase 2 is UNCHANGED by the "
                  f"controller: max_silence {base.max_silence_s}s -> {r2.max_silence_s}s "
                  f"(the controller cannot create text the LLM has not generated — R0018).")


if __name__ == "__main__":
    main()
