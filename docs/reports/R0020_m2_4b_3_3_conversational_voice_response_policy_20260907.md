# R0020 — M2.4B.3.3: Conversational voice response policy

- **Date:** 2026-09-07
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.3.3 —
  implementation**
- **Related:** `docs/reports/R0019_…` (B.3.2 continuity controller —
  `0` holds on today's LLM rate; *"the highest-leverage next step is
  conversational reply shape"*), `docs/reports/R0018_…` (**the rate-budget
  authority** — `realtime_text_ratio ≈ 0.53`), `docs/reports/R0009_…`
  (the KV-cache discipline this stage extends),
  `docs/research/m2_4b_speech_flow/b33_ab.py` (+ `b33_ab_raw_20260907.txt`
  — the scripted hardware A/B).

**Scope: one new typed concept — `ResponseMode.{TEXT,VOICE}` — and one
constant `system` message added to the SAME `ConversationSession`'s wire
prompt when a reply will be spoken. No second session / persona / history
/ memory / model authority / response-language authority. Typed chat is
byte-for-byte unchanged. Not post-generation truncation. `gemma4:e4b`,
`num_predict = 200`, model routing, STT, TTS voice, `length_scale`, the
speech planner, the B.3.2 continuity controller, Piper `nice +10`, the
8 s TTS context timeout, the half-duplex gate, fillers — all untouched.
No M2.5. Not pushed.**

---

## TASK RESULT

**PASS.** In voice conversation the same canonical `ConversationSession`
now receives one transient, constant, fixed-position `system` instruction
(`voice_response_directive()`) telling it to answer directly, keep an
ordinary question to ~1–3 sentences with a short useful first sentence,
avoid lectures/lists by default, and **honour an explicit request for
detail / steps / a list / a comparison / more**. `ResponseMode.TEXT` (the
default) adds nothing — typed chat's provider message sequence is
identical to pre-B.3.3. The instruction is never stored in history; the
canonical assistant turn is still exactly the model output; the response
language is still decided solely by the per-turn `language_directive`
(R0009).

**Hardware A/B (real `gemma4:e4b` + real Piper `nice +10` + real audio +
real session; four ordinary questions asked *without* "krótko", plus one
"wyjaśnij dokładnie"):**

- **Ordinary questions now 1–3 sentences by default.** TEXT gave
  `2 / 2 / 5 / 11` sentences (Q-C a 3-paragraph mini-lecture with
  `**bold**`; Q-D an intro + 4-item numbered list + conclusion). VOICE
  gave `2 / 2 / 3 / 2` — **every** ordinary answer within policy, all flat
  conversational prose, **no markdown, no lists**. Mean ordinary answer
  length `395 → 186` chars (**‑53 %**).
- **Detail override respected.** "Wyjaśnij dokładnie, jak powstaje czarna
  dziura." → VOICE still answered in **5 sentences / 522 chars**,
  technical (kolaps jądra, ciśnienie degeneracji, granica
  Tolmana–Oppenheimera–Volkoff), **not clipped**. It is 2.3–3.3× longer
  than any ordinary VOICE answer this run — the model clearly acted on the
  explicit-detail phrasing.
- **First audio equal-or-better on 4 of 5** turns (Q-B `17.7 → 9.7 s`;
  Q-E `27.1 → 10.7 s`; Q-C `17.2 → 13.2 s`; Q-A unchanged — same answer).
- **Intra-response gaps on the ordinary questions collapsed**: TEXT
  `6.7 s` (Q-B), `5.7 s` (Q-C), **`41.6 s`** (Q-D — the speech planner
  holding a numbered list) → VOICE `gapless` (Q-B), `5.0 s` (Q-C),
  `2.9 s` (Q-D). Shorter, list-free replies = fewer and much smaller
  gaps, exactly as R0018/R0019 predicted.
- **`realtime_text_ratio ≈ 0.53` is unchanged.** The detail-override
  reply still gaps (4 gaps, mean `3.8 s`); a long answer at today's LLM
  rate still cannot be continuous. This stage attacks reply **shape**, not
  the generation rate — that remains the faster-generation track.

