# R0027 — M2.4B.5B: Response Language Override vs Sticky Preference

- **Date:** 2026-09-08
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.5B — correct the
  `ResponseLanguageResolver` one-turn-vs-sticky semantics** (small
  follow-up to R0025).
- **Status:** **DONE — semantics corrected, 18 new deterministic tests,
  full suite green.** No production behaviour outside the resolver
  changed. Not pushed.
- **Related:** `R0025` (introduced `ResponseLanguageResolver`; its
  original "every explicit request is sticky" is superseded here), `R0026`
  (the stability fix — untouched), `ADR-0003` Amendment 1 (the
  `InputSpeechLanguage ≠ ResponseLanguage` split it names is now
  implemented strictly).

---

## TASK RESULT

**PASS.** `ResponseLanguageResolver` now draws a strict line between a
**one-turn override** (this reply only, `ResponsePreference` untouched)
and a **sticky command / switch** (mutates `ResponsePreference.sticky`).
`R0025`'s behaviour — where *any* explicit request became sticky — is
replaced. Everything else (STT, `LanguageIdGuard`, whisper model, LLM,
serving config, warm-up, Piper, planner, continuity, the B.5A
half-duplex fix, `ResponseMode.TEXT`) is unchanged.

## OPERATOR ACCEPTANCE STATUS

- **M2.4B.5** — **OPERATOR-CONFIRMED (2026-09-08)** for normal bilingual
  PL/EN live voice operation: automatic PL↔EN switching worked, normal
  speaking voice transcribed correctly, STT stayed **~2.89–3.21 s**, no
  progressive 7–10 s slowdown, no hang/backlog, max concurrent STT = 1,
  max concurrent turns = 1, final STT queue depth = 0, final conversation
  queue depth = 0.
- **M2.4B.5A** — **OPERATOR-CONFIRMED** for normal post-fix live
  stability (same session).
- **TV-stress test** — **NOT PERFORMED / OPERATOR WAIVED.** The
  deterministic (`tests/test_bilingual_voice_stability.py`) + headless
  (`b5a_contention_probe.py`) busy-drop / contention evidence in R0026
  stands; a TV-stress *pass* is **not** claimed.
- **Low-volume note** — recognition quality can degrade when the operator
  speaks too quietly; normal speaking volume was accepted by the
  operator. **Not** an STT regression, **no** SpeechQualityGuard track.

## ONE-TURN OVERRIDE

A plain imperative about *this* answer:

> "Answer in English." · "Reply in English." · "Odpowiedz po angielsku." ·
> "Odpowiedz po polsku." · "Answer in Polish." · "Speak English."

- `detect_language_request(text)` → `LanguageRequest(language, kind="one_turn")`.
- `resolve(...)` → `ResponseLanguage = requested` **for this turn only**;
  `ResponsePreference.sticky` is **not** touched (`preference_changed =
  False`).
- The next normal turn resumes rule 1: sticky if one exists, else mirror
  `InputSpeechLanguage`.

```
speak PL "Odpowiedz po angielsku."   -> ResponseLanguage = en,  sticky = None
speak PL "Jak działa komputer?"      -> ResponseLanguage = pl   (mirror; sticky still None)
```

## STICKY PREFERENCE

A command with a "from now on / henceforth / always / od teraz / od tej
pory / na stałe / zawsze" marker:

> "From now on speak English." · "From now on answer in English." ·
> "Od teraz mów po angielsku." · "Od teraz odpowiadaj po angielsku."

- `LanguageRequest(language, kind="sticky")`.
- `resolve(...)` → **sets** `ResponsePreference.sticky = language`
  (`preference_changed = True` when it actually changed); this and all
  future turns use it until changed.

```
speak PL "Od teraz mów po angielsku." -> ResponseLanguage = en,  sticky = en
speak PL "Jak działa komputer?"       -> ResponseLanguage = en   (sticky)
```

### Sticky language switch

"switch / go back / return to <lang>" is inherently a mode change → also
sticky:

> "Wracamy do polskiego." · "Let's go back to Polish." ·
> "Switch back to Polish." · "Switch to English."

```
sticky = en, speak EN "Wracamy do polskiego." -> ResponseLanguage = pl, sticky = pl
sticky = pl, speak EN "Why is the sky blue?"  -> ResponseLanguage = pl  (sticky)
```

## CONTENT-MENTION SAFETY

The detector returns `None` (no preference change, just mirror input) for
utterances that merely *name* a language:

> "Tell me about Polish history." · "What is the English word for kot?" ·
> "Why is Polish difficult?" · "Translate this English sentence." ·
> "What does this Polish word mean?" · "Explain Polish grammar to me." ·
> "I am learning English." · "How do you say 'thank you' in Polish?"

Mechanism: a question-lead deny-list (`how do you say…`, `what is/are/does…`,
`why is/are…`, `jak się mówi…`, `co znaczy…`), a `translate…` /
`przetłumacz…` lead, and directive patterns that require an *instruction
verb* (`answer/reply/respond/speak/talk/write/say`,
`odpowiedz/odpowiadaj/mów/powiedz/pisz/napisz`) adjacent to the language.
A bare noun phrase ("Polish history", "the English word") matches nothing.

## ARCHITECTURE

Three concepts, never merged:

| concept | owner | lifetime |
|---|---|---|
| **InputSpeechLanguage** | `BilingualSpeechTranscriber` + `LanguageIdGuard` | per utterance |
| **ResponseLanguage** | `ResponseLanguageResolver.resolve()` return | per turn |
| **ResponseLanguagePreference** (`ResponsePreference.sticky`: `None`\|`pl`\|`en`) | `ResponseLanguageResolver` | transient session state — **not** a turn, **not** memory |

