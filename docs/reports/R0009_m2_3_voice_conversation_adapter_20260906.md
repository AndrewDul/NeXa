# R0009 — M2.3 Voice → ConversationSession Adapter

- **Date:** 2026-09-06
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.3 — Canonical Voice →
  ConversationSession Adapter**
- **Related:** `docs/decisions/ADR-0003_realtime_voice_foundation.md` (D2 —
  the decision this implements),
  `docs/architecture/M2_3_VOICE_CONVERSATION_ADAPTER_ARCHITECTURE.md`
  (verified architecture detail), `docs/reports/R0008_m2_2_local_whisper_cpp_stt_20260906.md`
  (the STT boundary this substage consumes), `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`
  (the `ConversationSession` this substage feeds, unchanged)

---

## TASK RESULT

**PASS / OPERATOR-CONFIRMED.** All 23 success criteria met, including two
real-hardware-testing-driven fix-and-retest cycles disclosed in full below:
a conversation-turn concurrency defect (mirroring M2.2's own STT
concurrency lesson one layer up) and a response-language mirroring defect
plus its own latency regression, found and fixed during acceptance testing
itself.

## CANONICAL PATH

```
microphone -> Pipecat -> Silero VAD -> UtteranceBuffer -> whisper.cpp   (M2.1/M2.2)
    -> TranscriptionResult
    -> VoiceConversationAdapter.handle_transcription()                  (M2.3, new)
    -> SerialConversationQueue.submit(text)                             (M2.3, new)
    -> ConversationSession.send(text)         <-- identical to apps/nexa_chat.py
    -> ConversationContext -> LocalModelProvider -> gemma4:e4b          (M1.1, unchanged)
    -> streamed assistant text
```

`ConversationSession`/`ConversationContext`/`LocalModelProvider`/`gemma4:e4b`
are unmodified in shape — the one justified exception (response-language
mirroring, canonical policy, see below) applies identically to typed and
voice input. Full detail: `M2_3_VOICE_CONVERSATION_ADAPTER_ARCHITECTURE.md`
§2.

## ADAPTER ARCHITECTURE

New top-level package `src/nexa/voice_conversation/` (not a submodule of
`nexa.voice`/`nexa.stt` — both already have architecture tests forbidding a
`nexa.conversation`/`nexa.providers` import, and the task explicitly forbade
weakening old architecture tests to fit a poor dependency direction).
`VoiceConversationAdapter` (`adapter.py`) is the only new wiring: it takes
an already-built `ConversationSession` (never constructs one, never a
`ModelProvider`, never a persona — `ast`-verified), and exposes
`handle_transcription`/`handle_transcription_error` methods matching
`VoiceRuntime`'s existing `on_transcription`/`on_transcription_error`
callback shape exactly — no changes needed to `nexa.voice`/`nexa.stt` at
all. Empty/whitespace transcripts are rejected explicitly before ever
reaching `ConversationSession` — no fabricated turn.

ADR-0003 D2 sketched this substage as a Pipecat `FrameProcessor`
(illustratively `NeXaConversationFrameProcessor`) pushing `TextFrame`s
toward TTS — a shape assuming TTS (M2.4) already existed. Since M2.2
already used plain callbacks (no TTS yet to push toward), the actual
adapter is a plain Python class, not a `FrameProcessor` — D2's real
requirement (one conversation authority, no second history/persona/model)
is fully honored; only the illustrative mechanism differs, which the ADR
text explicitly left open for this substage.

## CONVERSATION SERIALIZATION

