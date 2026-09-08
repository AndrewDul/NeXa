"""M2.4 half-duplex safety gate: suppress microphone input while NeXa's own
TTS audio is physically playing, so the reSpeaker cannot hear NeXa speak and
feed that back into STT -> ConversationSession (a self-conversation loop
observed on real hardware).

This is a **temporary M2.4 behaviour**. M2.5 replaces it with true
full-duplex barge-in (interruption, own-TTS suppression, echo handling). This
module deliberately does *not*:

- cancel TTS, LLM generation, or the ConversationSession when the user speaks
- add a ``SPEAKING`` value to the acoustic :class:`~nexa.voice.state.VoiceState`
  enum (that enum stays a pure record of what the *microphone* hears)
- infer "NeXa is speaking" from assistant text generation, timers, or any
  fake state

The single source of truth for "NeXa is speaking" is Pipecat's own real
output-transport playback lifecycle: ``BotStartedSpeakingFrame`` (emitted on
the first ``TTSAudioRawFrame`` that reaches the output transport) and
``BotStoppedSpeakingFrame`` (emitted on the turn's ``TTSStoppedFrame`` once
audio was actually produced, or after a multi-second audio-drain fallback).
Those frames are broadcast **upstream** as well as downstream, so a processor
sitting right after ``transport.input()`` sees them (see
``nexa.voice.runtime._MicGateFrameProcessor``).

Multi-sentence safety: one assistant reply can, in principle, produce more
than one ``BotStartedSpeakingFrame`` / ``BotStoppedSpeakingFrame`` pair if
Pipecat's TTS service emits an early ``TTSStoppedFrame`` between sentence
chunks. A raw ``bot_speaking`` boolean would then briefly re-open the mic
*between two sentences of the same answer* — long enough for NeXa to hear
herself. To prevent that, the gate also takes two response-level
notifications from :class:`nexa.voice_tts.AssistantSpeechBridge`
(``notify_response_dispatched`` when a user turn starts generating,
``notify_response_finished`` when generation completes) and stays closed,
once NeXa has started speaking a response, until **both** the audio has
stopped **and** that response has finished generating.
"""

from __future__ import annotations

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
)


class HalfDuplexGate:
    """Event-backed boolean: is the microphone currently suppressed because
    NeXa is (or is about to finish) speaking?

    All state transitions come from real events — Pipecat playback frames via
    :meth:`observe_frame`, and response-lifecycle notifications from the
    assistant-speech bridge. Nothing here is time-based.

    Not thread-safe by design: it is driven entirely from the single Pipecat
    asyncio event loop (the bridge callbacks run on that same loop via
    ``SerialConversationQueue``). Every mutation is a single boolean
    assignment regardless.
    """

    def __init__(self) -> None:
        # True strictly between BotStartedSpeakingFrame and
        # BotStoppedSpeakingFrame — real TTS audio is leaving the speaker.
        self._bot_speaking = False
        # True between notify_response_dispatched() and
        # notify_response_finished() — a user turn is being generated.
        self._response_generating = False
        # True once NeXa has started speaking the current response at least
        # once; reset when the next response is dispatched.
        self._spoke_this_response = False
        # True once a BotStoppedSpeakingFrame has arrived *after* generation
        # for this response already finished — the definitive end of the
        # reply. Reset when the next response is dispatched.
        self._response_playback_done = False

    @property
    def response_in_flight(self) -> bool:
        """M2.4B.5A: True from the moment a user turn is dispatched for a
        reply (``notify_response_dispatched``) until that reply is fully
        finished — generation complete **and**, if it produced audio,
        playback done. The strict pre-M2.5 half-duplex rule: while this is
        True no new utterance may become an STT or conversation turn (TV /
        ambient noise during the think/generate/synthesize window must not
        create work — root cause, R0026). This is NOT barge-in: nothing is
        cancelled; the user simply cannot start a new turn while NeXa is
        answering."""
        if self._response_generating:
            return True
        if self._spoke_this_response and not self._response_playback_done:
            return True
        return False

    @property
    def mic_suppressed(self) -> bool:
        """Whether microphone audio must not be allowed to start a new
        STT/conversation turn right now.

        M2.4B.5A: this now covers the **whole** response, not just
        playback. Before B.5A the mic stayed open through the
        think/generation/TTS-synth window (see
        ``notify_response_dispatched``'s original contract) — a 10–25 s
        hole on ``gemma4:e4b`` during which TV audio backlogged the STT and
        conversation queues (R0026). ``response_in_flight`` closes it."""
        return self._bot_speaking or self.response_in_flight

    # -- response lifecycle (from nexa.voice_tts.AssistantSpeechBridge) ------

    def notify_response_dispatched(self) -> None:
        """A user turn has started generating an assistant reply. M2.4B.5A:
        this now **closes the mic immediately** (via ``response_in_flight``)
        and keeps it closed through generation, TTS synthesis and playback,
        until ``notify_response_finished`` + playback-done. (Before B.5A it
        left the think/generation window open — R0026.)"""
        self._response_generating = True
        self._spoke_this_response = False
        self._response_playback_done = False

    def notify_response_finished(self) -> None:
        """Assistant generation for the current reply is complete
        (``on_assistant_complete`` / ``on_conversation_error``). If audio has
        already stopped, this is what finally re-opens the mic."""
        self._response_generating = False
        if not self._bot_speaking and self._spoke_this_response:
            self._response_playback_done = True

    # -- real playback frames (from the output transport, travelling upstream)

    def observe_frame(self, frame: Frame) -> None:
        """Feed every frame the mic-gate processor sees. Only playback
        lifecycle frames (and hard stops) change state."""
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._spoke_this_response = True
            self._response_playback_done = False
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            # The reply's audio is finished only when generation is also done;
            # otherwise this is just a gap between sentence chunks.
            if not self._response_generating and self._spoke_this_response:
                self._response_playback_done = True
        elif isinstance(frame, (EndFrame, CancelFrame, ErrorFrame)):
            # Pipeline is stopping or errored — never leave the mic latched shut.
            self._bot_speaking = False
            self._response_generating = False
            self._spoke_this_response = False
            self._response_playback_done = False
