# M2.3 — Voice → ConversationSession Adapter (verified architecture)

Status: **`VERIFIED FACT`** — implemented, tested, and operator-confirmed on
real hardware 2026-09-06. Per ADR-0003 D2. Unlike
`docs/architecture/FOUNDATION_ARCHITECTURE.md`'s Voice boundary (still
conceptual for TTS/barge-in beyond this), this document describes real code.

Related: `docs/decisions/ADR-0003_realtime_voice_foundation.md` (D2 — the
decision this implements), `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`
(the `ConversationSession` this substage feeds, unchanged),
`docs/architecture/M2_2_LOCAL_STT_ARCHITECTURE.md` (the STT boundary this
substage consumes), the M2.3 implementation report (`R0009`).

---

## 1. Scope (ADR-0003 M2.3)

One M2.2 `TranscriptionResult` → the **existing, unchanged**
`ConversationSession` (M1.1) as a normal user turn → streamed assistant
text. **No TTS, no Piper, no speaker playback of assistant responses, no
barge-in, no LiveKit/WebRTC, no second LLM path, no second history, no
second persona, no second model router.** Those are M2.4+ substages, not
built here.

## 2. The one path

```
microphone -> Pipecat -> Silero VAD -> UtteranceBuffer -> whisper.cpp   (M2.1/M2.2, unchanged)
    |
    v
TranscriptionResult
    |
    v
VoiceConversationAdapter.handle_transcription()     (new, M2.3)
    |  rejects empty/whitespace transcripts explicitly — no fabricated turn
    v
SerialConversationQueue.submit(text)                (new, M2.3 — FIFO, <=1 turn in flight)
    |
    v
ConversationSession.send(text)          <-- the EXACT SAME method apps/nexa_chat.py calls
    |  ConversationContext.build() -> to_provider_messages() (response-language
    |  mirroring lives here, R0009 — see §6)
    v
LocalModelProvider (Ollama) -> gemma4:e4b            (M1.1, frozen, unchanged)
    |
    v
streamed assistant text chunks -> on_assistant_token/on_assistant_complete callbacks
```

`ConversationSession`, `ConversationContext`, `LocalModelProvider`, and the
frozen `gemma4:e4b` baseline are **completely unmodified in shape** — M2.3
adds zero new conversation-authority code to any of them (the one small,
justified exception is the language-mirroring fix in `ConversationContext`,
which is canonical policy applying identically to typed and voice input —
see §6). Typed chat (`apps/nexa_chat.py`) and voice
(`apps/nexa_voice_chat_probe.py`) call the identical `ConversationSession`
instance shape, configured the same way, exactly as ADR-0003 D2 requires.

## 3. Package layout (`src/nexa/voice_conversation/`)

A new top-level package — not a submodule of `nexa.voice` or `nexa.stt`,
both of which have their own architecture tests forbidding a
`nexa.conversation`/`nexa.providers` import; putting the adapter inside
either would have required weakening those tests to pass, which the task
explicitly forbade.

| Module | Responsibility |
|---|---|
| `adapter.py` | `VoiceConversationAdapter` — the only new wiring; owns no history/context/persona/model choice of its own |
| `queue.py` | `SerialConversationQueue`, `ConversationQueueOverflowError` — pure asyncio, no `ConversationSession` import |

ADR-0003 D2 sketched this substage's component as a Pipecat `FrameProcessor`
(illustratively named `NeXaConversationFrameProcessor`), receiving a
`TextFrame` and pushing streamed chunks back downstream toward TTS — a
shape that assumed TTS (M2.4) already existed to receive those frames. M2.2
already established a simpler, non-Pipecat seam (`VoiceRuntime`'s plain
`on_transcription`/`on_transcription_error` callbacks) with no TTS yet to
push toward, so `VoiceConversationAdapter` is a plain Python class driven
by those callbacks, not a `FrameProcessor`. D2's actual requirement —
`ConversationSession` stays the sole conversation authority, no second
history/persona/model/provider choice — is fully honored; only the
illustrative mechanism differs, which the ADR text explicitly left open for
this substage to decide.

