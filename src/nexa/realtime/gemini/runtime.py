"""``GeminiVoiceRuntime`` — the production HYBRID audio wiring for cloud
realtime voice (M2.6B.3/M2.6B.3A, ADR-0004 Decisions B/C/H).

Wires the accepted, unchanged local-hardware components (reSpeaker input,
XVF3800 AEC far-end reference, Silero VAD as the local turn authority,
``BargeInController`` as the local barge-in authority) to the headless
``GeminiLiveProvider`` + ``ConversationRouter`` built in M2.6B.1/.2/.2A —
never a parallel architecture, never a second AEC/barge-in implementation.

Two independent Pipecat pipelines run side by side, bridged only by plain
async calls / an event queue — never by sharing frame-processor state:

* the provider's OWN headless pipeline (built inside
  ``GeminiLiveProvider.start()`` — aggregators + the Gemini service, no
  transport of its own, exactly as in M2.6B.2);
* this module's HARDWARE pipeline: ``[transport.input(), vad_processor,
  bargein_controller, _VadToProviderBridge, aec_feeder, transport.output()]``
  — real mic capture, local Silero VAD (server VAD stays OFF on the
  Gemini side, per the frozen M2.6A baseline), the existing
  ``BargeInController`` (unmodified, same class as the local voice path),
  and the existing ``AecReferenceFeeder`` (unmodified) teeing whatever
  audio reaches ``transport.output()`` to the XVF3800 far-end reference —
  identical mechanism to Piper's local TTS output, just fed Gemini's PCM
  instead.

``_VadToProviderBridge`` is the one new piece: it forwards
``VADUserStartedSpeakingFrame`` / ``InputAudioRawFrame`` /
``VADUserStoppedSpeakingFrame`` into ``GeminiLiveProvider.user_turn_start``
/ ``send_user_audio`` / ``user_turn_end`` — and only forwards audio to the
provider *while a locally-detected turn is open*, so the cloud never sees
audio local Silero didn't bracket as speech (ADR-0004 Decision C: local
Silero is the turn authority, not Gemini's own — disabled — server VAD).
It also observes ``BotStartedSpeakingFrame``/``BotStoppedSpeakingFrame``
(the real output-transport playback lifecycle, travelling upstream from
``transport.output()``) and feeds them to ``_ResponseLifecycle`` — see
M2.6B.3A below. It never holds a raw ``GeminiLiveProvider`` reference
directly; it holds a ``_ProviderHandle`` box so a mid-turn fresh-session
swap (M2.6B.3A §3) reaches it immediately.

Cloud audio is injected into the SAME hardware pipeline via
``worker.queue_frames([TTSAudioRawFrame(...)])`` from a background task
that consumes ``provider.events()`` — exactly the same injection mechanism
(``PipelineWorker.queue_frames``) already used for the one-time
``LLMRunFrame`` kickoff (R0031/M2.6B.2), so it reaches ``aec_feeder`` and
``transport.output()`` like any other TTS audio.

Local barge-in wiring: ``BargeInController.on_confirmed`` -> stop is
already handled by Pipecat's own ``broadcast_interruption()`` (called
internally by ``BargeInController`` — unchanged); the hook here additionally
tells the ROUTER (``router.on_interruption()``) and the PROVIDER
(``provider.cancel()``) — local speaker-stop authority never waits for
either.

M2.6B.3A — three production-significant seams hardened after R0035, before
any real Gemini/hardware operator run (see
``docs/reports/R0036_m2_6b_3a_pre_live_hardening_20260911.md``):

1. **Generation-complete is not playback-complete.** ``_ResponseLifecycle``
   is the cloud analogue of ``nexa.voice.gate.HalfDuplexGate``'s own
   generation+playback combinator: ``bargein.notify_response_finished()``
   now fires only once BOTH the model's own generation is complete AND
   real ``BotStoppedSpeakingFrame`` playback truth confirms every
   already-queued chunk has drained (never a timer). A ``TTSStoppedFrame``
   is injected into the hardware pipeline, in FIFO order right after every
   audio chunk of a generation, so that confirmation is deterministic
   rather than depending on Pipecat's multi-second silence-fallback timer
   (installed source: ``BaseOutputTransport`` only fires
   ``BotStoppedSpeakingFrame`` off a genuine ``TTSStoppedFrame`` — if the
   audio actually arrived — or, absent one, a ``BOT_VAD_STOP_FALLBACK_SECS``
   (3s) silence timeout).
2. **Interrupted-turn assistant text is never trusted from raw
   transcription.** The installed Pipecat source
   (``gemini_live/llm.py:_handle_msg_output_transcription``) documents,
   verbatim, that Gemini's own output-transcription messages can arrive
   *before*, and contain *more text than*, the corresponding audio — "on an
   interruption our recorded context will contain some text that was
   actually never spoken" is the library's own comment. So
   ``CloudTurnAccumulator.assistant_text`` is never trusted directly as an
   interrupted turn's prefix.

   M2.6B.3A first tried a one-audio-chunk-lag "high-water" mechanism
   (promote text-as-of-the-previous-chunk once a new chunk arrives).
   **M2.6B.3B (see
   ``docs/reports/R0037_m2_6b_3b_interrupted_cloud_history_safety_20260911.md``)
   found that mechanism still overclaimed**: a later chunk's mere
   existence proves nothing about how much of an *earlier* text snapshot
   that chunk's own audio actually covers (the audio could be "abc" while
   the snapshot already reads "abcdef ghijkl..."), so it could still credit
   unspoken future text. A further source check (``google.genai.types.
   Transcription.words`` / ``WordInfo.start_offset``/``end_offset`` DOES
   exist in the underlying SDK's schema) found Pipecat's installed
   ``_handle_msg_output_transcription`` **never reads or forwards
   ``words``** — only the concatenated ``.text`` reaches any frame NeXa's
   provider can see — so no real, already-wired alignment data is
   reachable through this stack without bypassing Pipecat's own service
   entirely (out of scope; ADR-0004 already forbids a second raw Gemini
   client).

   **Conclusion: no deterministic alignment exists in the integrated
   stack. v1 rule, fully conservative:** an interrupted cloud turn's
   assistant text is always empty — ``router.set_spoken_prefix("")`` is
   called unconditionally on every confirmed interruption, then
   ``router.on_interruption()``. Under-crediting (committing no assistant
   text for an interrupted reply that did partly play) is acceptable;
   crediting text that was never actually spoken is not. Normal,
   non-interrupted completions are entirely unaffected — they still store
   the full final assistant transcription via ``GenerationCompleteEvent``,
   exactly as before.
3. **Mid-turn fresh-session recovery is actually driven.**
   ``ConversationRouter.recover_from_mid_turn_loss()`` existed since R0035
   but had no caller. ``_consume_provider_events`` now polls
   ``provider.needs_fresh_session`` after every event; once true it stops
   reading the OLD provider's queue (single-consumer preserved — never a
   second concurrent reader), invokes the recovery path, and — on success —
   atomically swaps ``self.provider`` **and** the shared ``_ProviderHandle``
   the VAD bridge reads from, then resumes consuming the NEW provider's
   queue. The old, already-``stop()``'d provider's queue is never read
   again, so it can never commit a late turn.

M2.6B.4 (R0038) — the FIRST real operator hardware/Gemini run FAILED with
three regressions, fixed here (see
``docs/reports/R0038_m2_6b_4_hardware_acceptance_attempt1_fail_20260911.md``):

1. **`_VadToProviderBridge` setup/cleanup crashed.** Root cause: Pipecat's
   own ``FrameProcessor.__init__`` already assigns
   ``self._metrics = metrics or FrameProcessorMetrics()`` and its
   ``setup()``/``cleanup()`` call ``self._metrics.setup(...)``/
   ``self._metrics.cleanup()`` (installed source,
   ``frame_processor.py:256,653,669``) — NeXa's own ``__init__`` stored
   ``RuntimeMetrics`` under that SAME attribute name, silently clobbering
   Pipecat's real metrics object, so Pipecat's own lifecycle went on to
   call ``.setup()``/``.cleanup()`` on NeXa's telemetry instead. Fixed:
   renamed to ``self._nexa_metrics`` — never share an attribute name with
   any Pipecat-reserved one on a ``FrameProcessor`` subclass.
2. **Local playback did not stop on barge-in; the interrupted reply kept
   playing while the new one was already generating.**
   ``BargeInController.broadcast_interruption()`` (unmodified) only clears
   what ``BaseOutputTransport``'s own audio queue held at that instant —
   it does nothing about a provider event already sitting in
   ``provider.events()``'s own queue, or audio Gemini keeps generating for
   a moment after the cancel signal. Fixed with ``_ResponseGenerationGuard``:
   every ``AssistantAudioEvent`` is checked against the currently VALID
   response-generation id before it is ever handed to
   ``hw_worker.queue_frames()``; a confirmed local interruption
   invalidates the current generation immediately (synchronously, so
   there is no race on the single-threaded event loop) — "hard local
   output clear (``broadcast_interruption``) + generation invalidation
   (this guard)", never either alone.
3. **Native language mirroring (ADR-0004 Option A) failed live** — two
   English questions both answered in Polish. Reconstructed the exact
   production snapshot: ``apps/nexa_cloud_voice_app.py`` never passed
   ``language_preference`` to the snapshot builder (defaults to ``None``)
   and ``build_default_session()`` starts with empty history — so the
   snapshot IS neutral (no forced Polish bias anywhere in
   ``system_instruction``/``recent_turns``); this is a genuine Gemini
   native-mirroring reliability gap, not a NeXa-side bug, and activates
   ADR-0004's own documented Option-B fallback. Added optional, local,
   offline, same-turn PL/EN diagnostics — reusing the ALREADY-ACCEPTED
   local-voice mechanisms verbatim: ``nexa.stt.WhisperCppLanguageDetector``
   (R0024's ``argmax(p_pl, p_en)`` LID) and
   ``nexa.conversation.ResponseLanguageResolver`` (sticky preference set
   ONLY on an explicit directive, never from the language merely spoken).
   This updated ``self._recovery_language_preference`` for any FUTURE
   fresh-session snapshot (ADR-0004 Amendment 1 §2's own documented
   mechanism) and logged the charter's exact diagnostic keys. It did
   **not** retroactively steer the response Gemini was already
   generating for the CURRENT turn (native mirroring remained the active
   per-turn mechanism); no verified, low-risk same-turn steering
   primitive was found reachable through the installed stack without a
   live call to test it. **This whole diagnostic mechanism (the
   detector/resolver construction, the utterance-PCM correlation, and
   the background classification call) was REMOVED in M2.6B.4D (R0042,
   below)** — see that section for why and what replaced it (nothing;
   native mirroring is now the sole mechanism, with no local LID
   anywhere in the cloud path).

M2.6B.4C (R0041) — **PRODUCT DECISION: no local LID gate in the normal
cloud critical path.** M2.6A (the operator-confirmed spike, ``R0031``)
proved PL/EN native mirroring, switching, and barge-in all worked well
without any local LID. Attempt #1's language failure was NOT a clean
same-architecture experiment: it happened alongside the two real
integration bugs above. A source-level differential audit
(``docs/reports/R0041_...``) compared the spike against this module
line-by-line and found no concrete regression capable of explaining
EN->PL by itself — the one genuine wording difference (the cloud role
card's language-mirroring sentence had drifted from the spike's own
proven, more explicit "the language the user is currently speaking...if
explicitly asked...follow that request" framing to a terser "Mirror the
user's language") was restored (see ``nexa.realtime.snapshot.
CLOUD_ROLE_CARD``) — a low-risk alignment, not a proven fix, since this
cannot be verified without a live call. R0039/R0040's local-Whisper-LID
strict-routing research remains a **FALLBACK RESEARCH CANDIDATE ONLY**
(``ggml-tiny-q5_1``, R0040's DECISION GATE B) — not adopted here, and not
adopted unless a CLEAN retest (after this checkpoint's fixes, with no
setup/cleanup crash and no barge-in playback bug in the way) produces
REPEATED evidence that native mirroring is genuinely unreliable in
production. At the time of R0041, ``_analyze_turn_language`` (M2.6B.4/
R0038, above) remained wired as optional, fire-and-forget, offline
diagnostics. Also added: lightweight, non-blocking per-turn diagnostics
(``RuntimeMetrics.canonical_turn_committed``'s new
``user_transcript``/``assistant_transcript``/``provider_instance_id``
keyword args) built only from state the turn accumulator already held in
memory for the commit — no new I/O, no added latency, no raw audio. (The
``USER_TRANSCRIPT``/``ASSISTANT_TRANSCRIPT``/``PROVIDER_SESSION_ID``
diagnostics are unaffected by M2.6B.4D below and remain in place.)

M2.6B.4D (R0042) — **the fire-and-forget LID call itself is REMOVED from
the production cloud runtime**, tightening R0041's product decision. A
sleeping fake coroutine (R0041's own test) proves only that the event
loop does not *await* a slow detector — it does NOT prove that a REAL
whisper.cpp inference call (genuine CPU-bound native code, ~0.5-1.7s per
call per the R0039/R0040 benchmarks) has zero impact on Pipecat's own
scheduling, audio playback, AEC, VAD, or realtime response latency on a
resource-constrained Raspberry Pi, once actually exercised with real
audio instead of ``asyncio.sleep``. Per the operator's explicit
instruction — "normal cloud voice must run with ZERO local LID
inference" — this checkpoint removes, rather than merely fails-to-await:
``build_gemini_voice_runtime`` no longer constructs
``WhisperCppLanguageDetector``/``ResponseLanguageResolver`` at all (no
``import nexa.stt``, no CPU/RAM footprint, no construction-time whisper.cpp
model load); ``GeminiVoiceRuntime`` no longer has
``language_detector``/``language_resolver`` fields; ``_analyze_turn_language``
and ``RuntimeMetrics.language_diagnostics`` are deleted; ``_VadToProviderBridge``
no longer accumulates a parallel per-utterance PCM buffer (it was
accumulated ONLY to feed this now-deleted diagnostic — never used for
reconnect/mid-turn recovery, which is a structurally separate mechanism,
see below); ``_PendingUtteranceAudio`` is deleted (it had exactly one
caller, the deleted diagnostic). **Recovery buffering is unaffected**:
``GeminiLiveProvider.take_pending_audio()`` (``service.py``) — the
mechanism ``ConversationRouter.recover_from_mid_turn_loss`` actually
replays after a fresh-session swap — is a completely separate,
provider-internal ``UtteranceFramer``-backed buffer, never touched by
this checkpoint. Normal cloud voice now spends **zero** CPU on PL/EN
classification, holds ``activityEnd`` for **zero** milliseconds waiting
on language, and never changes provider on a language switch — Gemini
native mirroring (ADR-0004 Option A) is the sole language mechanism.
R0039/R0040's research (the ``ggml-tiny-q5_1`` benchmark, the downloaded
research models, the benchmark scripts/results) is **preserved
untouched** as fallback research only — nothing deleted from
``docs/research/``, only the now-dead production wiring in this module.

M2.6B.4H (R0046) — **``router.begin_cloud_turn()`` had NO production
caller at all until this checkpoint** (discovered during M2.6B.4G/R0045's
own audit: exhaustive ``grep`` found it only in test files). Every prior
M2.6A/M2.6B hardware run therefore never wrote a single cloud
conversation turn to canonical ``ConversationSession.history`` — audio
worked; canonical history did not. Fixed with the smallest state model
that avoids the overlap hazard R0045 identified (an interruption
CANDIDATE's own VAD start, before it is confirmed, must never abandon or
corrupt the still-open turn whose assistant reply it may be interrupting):
``_VadToProviderBridge`` now calls ``router.begin_cloud_turn()`` on every
``VADUserStartedSpeakingFrame`` **unless**
``router.has_turn_awaiting_assistant()`` is True (a NEW, minimal
``ConversationRouter`` method — True iff the current turn already has a
final user transcript but has not yet been committed, i.e. an assistant
reply is presumably in flight for it); ``_on_confirmed`` calls it
unconditionally, right after committing the just-interrupted turn, since
a confirmed interruption's own utterance is a continuation of an
ALREADY-open local VAD turn (no future ``VADUserStartedSpeakingFrame``
will ever arrive for it). ``CloudTurnAccumulator.on_user_transcription``
gained one companion guard (``cur.user_final`` in addition to
``cur.is_terminal``): once a turn's own user side is finalized it can
never be re-opened or overwritten — the second half of the same
protection, closing the window between a candidate's VAD start and a
confirmed/rejected outcome, during which the OLD (not-yet-quarantined)
provider could otherwise still deliver a stray transcript for the
candidate that would corrupt the currently open turn. After a confirmed
interruption, R0045's existing provider-instance isolation (the OLD
provider's ``events()`` queue is never read again) is what makes the
NEW provider the sole subsequent authority for the newly-opened turn's
transcription — R0046 adds no new mechanism for that half at all.
Audio ownership (R0045's active-utterance PCM buffer) and canonical
conversation-turn ownership (this section) are, and remain, distinct
mechanisms.

M2.6B.4J (R0049) — **R0048's real-hardware audio ingress parity audit
proved current production clips the first ``VAD_START_SECS`` (Pipecat's
own unmodified 0.2 s default) of every utterance**, both mechanically
(the installed ``pipecat`` source: ``VADProcessor`` forwards every frame
downstream before running VAD detection, so nothing is dropped upstream
of this module; ``_VadToProviderBridge`` itself only ever called
``send_user_audio()`` once ``VADUserStartedSpeakingFrame`` had already
fired) and empirically, on real reSpeaker hardware with real operator
speech (`docs/reports/R0048_...md`'s OPERATOR REAL HARDWARE FOLLOW-UP:
"Czarna dziura" arrived at the provider as "arna dziura"/"dziura"). The
accepted M2.6A spike never had this problem because its own Silero VAD
analyzer lived in the SAME Pipecat pipeline as ``GeminiLiveLLMService``,
whose own built-in preroll buffer (``user_audio_preroll_secs``) was
therefore continuously fed and auto-sized (to ``start_secs + 0.1 s``, via
a ``SpeechControlParamsFrame`` only a co-located ``VADController``
broadcasts) — current production's topology (VAD in a separate hardware
pipeline, turn boundaries delivered to the provider via explicit method
calls) left that SAME mechanism inside ``GeminiLiveLLMService``
permanently starved, never reimplemented, never removed — just never fed.

**Fix: `_VadToProviderBridge` now owns a bounded rolling PCM
pre-buffer**, restoring the exact M2.6A property (audio immediately
BEFORE local VAD confirms speech start is included in the user turn) with
the smallest possible change — no provider-boundary redesign, no
continuous-idle-audio streaming to Gemini, no second VAD instance, no
change to ``start_secs``/``stop_secs``/confidence/barge-in thresholds.
Reuses ``nexa.stt.utterance_buffer.UtteranceBuffer`` VERBATIM (unmodified
— the existing, already-tested, LOCAL-VOICE-proven "ring while idle,
linear buffer while capturing, exactly-once idempotent
start/stop" mechanism `nexa.voice.runtime` already depends on for the
SAME reason: M2.2's own pre-roll requirement) rather than reimplementing
an equivalent ring buffer — this import touches only a pure-Python,
zero-cost class (no whisper.cpp binding, no subprocess, no filesystem
access at import time — confirmed by direct source read of
``nexa/stt/__init__.py``'s own imports) and constructs no
``WhisperCppLanguageDetector``/``WhisperCppTranscriber``/
``BilingualSpeechTranscriber`` instance anywhere; R0042's actual,
mechanically-enforced guarantee ("`build_gemini_voice_runtime` must never
CONSTRUCT `WhisperCppLanguageDetector`" — see
``TestNoLocalLidInCloudRuntime``) is unaffected and re-verified.

M2.6B.4L (R0051) — **the pre-buffer's capacity was originally (R0049)
derived from Pipecat's own documented auto-sizing arithmetic
(``start_secs + a 0.1 s margin`` = 300 ms) — real hardware evidence
(``docs/reports/R0050_...md``) proved this insufficient**: a byte-exact
alignment of real operator captures showed the 300 ms preroll left
exactly 200 ms of the diagnostic's own pre-VAD-start window omitted,
and a plain PCM energy (RMS) analysis of that omitted window showed
real, rising acoustic energy in 8 of 10 real takes, beginning roughly
300-460 ms before VAD confirmation — consistent with a deeper source
audit showing ``start_secs`` alone (192 ms, from Silero's own 6×32 ms
chunk requirement) excludes Pipecat's own exponential volume-smoothing
lag (data-dependent, ~100-330 ms) and Silero's own model-internal
confidence timing. **This exact question was already answered once
before, empirically, for the IDENTICAL VAD mechanism** —
``nexa.stt.utterance_buffer``'s own docstring documents a real,
R0006-era measurement of confirmation delay at 288-352 ms against real
speech fixtures, which is why that module's own ``PRE_ROLL_MS`` default
is 500, not a Pipecat-derived 300. The pre-buffer's capacity is now
simply ``UtteranceBuffer``'s own canonical, already-validated
``PRE_ROLL_MS`` constant (imported directly, the SAME already-established
dependency edge R0049 introduced for the class itself — no new
layering, no second "500" defined anywhere else) — never a
NeXa/Pipecat-specific re-derivation of a number this codebase had
already measured correctly once. ``AUTOSIZED_PREROLL_MARGIN_SECS`` (the
now-proven-insufficient derivation) is removed entirely, not left
behind as a dead/misleading constant.

``_VadToProviderBridge`` no longer maintains its own separate
``_utterance_buffer`` bytearray at all: ``UtteranceBuffer`` now serves
BOTH roles through its own existing, unmodified state machine —
``append_audio()`` routes every frame to its bounded ring while idle, or
to its linear per-utterance buffer while "capturing" (i.e. R0045's own
active-utterance accumulation, needed for exact-once barge-in replay,
now IS the same buffer the preroll seeds). ``mark_speech_started()``
transfers ring ownership into that linear buffer atomically — "one
coherent source of truth," never two buffers with overlapping ownership.
On ``VADUserStartedSpeakingFrame``: the ring's current bytes (the
preroll — already containing the very frame that triggered VAD
confirmation, since ``VADProcessor`` forwards it before emitting the
marker) are read, ``mark_speech_started()`` transfers them into the
linear buffer, the canonical turn opens exactly as R0046 already
requires, then (if not quarantined) ``user_turn_start()`` is called
followed by exactly ONE ``send_user_audio(preroll)`` call for that
retained prefix — before any further live frame. On
``VADUserStoppedSpeakingFrame``: ``mark_speech_stopped()`` returns the
COMPLETE utterance (preroll + every live frame since) idempotently
(``b""`` on a duplicate/spurious stop) — R0045's sealed-utterance replay
path is completely unchanged; it already receives the FULL, now-correct
PCM without any modification to ``_replace_provider_after_bargein``
itself.

Construction only (``dry=True``): builds every object EXCEPT the audio
device / network — no ``pyaudio.PyAudio()``, no device index lookup, no
Gemini connection. This module has NOT been validated against real
hardware — see ``docs/reports/R0035_...``/``R0036_...``/``R0038_...``/
``R0041_...``/``R0042_...`` for exactly what remains for a real operator
run.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ...stt.utterance_buffer import PRE_ROLL_MS, UtteranceBuffer
from ...voice.aec import AecReferenceHealth
from ...voice.aec_gain import CoherentReferenceGain
from ...voice.bargein import BargeInController, InterruptContext
from ...voice.config import LocalAudioConfig
from ...voice.device import find_device_index
from ...voice_tts.aec_reference import AEC_REFERENCE_PCM, AecReferenceFeeder
from ..policy import ConversationPolicy
from ..provider import (
    AssistantAudioEvent,
    AssistantTranscriptionEvent,
    GenerationCompleteEvent,
    ProviderInterruptionEvent,
    ProviderReadiness,
    RealtimeProviderFailedError,
    UserTranscriptionEvent,
)
from ..router import ConversationRouter
from ..snapshot import CloudContextSnapshot
from .service import (
    INPUT_SAMPLE_RATE_HZ,
    OUTPUT_SAMPLE_RATE_HZ,
    GeminiLiveProvider,
)
from .voice import DEFAULT_VOICE_PREFERENCE

logger = logging.getLogger(__name__)


def _pipecat_hw_imports() -> dict[str, Any]:
    """Deferred import of the hardware-pipeline-only Pipecat symbols (the
    provider's own imports live in ``service._pipecat_imports``). Never
    called at module import time — this module still requires only the
    optional ``cloud-gemini`` extra at *use* time, same as ``service.py``."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        InputAudioRawFrame,
        TTSAudioRawFrame,
        TTSStoppedFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
    from pipecat.workers.runner import WorkerRunner

    return {
        "SileroVADAnalyzer": SileroVADAnalyzer,
        "VADParams": VADParams,
        "BotStartedSpeakingFrame": BotStartedSpeakingFrame,
        "BotStoppedSpeakingFrame": BotStoppedSpeakingFrame,
        "InputAudioRawFrame": InputAudioRawFrame,
        "TTSAudioRawFrame": TTSAudioRawFrame,
        "TTSStoppedFrame": TTSStoppedFrame,
        "VADUserStartedSpeakingFrame": VADUserStartedSpeakingFrame,
        "VADUserStoppedSpeakingFrame": VADUserStoppedSpeakingFrame,
        "Pipeline": Pipeline,
        "PipelineParams": PipelineParams,
        "PipelineWorker": PipelineWorker,
        "VADProcessor": VADProcessor,
        "FrameDirection": FrameDirection,
        "FrameProcessor": FrameProcessor,
        "LocalAudioTransport": LocalAudioTransport,
        "LocalAudioTransportParams": LocalAudioTransportParams,
        "WorkerRunner": WorkerRunner,
    }


class _ProviderHandle:
    """A tiny mutable box holding the CURRENTLY active ``GeminiLiveProvider``
    (M2.6B.3A §3). The VAD bridge holds this box, never a raw provider
    reference, so a mid-turn fresh-session swap
    (``GeminiVoiceRuntime._consume_provider_events``) reaches it the
    instant ``self.current`` is reassigned — the bridge can never keep
    sending audio to a dead, already-``stop()``'d provider instance.

    M2.6B.4G (R0045) — extended with the coordination fields the atomic
    provider-replacement flow needs, shared between the SYNCHRONOUS
    ``BargeInController`` confirm hook (``_on_confirmed``, in
    ``build_gemini_voice_runtime``) and the ASYNC ``_VadToProviderBridge``
    (which reads ``quarantined`` on every frame) and
    ``GeminiVoiceRuntime._consume_provider_events`` (which reads/clears
    ``replacement_requested``/``old_provider`` and awaits
    ``sealed_utterance_ready``). At most ONE atomic replacement is ever in
    flight at a time — ``BargeInController``'s own single-candidate
    invariant already guarantees no second interruption is admitted while
    one is being processed."""

    def __init__(self, provider: GeminiLiveProvider) -> None:
        self.current = provider
        #: True from the instant a confirmed local barge-in requests
        #: atomic replacement until the replacement completes (new
        #: provider active, replay done). While True,
        #: ``_VadToProviderBridge`` must NOT send ANY turn-I/O call to
        #: ``self.current`` for the open (interrupting) utterance — it
        #: accumulates into its own local buffer instead, so the
        #: interrupting utterance is delivered exactly once, later, as a
        #: single controlled replay — never partially live-streamed to
        #: the doomed OLD provider or a not-yet-ready NEW one.
        self.quarantined = False
        #: Set synchronously by ``_on_confirmed``; consumed (and cleared)
        #: by ``_consume_provider_events``, which performs the actual
        #: replacement. ``old_provider`` is a snapshot of ``current`` at
        #: THE INSTANT confirm fired (never read from ``current`` again by
        #: the replacement logic, which could otherwise race a later
        #: reassignment).
        self.replacement_requested = False
        self.old_provider: GeminiLiveProvider | None = None
        #: The interrupting utterance's sealed PCM, handed off by the
        #: bridge the instant its ``VADUserStoppedSpeakingFrame`` arrives
        #: while quarantined. A FIFO list (not a scalar) so a second local
        #: utterance beginning and sealing before the runtime has drained
        #: the first can never overwrite it — see the module docstring's
        #: M2.6B.4G section. ``sealed_utterance_ready`` is the companion
        #: ``asyncio.Event`` the runtime awaits (level-triggered: safe
        #: even if the seal happens before the runtime starts waiting).
        self.sealed_utterances: list[bytes] = []
        self.sealed_utterance_ready = asyncio.Event()
        #: Self-interruption fix (post-R0071 differential audit) — True
        #: from the instant ``BargeInController`` classifies a local VAD
        #: start as an interruption CANDIDATE (``response_in_flight`` was
        #: True) until it is either CONFIRMED (cleared by ``_on_confirmed``,
        #: which also sets ``quarantined``) or REJECTED (cleared by
        #: ``_VadToProviderBridge`` itself, in its own
        #: ``VADUserStoppedSpeakingFrame`` handler, the instant it observes
        #: this flag still set with ``quarantined`` still False — see that
        #: method's own comment for why no dedicated "rejected" signal from
        #: the controller is needed for correctness). While True, the
        #: bridge MUST NOT call ``user_turn_start``/``send_user_audio`` for
        #: this utterance — only ``UtteranceBuffer`` accumulates it — so a
        #: candidate BargeInController later rejects can never have already
        #: reached the provider as a real user turn. This is the exact
        #: invariant the R0071 differential audit found broken: raw VAD
        #: candidates were being forwarded to Gemini unconditionally,
        #: before ``BargeInController`` — the single interruption
        #: authority — had decided anything.
        self.candidate_pending = False


class _ResponseLifecycle:
    """Cloud analogue of ``nexa.voice.gate.HalfDuplexGate``'s own
    generation+playback combinator (M2.6B.3A §1): a response is finished
    — safe to call ``on_finished()`` — only once BOTH the model's own
    generation is complete AND, if any audio was actually produced, a real
    ``BotStoppedSpeakingFrame`` confirms every already-queued chunk has
    drained. Never a timer; never "generation complete" alone.

    Unlike ``HalfDuplexGate`` (a passive property queried on demand) this
    actively fires ``on_finished()`` exactly once per response, the moment
    the combined state settles — ``BargeInController.notify_response_finished()``
    is event-driven, not polled.
    """

    def __init__(self, *, on_finished: Callable[[], None]) -> None:
        self._generating = False
        self._produced_audio = False
        self._bot_speaking = False
        self._playback_confirmed_drained = False
        self._finished_fired = False
        self._on_finished = on_finished

    def mark_dispatched(self) -> None:
        """A new response has started generating."""
        self._generating = True
        self._produced_audio = False
        self._bot_speaking = False
        self._playback_confirmed_drained = False
        self._finished_fired = False

    def mark_audio_produced(self) -> None:
        """At least one ``AssistantAudioEvent`` was seen for this response
        (known directly from the provider event stream — never inferred
        from ``BotStartedSpeakingFrame``, which can lag real delivery by a
        full pipeline hop)."""
        self._produced_audio = True

    def mark_generation_done(self) -> None:
        self._generating = False
        self._maybe_fire()

    def observe_bot_started(self) -> None:
        self._bot_speaking = True

    def observe_bot_stopped(self) -> None:
        self._bot_speaking = False
        if not self._generating:
            self._playback_confirmed_drained = True
        self._maybe_fire()

    def mark_interrupted(self) -> None:
        """A confirmed local interruption ends this response's lifecycle
        immediately (``BargeInController.notify_interruption_complete()``
        is called directly by the caller) — suppress a stale
        ``on_finished`` from a generation-complete/bot-stopped signal that
        arrives later for the response that was just talked over."""
        self._generating = False
        self._bot_speaking = False
        self._finished_fired = True

    def _maybe_fire(self) -> None:
        if self._finished_fired or self._generating:
            return
        if self._produced_audio and not self._playback_confirmed_drained:
            return  # audio was produced; still waiting for real BotStopped truth
        if self._bot_speaking:
            return  # defensive; should not combine with the above
        self._finished_fired = True
        self._on_finished()


#: M2.6B.3B — no deterministic assistant-text/audio alignment is reachable
#: through the installed Pipecat/google-genai stack (see the module
#: docstring's §2 for the source evidence: ``WordInfo.start_offset``/
#: ``end_offset`` exist in the SDK's own type schema, but Pipecat's
#: installed ``_handle_msg_output_transcription`` never reads or forwards
#: them). An interrupted cloud turn's committed assistant text is
#: therefore always this constant — never a partial-credit approximation
#: built from audio-chunk count, which cannot logically prove how much of
#: an earlier transcription snapshot a later chunk's own audio covers.
#: Under-crediting is acceptable; crediting unspoken words is not.
CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX = ""


class _ResponseGenerationGuard:
    """M2.6B.4 (R0038 FAILURE 2) — the single source of truth for which
    local response generation may still produce audible output.

    Root cause this exists to close: ``BargeInController.broadcast_interruption()``
    (Pipecat's own mechanism, unchanged) clears only what
    ``BaseOutputTransport``'s audio queue held at the instant it ran
    (installed source: cancels/recreates the output audio task, or resets
    the queue) — it does nothing about a provider event still sitting in
    ``provider.events()``'s own queue, or audio Gemini keeps generating
    for a few moments after receiving the cancel signal. Confirmed live in
    the first real operator run: an interrupted reply's audio kept
    playing to completion while the NEW reply was already being
    generated. This guard is what stops those late frames from ever
    reaching ``hw_worker.queue_frames()`` in the first place, and (M2.6B.4G
    /R0045) it is what stops any trailing OLD-provider frame that squeezes
    through in the brief window between a confirmed barge-in and
    ``_consume_provider_events`` noticing the atomic-replacement request —
    "hard local output clear (``broadcast_interruption``) + generation
    invalidation (this guard) + provider-instance isolation (R0045)",
    never any one alone.

    Deliberately does NOT decide *when* a new generation is dispatched —
    an earlier draft tried to fold "the next assistant output needs a
    fresh dispatch" into this guard too, keyed off the same
    interrupt-vs-valid state, and that conflated two different signals:
    "may this chunk play" (this class) and "has a genuinely NEW local
    user turn started". Kept deliberately dumb: only
    ``is_valid``/``start_new_generation``/``interrupt``/``valid_id`` — the
    caller (``_consume_provider_events``) decides the dispatch boundary
    (a fresh, final ``UserTranscriptionEvent`` for an ordinary turn —
    R0034's proven message-ordering guarantee — or, after a confirmed
    barge-in, the NEW provider instance's own first assistant event, per
    M2.6B.4G/R0045 below).

    M2.6B.4E/F (R0043/R0044) added, then hardened, a second,
    NeXa/Gemini-independent fallback re-arm signal
    (``provider.local_turn_closed_seq`` advancing by 2) for the case a
    non-lexical interrupting sound produces no transcript at all —
    proven, by adversarial test, to still leave ONE residual gap (old
    audio arriving after a genuinely new turn's own closure,
    indistinguishable from real content by any LOCAL signal, since
    Gemini's Live API exposes no response/turn/generation identifier on
    any server message — verified from ``google.genai.types``).

    M2.6B.4G (R0045) — **that whole fallback mechanism is REMOVED here,
    superseded, not merely narrowed.** Its ONLY use case was "recover
    dispatch after a confirmed barge-in whose own turn produced no
    transcript" — R0045 now handles EVERY confirmed barge-in via ATOMIC
    PROVIDER REPLACEMENT: the OLD provider's ``events()`` queue is
    STOPPED being read entirely (never merely "trusted again after N
    closures"), and the interrupting utterance is replayed, exactly once,
    to a BRAND NEW provider instance whose first assistant event
    dispatches normally via the ORIGINAL (unmodified) mechanism below —
    no transcript, and no turn-closure counting, ever required. This is
    strictly stronger than the removed fallback (see
    ``docs/reports/R0045_...`` CASE 2 — the ONE scenario R0044 could not
    close is now provably closed, since there is no "old provider" left
    to produce ambiguous audio from at all). Connection-loss recovery
    (``needs_fresh_session``/``RealtimeProviderFailedError``) never used
    this fallback either (both already force ``dispatched_for_turn``
    False directly) and is completely unaffected by its removal.
    """

    def __init__(self) -> None:
        self._current_id = 0
        self._valid_id = 0

    def start_new_generation(self) -> int:
        self._current_id += 1
        self._valid_id = self._current_id
        return self._current_id

    def is_valid(self, generation_id: int) -> bool:
        return generation_id == self._valid_id

    @property
    def valid_id(self) -> int | None:
        """The currently-valid generation id, or ``None`` if none is
        (``0`` is never a real id — the counter starts at 1). Read-only
        observability accessor — never used to decide dispatch;
        ``is_valid`` remains the sole gate."""
        return self._valid_id or None

    def interrupt(self) -> None:
        """The CURRENT generation can never produce audible output again.
        Called synchronously from ``_on_confirmed`` (no ``await`` before
        it in that function), so this always completes before any other
        coroutine gets a chance to run on this single-threaded event
        loop — no race with ``_consume_provider_events``'s own check."""
        self._valid_id = 0  # 0 is never a real id (the counter starts at 1)


def _make_vad_bridge_class(P: dict[str, Any]) -> type:
    """The one new FrameProcessor: local VAD frames -> provider turn I/O,
    and real playback-lifecycle frames -> ``_ResponseLifecycle``. Built
    lazily (needs ``FrameProcessor``, only available post-import)."""

    class _VadToProviderBridge(P["FrameProcessor"]):
        def __init__(
            self,
            *,
            provider_handle: _ProviderHandle,
            metrics: RuntimeMetrics,
            lifecycle: _ResponseLifecycle,
            router: ConversationRouter,
            preroll_ms: int,
        ) -> None:
            super().__init__()
            self._handle = provider_handle
            self._router = router
            # M2.6B.4 (R0038 FAILURE 1) -- ``FrameProcessor.__init__`` itself
            # already sets ``self._metrics = metrics or FrameProcessorMetrics()``
            # (installed source, frame_processor.py:256) and its own
            # ``setup()``/``cleanup()`` call ``self._metrics.setup(...)``/
            # ``self._metrics.cleanup()`` (frame_processor.py:653/669) on
            # WHATEVER object holds that name. Storing NeXa's RuntimeMetrics
            # under the SAME attribute name silently clobbered Pipecat's own
            # ``FrameProcessorMetrics`` instance, so Pipecat's real lifecycle
            # went on to call ``.setup()``/``.cleanup()`` on NeXa's telemetry
            # object instead -- confirmed live in the first real operator
            # run ("'RuntimeMetrics' object has no attribute 'setup'"),
            # reproduced deterministically in
            # ``TestVadBridgeProcessorLifecycle``. RuntimeMetrics is NeXa
            # runtime telemetry, never a Pipecat processor-metrics object --
            # a distinctly NeXa-prefixed attribute name is required for
            # every NeXa-owned field on a Pipecat FrameProcessor subclass.
            self._nexa_metrics = metrics
            self._lifecycle = lifecycle
            # M2.6B.4G (R0045) + M2.6B.4J (R0049) -- ONE coherent PCM
            # buffer serves BOTH roles that used to be two separate,
            # overlapping-ownership fields: a bounded rolling pre-roll
            # ring while NO local turn is open, and the ACTIVE utterance
            # accumulation (R0045's own exact-once-barge-in-replay need)
            # while one is. ``UtteranceBuffer`` (``nexa.stt``, unmodified,
            # already the LOCAL-VOICE-proven mechanism for the SAME M2.2
            # pre-roll requirement) already implements exactly this
            # ring-while-idle / linear-while-capturing state machine, with
            # idempotent start/stop -- reused verbatim rather than
            # reimplementing an equivalent buffer. Its own internal
            # ``append_audio()`` call is UNCONDITIONAL and cheap (no I/O,
            # no inference -- NOT local language-ID; R0042's zero-LID
            # decision is unaffected, see the module docstring's M2.6B.4J
            # section for why this import is zero-cost), so a normal,
            # non-interrupted turn incurs zero added latency.
            self._pcm = UtteranceBuffer(sample_rate=INPUT_SAMPLE_RATE_HZ, pre_roll_ms=preroll_ms)

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["VADUserStartedSpeakingFrame"]):
                # M2.6B.4J (R0049) -- read the retained pre-roll BEFORE
                # ``mark_speech_started()`` transfers its ownership into
                # the linear (capturing) buffer. Because ``VADProcessor``
                # forwards every ``InputAudioRawFrame`` downstream BEFORE
                # running VAD detection on it (installed source,
                # confirmed in R0048), the very frame whose arrival
                # finally confirmed speech start is already IN this ring
                # by the time this marker frame reaches us -- restoring
                # the accepted M2.6A property that audio immediately
                # before local VAD confirmation is never lost.
                preroll = b"".join(self._pcm._ring)  # noqa: SLF001 - read-only snapshot
                self._pcm.mark_speech_started()
                self._nexa_metrics.local_vad_start()
                # M2.6B.4H (R0046) -- open exactly one canonical cloud turn
                # for a GENUINE new local user turn. ``has_turn_awaiting_
                # assistant()`` is the ONE local-authority signal that
                # blocks this: a local interruption CANDIDATE's own VAD
                # start (the user speaking while an assistant reply is
                # still in flight, not yet confirmed as an interruption)
                # must NOT call ``begin_cloud_turn()`` here -- doing so
                # would let ``CloudTurnAccumulator.start_turn()``'s own
                # "abandon the still-open current turn" rule discard the
                # turn that reply belongs to, even though it may yet
                # finish normally (candidate rejected) or be safely
                # committed first (candidate confirmed -- see
                # ``_on_confirmed``, which opens the NEW canonical turn for
                # the promoted interrupting utterance itself, since no
                # further ``VADUserStartedSpeakingFrame`` will ever arrive
                # for it). See the module docstring's M2.6B.4H section.
                if not self._router.has_turn_awaiting_assistant():
                    self._router.begin_cloud_turn()
                if self._handle.candidate_pending:
                    # Self-interruption fix -- BargeInController (which
                    # runs BEFORE this bridge in the pipeline and has
                    # therefore already processed this SAME frame) has
                    # classified this VAD start as an interruption
                    # CANDIDATE, not yet confirmed or rejected (its own
                    # ``on_candidate`` hook already set this flag,
                    # synchronously, before this frame was forwarded
                    # downstream). Buffer only -- ``mark_speech_started()``
                    # above already did that -- never tell the provider
                    # anything happened until ``BargeInController`` itself
                    # decides (see ``_on_confirmed`` and the
                    # ``VADUserStoppedSpeakingFrame`` branch below for the
                    # confirmed/rejected outcomes).
                    pass
                elif not self._handle.quarantined:
                    await self._handle.current.user_turn_start()
                    # M2.6B.4J (R0049) -- inject the retained prefix
                    # exactly ONCE, before any further live frame, in one
                    # call (never per-byte, never re-chunked).
                    if preroll:
                        await self._handle.current.send_user_audio(preroll)
            elif isinstance(frame, P["InputAudioRawFrame"]):
                # Unconditional: routes to the ring (idle) or the linear
                # capture buffer (open turn) via ``UtteranceBuffer``'s own
                # ``is_capturing`` state -- never a separate NeXa-owned
                # flag that could drift from it.
                self._pcm.append_audio(frame.audio)
                if (
                    self._pcm.is_capturing
                    and not self._handle.quarantined
                    and not self._handle.candidate_pending
                ):
                    await self._handle.current.send_user_audio(frame.audio)
            elif isinstance(frame, P["VADUserStoppedSpeakingFrame"]):
                self._nexa_metrics.local_vad_eot()
                # M2.6B.4J (R0049) -- the COMPLETE utterance (preroll +
                # every live frame since), idempotent (``b""`` on a
                # duplicate/spurious stop -- never leaks a previous
                # turn's audio forward).
                complete_utterance = self._pcm.mark_speech_stopped()
                if self._handle.quarantined:
                    # M2.6B.4G (R0045) -- CONFIRMED. Since the
                    # self-interruption fix, nothing for this utterance
                    # was ever sent live at all (candidate_pending kept
                    # it buffered-only from VAD start; ``_on_confirmed``
                    # then set ``quarantined`` for the remainder). Hand
                    # the COMPLETE sealed copy (preroll included) to the
                    # replacement flow instead of closing a turn on
                    # `current` (which, while quarantined, may still be
                    # the doomed OLD provider, or a not-yet-ready NEW one
                    # that must receive this as ONE coherent replay,
                    # never a live partial send).
                    self._handle.sealed_utterances.append(complete_utterance)
                    self._handle.sealed_utterance_ready.set()
                elif self._handle.candidate_pending:
                    # Self-interruption fix -- REJECTED. BargeInController
                    # never confirmed this candidate (VAD stopped before
                    # ``confirm_hold_secs`` elapsed -- its own
                    # ``on_candidate_rejected`` hook already fired,
                    # synchronously, before this frame was forwarded
                    # downstream, for observability only). Nothing was
                    # ever sent live to the provider for it (see the
                    # VADUserStartedSpeakingFrame/InputAudioRawFrame
                    # branches above), so there is nothing to retract and
                    # no ``user_turn_end()`` to send either -- the
                    # provider never learns this episode existed. Discard
                    # the buffered PCM silently; the in-flight assistant
                    # response is untouched.
                    self._handle.candidate_pending = False
                    self._nexa_metrics.candidate_rejected_discarded()
                else:
                    await self._handle.current.user_turn_end()
            elif isinstance(frame, P["BotStartedSpeakingFrame"]):
                self._lifecycle.observe_bot_started()
                self._nexa_metrics.first_assistant_audio_played()
                self._nexa_metrics.bot_started()
            elif isinstance(frame, P["BotStoppedSpeakingFrame"]):
                self._lifecycle.observe_bot_stopped()
                self._nexa_metrics.bot_stopped()
            await self.push_frame(frame, direction)

    return _VadToProviderBridge


