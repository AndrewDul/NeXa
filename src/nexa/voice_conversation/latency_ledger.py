"""M2.5B.1 — per-turn latency ledger.

One record per assistant response (normal or a barge-in replacement),
assembled from things the pipeline already exposes:

- STT: ``LanguageDecision.detect_latency_s`` / ``decode_latency_s`` /
  ``total_latency_s`` (the field the live 7.28 s spike came from).
- model: the Ollama server's own counters
  (``LocalModelProvider.last_metrics``: ``prompt_eval_count`` /
  ``prompt_eval_duration`` / ``eval_count`` / ``eval_duration`` /
  ``load_duration`` — ns) + the first-token wall time (TTFT).
- output: first synthesized sentence, first audio played.
- barge-in: ``InterruptedTurn`` + ``CancelCompletion``
  (``cancel_to_worker_stop_ms``).
- queues: depth **and** in-flight for STT + conversation; AEC queued /
  dropped.
- growth: history turn count, approx prompt tokens (== prompt_eval_count).

Measurement only — no control flow depends on it. Pure (no I/O beyond an
optional JSONL append). ``time.monotonic`` timestamps; wall clock only for
the human-readable ``at``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


def _ms(ns: int | None) -> float | None:
    return round(ns / 1e6, 1) if isinstance(ns, int) else None


@dataclass
class TurnLedgerRecord:
    response_id: int | None = None
    is_interruption: bool = False
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds"))
    # marks (monotonic seconds; None until seen)
    _t: dict[str, float] = field(default_factory=dict, repr=False)
    # scalar fields set directly
    fields: dict[str, object] = field(default_factory=dict)

    def mark(self, name: str, t: float | None = None) -> None:
        self._t.setdefault(name, t if t is not None else time.monotonic())

    def set(self, **kw: object) -> None:
        self.fields.update({k: v for k, v in kw.items() if v is not None})

    def _delta_ms(self, a: str, b: str) -> float | None:
        if a in self._t and b in self._t:
            return round((self._t[b] - self._t[a]) * 1000, 1)
        return None

    def to_dict(self) -> dict:
        f = dict(self.fields)
        m = f.get("model_metrics") or {}
        out = {
            "at": self.at,
            "response_id": self.response_id,
            "is_interruption": self.is_interruption,
            # STT
            "stt_detect_s": f.get("stt_detect_s"),
            "stt_decode_s": f.get("stt_decode_s"),
            "stt_total_s": f.get("stt_total_s"),
            "stt_queue_depth": f.get("stt_queue_depth"),
            "stt_in_flight": f.get("stt_in_flight"),
            # conversation queue
            "conv_queue_depth": f.get("conv_queue_depth"),
            "conv_in_flight": f.get("conv_in_flight"),
            # model
            "model_ttft_ms": self._delta_ms("model_request", "model_first_token"),
            "prompt_eval_count": m.get("prompt_eval_count"),
            "prompt_eval_ms": _ms(m.get("prompt_eval_duration")),
            "eval_count": m.get("eval_count"),
            "eval_ms": _ms(m.get("eval_duration")),
            "load_ms": _ms(m.get("load_duration")),
            "decode_tok_per_s": (
                round(m["eval_count"] / (m["eval_duration"] / 1e9), 1)
                if isinstance(m.get("eval_count"), int)
                and isinstance(m.get("eval_duration"), int) and m["eval_duration"] > 0
                else None
            ),
            # output
            "planner_first_sentence_ms": self._delta_ms(
                "user_speech_end", "planner_first_sentence"
            ),
            "first_audio_ms": self._delta_ms("user_speech_end", "first_audio_played"),
            "eot_to_first_audio_ms": self._delta_ms("user_speech_end", "first_audio_played"),
            # barge-in
            "cancel_observed": f.get("cancel_observed"),
            "worker_stopped": f.get("worker_stopped"),
            "cancel_to_worker_stop_ms": f.get("cancel_to_worker_stop_ms"),
            "interrupt_segments": f.get("interrupt_segments"),
            # growth
            "history_turns": f.get("history_turns"),
            # AEC
            "aec_ref_active": f.get("aec_ref_active"),
            "aec_queued": f.get("aec_queued"),
            "aec_dropped": f.get("aec_dropped"),
        }
        return {k: v for k, v in out.items() if v is not None}


class LatencyLedger:
    def __init__(self, jsonl_path: str | Path | None = None,
                 on_record: Callable[[dict], None] | None = None) -> None:
        self._path = Path(jsonl_path) if jsonl_path else None
        self._on_record = on_record
        self._cur: TurnLedgerRecord | None = None
        self.records: list[dict] = []

    def begin_turn(self, response_id: int | None, *, is_interruption: bool = False) -> None:
        # finish any dangling record first (defensive)
        if self._cur is not None:
            self.finish_turn()
        self._cur = TurnLedgerRecord(
            response_id=response_id, is_interruption=is_interruption
        )

    def mark(self, name: str, t: float | None = None) -> None:
        if self._cur is not None:
            self._cur.mark(name, t)

    def set(self, **kw: object) -> None:
        if self._cur is not None:
            self._cur.set(**kw)

    def finish_turn(self) -> dict | None:
        if self._cur is None:
            return None
        rec = self._cur.to_dict()
        self._cur = None
        self.records.append(rec)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if self._on_record is not None:
            self._on_record(rec)
        return rec

    def summary(self) -> dict:
        """Aggregate — early turns vs late turns, for regression checks."""
        r = self.records
        if not r:
            return {}
        n = len(r)
        half = max(1, n // 3)

        def _stat(key: str, rows: list[dict]) -> dict | None:
            xs = [x[key] for x in rows if isinstance(x.get(key), (int, float))]
            if not xs:
                return None
            xs.sort()
            return {"n": len(xs), "mean": round(sum(xs) / len(xs), 1),
                    "p50": xs[len(xs) // 2], "max": xs[-1]}

        keys = ["stt_total_s", "model_ttft_ms", "eot_to_first_audio_ms",
                "prompt_eval_count", "prompt_eval_ms", "cancel_to_worker_stop_ms"]
        return {
            "turns": n,
            "early": {k: _stat(k, r[:half]) for k in keys},
            "late": {k: _stat(k, r[-half:]) for k in keys},
            "all": {k: _stat(k, r) for k in keys},
        }