## M2.4B.3.3 STATUS

Implemented, unit-tested (17 deterministic offline tests, all green),
full suite green (`pytest` 433 passed / 7 skipped; `unittest discover`
440 OK / 7 skipped — up from 416/423 with the new file, no regression),
`ruff` + `git diff --check` clean, and exercised on real hardware
(`b33_ab.py`). Not `OPERATOR-CONFIRMED` yet — awaiting the operator's own
voice session.

## RESPONSE MODE ARCHITECTURE

**The seam.** `src/nexa/conversation/response_mode.py` — a new
`StrEnum`:

```python
class ResponseMode(StrEnum):
    TEXT = "text"
    VOICE = "voice"
```

It is threaded as an explicit keyword, default `TEXT`, through exactly two
existing call sites — nothing else in `nexa.conversation` changes:

| layer | change |
|---|---|
| `ConversationSession.send(user_text, *, cancel_token=None, response_mode=ResponseMode.TEXT)` | passes `response_mode` straight to `context.to_provider_messages(...)`; **no new field on the dataclass** — it is a per-request hint, so one session serves both modes |
| `ConversationContext.to_provider_messages(*, response_mode=ResponseMode.TEXT)` | if `response_mode == VOICE`, insert **one** `ProviderMessage("system", voice_response_directive())` at **index 1** — right after the persona system prompt, before any turn. `TEXT` adds nothing (literally unchanged code path). |
| `VoiceConversationAdapter.__init__(..., response_mode=ResponseMode.VOICE)` | the voice surface defaults to `VOICE`; it still only calls `session.send(text, response_mode=self._response_mode)` — owns no history/persona/model/language logic |

