# m2_6_cloud_realtime_voice — research spikes

Supporting evidence for **M2.6 — Cloud Realtime Voice**:

- `docs/reports/R0030_cloud_realtime_voice_research_architecture_20260910.md`
  (research / architecture + **PHASE 0 CORRECTIONS**)
- `docs/reports/R0031_m2_6a_gemini_live_hardware_spike_20260910.md`
  (M2.6A feasibility spike — READY FOR OPERATOR TEST)

Research-only. No production `src/` change. No tracked-dependency-file
change.

| File | What it is |
|---|---|
| `inspect_pipecat_gemini_live.py` | OFFLINE static audit of the installed Pipecat 1.8.1 `GeminiLiveLLMService` — issue #5465 silent send-guards, `GoAway` handling, session-resumption / reconnect, Gemini-3.x async-tool support. No import, no network. |
| `inspect_pipecat_gemini_live_output_20260910.txt` | Captured output of the above (pipecat 1.8.1, 2026-09-10). |
| `m2_6a_connect_smoke.py` | Smallest authenticated Gemini Live connection smoke — credential accepted, model reachable, WebSocket + setup handshake, clean disconnect. **No microphone, no speaker, no conversation.** Exit 0 = OK. |
| `m2_6a_gemini_live_probe.py` | The M2.6A real-hardware probe. `--dry` = build the cloud-side object graph only (no device, no network); `--lifecycle-smoke` = ONE authenticated **no-microphone** check that the `LLMRunFrame` kickoff makes `GeminiLiveLLMService` reach realtime-ready (near-zero tokens; proves the operator-attempt-#1 fix); no flag = full session (mic → XVF3800 AEC path → Gemini Live → speaker) with a one-clock event timeline, Pipecat-#5465 NOT_READY audio accounting, AEC-reference health, and a token/cost estimate → timestamped JSON here. Hard 15-minute cap. **Operator attempt #1 failed silent-after-speech because the probe never queued the one-time `LLMRunFrame`; fixed here — see R0031.** |
| `m2_6a_probe_results_*.json` | Written by a live probe run (git-ignored via the repo's `*.json`? — no; committed as evidence per the research-dir convention). Not present until a session runs. |

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

# full operator session
set -a; . ~/.config/nexa/secrets/gemini.env; set +a
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py
```

## Dependency note

`google-genai 2.22.0` was installed into the research `.venv` (Pipecat
1.8.1's own `[google]` constraint `>=1.68.0,<3`; only `google-genai` is
needed, not the full extra). It forced `websockets` 17.1 → 16.1.1 (within
Pipecat's `>=13.1`; `pip check` clean). **`pyproject.toml` / lock files
were not touched** — ratifying the dependency is an ADR-0004 item.
