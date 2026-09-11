"""``RealtimeVoiceProvider`` — the NeXa-owned boundary for a realtime
speech-to-speech conversation backend (ADR-0004 Decision B).

A **peer of** ``nexa.providers.ModelProvider``, never a subtype:
``ModelProvider`` is a text-stream contract (``messages ->
AsyncIterator[str]``); a realtime voice provider is a bidirectional
speech-to-speech *session* with its own lifecycle, readiness state, and
event stream. Forcing it into ``ModelProvider`` would misrepresent the
contract and push audio/lifecycle concerns into ``ConversationSession``.

Neither NeXa's identity nor its canonical conversation lives here (ADR-0004
Decision A / O) — this module only defines the shape an implementation must
have. ``ConversationRouter`` (M2.6B.2+) is the only thing that drives a live
instance and the only thing that ever writes into ``ConversationSession``;
a provider implementation must never mutate ``ConversationSession`` itself.

No cloud SDK import anywhere in this module (ADR-0004 Decision L / M2.6B
acceptance gate 1) — a concrete implementation (e.g.
``nexa.realtime.gemini.service.GeminiLiveProvider``, M2.6B.2) lives in its
own package and is only imported when that provider is actually selected.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum

from .snapshot import CloudContextSnapshot
from .usage import ProviderUsageEvent


class ProviderReadiness(StrEnum):
    """Lifecycle state of one realtime-provider session (ADR-0004 provider
    interface & lifecycle contract).

    ``CONNECTING`` -> ``CONTEXT_INIT`` -> ``READY``; ``READY`` <-> ``DEGRADED``;
    any state -> ``RECONNECTING`` -> (``READY`` | ``FAILED``); any state ->
    ``FAILED`` on a terminal error.
    """

    CONNECTING = "connecting"
    CONTEXT_INIT = "context_init"
    READY = "ready"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RealtimeProviderCapabilities:
    """Static capabilities of one provider implementation — answerable
    before ``start()`` so a router can plan without connecting first."""

    provider_name: str
    supports_interruption: bool
    supports_session_resumption: bool
    supports_server_transcription: bool
    supports_function_calling: bool
    input_sample_rate_hz: int
    output_sample_rate_hz: int
    #: ``None`` = no known hard cap.
    max_session_seconds: float | None = None


# --------------------------------------------------------------------- #
# Provider -> NeXa event stream. Small, typed, immutable events — not a
# union of loose dicts (ADR-0004 provider interface contract).
# --------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class UserTranscriptionEvent:
    """A transcription of what the user said, for canonical commit
    (ADR-0004 Decision A). ``final=False`` events are informational only —
    only a ``final=True`` event may ever be committed to history."""

    text: str
    final: bool


@dataclass(frozen=True, slots=True)
class AssistantTranscriptionEvent:
    """A transcription of the assistant's spoken output, for canonical
    commit (ADR-0004 Decision A)."""

    text: str
    final: bool


@dataclass(frozen=True, slots=True)
class AssistantAudioEvent:
    """One chunk of assistant speech audio to route to the speaker."""

    pcm: bytes


@dataclass(frozen=True, slots=True)
class ProviderInterruptionEvent:
    """The provider acknowledged an interruption server-side (e.g. Gemini
    ``serverContent.interrupted``). Informational only — NeXa's own local
    barge-in authority (``BargeInController``) is never derived from this
    (ADR-0004 Decision C); the local speaker stop always happens first."""


@dataclass(frozen=True, slots=True)
class CancellationCompleteEvent:
    """The provider confirms it stopped generating after a NeXa-issued
    ``cancel()``."""


@dataclass(frozen=True, slots=True)
class GenerationCompleteEvent:
    """The provider finished generating its reply for the current turn
    (e.g. Gemini's ``serverContent.turnComplete`` / Pipecat's
    ``LLMFullResponseEndFrame``). This is the ``ConversationRouter``'s
    signal to commit the accumulated cloud turn (ADR-0004 Decision A) —
    distinct from any assistant transcription text event, so "turn
    complete" is never conflated with "assistant said nothing"."""


