"""``ConversationRouter`` — owns ``ConversationPolicy`` / ``active_provider``,
provider lifecycle, ``CloudContextSnapshot`` creation, and the canonical
cloud-turn write path (ADR-0004 Decisions A, D, I, J).

This module imports **no** cloud SDK and constructs no concrete provider
itself: the cloud provider comes from an injected ``cloud_provider_factory``
callable, so ``LOCAL_ONLY`` never even imports
``nexa.realtime.gemini.service`` transitively through this module. A
``RealtimeVoiceProvider`` implementation never mutates
``ConversationSession`` itself (ADR-0004 Decision B) — this router is the
only thing that does, via ``CloudTurnAccumulator`` ->
``ConversationSession.record_external_exchange``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from ..conversation.session import ConversationSession, ExternalExchangeOutcome
from .policy import ActiveProvider, ConversationPolicy, ProviderEligibilityPolicy
from .provider import (
    AssistantTranscriptionEvent,
    GenerationCompleteEvent,
    ProviderEvent,
    ProviderInterruptionEvent,
    RealtimeProviderFailedError,
    RealtimeVoiceProvider,
    UserTranscriptionEvent,
)
from .snapshot import CloudContextSnapshot, build_cloud_context_snapshot
from .turn import CloudTurnAccumulator

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderSwitchNotice:
    """An observable notice of a provider switch (ADR-0004 Decision J — no
    silent fallback: every switch away from cloud produces one of these)."""

    to: ActiveProvider
    reason: str


class ConversationRouter:
    """NeXa's single authority for which provider is active and for every
    cloud-turn commit into the one canonical ``ConversationSession``.

    ``cloud_provider_factory`` is called at most once per ``start_cloud()``
    attempt and never at all under ``LOCAL_ONLY`` — this is how
    ``LOCAL_ONLY`` guarantees it never even constructs a cloud provider,
    never builds a ``CloudContextSnapshot``, and never loads a credential
    (whatever the factory would do to obtain one happens inside the
    factory, which is simply never called).
    """

    def __init__(
        self,
        session: ConversationSession,
        *,
        policy: ConversationPolicy = ConversationPolicy.LOCAL_ONLY,
        eligibility: ProviderEligibilityPolicy | None = None,
        target_user_in_eea_ch_uk: bool = False,
        cloud_provider_factory: Callable[[], RealtimeVoiceProvider] | None = None,
        snapshot_builder: Callable[..., CloudContextSnapshot] = build_cloud_context_snapshot,
        connectivity_check: Callable[[], bool] | None = None,
    ) -> None:
        self._session = session
        self._policy = policy
        self._eligibility = eligibility or ProviderEligibilityPolicy()
        self._target_user_in_eea_ch_uk = target_user_in_eea_ch_uk
        self._cloud_provider_factory = cloud_provider_factory
        self._snapshot_builder = snapshot_builder
        self._connectivity_check = connectivity_check or (lambda: True)
        self._active_provider = ActiveProvider.LOCAL
        self._cloud_provider: RealtimeVoiceProvider | None = None
        self._turn = CloudTurnAccumulator()
        self._notices: list[ProviderSwitchNotice] = []

    # -- policy / state -----------------------------------------------------
    @property
    def policy(self) -> ConversationPolicy:
        return self._policy

    def set_policy(self, policy: ConversationPolicy) -> None:
        """``SetConversationPolicy`` (ADR-0004 Decision D) — NeXa's own
        command surface; a model may only *recognise* a switch intent, it
        never calls this itself."""
        self._policy = policy

    @property
    def active_provider(self) -> ActiveProvider:
        return self._active_provider

    @property
    def notices(self) -> tuple[ProviderSwitchNotice, ...]:
        return tuple(self._notices)

    def _cloud_eligible(self) -> bool:
        if self._policy is ConversationPolicy.LOCAL_ONLY:
            return False
        if not self._eligibility.cloud_allowed_for(
            target_user_in_eea_ch_uk=self._target_user_in_eea_ch_uk
        ):
            return False
        # AUTO is provisional in M2.6B (ADR-0004 Decision D): gated only on
        # reachability + eligibility, no privacy/task/cost classifier.
        return self._connectivity_check()

    # -- provider lifecycle --------------------------------------------------
    async def start_cloud(self, *, language_preference: str | None = None) -> bool:
        """Attempt to start the cloud provider.

        Returns whether it actually started. Under ``LOCAL_ONLY`` this
        returns ``False`` immediately without calling
        ``cloud_provider_factory``, without building a
        ``CloudContextSnapshot``, and without touching anything cloud
        (ADR-0004 acceptance gate 1).
        """
        if self._policy is ConversationPolicy.LOCAL_ONLY:
            return False
        if not self._cloud_eligible():
            return False
        if self._cloud_provider_factory is None:
            return False

        snapshot = self._snapshot_builder(
            self._session,
            language_preference=language_preference,
            policy_name=self._policy.value,
            active_provider_name=ActiveProvider.CLOUD.value,
        )
        provider = self._cloud_provider_factory()
        try:
            await provider.start(snapshot)
        except Exception as exc:  # noqa: BLE001 — any start failure is a fallback trigger
            self._handle_cloud_failure(reason=f"start failed: {exc}")
            return False
        self._cloud_provider = provider
        self._active_provider = ActiveProvider.CLOUD
        return True

    async def stop_cloud(self, *, reason: str) -> None:
        if self._cloud_provider is not None:
            await self._cloud_provider.stop(reason=reason)
            self._cloud_provider = None
        self._active_provider = ActiveProvider.LOCAL

    def _handle_cloud_failure(self, *, reason: str) -> None:
        """ADR-0004 Decision J — explicit failure -> fallback: never a
        silent switch, and never automatic re-entry to cloud (a fresh
        ``start_cloud()`` call, e.g. at the next session boundary, is
        required — this router does not flap on its own)."""
        self._cloud_provider = None
        self._active_provider = ActiveProvider.LOCAL
        notice = ProviderSwitchNotice(to=ActiveProvider.LOCAL, reason=reason)
        self._notices.append(notice)
        logger.warning("nexa.realtime.router: switched to LOCAL — %s", reason)

    async def request_fresh_snapshot_after_resumption_failure(
        self, *, language_preference: str | None = None
    ) -> CloudContextSnapshot:
        """ADR-0004 Decision I (Amendment 1 §4) — resumption failed or was
        unsafe: rebuild from the current canonical ``ConversationSession``.
        A stale provider-owned context is never trusted as canonical."""
        return self._snapshot_builder(
            self._session,
            language_preference=language_preference,
            policy_name=self._policy.value,
            active_provider_name=ActiveProvider.CLOUD.value,
        )

    async def start_fresh_cloud_provider(
        self, *, language_preference: str | None = None, failure_reason_prefix: str = ""
    ) -> RealtimeVoiceProvider | None:
        """M2.6B.4G (R0045) — the shared "destroy-and-recreate" primitive:
        build a fresh ``CloudContextSnapshot`` from canonical NeXa state
        and construct+start a BRAND NEW provider instance from it. Pure
        construction — never touches an old instance (the caller decides
        whether/when to stop one; the two existing callers,
        ``recover_from_mid_turn_loss`` and the barge-in atomic-replacement
        path in ``GeminiVoiceRuntime``, have DIFFERENT pending-audio
        sources and different timing constraints, so replay is each
        caller's own responsibility, never this method's).

        Returns the new provider (already the router's own
        ``_cloud_provider``/``_active_provider``) on success, or ``None``
        if no cloud provider factory is configured or ``start()`` raised
        (either case already routed through the Decision J failure path —
        the caller falls back to LOCAL)."""
        if self._cloud_provider_factory is None:
            self._handle_cloud_failure(
                reason=f"{failure_reason_prefix}no cloud provider factory"
            )
            return None

        snapshot = await self.request_fresh_snapshot_after_resumption_failure(
            language_preference=language_preference
        )
        new_provider = self._cloud_provider_factory()
        try:
            await new_provider.start(snapshot)
        except Exception as exc:  # noqa: BLE001 — a second failure is still a fallback trigger
            self._handle_cloud_failure(
                reason=f"{failure_reason_prefix}fresh session start failed: {exc}"
            )
            return None

        self._cloud_provider = new_provider
        self._active_provider = ActiveProvider.CLOUD
        return new_provider

    async def recover_from_mid_turn_loss(
        self, old_provider: RealtimeVoiceProvider, *, language_preference: str | None = None
    ) -> RealtimeVoiceProvider | None:
        """M2.6B.3 — the mid-turn-unsafe reconnect path (R0034/ADR-0004
        Decision I, hardened): called once ``old_provider.needs_fresh_session``
        is observed True. Destroys ``old_provider`` and starts a brand-new
        one from a freshly rebuilt ``CloudContextSnapshot`` — never trusts
        whatever recovery the old instance's underlying connection
        performed on its own for the turn that was open when it dropped.

        Any not-yet-delivered audio on the old instance
        (``take_pending_audio()``) is replayed into the new provider as
        ONE new, self-contained utterance — never the audio already
        confirmed sent before the loss (that stays with the old, discarded
        instance; ``take_pending_audio`` only ever returns what it never
        delivered). Returns the new provider, or ``None`` if no cloud
        provider factory is configured (nothing to recover into — the
        caller falls back to LOCAL via the normal failure path).
        """
        take_pending = getattr(old_provider, "take_pending_audio", None)
        pending: list[bytes] = take_pending() if callable(take_pending) else []
        await old_provider.stop(reason="mid-turn connection loss — fresh session required")

        new_provider = await self.start_fresh_cloud_provider(
            language_preference=language_preference, failure_reason_prefix="mid-turn loss, "
        )
        if new_provider is None:
            return None
        if pending:
            await new_provider.user_turn_start()
            for chunk in pending:
                await new_provider.send_user_audio(chunk)
            await new_provider.user_turn_end()
        return new_provider

    # -- canonical cloud-turn write path (ADR-0004 Decision A) --------------
    def begin_cloud_turn(self) -> None:
        self._turn.start_turn()

    def on_user_transcription(self, text: str, *, final: bool) -> None:
        self._turn.on_user_transcription(text, final=final)

    def on_assistant_transcription(self, text: str, *, final: bool) -> None:
        self._turn.on_assistant_transcription(text, final=final)

    def on_interruption(self) -> None:
        self._turn.on_interruption()

    def set_spoken_prefix(self, spoken_text: str) -> None:
        self._turn.set_spoken_prefix(spoken_text)

    def commit_cloud_turn(
        self, *, response_language: str | None = None
    ) -> ExternalExchangeOutcome | None:
        """Commit the current cloud turn (called on ``turnComplete`` /
        confirmed interruption). Returns ``None`` if there was nothing to
        commit (no turn in progress, or it was already committed /
        superseded — ``CloudTurnAccumulator`` guarantees at most one
        commit per turn)."""
        turn = self._turn.on_turn_complete()
        if turn is None or not turn.user_text:
            return None
        return self._session.record_external_exchange(
            turn.user_text,
            turn.assistant_text or None,
            interrupted=turn.interrupted,
            response_language=response_language,
        )

    def handle_cloud_session_lost(self, *, reason: str) -> ExternalExchangeOutcome | None:
        """Session/provider lost before an explicit ``turnComplete`` —
        commit whatever was accumulated (spoken-prefix rule applies the
        same way), then apply the Decision J fallback."""
        turn = self._turn.on_session_lost()
        outcome: ExternalExchangeOutcome | None = None
        if turn is not None and turn.user_text:
            outcome = self._session.record_external_exchange(
                turn.user_text, turn.assistant_text or None, interrupted=turn.interrupted
            )
        self._handle_cloud_failure(reason=reason)
        return outcome

    def handle_provider_event(self, event: ProviderEvent) -> ExternalExchangeOutcome | None:
        """Consume ONE event from a ``RealtimeVoiceProvider``'s event
        stream (ADR-0004 provider interface contract) and drive the
        canonical cloud-turn write path from it — this is the *only*
        place a provider's output ever reaches ``ConversationSession``,
        and it always goes through ``CloudTurnAccumulator`` first
        (M2.6B.2A: closes the gap where a test could otherwise call
        ``on_user_transcription`` directly and claim the provider path
        works without ever driving it from a real event).

        Returns whatever ``commit_cloud_turn`` /
        ``handle_cloud_session_lost`` returns when the event closes a
        turn; ``None`` for every other event (mid-turn events, telemetry,
        recoverable errors — none of those commit anything by
        themselves).
        """
        if isinstance(event, UserTranscriptionEvent):
            self.on_user_transcription(event.text, final=event.final)
            return None
        if isinstance(event, AssistantTranscriptionEvent):
            self.on_assistant_transcription(event.text, final=event.final)
            return None
        if isinstance(event, ProviderInterruptionEvent):
            self.on_interruption()
            return None
        if isinstance(event, GenerationCompleteEvent):
            return self.commit_cloud_turn()
        if isinstance(event, RealtimeProviderFailedError):
            return self.handle_cloud_session_lost(reason=str(event))
        # ReadinessChangedEvent / ProviderUsageEvent / CancellationCompleteEvent
        # / ReconnectingEvent / ResumedEvent / RealtimeProviderError:
        # telemetry or reconnect-policy concerns, not canonical-turn state
        # (M2.6B.3 wires reconnect policy off these).
        return None