@dataclass
class RuntimeMetrics:
    """Lightweight production metrics (ADR-0004 M2.6B charter, Phase 5) —
    logged only, no raw audio retained, no credential ever logged."""

    _first_audio_played_marked: bool = False

    def provider_readiness(self, readiness: ProviderReadiness) -> None:
        logger.info("nexa.realtime.metrics: provider readiness -> %s", readiness.value)

    def aec_reference(self, active: bool) -> None:
        logger.info("nexa.realtime.metrics: AEC reference %s", "ACTIVE" if active else "DOWN")

    def local_vad_start(self) -> None:
        logger.info("nexa.realtime.metrics: local VAD start (turn open)")

    def local_vad_eot(self) -> None:
        logger.info("nexa.realtime.metrics: local VAD end-of-turn")

    def input_transcription_final(self, text: str) -> None:
        logger.info(
            "nexa.realtime.metrics: input transcription final (%d chars)", len(text)
        )

    def first_assistant_audio_received(self) -> None:
        logger.info("nexa.realtime.metrics: first assistant audio received")

    def first_assistant_audio_played(self) -> None:
        # Fires once per BotStartedSpeakingFrame; only the FIRST one this
        # session is a distinct milestone worth its own line.
        if not self._first_audio_played_marked:
            self._first_audio_played_marked = True
            logger.info("nexa.realtime.metrics: first assistant audio played")

    def candidate_pending_opened(self) -> None:
        # Self-interruption fix -- observability only: BargeInController
        # classified a local VAD start as an interruption candidate; the
        # bridge is now buffering it, not forwarding it to the provider,
        # pending confirm/reject.
        logger.info("nexa.realtime.metrics: CANDIDATE_PENDING (buffered, not sent to provider)")

    def candidate_rejected_discarded(self) -> None:
        # Self-interruption fix -- the buffered candidate PCM was
        # discarded; the provider never learned this episode existed.
        logger.info("nexa.realtime.metrics: CANDIDATE_REJECTED_DISCARDED (never reached provider)")

    def local_interruption_confirmed(
        self,
        *,
        confirm_count: int | None = None,
        generation_id: int | None = None,
        bargein_state: str | None = None,
    ) -> None:
        # M2.6B.4E (R0043) -- the charter's exact LOCAL_BARGEIN_CONFIRMED
        # keys. confirm_count is a per-runtime monotonic counter (never
        # confused with PROVIDER_INTERRUPTION_ACK's own count -- ONE local
        # confirmation legitimately produces MULTIPLE provider acks, see
        # R0043's source audit; the two counters are deliberately kept
        # separate so this is directly observable, not assumed).
        logger.info(
            "nexa.realtime.metrics: LOCAL_BARGEIN_CONFIRMED confirm_count=%s "
            "generation_id=%s bargein_state=%s",
            confirm_count, generation_id, bargein_state,
        )

    def provider_interruption_ack(self, *, count: int | None = None) -> None:
        # M2.6B.4E (R0043) -- PROVIDER_INTERRUPTION_ACK. Now actually wired
        # (previously defined but never called from the consumer loop).
        # Confirmed by source audit: Pipecat's own `broadcast_interruption()`
        # emits TWO InterruptionFrame instances (upstream + downstream) per
        # call, and BOTH the provider's own `cancel()` AND Gemini's
        # independent `serverContent.interrupted` server-side ack each
        # trigger one such broadcast inside the provider's headless
        # pipeline -- so 4 acks from ONE confirmed local interruption is
        # EXPECTED Pipecat/Gemini behaviour, not a NeXa-side bug. See the
        # R0043 report for the full trace.
        logger.info(
            "nexa.realtime.metrics: PROVIDER_INTERRUPTION_ACK count=%s", count
        )

    def generation_invalidated(self, *, generation_id: int) -> None:
        logger.info(
            "nexa.realtime.metrics: GENERATION_INVALIDATED id=%d", generation_id
        )

    def output_interruption_broadcast(self, *, count: int) -> None:
        logger.info(
            "nexa.realtime.metrics: OUTPUT_INTERRUPTION_BROADCAST count=%d", count
        )

    def user_transcription_final_state(
        self, *, dispatched_for_turn: bool, generation_id: int, generation_valid: bool
    ) -> None:
        logger.info(
            "nexa.realtime.metrics: USER_TRANSCRIPTION_FINAL dispatched_for_turn=%s "
            "generation_id=%d generation_valid=%s",
            dispatched_for_turn, generation_id, generation_valid,
        )

    def assistant_response_dispatch(
        self, *, generation_id: int, triggering_event: str
    ) -> None:
        logger.info(
            "nexa.realtime.metrics: ASSISTANT_RESPONSE_DISPATCH generation_id=%d "
            "triggering_event=%s",
            generation_id, triggering_event,
        )

    def assistant_audio_received(
        self, *, generation_id: int, valid: bool, chunk_count: int, byte_count: int
    ) -> None:
        logger.info(
            "nexa.realtime.metrics: ASSISTANT_AUDIO_RECEIVED generation_id=%d "
            "valid=%s chunk_count=%d bytes=%d",
            generation_id, valid, chunk_count, byte_count,
        )

    def assistant_audio_hw_queued(self, *, generation_id: int, chunk_count: int) -> None:
        logger.info(
            "nexa.realtime.metrics: ASSISTANT_AUDIO_HW_QUEUED generation_id=%d "
            "chunk_count=%d",
            generation_id, chunk_count,
        )

    def bot_started(self) -> None:
        logger.info("nexa.realtime.metrics: BOT_STARTED")

    def bot_stopped(self) -> None:
        logger.info("nexa.realtime.metrics: BOT_STOPPED")

    def canonical_turn_committed(
        self,
        outcome: Any,
        generation: int | None,
        *,
        user_transcript: str | None = None,
        assistant_transcript: str | None = None,
        provider_instance_id: str | None = None,
    ) -> None:
        # M2.6B.4C (R0041) -- lightweight, non-blocking retest diagnostics:
        # the charter's exact keys, built ONLY from state the accumulator
        # already held in memory for this commit (CloudTurn.user_text /
        # .assistant_text, already-allocated generation id) -- no new I/O,
        # no added latency, no raw audio, no credential. provider_instance_id
        # is a process-local id (str(id(provider))), NOT a Gemini server
        # session id -- no true server-side session id is tracked anywhere
        # in this stack (see ProviderUsageEvent.session_id, same convention).
        logger.info(
            "nexa.realtime.metrics: canonical cloud turn committed outcome=%s "
            "generation=%s PROVIDER_SESSION_ID=%s USER_TRANSCRIPT=%r "
            "ASSISTANT_TRANSCRIPT=%r",
            outcome, generation, provider_instance_id or "unknown",
            user_transcript or "", assistant_transcript or "",
        )

    def reconnecting(self, reason: str) -> None:
        logger.info("nexa.realtime.metrics: reconnecting (%s)", reason)

    def resumed(self) -> None:
        logger.info("nexa.realtime.metrics: resumed")

    def mid_turn_recovery(self, *, succeeded: bool) -> None:
        logger.info(
            "nexa.realtime.metrics: mid-turn fresh-session recovery %s",
            "SUCCEEDED" if succeeded else "FAILED (fell back to LOCAL)",
        )

    def inbound_buffer_state(self, *, buffered: int, dropped: int) -> None:
        logger.info(
            "nexa.realtime.metrics: inbound buffer buffered=%d dropped=%d", buffered, dropped
        )

    def usage(self, event: Any) -> None:
        logger.info(
            "nexa.realtime.metrics: usage in_text=%s in_audio=%s out_text=%s out_audio=%s",
            event.input_text_tokens, event.input_audio_tokens,
            event.output_text_tokens, event.output_audio_tokens,
        )

    def dropped_invalidated_generation_audio(
        self,
        *,
        generation_id: int,
        reason: str = "generation_invalidated",
        valid_generation_id: int | None = None,
    ) -> None:
        # M2.6B.4 FAILURE 2 -- visibility into the generation guard
        # actually doing its job: a chunk belonging to an interrupted
        # generation was correctly kept off the hardware pipeline.
        # M2.6B.4E (R0043) -- the charter's exact ASSISTANT_AUDIO_DROPPED
        # keys (reason / generation_id / the currently-valid id, if any).
        logger.info(
            "nexa.realtime.metrics: ASSISTANT_AUDIO_DROPPED reason=%s "
            "generation_id=%d valid_generation_id=%s",
            reason, generation_id, valid_generation_id,
        )

    # -- M2.6B.4G (R0045) atomic-provider-replacement timing instrumentation --
    # Deterministic monotonic timestamps only (never wall-clock/PII, never raw
    # audio) -- for the LATER live acceptance run to measure: barge-in ->
    # old-playback-stop latency, barge-in -> new-provider-ready latency, and
    # whether the new connection's handshake is hidden under the time the
    # user keeps speaking (EOT -> first new audio).
    def bargein_confirmed_t(self, *, monotonic_s: float) -> None:
        logger.info("nexa.realtime.metrics: BARGEIN_CONFIRMED_T %.6f", monotonic_s)

    def old_audio_stop_t(self, *, monotonic_s: float) -> None:
        logger.info("nexa.realtime.metrics: OLD_AUDIO_STOP_T %.6f", monotonic_s)

    def replacement_start_t(self, *, monotonic_s: float) -> None:
        logger.info("nexa.realtime.metrics: REPLACEMENT_START_T %.6f", monotonic_s)

    def new_provider_ready_t(self, *, monotonic_s: float) -> None:
        logger.info("nexa.realtime.metrics: NEW_PROVIDER_READY_T %.6f", monotonic_s)

    def interrupting_utterance_end_t(self, *, monotonic_s: float) -> None:
        logger.info("nexa.realtime.metrics: INTERRUPTING_UTTERANCE_END_T %.6f", monotonic_s)

    def replay_start_t(self, *, monotonic_s: float, byte_count: int) -> None:
        logger.info(
            "nexa.realtime.metrics: REPLAY_START_T %.6f bytes=%d", monotonic_s, byte_count
        )

    def replay_end_t(self, *, monotonic_s: float) -> None:
        logger.info("nexa.realtime.metrics: REPLAY_END_T %.6f", monotonic_s)

    def first_new_assistant_audio_t(self, *, monotonic_s: float) -> None:
        logger.info("nexa.realtime.metrics: FIRST_NEW_ASSISTANT_AUDIO_T %.6f", monotonic_s)

    def bargein_replacement_failed(self, *, reason: str) -> None:
        logger.warning(
            "nexa.realtime.metrics: atomic provider replacement FAILED (%s) "
            "-- falling back to LOCAL",
            reason,
        )