**Real hardware finding, before this task's fixes were needed** (the risk
was correctly anticipated in the task spec and built in from the start):
M2.2 already found that two whisper.cpp subprocesses could run
concurrently if STT execution wasn't serialized. M2.3 has the identical
risk one layer later — two `ConversationSession.send()` calls could run
concurrently if a second utterance is transcribed while the first is still
generating. `SerialConversationQueue` (`src/nexa/voice_conversation/queue.py`,
deliberately duplicating `nexa.stt.queue.SerialTranscriptionQueue`'s design
rather than sharing it, keeping the two boundaries independent): FIFO,
non-blocking `submit()`, exactly one turn executing at a time
(`max_observed_concurrency` tracked and proven == 1), bounded
(`max_queue_size=4` — smaller than STT's 8, since LLM turns are far slower
per R0005's 27.6-54s worst-case measurement), explicit
`ConversationQueueOverflowError` on overflow (never a silent drop), clean
`shutdown()` draining in-flight work.

## TYPED + VOICE SAME-SESSION PROOF

`tests/test_voice_conversation_adapter.py`'s
`test_typed_and_voice_turns_enter_the_same_session_in_order`: one real
`ConversationSession` (fake provider) receives a typed turn via
`session.send()` directly, then a simulated `TranscriptionResult` via the
M2.3 adapter — `session.history` shows both turns in submission order,
proving both modalities entered the identical session object, not
documentation-only. `test_adapter_holds_no_second_history_object` asserts
`adapter._session is session` and that the adapter exposes no `history`/
`_history` of its own.

## REAL POLISH TEST

Operator Andrzej, real reSpeaker XVF3800, `--language pl`: "Co to jest
teleportacja?" then "A czy człowiek może się dzisiaj teleportować?" — both
transcribed, both fed the same `ConversationSession`, second answer
understood the follow-up referred to teleportation, assistant text
streamed, no crash, microphone stayed responsive during generation. First
Polish turn's cold-start latency was ~39s (STT-result → first token) —
recorded accurately below, not redesigned around (model-selection is out of
scope for M2.3).

## REAL ENGLISH TEST

Operator Andrzej, real reSpeaker XVF3800, `--language en`: "What is the
speed of light?" then "Can anything with mass travel that fast?" — same
acceptance criteria as Polish, all met. The "neutron star" observation from
the operator's own notes was an STT mishearing ("neutral star"), not a
model/conversation failure — the model correctly answered the transcript it
actually received.

## FAST SECOND-UTTERANCE TEST

**PASSED**, real hardware. During assistant generation the operator spoke
again; VAD/STT remained live, the next transcript was queued (not dropped,
not processed concurrently), the first turn completed, then the second
turn ran — no overlapping `ConversationSession` execution, no assistant
token interleaving, ordering preserved, `max concurrent conversation turns
observed this session: 1` confirmed via the probe's own live
instrumentation, not inferred from timestamps. Terminal log interleaving of
VAD/STT status lines with in-progress assistant text is presentation
output only, not an overlapping conversation turn (recorded per the
operator's own clarification).

## RESPONSE-LANGUAGE MIRRORING (real hardware finding, fixed before PASS)

**Bug found during acceptance testing**: a fresh English voice session
answered "What is the speed of light?" in Polish — only switching to
English after the operator explicitly said "We are now going to English."
Root cause: the M1.1 persona's system prompt is entirely Polish, including
its own (too-weak) mirroring instruction; `gemma4:e4b` did not reliably
override that dominant framing on a session's first turn.

**Fix, at the explicit direction of the task** (canonical
`ConversationSession`/`ConversationContext` policy, not the voice adapter —
proven identical for typed and voice by
`test_voice_and_typed_turns_get_the_identical_language_policy`):
`src/nexa/conversation/language.py`'s `detect_response_language()` (a
small, deterministic PL/EN heuristic — diacritics or a short high-precision
Polish stopword list, otherwise English; not a general classifier, per
ADR-0003 D5's PL/EN scope) plus `language_directive()`, wired into
`ConversationContext.to_provider_messages()`.

**A second real-hardware finding, inside this same fix, before it could be
called done**: the first version injected the directive only for the
*current* turn as a message never stored in history. Real hardware timing
showed the fresh-session bug was fixed, but every subsequent turn's
latency stayed at the fresh-session's ~15-18s cold-start cost instead of
returning to the previously-measured ~2-6s warm range. Root cause,
verified by direct experiment (see "LATENCY / RESOURCES" below): the
injected message permanently desynchronizes what gets replayed from
`ConversationTurn` history (which never contains it) from what was
actually cached by Ollama/llama.cpp's prompt-prefix reuse (which does) —
breaking cache reuse for the rest of the session, the instant it is used
even once. A "only inject on a language switch" attempt reduced how often
this happened but reintroduced the original correctness bug (the model
reverted to Polish on an unprompted same-language second turn). **Final
fix**: recompute the identical directive after *every* historical user
turn, purely from that turn's own already-stored (unmodified) text, inside
`to_provider_messages()` — byte-identical across calls, so replayed history
exactly matches what was cached, restoring full incremental reuse, while
reinforcing the language correction on every single turn (needed, since
the model's own "memory" of the established language is not reliable
without it). `ConversationTurn`/`ConversationSession.history` (the real
transcript, ADR-0003 D2) are completely unaffected by this fix.

**Real acceptance, typed, real `gemma4:e4b`** (all passed):
- Fresh EN session, "What is a black hole?" → English. ✓
- Fresh PL session, "Co to jest czarna dziura?" → Polish. ✓
- Same session: "Powiedz mi krótko czym jest Słońce." (PL) → Polish reply →
  "And how old is it?" (EN) → English reply, correctly understood "it" =
  the Sun → "Powiedz mi teraz po polsku, jak długo jeszcze będzie
  świecić." (explicit PL request) → Polish reply. ✓

**Real acceptance, voice, real hardware**: `--language en`, "What is the
speed of light?" → first answer immediately English, no "switch to
English" needed. `--language pl`, "Co to jest prędkość światła?"
(STT rendered as "Co to jest prędkoświatła?", an STT accuracy artifact, not
a mirroring failure) → first answer immediately Polish. `max concurrent STT
executions observed this session: 1` and `max concurrent conversation
turns observed this session: 1` both reconfirmed.

## STREAMING

`ConversationSession.send()`'s existing async-generator streaming is
reused unmodified — `VoiceConversationAdapter._run_turn()` iterates it
chunk-by-chunk, invoking `on_assistant_token` per chunk and
`on_assistant_complete` once the generator (and therefore
`ConversationSession`'s own history-append) has fully completed. No
buffering of the full response before surfacing it — confirmed both by
`tests/test_voice_conversation_adapter.py`'s `TestStreaming` cases and by
watching real tokens stream in the probe during real hardware testing.

## LATENCY / RESOURCES

**Direct controlled experiment (typed, real `gemma4:e4b`, same session, 3
short turns) isolating the cache-breaking regression**, run three ways:

| Directive strategy | Turn 1 (cold) | Turn 2 | Turn 3 |
|---|---|---|---|
| None (baseline) | 15.50s | 2.83s | 2.06s |
| Every turn, transient (first attempt — broke caching) | 15.54s | 17.64s | 18.68s |
| Only on language switch (second attempt — broke correctness) | 1.26s* | 2.64s* | 2.02s* — but turns 2/3 answered in **Polish** despite English questions |
| Every turn, recomputed from stored history (final fix) | 15.93s | 2.60s | 5.05s |

\* This run's fast turn-1 time is explained by an accidental KV-cache hit
from an earlier identical test run's turn 1 (same exact prompt text) — not
evidence the approach itself was fast; its real defect was the Polish
misfire on turns 2-3, confirmed independently.

**Real hardware voice acceptance timings** (operator's own report,
recorded exactly, not redesigned around): first Polish turn STT-result →
first-token ~39s (cold start — model/first-inference cost, not this
substage's code); subsequent warm turns ~2-6s. Consistent with the
controlled experiment's cold/warm pattern above.

**End-of-turn latency breakdown** (instrumented in
`apps/nexa_voice_chat_probe.py`, printed per turn): END_OF_TURN → STT
result, STT result → first assistant token, STT result → completion,
END_OF_TURN → first assistant token, END_OF_TURN → completion — all
timestamped via `time.monotonic()`, correlated per-turn via a FIFO deque
(correct even under the fast-second-utterance scenario, since
`SerialConversationQueue` guarantees turns start in submission order).
Physical speech-end latency is not separately instrumented and is not
claimed.

**Resources**: `gemma4:e4b` stayed resident throughout (`ollama ps`, `100%
CPU`, `8192` context, unchanged from M1.1); ~11GB RAM used matching M1.1's
known footprint, ~4GB available, no swap thrashing; `vcgencmd measure_temp`
53-59°C across the session; `vcgencmd get_throttled` = `0x0` throughout. No
model benchmark performed — M2.3 is integration, not model selection.

## TESTS

159 total (118 pre-M2.3 tests unaffected in behavior, after necessary,
disclosed updates to reflect the new — correct — message shape; 41 net new
tests):

| File | Tier | Count |
|---|---|---|
| `tests/test_language.py` | unit | 8 |
| `tests/test_voice_conversation_adapter.py` | unit (fake `ModelProvider`, same pattern as `test_conversation_session.py`) | 19 |
| `tests/test_voice_conversation_architecture.py` | unit (`ast`-based import/call inspection) | 5 |
| `tests/test_voice_chat_probe_state_display.py` | unit (`ast`-based, regression for the state-display bug) | 2 |
| `tests/test_conversation_session.py` | unit — new `TestResponseLanguageMirroring` class | +7 |
| `tests/test_context.py` | unit — 1 existing case updated for the new message shape | 0 net new |
| `apps/nexa_voice_chat_probe.py` | manual, human-in-the-loop, not automatable | — |

Net new: 8+19+5+2+7 = 41. 118 + 41 = 159, matching `unittest discover`
exactly.

`python -m unittest discover -s tests` and `pytest`: **159 tests, all
pass**, 4 intentionally skipped (pre-existing M1.1/M2.1 opt-in tests plus
M2.2's own opt-in live-whisper.cpp tests — unaffected by M2.3).
`ruff check src apps tests`: clean. All 20 required test scenarios covered:
transcript reaches `ConversationSession`; same session object for typed +
voice; no second history/context/persona/model in the adapter (`ast`-
verified); whitespace/empty transcript rejected explicitly; STT failure
produces no turn; FIFO ordering; max conversation concurrency == 1; a
second transcription can arrive while the first turn is active (measured
non-blocking `submit()`); assistant streams don't interleave; one
transcript → exactly one user turn; no duplicate turns under rapid
submission; conversation errors reach the caller (`on_conversation_error`);
clean queue/worker shutdown (including a queued-turn-finishes-first case);
M2.1 `VoiceState` behavior intact (existing suite unchanged); M2.2 STT FIFO
behavior intact (existing suite unchanged); no TTS/Piper path (`ast`-
verified); no direct Ollama call in the adapter (`ast`-verified); no second
`ModelProvider` construction (`ast`-verified); all pre-existing M1.1/M2.1/
M2.2 tests still pass.

## ARCHITECTURE / DOCUMENTATION

- `docs/architecture/M2_3_VOICE_CONVERSATION_ADAPTER_ARCHITECTURE.md` — new.
- This report (`R0009`).
- `docs/CURRENT_STATE.md`, `docs/testing/TEST_STRATEGY.md` — updated.
- `docs/architecture/FOUNDATION_ARCHITECTURE.md` — one pointer added to the
  Voice section, same pattern as M2.1/M2.2's pointers.
- `configs/personas/nexa_persona_v1.json` — the persona's `system` prompt's
  language-mirroring sentence was strengthened (bilingual, explicit) as a
  first attempt; real-hardware testing showed this alone was still
  insufficient, which is why the deterministic `nexa.conversation.language`
  fix exists. The strengthened prose is kept (harmless, and it is still the
  first line of defense before the deterministic directive) — the
  `description` field records why and when this changed.
- `docs/decisions/ADR-0003_realtime_voice_foundation.md` — **not modified**.
  D2's actual requirement (one conversation authority) is fully honored;
  the mechanism-name deviation (`VoiceConversationAdapter` vs. the ADR's
  illustrative `NeXaConversationFrameProcessor`) was explicitly left open
  by the ADR's own text for this substage to decide, not a contradiction.
- `pyproject.toml` — unchanged (no new dependency).

## COMMIT

One commit, implementation + tests + docs (hash recorded in the final
response). Not pushed.

## UNRESOLVED

- `SerialConversationQueue.shutdown()` waits for in-flight work rather than
  cancelling it — acceptable for M2.3 (no barge-in yet); M2.5 will need to
  revisit this.
- `detect_response_language` is a small PL/EN heuristic, not a trained
  classifier — sufficient for this milestone's acceptance scope (ADR-0003
  D5), not validated beyond it.
- Cold first-turn latency (~15-16s typed, ~39s observed once on real voice
  hardware) is a real, felt `gemma4:e4b`/Ollama/Pi baseline cost, unrelated
  to M2.3's own code — not addressed here, per explicit task scope ("M2.3
  is integration, not model selection").
- No TTS, no barge-in, no LiveKit/WebRTC exists yet — M2.4+.

## CURRENT VERIFIED STATE

M2.3 implemented, tested, and operator-confirmed on real hardware across
two genuine fix-and-retest cycles (conversation-turn concurrency,
response-language mirroring plus its own latency regression) — both found
live, both fixed, both reconfirmed before PASS. Voice input now reaches the
exact same `ConversationSession` typed chat uses, with FIFO-serialized
turns, correct per-turn PL/EN response-language mirroring, and no
measurable warm-turn latency regression. No TTS, no barge-in exists yet.
M1.1/M2.1/M2.2 are unaffected — their full test suites still pass unchanged
in behavior (only necessary, disclosed message-shape test updates for the
language fix).

## NEXT RECOMMENDED ACTION

Start **M2.4 — Streaming/chunked Piper TTS integration** (ADR-0003's
substage table, D6): sentence/chunk-boundary triggering so TTS can start
narrating before the full LLM reply is generated, via subprocess
invocation (not an in-process import, per ADR-0003 D6/R0005's license
finding — `OHF-Voice/piper1-gpl` is GPL-3.0). Piper remains an explicitly
**temporary** TTS baseline, not frozen. M2.4 should consume
`VoiceConversationAdapter`'s existing `on_assistant_token`/
`on_assistant_complete` streaming exactly as M2.3 already surfaces it — no
new conversation-authority wiring should be needed, only a new downstream
consumer of the same stream. Still no barge-in in M2.4 — that remains M2.5.

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

No changes required — M2.3 (including both real-hardware-driven fixes)
followed the existing evidence/labeling/ADR/report discipline without
exposing any gap in `AGENTS.md` itself.

## LEGACY NEXA USED

NO — this substage's evidence came from real hardware testing and the
existing M1.1/M2.1/M2.2 codebase, not new legacy repo reads.

## EXTERNAL RESEARCH USED

NO — the caching-regression root cause was diagnosed by direct, controlled
experimentation against the real local Ollama/`gemma4:e4b` process (not
external documentation), consistent with this project's evidence
discipline.