**Why this shape, not another.** The task asked for "the smallest clean
point where a transient response-mode hint can be supplied to the SAME
`ConversationSession`/model request … an explicit typed concept". A
`send()` keyword satisfies *transient / per-request* (a session field
would make it sticky and let it drift into "a second mode with its own
state"); a typed enum satisfies *explicit, not a hidden string hack*
threaded through unrelated layers; inserting the message in
`to_provider_messages` — where the R0009 language directive already lives
— keeps all wire-prompt construction in one place. `SpeechPlanner` is not
touched: it remains TTS formatting/segmentation only and has no say in
answer length.

**Not a second brain.** `response_mode.py` contains exactly one class (the
enum) and imports only `__future__` and `enum` — asserted by AST in
`test_15`. No `ConversationSession`, no context, no history, no persona,
no provider, no memory, no `language_directive`. The adapter still holds a
single `session` reference and never constructs a second one. Typed chat
and voice chat are the **same** `ConversationSession` instance with the
**same** history — proven by `test_3` (one session, both modes, history =
`[USER, ASSISTANT, USER, ASSISTANT]`, only call #2 carries the voice
directive).

## VOICE RESPONSE POLICY

One bilingual `system` message (`_VOICE_RESPONSE_DIRECTIVE`, returned by
`voice_response_directive()`), mirroring the persona's own bilingual
PL↔EN structure:

> Rozmawiasz teraz na żywo, głosowo. Odpowiadaj wprost i naturalnie. Na
> zwykłe pytanie odpowiadaj zwięźle — zwykle 1–3 zdania. Niech pierwsze
> zdanie będzie krótkie i konkretne, żeby mowa mogła szybko ruszyć. Nie
> zamieniaj prostego pytania w wykład ani listę i zostaw użytkownikowi
> miejsce na kolejne pytanie. Jeśli użytkownik prosi o szczegóły,
> wyjaśnienie, kroki, porównanie, listę lub dłuższą odpowiedź — podaj
> dokładnie to, o co prosi.
> You are in a live voice conversation. Answer directly and naturally. For
> an ordinary question, be concise — usually 1–3 sentences. Keep the first
> sentence short and concrete so speech can begin quickly. Don't turn a
> simple question into a lecture or a list, and leave room for the user's
> next turn. If the user asks for detail, an explanation, steps, a
> comparison, a list, or a longer answer, give exactly what they ask for.

Four things it deliberately encodes, and one it deliberately does not:

1. **Concise default** — "1–3 zdania" / "1–3 sentences" for an *ordinary*
   question (not a hard cap — see §DETAIL OVERRIDE).
2. **Short first sentence** — for first-audio latency (R0019 found warm
   END_OF_TURN → first TTS audio ≈ 12.5–17.1 s; most of that is
   generating the whole first sentence at ~8.5 chars/s). "krótkie i
   konkretne" — not *unnaturally tiny*; naturalness stays the priority.
3. **No lecture / list by default** — "Nie zamieniaj … w wykład ani
   listę"; "leave room for the user's next turn".
4. **Explicit-detail override** — an enumerated list of triggers
   (szczegóły / wyjaśnienie / kroki / porównanie / listę / dłuższą
   odpowiedź; detail / explanation / steps / comparison / list / longer)
   → "podaj dokładnie to, o co prosi" / "give exactly what they ask for".

It does **not** name a response language. The per-turn
`language_directive` (`"Odpowiedz … po polsku."` / `"Respond … in
English."`) is still the sole response-language authority and still
appears after every user turn (`test_6`, `test_7`, `test_8`). The voice
directive is language-neutral by construction (`test_language_neutral`).

**KV-cache discipline (extends R0009).** The directive is a **fixed
constant string** placed at a **fixed index (1)**, recomputed identically
on every `to_provider_messages()` call. The wire prompt's prefix
(`persona` + `voice directive`) is therefore byte-stable across turns, so
Ollama/llama.cpp prompt-prefix KV-cache reuse is preserved — the same
property R0009 restored after the first language-directive attempt broke
it. Asserted by `test_voice_directive_is_a_constant_fixed_position_string`.

## DETAIL OVERRIDE

The directive is a **generation policy, not output clipping**. There is no
token or sentence cap anywhere in the code: `num_predict` is unchanged at
`200`, `send()` still streams and appends whatever the model produces, and
`test_10_11_12` asserts the directive text contains **none** of
`maksymalnie` / `nie więcej niż` / `at most` / `no more than` / `truncat`
/ `obetnij`.

Hardware: "Wyjaśnij dokładnie, jak powstaje czarna dziura." under
`ResponseMode.VOICE` produced a 5-sentence, 522-char technical answer
(star collapse → loss of fusion pressure → runaway infall → TOV limit →
event horizon). It was **not** forced down to 1–3 sentences; it is far
longer than the four ordinary VOICE answers (160–226 chars) in the same
session. The only length limit it met is the pre-existing
`num_predict = 200` serving ceiling (which also truncated the TEXT-mode
Q-D and Q-E answers mid-word) — out of scope for B.3.3.

## TEXT MODE INVARIANT

`ResponseMode.TEXT` is the default and its branch in
`to_provider_messages` is the **unchanged** pre-B.3.3 code. `test_2`
pins it exactly:

```
to_provider_messages()  ==  to_provider_messages(response_mode=ResponseMode.TEXT)
  == [ ("system", SYSTEM_PROMPT),
       ("user",   "Co to jest czarna dziura?"),
       ("system", language_directive("pl")) ]        # no voice message anywhere
```

`test_3` confirms a session used first in TEXT then VOICE mode sends the
voice directive **only** on the VOICE call. The full pre-existing
conversation + voice-conversation + language suites stay green
(56 conversation/adapter/language tests; `test_voice_conversation_architecture.py`
unchanged — the adapter imports the `nexa.conversation.response_mode`
submodule, which its allow-list already permits, not bare
`nexa.conversation`). Typed chat behaviour is unchanged.

## HISTORY INVARIANT

`ConversationSession._history` still stores exactly two kinds of turn:
the verbatim user text and the verbatim model output.

- `test_4` — after a VOICE turn, no history turn equals or contains
  `voice_response_directive()`; `history[0]` is the user's text verbatim.
- `test_5` — `history[1]` (assistant) is exactly the streamed model
  output (`"Gwiazda to kula gazu."`), byte-for-byte, mode
  notwithstanding.

The voice directive exists only in the transient `messages` list handed
to `provider.generate()` for that one call. It is not a fake user
message, not a fake assistant message, not persisted anywhere.

## PL/EN RESULT

- **PL — hardware.** All five `b33_ab.py` VOICE turns were Polish
  questions; every answer was Polish; `language_directive("pl")` was
  present on every turn (the voice directive did not displace or alter
  it). The concise-default and detail-override behaviours above are all
  from the Polish runs.
- **EN — automated.** `test_8` proves an English voice request
  (`"What is a star made of?"`) puts `voice_response_directive()` at
  index 1 **and** `language_directive("en")` after the user turn; the
  directive's English half carries the same policy as its Polish half
  (`test_9`, `test_10_11_12`). EN was **not** exercised on hardware this
  round (the agent has no microphone and the scripted harness fed Polish
  transcripts); the EN wire prompt is identical in structure to the
  verified PL one.

## FIRST-SENTENCE RESULT

| Q (no "krótko") | TEXT 1st-sentence chars | VOICE 1st-sentence chars |
|---|---|---|
| A — Co to jest czarna dziura? | 130 | 130 *(identical answer — already one clean sentence)* |
| B — Z czego składa się gwiazda? | 113 | **57** |
| C — Jak powstaje hel w gwieździe? | 125 | **80** |
| D — Po co człowiekowi sen? | 46 *(a throwaway "…ma wiele funkcji. Najważniejsze to:")* | 68 *(substantive)* |
| E — Wyjaśnij dokładnie … | 210 | **69** *(then continues in detail)* |

The first spoken sentence is shorter and immediately useful in VOICE mode
on B, C and E. It was not driven to an unnatural length — the shortest
(B, 57 chars) is still a complete, natural clause ("Gwiazda składa się
głównie z gazu, głównie wodoru i helu.").

## FIRST-AUDIO BEFORE/AFTER

END_OF_TURN → first TTS audio (harness `stt_result` base == end of turn):

| Q | TEXT (baseline) | VOICE | Δ |
|---|---|---|---|
| A | 15.21 s | 15.64 s | +0.4 s *(same answer)* |
| B | 17.68 s | **9.73 s** | **‑7.95 s** |
| C | 17.21 s | **13.19 s** | ‑4.02 s |
| D | 6.39 s | 8.43 s | +2.04 s *(TEXT opened with a 46-char throwaway; VOICE's first line is substantive)* |
| E | 27.08 s | **10.71 s** | **‑16.37 s** |
| ordinary mean (A–D) | 14.12 s | **11.75 s** | ‑2.4 s |

R0019 warm baseline was 12.5–17.1 s across all continuity targets. VOICE
brings B and D under 10 s and E from 27 s to 11 s; the improvement tracks
the shorter first sentence. First-token latency itself is unchanged
(~2.1–2.8 s both configs) — that is the separate LLM-serving problem.

## REPLY LENGTH BEFORE/AFTER

| Q (no "krótko") | TEXT sentences | TEXT chars | VOICE sentences | VOICE chars | TEXT shape |
|---|---|---|---|---|---|
| A | 2 | 195 | 2 | 195 | prose (already fine) |
| B | 2 | 248 | 2 | **163** | prose |
| C | 5 | 493 | 3 | **226** | **3 paragraphs + `**bold**`** |
| D | 11¹ | 643² | 2 | **160** | **intro + 4-item numbered list + conclusion** |
| **ordinary mean (A–D)** | — | **394.8** | — | **185.8** (‑53 %) | — |
| E (detail) | 8 | 649² | 5 | 522 | numbered list |

¹ sentence-splitter fragment count of a list. ² TEXT Q-D and Q-E both hit
`num_predict = 200` and truncated mid-word — the model wanted to write
still more.

Every ordinary VOICE answer is 2–3 sentences of flat prose. The two
TEXT lectures/lists (C, D) became conversational sentences. The detail
answer (E) stayed long.

## CONTINUITY BEFORE/AFTER

B.3.2 continuity controller kept at its shipped default
(`target_reserve_s = 2.0`, `enabled`). Its behaviour is **unchanged** —
phrase 0 immediate; one brief `0.40 s` `HOLD_EXPIRED` hold on the
2-chunk replies (Q-A both configs), `0` holds once the estimated reserve
is already negative. B.3.3 changed reply shape, not the controller.

| Q | TEXT max gap · underruns | VOICE max gap · underruns |
|---|---|---|
| A | — (gapless) · 1 | — (gapless) · 1 |
| B | 6.70 s · 2 | **— (gapless)** · 1 |
| C | 5.68 s · 1 | 4.96 s · 1 |
| D | **41.59 s** · 1 | **2.86 s** · 2 |
| E (detail) | 35.07 s (1 gap) · 1 | 6.90 s (4 gaps, mean 3.81 s) · 1 |

The 41.6 s Q-D gap in TEXT mode — the speech planner holding a numbered
list until it could be spoken as prose — is **gone** in VOICE mode
because the model produced no list. The ordinary-question gaps are now
`≤ 5 s` on 2–3 sentence replies. The rate deficit itself is untouched:
Q-C, Q-D and Q-E still gap (`audio-s per wall-s` 0.57–0.64 on the longer
replies), which is the expected `realtime_text_ratio ≈ 0.53` behaviour,
**not** a B.3.3 regression. A long answer (E) still cannot be made
continuous at today's LLM rate — that is the faster-generation track.

## REAL HARDWARE ACCEPTANCE

`docs/research/m2_4b_speech_flow/b33_ab.py` — real `gemma4:e4b`
(Ollama) + real external Piper HTTP (`nice +10`, `stop_frame_timeout_s
= 8`) + real `LocalAudioOutputTransport` (`usb_speaker`, index 2) + real
`ConversationSession` + `AssistantSpeechBridge` → `NexaSpeechPlanner` →
`NexaSpeechContinuityController` (default 2.0) → `TimedPiperHttpTTSService`
→ `TtsStatusObserver`. Only mic/STT is scripted (`TranscriptionResult`
fed directly). Warm-up "Ile jest osiem razy siedem?" run first and
excluded. Two configs: `ResponseMode.TEXT` (baseline) then
`ResponseMode.VOICE`. Raw: `b33_ab_raw_20260907.txt`.

Canonical answers (VOICE):

- **A** "Czarna dziura to obszar czasoprzestrzeni, w którym grawitacja
  jest tak silna, że nic, nawet światło, nie może się z niej wydostać.
  Powstają one zazwyczaj w wyniku kolapsu bardzo masywnych gwiazd."
  *(2 sentences — identical to TEXT; already within policy)*
- **B** "Gwiazda składa się głównie z gazu, głównie wodoru i helu. Te
  elementy poddawane są procesom termojądrowym, które generują energię i
  utrzymują gwiazdę w równowadze." *(2 sentences; TEXT was 248 chars)*
- **C** "Hel nie »powstaje« w sensie chemicznej reakcji, ale jest
  produktem fuzji wodoru. W jądrze gwiazdy, pod wpływem ogromnego
  ciśnienia i temperatury, wodór łączy się, tworząc jądra helu. Proces
  ten nazywa się cyklem proton-proton." *(3 sentences, prose; TEXT was a
  3-paragraph mini-lecture with `**bold**`)*
- **D** "Sen jest kluczowy dla regeneracji fizycznej i psychicznej
  organizmu. Pozwala mózgowi na konsolidację pamięci, oczyszczenie się z
  toksyn oraz regulację hormonów." *(2 sentences; TEXT was intro + 4-item
  numbered list + conclusion, 643 chars)*
- **E (detail)** "Powstawanie czarnej dziury to proces kolapsu bardzo
  masywnej gwiazdy. Gdy gwiazda zużyje swoje paliwo jądrowe, przestaje
  generować ciśnienie wystarczające do równoważenia własnej grawitacji.
  W efekcie siły grawitacyjne powodują, że cała materia gwiazdy zapada
  się w coraz mniejszy punkt. Jeśli pozostała po kolapsie masa przekracza
  granicę Tolmana-Oppenheimera-Volkoff, nic nie jest w stanie zatrzymać
  tego zapadania…" *(5 sentences / 522 chars — detailed, uncut)*

Answers to the task's four questions:

1. **Is a normal voice question now ~1–3 natural sentences by default?**
   Yes — `2 / 2 / 3 / 2` sentences, all flat prose, on the four questions
   asked without "krótko". (Baseline TEXT: `2 / 2 / 5 / 11`.)
2. **Is the first sentence shorter?** Yes on B (113→57), C (125→80),
   E (210→69); A unchanged (same answer); D already short.
3. **Did first-audio improve?** Yes on 4 of 5 (B ‑8.0 s, C ‑4.0 s,
   E ‑16.4 s; A flat; D +2 s but its TEXT first line was a throwaway).
4. **Are normal short answers gapless or substantially smoother?**
   Substantially smoother — B gapless (was 6.7 s), D 2.9 s (was 41.6 s),
   C 5.0 s (was 5.7 s). Long-answer gaps (E) remain, as expected at
   `realtime_text_ratio ≈ 0.53`.

**Detail-override hardware check:** "Wyjaśnij dokładnie, jak powstaje
czarna dziura." under `ResponseMode.VOICE` → 5 sentences / 522 chars,
technical, **no artificial 1–3 sentence clipping**; canonical answer
complete up to the pre-existing `num_predict = 200` ceiling. The
remaining long-answer gaps are the known rate deficit, not a B.3.3
failure.

Not operator-confirmed — awaiting the operator's own live voice session
(incl. an English turn).

## TEST RESULTS

`tests/test_conversation_response_mode.py` — **17 deterministic offline
tests** (`FakeModelProvider`; no Ollama, no TTS, no mic), all green,
`ruff` clean:

| # | test | proves |
|---|---|---|
| 1 | `test_1_voice_mode_injects_the_voice_directive_once_after_persona` | VOICE adds the directive once, at index 1 |
| 2 | `test_2_text_mode_is_unchanged_from_pre_b33` | `default == TEXT ==` exact pre-B.3.3 wire list |
| 3 | `test_3_same_session_serves_both_modes` | one session, one history, directive only on the VOICE call |
| 4 | `test_4_voice_directive_is_never_stored_in_history` | no history turn is/contains the directive |
| 5 | `test_5_assistant_canonical_response_stored_unchanged` | `history[1]` == exact streamed model output |
| 6 | `test_6_language_directive_still_follows_every_user_turn_in_voice_mode` | per-turn PL/EN directive still canonical; directive names no language |
| 7 | `test_7_polish_voice_request_works` | PL: directive @1 + `language_directive("pl")` |
| 8 | `test_8_english_voice_request_works` | EN: directive @1 + `language_directive("en")` |
| 9 | `test_9_expresses_concise_default` | "1–3 zdania"/"1–3 sentences" + "pierwsze zdanie"/"first sentence" |
| 10–12 | `test_10_11_12_expresses_detail_override_not_a_hard_clip` | detail/steps/list/comparison/longer triggers present; **no** cap words |
| 13 | `test_13_response_mode_module_has_no_speech_planner_or_tts_dep` | `response_mode.py` imports nothing TTS/planner/pipecat |
| 14 | `test_14_conversation_package_never_imports_tts_or_pipecat` | whole `nexa.conversation` stays TTS-free |
| 15 | `test_15_no_second_session_persona_or_history_authority` | AST: one class (the enum), imports ⊆ `{__future__, enum}`, no session/history/persona field |
| 16 | `test_voice_directive_is_a_constant_fixed_position_string` | KV-cache discipline: identical string, identical index, every build |
| 17 | `test_voice_adapter_still_only_calls_session_send` | adapter imports the `response_mode` submodule only; no `ConversationContext`/`language_directive`/`detect_response_language` |
| + | `test_options_and_persona_unchanged_by_mode`, `test_language_neutral_wrt_response_language` | `GenerationOptions` + persona identical across modes; directive ≠ either language directive |

Full suite (repo `.venv`):

- **`pytest tests/ -q` → 433 passed, 7 skipped, 14 subtests** (was
  416/7/14 pre-B.3.3 — `+17` new, no regression).
- **`python -m unittest discover -s tests` → 440 OK, 7 skipped** (was
  423/7).
- `ruff check src tests apps scripts/setup_piper_http.py` — clean.
  `git diff --check` — clean.
- All B.2/B.2A planner tests, B.3.1 tests, B.3.2 continuity tests,
  `test_voice_conversation_architecture.py` — still green, unmodified.

## FILES CHANGED

| file | change |
|---|---|
| `src/nexa/conversation/response_mode.py` | **new** — `ResponseMode` `StrEnum` + `voice_response_directive()` (one constant bilingual `system` message) |
| `src/nexa/conversation/__init__.py` | export `ResponseMode`, `voice_response_directive` |
| `src/nexa/conversation/context.py` | `to_provider_messages(*, response_mode=TEXT)`; insert the directive at index 1 for `VOICE`; `TEXT` path unchanged |
| `src/nexa/conversation/session.py` | `send(..., response_mode=TEXT)` → passed straight to `to_provider_messages`; no new dataclass field |
| `src/nexa/voice_conversation/adapter.py` | `__init__(..., response_mode=ResponseMode.VOICE)`; `session.send(text, response_mode=self._response_mode)` |
| `apps/nexa_voice_tts_probe.py` | `--response-mode {text,voice}` (default `voice`); pass `response_mode` to the adapter; status print |
| `tests/test_conversation_response_mode.py` | **new** — 17 tests |
| `docs/reports/R0020_…md` | **new** — this report |
| `docs/research/m2_4b_speech_flow/b33_ab.py` + `b33_ab_raw_20260907.txt` | **new** — scripted hardware A/B harness + raw |
| `docs/research/m2_4b_speech_flow/README.md` | B.3.3 note |
| `docs/CURRENT_STATE.md` | B.3.3 done; latest report → R0020; next-task update |

Not changed: `nexa.tts` (Piper `nice`/timeout), the speech planner, the
B.3.2 continuity controller and its thresholds, `nexa.stt`,
`nexa.providers`, `nexa.voice`, the persona JSON, `pyproject.toml`, the
frozen M2.4 path. No `gemma4:e4b`, `num_predict`, model routing, STT, TTS
voice, `length_scale`, filler, half-duplex-gate, or M2.5 change. No
post-generation truncation.

## COMMIT HASH

One coherent local commit — `feat: add conversational voice response
policy` (tip of `main`; see `git log -1`).

## GIT STATUS

Branch `main`, working tree clean after the commit, **12 commits ahead of
`origin/main`, not pushed**. `git diff --check` clean; no model/voice
binaries staged.

## NEXT STEP

Operator voice-session confirmation of B.3.3 (incl. one English turn and
a "rozwiń" / "tell me more" follow-up). Then the **~2× faster-generation /
model-serving track** (R0018's asymptotic fix — the only thing that makes
a *long* voice answer continuous) and/or **M2.5 — barge-in**, replacing
the temporary half-duplex gate. Non-blocking: `num_predict = 200` review
(it truncated both TEXT-mode long answers mid-word), Ollama `keep_alive`,
STT quality (R0016). **Do not benchmark or change the model yet. Do not
start M2.5 yet. Not pushed.**

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

R0018 stayed the authority: this stage is explicitly *reply shape*, the
`realtime_text_ratio ≈ 0.53` deficit is stated as unchanged and the
surviving long-answer gaps are labelled expected, not fixed. The
one-brain rule (AGENTS.md §3.2/§3.3, ADR-0002 D1, ADR-0003 D2) is
enforced by AST tests, not just prose. The transient hint follows the
R0009 KV-cache discipline. No post-generation truncation. No gap exposed.