@dataclass
class GeminiVoiceRuntime:
    """Holds every object this module constructs — the app starts/stops
    it as a unit."""

    provider: GeminiLiveProvider
    provider_handle: _ProviderHandle
    router: ConversationRouter
    hw_worker: Any
    hw_runner: Any
    aec_health: AecReferenceHealth
    bargein: BargeInController
    metrics: RuntimeMetrics
    lifecycle: _ResponseLifecycle
    generation_guard: _ResponseGenerationGuard
    #: Optional operator/observability hook, called once per event
    #: *from the same single consumption loop* that drives the canonical
    #: write path -- ``provider.events()`` is backed by ONE
    #: ``asyncio.Queue`` (single-consumer by construction); a second
    #: independent reader would race this loop for events and could steal
    #: ones the router needs to commit a turn. Never add a second
    #: ``provider.events()`` consumer -- hook in here instead.
    on_event: Any = None
    _events_task: asyncio.Task | None = None
    _hw_run_task: asyncio.Task | None = None
    _recovery_language_preference: str | None = field(default=None, repr=False)

    async def start(self, snapshot: CloudContextSnapshot) -> None:
        await self.provider.start(snapshot)
        self.provider_handle.current = self.provider
        self._recovery_language_preference = snapshot.language_preference
        self.metrics.provider_readiness(self.provider.readiness)
        await self.hw_runner.add_workers(self.hw_worker)
        self._hw_run_task = asyncio.create_task(self.hw_runner.run())
        self._events_task = asyncio.create_task(self._consume_provider_events())

    async def _consume_provider_events(self) -> None:
        """Drain ``provider.events()`` and (1) drive the canonical write
        path via the router's one true entry point, (2) keep
        ``BargeInController``'s own state machine in sync via
        ``_ResponseLifecycle`` (real generation+playback truth, never a
        naive generation-complete==finished conflation), (3) inject
        assistant audio (and a deterministic ``TTSStoppedFrame``) into the
        hardware pipeline -- but ONLY for the CURRENTLY VALID response
        generation (M2.6B.4 FAILURE 2: ``_ResponseGenerationGuard`` drops
        anything belonging to a generation a confirmed local interruption
        already invalidated, no matter how many more events for it were
        already in flight), and (4) drive mid-turn fresh-session recovery
        the instant ``provider.needs_fresh_session`` is observed true —
        never a second concurrent ``provider.events()`` reader; the OLD
        provider's queue is abandoned (never read again) the moment
        recovery begins. M2.6B.4C (R0041)/M2.6B.4D (R0042) -- NO local
        language-ID runs anywhere in this loop: native Gemini mirroring
        is the sole language mechanism, per the operator's explicit
        product decision (see the module docstring).

        M2.6B.4E/F (R0043/R0044) added, then hardened, a second,
        NeXa/Gemini-independent fallback re-arm signal
        (``provider.local_turn_closed_seq`` advancing by 2 past its value
        at interrupt time) for the case a non-lexical interrupting sound
        produces no transcript at all -- proven, by adversarial test, to
        still leave one residual gap.

        M2.6B.4G (R0045) -- **that whole fallback is REMOVED, superseded,
        not merely narrowed.** Every confirmed local barge-in is now
        handled by ATOMIC PROVIDER REPLACEMENT (see
        ``provider_handle.replacement_requested`` below and
        ``_replace_provider_after_bargein``): the OLD (interrupted)
        provider's queue is stopped being read entirely, exactly like the
        EXISTING mid-turn-connection-loss recovery below (never merely
        "trusted again after N local turn closures") -- so dispatch
        re-arm again depends on ONLY the original, simple mechanism: a
        fresh, final ``UserTranscriptionEvent`` (R0034's proven ordering)
        OR, after a replacement, the BRAND NEW provider's own first
        assistant event, whose ``dispatched_for_turn=False`` reset is set
        directly by the replacement itself (identical to how
        ``needs_fresh_session`` recovery already resets it) -- never
        turn-closure counting on the SAME, still-being-read provider."""
        P = _pipecat_hw_imports()
        first_audio_seen = False
        # M2.6B.4G (R0045) -- set True the instant a barge-in replacement
        # hands control to a brand new provider; cleared (and logged) on
        # that provider's own first AssistantAudioEvent, for the
        # FIRST_NEW_ASSISTANT_AUDIO_T performance instrumentation point.
        awaiting_first_new_audio = False
        current_gid = 0
        # True once the CURRENT open user turn's assistant output has
        # already been dispatched -- reset on a fresh, FINAL
        # UserTranscriptionEvent (a genuinely NEW local user turn was just
        # heard -- R0034's proven message-ordering guarantee: for a
        # NORMAL, transcribed turn this always arrives before that turn's
        # own assistant content) or directly by a provider swap (mid-turn
        # loss recovery, or M2.6B.4G/R0045's atomic barge-in replacement)
        # -- never by counting local turn closures on the SAME provider
        # (R0043/R0044's now-removed fallback; see this method's own
        # docstring for why R0045 supersedes it entirely).
        dispatched_for_turn = False
        provider_interruption_ack_count = 0
        audio_chunk_count = 0

        while True:
            provider = self.provider
            recovered = False
            async for event in provider.events():
                if self.on_event is not None:
                    try:
                        self.on_event(event)
                    except Exception:  # noqa: BLE001 - an observer must never break audio
                        logger.exception(
                            "nexa.realtime.gemini.runtime: on_event hook raised"
                        )

                if isinstance(event, ProviderInterruptionEvent):
                    # M2.6B.4E (R0043) -- NOT the same thing as a local
                    # confirmation: ONE confirmed local interruption can
                    # legitimately produce SEVERAL of these (Pipecat's own
                    # broadcast_interruption() fans out two InterruptionFrame
                    # instances per call, and both our own cancel() and
                    # Gemini's independent serverContent.interrupted ack
                    # each trigger one broadcast -- see the module
                    # docstring). Counted here purely for observability;
                    # nothing in this loop reacts to the count.
                    provider_interruption_ack_count += 1
                    self.metrics.provider_interruption_ack(
                        count=provider_interruption_ack_count
                    )

                # M2.6B.4G (R0045) -- checked FIRST, ahead of EVERY other
                # handler below (including `router.handle_provider_event`):
                # a confirmed local barge-in already quarantined this
                # provider (synchronously, in `_on_confirmed`) -- the
                # CURRENT event (whatever just woke this loop up -- most
                # often the `cancel()`-triggered `CancellationCompleteEvent`
                # or `ProviderInterruptionEvent`, occasionally a trailing
                # `AssistantAudioEvent`/`UserTranscriptionEvent` already in
                # flight) is deliberately NEVER processed by any handler
                # below once this fires -- not merely audio-dropped-and-
                # logged, but never reaching the router/canonical history,
                # never re-arming dispatch, never touched at all. This is
                # the "stop consuming the old provider entirely" isolation
                # boundary (never merely "trusted again after enough local
                # turn closures"); no FURTHER old-provider event is ever
                # read after this `break`, and this one is not read either
                # in the sense of being acted on.
                if self.provider_handle.replacement_requested:
                    self.provider_handle.replacement_requested = False
                    old_provider = self.provider_handle.old_provider
                    self.provider_handle.old_provider = None
                    new_provider = await self._replace_provider_after_bargein(
                        old_provider=old_provider or provider
                    )
                    if new_provider is None:
                        # _replace_provider_after_bargein already logged
                        # the specific failure reason; the router has
                        # already fallen back to LOCAL (Decision J) --
                        # nothing more to consume on the cloud side.
                        return
                    self.provider = new_provider
                    dispatched_for_turn = False
                    awaiting_first_new_audio = True
                    recovered = True
                    break  # stop reading the OLD provider's queue

                if isinstance(
                    event, (AssistantAudioEvent, AssistantTranscriptionEvent)
                ) and not dispatched_for_turn:
                    dispatched_for_turn = True
                    current_gid = self.generation_guard.start_new_generation()
                    self.bargein.notify_response_dispatched()
                    self.lifecycle.mark_dispatched()
                    self.metrics.assistant_response_dispatch(
                        generation_id=current_gid,
                        triggering_event=type(event).__name__,
                    )

                outcome = self.router.handle_provider_event(event)

                if isinstance(event, UserTranscriptionEvent) and event.final:
                    dispatched_for_turn = False
                    self.metrics.input_transcription_final(event.text)
                    self.metrics.user_transcription_final_state(
                        dispatched_for_turn=dispatched_for_turn,
                        generation_id=current_gid,
                        generation_valid=self.generation_guard.is_valid(current_gid),
                    )
                elif isinstance(event, GenerationCompleteEvent):
                    self.lifecycle.mark_generation_done()
                    if self.generation_guard.is_valid(current_gid):
                        # Deterministic BotStopped confirmation (M2.6B.3A
                        # §1): queued strictly after every audio chunk of
                        # this generation (same FIFO the audio chunks
                        # went through), so the output transport fires a
                        # real BotStoppedSpeakingFrame once (and only
                        # once) that whole queue has actually drained --
                        # never a multi-second silence-fallback guess. A
                        # late generation-complete for an already-
                        # invalidated generation never re-stops anything.
                        await self.hw_worker.queue_frames([P["TTSStoppedFrame"]()])
                elif isinstance(event, RealtimeProviderFailedError):
                    dispatched_for_turn = False
                    self.generation_guard.interrupt()

                if outcome is not None:
                    turn = self.router._turn.current  # noqa: SLF001 - metrics only
                    self.metrics.canonical_turn_committed(
                        outcome,
                        turn.generation if turn else None,
                        user_transcript=turn.user_text if turn else None,
                        assistant_transcript=turn.assistant_text if turn else None,
                        provider_instance_id=str(id(self.provider)),
                    )
                if isinstance(event, AssistantAudioEvent):
                    if not first_audio_seen:
                        first_audio_seen = True
                        self.metrics.first_assistant_audio_received()
                    if awaiting_first_new_audio:
                        awaiting_first_new_audio = False
                        self.metrics.first_new_assistant_audio_t(
                            monotonic_s=time.monotonic()
                        )
                    self.lifecycle.mark_audio_produced()
                    audio_chunk_count += 1
                    valid = self.generation_guard.is_valid(current_gid)
                    self.metrics.assistant_audio_received(
                        generation_id=current_gid,
                        valid=valid,
                        chunk_count=audio_chunk_count,
                        byte_count=len(event.pcm),
                    )
                    if valid:
                        frame = P["TTSAudioRawFrame"](
                            audio=event.pcm, sample_rate=OUTPUT_SAMPLE_RATE_HZ,
                            num_channels=1,
                        )
                        await self.hw_worker.queue_frames([frame])
                        self.metrics.assistant_audio_hw_queued(
                            generation_id=current_gid, chunk_count=audio_chunk_count
                        )
                    else:
                        # A confirmed local interruption already invalidated
                        # this generation -- this chunk was already in
                        # flight (queued on provider.events() before the
                        # interruption, or produced by Gemini in the brief
                        # window before it honoured the cancel signal) and
                        # must NEVER reach the speaker or the AEC reference.
                        self.metrics.dropped_invalidated_generation_audio(
                            generation_id=current_gid,
                            reason="generation_invalidated",
                            valid_generation_id=self.generation_guard.valid_id,
                        )

                if provider.needs_fresh_session:
                    new_provider = await self.router.recover_from_mid_turn_loss(
                        old_provider=provider,
                        language_preference=self._recovery_language_preference,
                    )
                    if new_provider is None:
                        # Router already fell back to LOCAL (Decision J
                        # notice) -- nothing more to consume on the cloud
                        # side; the old provider is stopped and its queue
                        # is abandoned here, never read again.
                        self.metrics.mid_turn_recovery(succeeded=False)
                        return
                    self.metrics.mid_turn_recovery(succeeded=True)
                    self.provider = new_provider
                    self.provider_handle.current = new_provider
                    dispatched_for_turn = False
                    self.generation_guard.interrupt()
                    recovered = True
                    break  # stop reading the OLD provider's queue

            if not recovered:
                return  # the provider's own events() ended (stop()/cancel)

    async def _replace_provider_after_bargein(
        self, *, old_provider: GeminiLiveProvider
    ) -> GeminiLiveProvider | None:
        """M2.6B.4G (R0045) — ATOMIC PROVIDER REPLACEMENT after a
        confirmed local barge-in. Reuses
        ``ConversationRouter.start_fresh_cloud_provider`` — the SAME
        destroy-and-recreate primitive ``recover_from_mid_turn_loss``
        already uses for connection loss — but drives replay from the
        LOCAL ``_ProviderHandle.sealed_utterances`` FIFO: the
        interrupting utterance's own complete PCM, captured by the bridge
        in parallel with (never delaying) whatever it managed to stream
        live before quarantine began — never ``take_pending_audio()``
        (that is the OLD provider's own not-yet-delivered-while-not-ready
        buffer, an unrelated mechanism for a different failure mode).

        Starts the new provider AND waits for the interrupting utterance
        to SEAL (VAD end) CONCURRENTLY — this is what "start creating a
        fresh provider/session immediately, in parallel with the user
        continuing to speak" (the charter's own words) actually means:
        the connection handshake is never serialised behind waiting for
        the user to finish talking, and vice versa. Handles BOTH
        orderings (new provider READY before VAD END, or VAD END before
        READY) identically, since ``asyncio.gather`` simply waits for
        whichever finishes last. Drains EVERY sealed utterance currently
        queued (not just the first) before clearing quarantine, so a
        second local utterance that seals during the replacement window
        is never orphaned.
        """
        self.metrics.replacement_start_t(monotonic_s=time.monotonic())

        async def _start_new() -> GeminiLiveProvider | None:
            new_provider = await self.router.start_fresh_cloud_provider(
                language_preference=self._recovery_language_preference,
                failure_reason_prefix="confirmed barge-in, ",
            )
            if new_provider is not None:
                self.metrics.new_provider_ready_t(monotonic_s=time.monotonic())
            return new_provider

        async def _wait_first_seal() -> None:
            await self.provider_handle.sealed_utterance_ready.wait()
            self.metrics.interrupting_utterance_end_t(monotonic_s=time.monotonic())

        # Tear down the OLD (already-quarantined) provider concurrently
        # too -- its own connection teardown is on neither the new
        # provider's readiness nor the interrupting utterance's own seal.
        stop_old_task = asyncio.create_task(
            old_provider.stop(
                reason="confirmed local barge-in — atomic provider replacement"
            )
        )
        new_provider, _ = await asyncio.gather(_start_new(), _wait_first_seal())
        await stop_old_task

        if new_provider is None:
            self.metrics.bargein_replacement_failed(reason="new provider failed to start")
            self.provider_handle.sealed_utterances.clear()
            self.provider_handle.sealed_utterance_ready.clear()
            self.provider_handle.quarantined = False
            return None

        self.provider_handle.current = new_provider
        # Drain EVERY sealed utterance currently queued, in order --
        # normally exactly one (the interrupting utterance itself), but
        # never fewer, and never more than once each, even if a second
        # local utterance sealed during the replacement window above.
        while self.provider_handle.sealed_utterances:
            pcm = self.provider_handle.sealed_utterances.pop(0)
            if not self.provider_handle.sealed_utterances:
                self.provider_handle.sealed_utterance_ready.clear()
            if pcm:
                self.metrics.replay_start_t(
                    monotonic_s=time.monotonic(), byte_count=len(pcm)
                )
                await new_provider.user_turn_start()
                await new_provider.send_user_audio(pcm)
                await new_provider.user_turn_end()
                self.metrics.replay_end_t(monotonic_s=time.monotonic())
        self.provider_handle.quarantined = False
        return new_provider

    async def stop(self, *, reason: str) -> None:
        if self._events_task is not None:
            self._events_task.cancel()
            self._events_task = None
        await self.provider.stop(reason=reason)
        try:
            await self.hw_runner.end(reason=reason)
        except Exception as exc:  # noqa: BLE001 — best-effort teardown
            logger.warning(
                "nexa.realtime.gemini.runtime: error stopping hardware pipeline: %r", exc
            )