`apps/nexa_voice_chat_probe.py` is a new, separate thin CLI wrapper
(`apps/README.md`'s convention) — `apps/nexa_stt_probe.py` (M2.2) was not
extended, per the task's explicit instruction.

## 4. Architectural ownership — verified, not just asserted

`src/nexa/voice_conversation/*.py` never constructs `ConversationSession`,
`LocalModelProvider`, `LlamaServerProvider`, or `PersonaConfig` itself
(`ast.Call` inspection); never imports `nexa.bootstrap`/`nexa.config`
(it is *handed* an already-built session, exactly like `apps/nexa_chat.py`);
never imports an LLM client (`ollama`) directly; never references a TTS
engine. It also never imports `nexa.conversation.language` or calls its
functions directly — response-language mirroring is `ConversationSession`/
`ConversationContext` policy, and the adapter only ever calls
`session.send(text)`, identically to typed chat. All enforced by
`tests/test_voice_conversation_architecture.py` (`ast`-based), mirroring
M2.1/M2.2's own architecture tests.

## 5. Conversation-turn serialization (the M2.2 lesson, one layer up)

M2.2 found and fixed a real concurrency defect: two whisper.cpp
subprocesses could run at once if a short utterance followed quickly.
M2.3 has the exact same risk one layer later — a second `TranscriptionResult`
can arrive while the first is still generating an LLM reply. Concurrent
`ConversationSession.send()` calls against the same session could corrupt
turn ordering, history, and streamed-output ordering.

**`SerialConversationQueue`** (`src/nexa/voice_conversation/queue.py`,
deliberately mirroring `nexa.stt.queue.SerialTranscriptionQueue`'s design,
duplicated rather than shared to keep the two boundaries independent):

- `submit(text)` is synchronous and non-blocking — audio/VAD/STT capture is
  never slowed down by an in-flight conversation turn.
- A single background worker processes turns strictly FIFO, one
  `ConversationSession.send()` call at a time —
  `max_observed_concurrency` is tracked and proven to stay at 1
  (`tests/test_voice_conversation_adapter.py`, and live on real hardware —
  the fast-second-utterance acceptance test, §8).
- Bounded (`max_queue_size=4` — smaller than `nexa.stt`'s 8, because LLM
  turns are far slower and more expensive than STT calls, R0005 measured
  27.6-54s worst case) — overflow raises `ConversationQueueOverflowError`
  explicitly; a transcript is never dropped silently.
- `shutdown()` drains whatever is queued (does not drop it), then exits.

## 6. Response-language mirroring (real hardware finding, R0009)

**Not originally in scope** — added after real-hardware acceptance testing
found a real product defect: a fresh English voice session answered "What
is the speed of light?" in Polish. Root cause: the M1.1 persona's system
prompt is entirely Polish text, including its own language-mirroring
instruction ("Piszesz w tym samym języku...") — on a model as small as
`gemma4:e4b`, that single Polish-language instruction, surrounded by an
otherwise all-Polish prompt, was not a strong enough signal to override the
prompt's own dominant language, especially on a session's first turn.

**Fix location, per explicit task instruction**: canonical
`ConversationSession`/`ConversationContext` policy
(`src/nexa/conversation/language.py`, wired into
`ConversationContext.to_provider_messages()`), not the voice adapter — it
applies identically to typed and voice input, proven by
`tests/test_voice_conversation_adapter.py`'s
`test_voice_and_typed_turns_get_the_identical_language_policy`.

- `detect_response_language(text) -> "pl" | "en" | None`: a small,
  deterministic PL/EN heuristic (Polish diacritics, or a short
  high-precision Polish stopword list; otherwise English) — **not a general
  classifier**, matching ADR-0003 D5's PL/EN-only scope. Added only after
  actual testing proved the model-level prose instruction insufficient, per
  the task's explicit escalation criterion.
- `to_provider_messages()` inserts `language_directive(language)` (a short,
  explicit system-role reminder) immediately after **every** historical
  user turn, not just the current one, recomputed fresh from that turn's
  own unmodified stored text on every call.

**Why after *every* turn, and why recomputed rather than stored (a second,
real-hardware-caught bug in this same fix)**: the first version injected
the directive only for the *current* turn, as a message never stored in
`ConversationTurn` history. That fixed correctness but **permanently broke
Ollama/llama.cpp's prompt-prefix KV-cache reuse** the instant it was used
once — every later turn's context, rebuilt from the (unmodified) history,
then structurally diverges from what was actually cached (which included
that one extra message) at the exact point it was inserted, so nothing
downstream of that point is ever a cache hit again for the rest of the
session. Measured impact: "warm" turns that were normally ~2-6s to first
token stayed at ~15-20s for **every** subsequent turn, not just the one
with the directive. A second attempt (inject only on an actual language
*switch*, tracking session state) reduced how often this happened but
reintroduced the original correctness bug — the model reverted to Polish
on an unprompted same-language second turn without a fresh reminder, since
its own prose-level mirroring is unreliable even one turn in. The final
fix — recompute the same directive after *every* user turn, purely as a
function of that turn's own already-stored text — needs no session state,
is byte-identical across calls (so replayed history matches exactly what
was cached, restoring full incremental reuse), and reinforces the
correction every turn (needed, since the model's own memory of "we're in
English now" is not reliable on its own). `ConversationTurn`/
`ConversationSession.history` are completely unaffected — the directive
exists only in the wire-level message list `to_provider_messages()`
builds fresh each call, never in the real transcript.

Measured, real `gemma4:e4b`, this Pi (`R0009` has the full table): turn 1
(cold, unavoidable) ~15-16s to first token; turns 2+ (warm) ~2-5s,
regardless of language or language switches — matching the pre-fix,
no-directive baseline exactly, with correct language mirroring preserved
throughout.

## 7. Voice state vs. conversation status — not conflated

A real hardware finding (mirroring M2.2's own STT-probe lesson) required a
second fix in `apps/nexa_voice_chat_probe.py`: printing `voice state: ...`
must come **only** from the real `VoiceStateMachine` event stream
(`on_event`), unconditionally, in order. The conversation-side callbacks
(`on_user_transcript`/`on_assistant_token`/`on_assistant_complete`/
`on_conversation_error`) print only conversation-specific status
(`conversation: QUEUED`/`THINKING`, `user: ...`, `assistant: ...`) and never
claim a voice state — the acoustic `VoiceState` can have already moved on
(e.g. to a new `USER_SPEAKING`) while a conversation turn is still
generating. Enforced by `tests/test_voice_chat_probe_state_display.py`
(`ast`-based), mirroring M2.2's `test_stt_probe_state_display.py`. No new
`VoiceState` enum members (`THINKING`/`RESPONDING`) were added — conversation
status is tracked and printed entirely separately by the probe itself.

## 8. Real hardware test — canonical path, FIFO, language mirroring

`VERIFIED FACT`, this Pi, real reSpeaker XVF3800, operator Andrzej,
2026-09-06:

- **Canonical path**: voice questions reached the real `ConversationSession`
  → real `LocalModelProvider` → real `gemma4:e4b`, streamed text, identical
  to typed chat's path.
- **FIFO/concurrency**: a fast second utterance spoken while the first
  answer was still streaming was captured, queued, and processed after the
  first completed — no interleaved assistant text, `max concurrent
  conversation turns observed this session: 1` throughout (alongside M2.2's
  own `max concurrent STT executions observed this session: 1`).
- **Response-language mirroring** (after the fix above): a fresh English
  session answered "What is the speed of light?" in English immediately,
  with no "switch to English" prompt needed; a fresh Polish session
  answered "Co to jest prędkość światła?" in Polish immediately.
- **Multi-turn cross-language context** (typed acceptance, real
  `gemma4:e4b`): "Powiedz mi krótko czym jest Słońce." (PL) → "And how old
  is it?" (EN, correctly understood "it" = the Sun, answered in English) →
  "Powiedz mi teraz po polsku..." (PL, explicit request honored).

## 9. Known limitations, stated plainly

- **`SerialConversationQueue.shutdown()` waits for in-flight work**: a
  turn already generating is not cancelled, only drained — acceptable for
  M2.3 (no barge-in yet); M2.5's barge-in design will need to revisit this.
- **`detect_response_language` is PL/EN-only and heuristic**: a short
  high-precision Polish stopword list plus diacritics — not a trained
  classifier. Explicit language requests are honored only because the
  request phrase itself is typically written in the requested language
  (e.g. "odpowiedz po polsku"); a request phrased in a *different* language
  than the one requested (e.g. an all-English sentence asking for a Polish
  reply) is not specifically handled and was not required by this
  milestone's acceptance test.
- **No TTS, no barge-in, no LiveKit/WebRTC** — M2.4+, per ADR-0003's
  substage table.
- **Cold first-turn latency (~15-16s) is a real, felt cost**, unrelated to
  this substage's own code — it is `gemma4:e4b`/Ollama/this Pi's baseline
  first-inference cost (ADR-0002 Amendment 2's accepted RAM/latency
  tradeoff), not something M2.3 introduced or is scoped to fix.