Resolution order in `resolve(transcript, *, input_language)`:

1. `detect_language_request` → `sticky` → set `preference.sticky`, use it.
2. `detect_language_request` → `one_turn` → use requested language, **do
   not** touch `preference`.
3. `preference.sticky` set → use it (regardless of `input_language`).
4. else → mirror `input_language`.

The resolver mutates exactly one field, in exactly one branch
(`self.preference.sticky = …` on the sticky branch — asserted by an
AST/text test). It imports nothing conversation-history-shaped; the LLM is
never the routing authority. Threading to the model is unchanged from
R0025: `ConversationSession.send(..., response_language=…)` →
`ConversationContext` emits `language_directive(response_language)` for the
current turn, historical turns keep their stored resolved language
(replay-stable, R0009); `None` / typed chat is byte-for-byte unchanged.

**API:** new `detect_language_request(text) -> LanguageRequest | None`
(`LanguageRequest(language, kind)`); `ResponseLanguageDecision` gains
`request_kind` (`None` | `"one_turn"` | `"sticky"`);
`detect_explicit_language_request` kept as a thin back-compat wrapper
returning just the language.

## TEST RESULTS

`tests/test_response_language_override_vs_sticky.py` — **18 new**,
deterministic, offline:

| # | assertion | ✓ |
|---|---|---|
| 1 | "Odpowiedz po angielsku." from PL → ResponseLanguage=en, sticky None | ✓ |
| 2 | next PL turn → ResponseLanguage=pl | ✓ |
| 3 | "Answer in Polish." from EN → ResponseLanguage=pl, sticky None | ✓ |
| 4 | next EN turn → ResponseLanguage=en | ✓ |
| 5 | "Od teraz mów po angielsku." → sticky=en (`preference_changed`) | ✓ |
| 6 | next PL turn → ResponseLanguage=en | ✓ |
| 7 | "Wracamy do polskiego." → sticky=pl | ✓ |
| 8 | next EN turn → ResponseLanguage=pl | ✓ |
| 9 | 8 ordinary language mentions → no preference change, mirror input | ✓ |
| 10 | `ResponseMode.TEXT` behaviour byte-for-byte unchanged | ✓ |
| 11 | one `ConversationSession` / provider / model across PL+EN turns | ✓ |
| 12–14 | `DEFAULT_LOCAL_MODEL=gemma4:e4b`, `num_thread=2`, `keep_alive=30m` | ✓ |
| 15 | `warm_up_session` importable / unchanged | ✓ |
| 16 | `LanguageIdGuard` thresholds (`0.60`, `2.0 s`) unchanged | ✓ |
| 17 | `HalfDuplexGate` B.5A fix (`response_in_flight` on dispatch) intact | ✓ |
| + | one-turn override never mutates preference (×5 repeats); input≠response≠preference; from-now-on / switch variants; reply/answer are one-turn | ✓ |

Updated for the corrected semantics:
`tests/test_bilingual_voice_input.py` —
`test_explicit_one_turn_request_switches_and_sets_sticky` →
`test_one_turn_override_switches_this_turn_only_no_sticky`; the adapter
"input ≠ response" test now uses a **sticky** command ("Od teraz mów po
angielsku.") instead of the one-turn "Answer in English.".

Full suite: **`pytest` 587 passed / 7 skipped / 14 subtests**;
**`python -m unittest discover -s tests` 594 OK / 7 skipped**;
**`ruff check src tests apps docs/research/m2_4b_bilingual_stt`** clean;
**`git diff --check`** clean.

## FILES CHANGED

- `src/nexa/conversation/response_language.py` — `LanguageRequest`,
  `detect_language_request`, sticky-marker + switch-phrase regexes,
  `ResponseLanguageDecision.request_kind`, corrected `resolve()`;
  `detect_explicit_language_request` now a wrapper.
- `src/nexa/conversation/__init__.py` — exports `LanguageRequest`,
  `detect_language_request`.
- `tests/test_response_language_override_vs_sticky.py` (new, 18).
- `tests/test_bilingual_voice_input.py` — 2 tests updated for the
  corrected semantics.
- `docs/reports/R0025_…md`, `docs/reports/R0026_…md` (status +
  one-turn/sticky sections), `docs/reports/R0027_…md` (this),
  `docs/CURRENT_STATE.md`.

**Unchanged:** `gemma4:e4b`, `num_thread=2`, `keep_alive=30m`, warm-up,
`ResponseMode`, STT / `BilingualSpeechTranscriber` / `LanguageIdGuard` /
whisper model, Piper voices+speed, `SpeechPlanner`, continuity
controller, the M2.4B.5A `HalfDuplexGate` fix, one `ConversationSession` /
provider / model. No `nexa.config` / `nexa.bootstrap` change. M2.5 not
started.

## COMMIT HASH

`01cdf37` — `fix: one-turn response-language override vs sticky preference (M2.4B.5B / R0027)` (tip of `main`). Not pushed. (This hash-record edit lands in the next commit.)

## GIT STATUS

Branch `main`, not pushed. `ruff` clean for this stage's scope;
`git diff --check` clean.

## NEXT STEP

**M2.5 — barge-in / interruption** (replaces the temporary
`HalfDuplexGate`). Not started in this run. Separate/optional later:
Polish transcript-quality model track (`large-v3-turbo-q5_0`, operator
approval before download); a within-utterance code-switch stage if it
proves to matter; tune the `LanguageIdGuard` threshold from accumulated
per-turn telemetry.
