"""M2.6B — the provider-agnostic Cloud Realtime Voice boundary (ADR-0004).

``RealtimeVoiceProvider`` is a peer of ``nexa.providers.ModelProvider``,
never a subtype. Nothing in this package (nor its submodules re-exported
here) imports a cloud SDK: a concrete provider implementation (e.g.
``nexa.realtime.gemini``, M2.6B.2) lives in its own subpackage and is only
imported when that provider is actually selected, so a ``LOCAL_ONLY`` /
local-first install never pulls in Google cloud code (ADR-0004 Decision L).

M2.6B.1 shipped the provider-agnostic foundation: the
``RealtimeVoiceProvider`` boundary, ``ConversationPolicy`` /
``ProviderEligibilityPolicy``, ``CloudContextSnapshot``, the NeXa-owned
inbound audio buffer (Pipecat #5465 protection), usage telemetry types, and
a deterministic ``ReconnectController``. M2.6B.2 adds the canonical
cloud-turn write path: ``CloudTurnAccumulator`` (one logical cloud turn ->
at most one canonical commit), ``UtteranceFramer`` (preserves the
activity-start/audio/activity-end envelope through a NOT_READY window),
``ConversationRouter`` (owns policy / active_provider / the cloud-turn
commits), and ``ConversationSession.record_external_exchange`` (additive,
in ``nexa.conversation``). ``nexa.realtime.gemini.service.GeminiLiveProvider``
is the first concrete ``RealtimeVoiceProvider`` (needs the optional
``cloud-gemini`` dependency extra at *use* time, not at import time).
"""

from __future__ import annotations

from .inbound_audio_buffer import (
    DEFAULT_MAX_BUFFER_BYTES,
    DEFAULT_MAX_BUFFER_SECONDS,
    BufferedAudioFrame,
    InboundAudioBuffer,
    InboundAudioBufferStats,
)
from .policy import (
    DEFAULT_CONVERSATION_POLICY,
    ActiveProvider,
    ConversationPolicy,
    DistributionMode,
    ProviderEligibilityPolicy,
    conversation_policy_from_env,
    provider_eligibility_policy_from_env,
)
from .provider import (
    AssistantAudioEvent,
    AssistantTranscriptionEvent,
    CancellationCompleteEvent,
    GenerationCompleteEvent,
    ProviderEvent,
    ProviderInterruptionEvent,
    ProviderReadiness,
    ReadinessChangedEvent,
    RealtimeProviderCapabilities,
    RealtimeProviderError,
    RealtimeProviderFailedError,
    RealtimeVoiceProvider,
    ReconnectingEvent,
    ResumedEvent,
    UserTranscriptionEvent,
)
from .reconnect import (
    DEFAULT_MAX_RECONNECT_ATTEMPTS,
    DEFAULT_PROACTIVE_RECONNECT_AGE_S,
    ReconnectController,
    ReconnectOutcome,
    ReconnectTrigger,
    SessionResumptionHandle,
)
from .router import ConversationRouter, ProviderSwitchNotice
from .snapshot import (
    CLOUD_ROLE_CARD,
    DEFAULT_SNAPSHOT_CHAR_BUDGET,
    DEFAULT_SNAPSHOT_TURNS,
    CloudContextSnapshot,
    SnapshotTurn,
    build_cloud_context_snapshot,
)
from .turn import CloudTurn, CloudTurnAccumulator, CloudTurnState
from .turn_framing import EnvelopeEvent, EnvelopeEventKind, UtteranceFramer
from .usage import ProviderUsageEvent, SessionUsageAggregate, UsagePriceTable

__all__ = [
    # provider boundary
    "RealtimeVoiceProvider",
    "ProviderReadiness",
    "RealtimeProviderCapabilities",
    "ProviderEvent",
    "UserTranscriptionEvent",
    "AssistantTranscriptionEvent",
    "AssistantAudioEvent",
    "ProviderInterruptionEvent",
    "CancellationCompleteEvent",
    "GenerationCompleteEvent",
    "ReconnectingEvent",
    "ResumedEvent",
    "ReadinessChangedEvent",
    "RealtimeProviderError",
    "RealtimeProviderFailedError",
    # policy
    "ConversationPolicy",
    "ActiveProvider",
    "DistributionMode",
    "ProviderEligibilityPolicy",
    "DEFAULT_CONVERSATION_POLICY",
    "conversation_policy_from_env",
    "provider_eligibility_policy_from_env",
    # snapshot
    "CloudContextSnapshot",
    "SnapshotTurn",
    "build_cloud_context_snapshot",
    "CLOUD_ROLE_CARD",
    "DEFAULT_SNAPSHOT_TURNS",
    "DEFAULT_SNAPSHOT_CHAR_BUDGET",
    # inbound audio buffer (#5465)
    "InboundAudioBuffer",
    "InboundAudioBufferStats",
    "BufferedAudioFrame",
    "DEFAULT_MAX_BUFFER_SECONDS",
    "DEFAULT_MAX_BUFFER_BYTES",
    # usage
    "ProviderUsageEvent",
    "SessionUsageAggregate",
    "UsagePriceTable",
    # reconnect
    "ReconnectController",
    "ReconnectOutcome",
    "ReconnectTrigger",
    "SessionResumptionHandle",
    "DEFAULT_PROACTIVE_RECONNECT_AGE_S",
    "DEFAULT_MAX_RECONNECT_ATTEMPTS",
    # canonical cloud-turn write path (M2.6B.2)
    "CloudTurnAccumulator",
    "CloudTurn",
    "CloudTurnState",
    "UtteranceFramer",
    "EnvelopeEvent",
    "EnvelopeEventKind",
    "ConversationRouter",
    "ProviderSwitchNotice",
]