def build_gemini_voice_runtime(
    *,
    session,
    api_key: str,
    audio_config: LocalAudioConfig | None = None,
    voice_preference: str = DEFAULT_VOICE_PREFERENCE,
    policy: ConversationPolicy = ConversationPolicy.CLOUD_PREFERRED,
    on_event: Any = None,
    on_aec_change: Callable[[bool], None] | None = None,
    dry: bool = False,
) -> GeminiVoiceRuntime:
    """Construct the full HYBRID cloud-voice runtime.

    ``dry=True`` builds every object EXCEPT the audio device / network:
    validates the object graph (mirrors the M2.6A probe's own ``--dry``),
    no ``pyaudio.PyAudio()`` call, no device index lookup, no Gemini
    connection. Use it to catch construction errors before ever touching
    hardware or spending Gemini quota.

    ``on_aec_change`` (M2.6B.3A §5) is composed with the metrics logger —
    both fire on every real ``AecReferenceHealth`` transition; this is the
    ONE supported way for a caller (e.g. the operator app) to observe AEC
    status, rather than reaching into ``aec_health``'s private callback
    attribute after construction (which would silently replace, not
    compose with, the metrics logging).
    """
    cfg = audio_config or LocalAudioConfig()
    metrics = RuntimeMetrics()

    provider = GeminiLiveProvider(api_key=api_key, voice_preference=voice_preference)
    provider_handle = _ProviderHandle(provider)

    def _cloud_factory() -> GeminiLiveProvider:
        return GeminiLiveProvider(api_key=api_key, voice_preference=voice_preference)

    router = ConversationRouter(
        session, policy=policy, cloud_provider_factory=_cloud_factory
    )

    def _combined_aec_change(active: bool) -> None:
        metrics.aec_reference(active)
        if on_aec_change is not None:
            on_aec_change(active)

    aec_health = AecReferenceHealth(on_change=_combined_aec_change)
    lifecycle = _ResponseLifecycle(on_finished=lambda: bargein.notify_response_finished())
    generation_guard = _ResponseGenerationGuard()
    # M2.6B.4E (R0043) -- plain closure counters, purely for observability
    # (LOCAL_BARGEIN_CONFIRMED / OUTPUT_INTERRUPTION_BROADCAST). Deliberately
    # separate from `_ResponseGenerationGuard`'s own id counter and from
    # PROVIDER_INTERRUPTION_ACK's count -- ONE local confirmation can
    # legitimately produce SEVERAL provider acks (see the module
    # docstring); keeping the counters distinct is what makes that
    # observable instead of assumed.
    _confirm_counts = {"local_bargein_confirmed": 0, "output_interruption_broadcast": 0}

    def _on_candidate(response_id: int | None) -> None:  # noqa: ARG001 - hook signature
        # Self-interruption fix -- BargeInController just classified a
        # local VAD start as an interruption CANDIDATE (response_in_flight
        # was True). Runs synchronously inside BargeInController's own
        # VADUserStartedSpeakingFrame handling, i.e. BEFORE that same
        # frame is forwarded downstream to `bridge` (bargein precedes
        # bridge in the hardware pipeline) -- so `bridge` is guaranteed to
        # see this flag already set when it processes the same frame.
        provider_handle.candidate_pending = True
        metrics.candidate_pending_opened()

    def _on_candidate_rejected() -> None:
        # Self-interruption fix -- observability only. BargeInController
        # rejected the candidate (VAD stopped before confirm_hold_secs).
        # `bridge`'s own VADUserStoppedSpeakingFrame handler is what
        # actually clears `candidate_pending` and discards the buffered
        # PCM -- it processes the SAME stop frame immediately after this
        # hook returns (bargein precedes bridge), so it always sees
        # `candidate_pending` still True and `quarantined` still False,
        # which unambiguously means "rejected" (a confirm, if it had
        # happened first, would already have set `quarantined`). No
        # cross-object state mutation is needed here for correctness.
        logger.info("nexa.realtime.metrics: BARGEIN_CANDIDATE_REJECTED (observability)")

    def _on_confirmed(ctx: InterruptContext) -> None:
        t_confirm = time.monotonic()
        metrics.bargein_confirmed_t(monotonic_s=t_confirm)
        # Self-interruption fix -- this candidate is now resolved (
        # confirmed, not rejected). `quarantined` (set below) is what
        # `bridge` checks from this point on; clearing `candidate_pending`
        # here is state hygiene, not load-bearing (the bridge's own
        # branch ordering already checks `quarantined` before
        # `candidate_pending` everywhere).
        provider_handle.candidate_pending = False
        _confirm_counts["local_bargein_confirmed"] += 1
        metrics.local_interruption_confirmed(
            confirm_count=_confirm_counts["local_bargein_confirmed"],
            generation_id=ctx.invalidated_response_id,
            bargein_state=bargein.state_machine.state.value,
        )
        # M2.6B.4 FAILURE 2 -- invalidate the CURRENT response generation
        # BEFORE anything else below (see _ResponseGenerationGuard's own
        # docstring for why this is race-free on a single-threaded event
        # loop): broadcast_interruption() (already called by
        # BargeInController just before this hook, per its own contract)
        # only clears what the hardware output queue held at this
        # instant -- this guard is what stops any MORE audio for this
        # same generation, already in flight on provider.events()'s own
        # queue, from ever reaching hw_worker.queue_frames() afterward.
        generation_guard.interrupt()
        if ctx.invalidated_response_id is not None:
            metrics.generation_invalidated(generation_id=ctx.invalidated_response_id)
        # M2.6B.4G (R0045) -- QUARANTINE the OLD provider's epoch
        # SYNCHRONOUSLY, in the same breath as the generation-guard
        # invalidation above: the bridge must never send another
        # turn-I/O call to it for the rest of the interrupting
        # utterance, and `_consume_provider_events` must stop reading
        # its `events()` queue at the very next opportunity (see that
        # method's own docstring). `old_provider` is snapshotted HERE,
        # not read from `provider_handle.current` again later, so the
        # replacement logic can never race a later reassignment.
        provider_handle.old_provider = provider_handle.current
        provider_handle.quarantined = True
        provider_handle.replacement_requested = True
        # Local speaker-stop authority already happened: BargeInController
        # calls Pipecat's own broadcast_interruption() (which tears down
        # queued/playing output audio) BEFORE this hook runs, and that call
        # never waits on anything below -- the R0034 late-server-event
        # protections stay in force regardless of what the cloud side does.
        # Logged at this same synchronous instant -- there is no later,
        # more precise hook to observe the stop from.
        metrics.old_audio_stop_t(monotonic_s=t_confirm)
        # M2.6B.4E (R0043) -- ``BargeInController._do_confirm`` calls this
        # hook, then unconditionally awaits its OWN `broadcast_interruption()`
        # once (bargein.py) -- so this count tracks 1:1 with confirmed
        # local interruptions, deliberately logged here (not inside the
        # shared, local-voice-frozen `nexa.voice.bargein` module) so this
        # observability addition never touches that shared class.
        _confirm_counts["output_interruption_broadcast"] += 1
        metrics.output_interruption_broadcast(
            count=_confirm_counts["output_interruption_broadcast"]
        )
        #
        # M2.6B.3B -- the interrupted spoken prefix is UNCONDITIONALLY
        # empty, never the raw running CloudTurnAccumulator.assistant_text
        # and never a partial-credit approximation. Installed Pipecat
        # source proves output-transcription can arrive ahead of, and
        # contain more text than, the audio actually produced, and a
        # one-audio-chunk-lag mechanism (M2.6B.3A's first attempt) still
        # cannot prove how much of an EARLIER text snapshot a LATER
        # chunk's own audio actually covers. No deterministic
        # assistant-text/audio alignment is reachable through the
        # installed Pipecat/google-genai stack (see the module docstring
        # §2) -- so under-crediting (no assistant text at all for an
        # interrupted reply) is the only safe choice; crediting unspoken
        # words is not acceptable. Set BEFORE on_interruption() per the
        # charter's own example ordering (functionally either order is
        # safe -- neither accumulator method depends on the other having
        # already run).
        router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)
        router.on_interruption()
        # M2.6B.4G (R0045) -- commit the interrupted turn NOW,
        # synchronously, matching the exact pattern every existing
        # accepted test already uses (set_spoken_prefix ->
        # on_interruption -> commit_cloud_turn). Closes a real,
        # separately-discovered gap: nothing in the PRODUCTION path
        # previously committed an interrupted turn at all (see the
        # module docstring's own M2.6B.4G section for the full audit --
        # `router.begin_cloud_turn()` itself is STILL never called in
        # production either, a distinct, pre-existing, NOT-yet-fixed
        # gap this checkpoint does not attempt to close). This call is
        # a safe no-op if no turn is currently open.
        outcome = router.commit_cloud_turn()
        if outcome is not None:
            turn = router._turn.current  # noqa: SLF001 - metrics only, already terminal
            metrics.canonical_turn_committed(
                outcome,
                turn.generation if turn else None,
                user_transcript=turn.user_text if turn else None,
                assistant_transcript=turn.assistant_text if turn else None,
                provider_instance_id=str(id(provider_handle.old_provider)),
            )
        # M2.6B.4H (R0046) -- promote the interrupting/candidate utterance
        # to its own canonical turn NOW. No future
        # ``VADUserStartedSpeakingFrame`` will ever arrive for it (it is a
        # continuation of the SAME already-open local utterance the
        # bridge saw earlier, while ``has_turn_awaiting_assistant()`` was
        # True for turn N and therefore deliberately did NOT open a
        # canonical turn for it then) -- this is the ONE other call site.
        # Ordered strictly AFTER committing turn N above:
        # ``CloudTurnAccumulator.start_turn()``'s own "abandon the
        # still-open current turn" branch safely no-ops for an
        # already-terminal (just-committed) turn. The NEW provider
        # R0045's atomic replacement creates is the sole subsequent
        # authority for this turn's transcription (its own
        # ``UserTranscriptionEvent``s reach `router.on_user_transcription`
        # normally, exactly like any other turn -- see the module
        # docstring's M2.6B.4H section for why the OLD provider's own
        # trailing candidate transcript, if any, can never reach it
        # either). Closes a real, separately-discovered gap:
        # `router.begin_cloud_turn()` previously had NO production caller
        # at all (see M2.6B.4G/R0045's own audit) -- normal turns and
        # confirmed interruptions now share one explicit, deterministic
        # canonical ownership lifecycle.
        router.begin_cloud_turn()
        lifecycle.mark_interrupted()
        # provider_handle.old_provider (the SAME snapshot the replacement
        # flow will read) -- cancel() is async; this hook is sync
        # (BargeInController's contract) -- fire-and-forget is correct:
        # cancellation reaching the cloud side is not on the critical
        # path for local speaker-stop.
        asyncio.create_task(provider_handle.old_provider.cancel())
        # Cloud turns need no segment-coalescing capture phase (unlike the
        # local path): the interrupting utterance is simply the next local
        # VAD turn the bridge opens. Exit INTERRUPTING immediately so
        # BargeInController is ready to admit a future interruption again
        # -- unrelated to, and independent of, `quarantined`/
        # `replacement_requested` above, which stay set until the atomic
        # replacement itself completes.
        bargein.notify_interruption_complete()

    bargein = BargeInController(
        aec_health=aec_health,
        on_confirmed=_on_confirmed,
        on_candidate=_on_candidate,
        on_candidate_rejected=_on_candidate_rejected,
    )

    if dry:
        return GeminiVoiceRuntime(
            provider=provider,
            provider_handle=provider_handle,
            router=router,
            hw_worker=None,
            hw_runner=None,
            aec_health=aec_health,
            bargein=bargein,
            metrics=metrics,
            lifecycle=lifecycle,
            generation_guard=generation_guard,
            on_event=on_event,
        )

    P = _pipecat_hw_imports()
    import pyaudio

    pa = pyaudio.PyAudio()
    in_idx = find_device_index(pa, cfg.input_device_name, require_input=True)
    out_idx = find_device_index(pa, cfg.output_device_name, require_output=True)

    transport_params = P["LocalAudioTransportParams"](
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=INPUT_SAMPLE_RATE_HZ,
        audio_out_sample_rate=OUTPUT_SAMPLE_RATE_HZ,
        audio_in_channels=1,
        audio_out_channels=1,
        input_device_index=in_idx,
        output_device_index=out_idx,
    )
    acoustic_frontend = None
    reference_gain = CoherentReferenceGain(card=cfg.output_alsa_mixer_card)
    if cfg.scheduled_aec_reference:
        from ...voice.acoustic.local_transport import ScheduledReferenceTransport
        from ...voice.acoustic.scheduled_reference import (
            AlsaReferenceSink,
            ScheduledNativeFrontend,
        )

        acoustic_frontend = ScheduledNativeFrontend(
            aec_health=aec_health,
            sample_rate=OUTPUT_SAMPLE_RATE_HZ,
            channels=1,
            sink_factory=lambda: AlsaReferenceSink(
                device=AEC_REFERENCE_PCM, sample_rate=OUTPUT_SAMPLE_RATE_HZ, channels=1,
            ),
            gain_source=reference_gain.current_gain,
            gain_valid=lambda: reference_gain.last_read_succeeded,
        )
        transport = ScheduledReferenceTransport(transport_params, frontend=acoustic_frontend)
    else:
        transport = P["LocalAudioTransport"](transport_params)
    vad_analyzer = P["SileroVADAnalyzer"](
        sample_rate=INPUT_SAMPLE_RATE_HZ, params=P["VADParams"](stop_secs=0.5)
    )
    vad_processor = P["VADProcessor"](vad_analyzer=vad_analyzer)
    # M2.6B.4L (R0051) -- R0049's own derivation (start_secs + a 0.1s
    # margin = 300ms) was PROVEN insufficient by real hardware evidence
    # (docs/reports/R0050_...md: byte-exact alignment + PCM energy
    # analysis of real operator captures). Use NeXa's own canonical,
    # already-EMPIRICALLY-validated preroll capacity instead --
    # ``UtteranceBuffer``'s own ``PRE_ROLL_MS`` (500ms, measured against
    # real speech for this EXACT VAD mechanism, R0006-era) -- never a
    # second, re-derived "500" defined anywhere else.
    preroll_ms = PRE_ROLL_MS
    bridge_cls = _make_vad_bridge_class(P)
    bridge = bridge_cls(
        provider_handle=provider_handle,
        metrics=metrics,
        lifecycle=lifecycle,
        router=router,
        preroll_ms=preroll_ms,
    )
    # M2.6B.4N (R0053) -- coherent reference/audible gain. Root cause: the
    # reSpeaker's own reference-injection mixer and the USB speaker's own
    # audible-output mixer are two independent ALSA hardware controls
    # (real amixer/asound.conf audit); raising real speaker volume never
    # changed the reference PCM's amplitude, degrading the XVF3800's own
    # AEC cancellation at higher volume (real evidence: 0/5 false
    # barge-ins at LOW, 1/5 at NORMAL, 5/5 at MAX). Reads the audible
    # device's own real mixer gain (bounded-cost, cached) and scales the
    # reference PCM to match -- never touches VAD/Silero/BargeInController.
    stages = [transport.input()]
    if acoustic_frontend is not None:
        from ...voice.acoustic.local_transport import AcousticCaptureProcessor

        stages.append(AcousticCaptureProcessor(acoustic_frontend))
    stages.extend([vad_processor, bargein, bridge])
    if acoustic_frontend is None:
        stages.append(AecReferenceFeeder(
            aec_health=aec_health, sample_rate=OUTPUT_SAMPLE_RATE_HZ, channels=1,
            gain_source=reference_gain.current_gain,
        ))
    stages.append(transport.output())
    pipeline = P["Pipeline"](stages)
    hw_worker = P["PipelineWorker"](
        pipeline,
        params=P["PipelineParams"](
            audio_in_sample_rate=INPUT_SAMPLE_RATE_HZ,
            audio_out_sample_rate=OUTPUT_SAMPLE_RATE_HZ,
        ),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    hw_runner = P["WorkerRunner"]()

    return GeminiVoiceRuntime(
        provider=provider,
        provider_handle=provider_handle,
        router=router,
        hw_worker=hw_worker,
        hw_runner=hw_runner,
        aec_health=aec_health,
        bargein=bargein,
        metrics=metrics,
        lifecycle=lifecycle,
        generation_guard=generation_guard,
        on_event=on_event,
    )
