# m2_6_cloud_realtime_voice — research spikes

Supporting evidence for **R0030 — Cloud Realtime Voice: Research / Architecture**
(`docs/reports/R0030_cloud_realtime_voice_research_architecture_20260910.md`).

Research-only. No production `src/` change. No API key, no network.

| File | What it is |
|---|---|
| `inspect_pipecat_gemini_live.py` | OFFLINE static audit of the *installed* Pipecat 1.8.1 `GeminiLiveLLMService` source. Checks for the silent send-guards behind GitHub issue #5465, GoAway handling, session-resumption / reconnect, and Gemini-3.x async-tool support. Runs a regex pass over the source file (it cannot `import` the module — `google-genai` is deliberately not installed). Exit 0 always; it is evidence, not a gate. |
| `inspect_pipecat_gemini_live_output_20260910.txt` | Captured output of the above on 2026-09-10, pipecat 1.8.1. |

Run:

```
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/inspect_pipecat_gemini_live.py
```
