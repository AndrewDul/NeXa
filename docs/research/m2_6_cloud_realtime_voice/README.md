# m2_6_cloud_realtime_voice — research spikes

Supporting evidence for **M2.6 — Cloud Realtime Voice**:

- `docs/reports/R0030_cloud_realtime_voice_research_architecture_20260910.md`
  (research / architecture + **PHASE 0 CORRECTIONS**)
- `docs/reports/R0031_m2_6a_gemini_live_hardware_spike_20260910.md`
  (M2.6A feasibility spike — **PASS / OPERATOR-CONFIRMED 2026-09-10**)

Research-only. No production `src/` change. No tracked-dependency-file
change.

| File | What it is |
|---|---|
| `inspect_pipecat_gemini_live.py` | OFFLINE static audit of the installed Pipecat 1.8.1 `GeminiLiveLLMService` — issue #5465 silent send-guards, `GoAway` handling, session-resumption / reconnect, Gemini-3.x async-tool support. No import, no network. |
| `inspect_pipecat_gemini_live_output_20260910.txt` | Captured output of the above (pipecat 1.8.1, 2026-09-10). |
| `m2_6a_connect_smoke.py` | Smallest authenticated Gemini Live connection smoke — credential accepted, model reachable, WebSocket + setup handshake, clean disconnect. **No microphone, no speaker, no conversation.** Exit 0 = OK. |
| `m2_6a_gemini_live_probe.py` | The M2.6A real-hardware probe. `--dry` = build the cloud-side object graph only (no device, no network); `--lifecycle-smoke` = ONE authenticated no-mic readiness check (near-zero tokens); `--recompute <json…>` = re-derive metrics (incl. the **corrected barge-in analysis**) from existing result timelines, **no cloud call**; `--voice <name>` (default **`Sulafat`**, "Warm"); no flag = full session (mic → XVF3800 AEC path → Gemini Live → speaker) → timestamped JSON here. Hard 15-min cap. |
| `m2_6a_probe_results_20260910T2121*.json` | **Operator attempt #2 evidence** (feasibility PASS): 2 real PL/EN sessions, 9 turns. |
| `m2_6a_probe_results_*_recomputed.json` | Metrics re-derived from the above with the corrected `bot_is_speaking` barge-in filter (the original `vad_start_to_local_playback_stopped_s` etc. were semantically invalid). |

### Corrected barge-in metric (2026-09-10)

The naive "pair each `LOCAL_VAD_START` with the next `LOCAL_PLAYBACK_STOPPED`"
also matched ordinary user turns where the bot was not speaking (→ 8–21 s
"latencies"). The probe now tracks `bot_is_speaking`
(`FIRST_AUDIO_PLAYED` → True; `LOCAL_PLAYBACK_STOPPED` / `BOT_AUDIO_STOPPED`
/ `TURN_COMPLETE` → False) and counts only VAD-starts while the bot plays.
Recomputed result: session 1 = 5 real barge-ins, median VAD-start →
playback-stop **≈ 2.3 ms**; local speaker silenced **~27 ms before** the
later (server-round-trip) interruption frame. `SERVER_INTERRUPTED`
renamed `INTERRUPTION_DOWNSTREAM` (source ambiguous).

## Credential (never in the repo)

The Gemini API key is **not** stored in git. For the spike it lives at:

```
~/.config/nexa/secrets/gemini.env      (dir 700, file 600)
```

as `NEXA_GEMINI_API_KEY=<value>`. Load it before a live run:

```
set -a; . ~/.config/nexa/secrets/gemini.env; set +a
```

Both `m2_6a_*.py` scripts also read that file directly if the env var is
unset. The value is never printed, logged, or written to the JSON.

## Run

```
# offline audit
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/inspect_pipecat_gemini_live.py

# authenticated no-audio smoke
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6a_connect_smoke.py

# probe config check (no device, no network)
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py --dry

# re-derive metrics from existing evidence (NO cloud call)
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py \
  --recompute docs/research/m2_6_cloud_realtime_voice/m2_6a_probe_results_*.json

# full operator session (default voice = Sulafat)
set -a; . ~/.config/nexa/secrets/gemini.env; set +a
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py
```

## Dependency note

`google-genai 2.22.0` was installed into the research `.venv` (Pipecat
1.8.1's own `[google]` constraint `>=1.68.0,<3`; only `google-genai` is
needed, not the full extra). It forced `websockets` 17.1 → 16.1.1 (within
Pipecat's `>=13.1`; `pip check` clean). **`pyproject.toml` / lock files
were not touched** — ratifying the dependency is an ADR-0004 item.