@dataclass(frozen=True, slots=True)
class ReconnectingEvent:
    """The provider is reconnecting (proactive age-timer, ``GoAway``, or a
    connection error) — ADR-0004 Decision I."""

    reason: str


@dataclass(frozen=True, slots=True)
class ResumedEvent:
    """A reconnect completed. ``from_handle=True`` means Gemini restored
    its own context via session resumption (NeXa touches nothing
    canonical); ``False`` means a fresh session was started and NeXa must
    rebuild a ``CloudContextSnapshot`` (ADR-0004 Decision I)."""

    from_handle: bool


@dataclass(frozen=True, slots=True)
class ReadinessChangedEvent:
    readiness: ProviderReadiness


class RealtimeProviderError(Exception):
    """A recoverable provider error — the provider transitions to
    ``DEGRADED`` or ``RECONNECTING``, never silently swallowed."""


class RealtimeProviderFailedError(Exception):
    """A terminal provider failure — the provider transitions to
    ``FAILED``; the caller (``ConversationRouter``, M2.6B.2+) applies the
    ADR-0004 Decision J failure/fallback table."""


#: The provider -> NeXa event stream's element type.
ProviderEvent = (
    UserTranscriptionEvent
    | AssistantTranscriptionEvent
    | AssistantAudioEvent
    | ProviderInterruptionEvent
    | CancellationCompleteEvent
    | GenerationCompleteEvent
    | ReconnectingEvent
    | ResumedEvent
    | ReadinessChangedEvent
    | ProviderUsageEvent
    | RealtimeProviderError
    | RealtimeProviderFailedError
)


class RealtimeVoiceProvider(ABC):
    """A replaceable realtime speech-to-speech conversation backend.

    A peer of ``ModelProvider`` — never a subtype (ADR-0004 Decision B).
    An implementation owns only the mechanics of talking to one realtime
    voice backend; it must never mutate ``ConversationSession`` directly,
    hold NeXa's persona/identity, or decide routing/fallback policy — those
    stay with ``ConversationRouter`` and the rest of NeXa core.
    """

    @abstractmethod
    def capabilities(self) -> RealtimeProviderCapabilities:
        """Static capabilities, answerable before ``start()``."""

    @property
    @abstractmethod
    def readiness(self) -> ProviderReadiness:
        """Current lifecycle state."""

    @abstractmethod
    async def start(self, snapshot: CloudContextSnapshot) -> None:
        """Connect, run setup, seed ``snapshot`` once, become ready.

        ``snapshot`` is the only NeXa-derived context this call may use —
        nothing here may reach for anything not already in ``snapshot``
        (ADR-0004 Decision E).
        """

    @abstractmethod
    async def stop(self, *, reason: str) -> None:
        """Disconnect cleanly and release audio/session resources. Must be
        idempotent — safe to call more than once or on an already-stopped
        provider."""

    @abstractmethod
    async def user_turn_start(self) -> None:
        """NeXa's local turn authority (e.g. Silero VAD) detected the start
        of a user utterance."""

    @abstractmethod
    async def send_user_audio(self, pcm: bytes) -> None:
        """Stream one chunk of already AEC-processed mic PCM.

        Must not be treated as delivered to the model unless
        ``readiness == READY``; while not ``READY`` incoming audio belongs
        in a ``nexa.realtime.inbound_audio_buffer.InboundAudioBuffer``
        (ADR-0004 Decision H), not silently dropped or sent anyway.
        """

    @abstractmethod
    async def user_turn_end(self) -> None:
        """NeXa's local turn authority detected end-of-turn."""

    @abstractmethod
    async def cancel(self) -> None:
        """Ask the provider to stop generating (e.g. on a confirmed local
        barge-in). Completion is reported via ``CancellationCompleteEvent``
        on the event stream, not by this call's return."""

    @abstractmethod
    def events(self) -> AsyncIterator[ProviderEvent]:
        """The provider -> NeXa event stream.

        Consumed by ``ConversationRouter`` (M2.6B.2+); a provider
        implementation never writes to ``ConversationSession`` itself.
        """
