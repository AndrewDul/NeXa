"""Gemini Live ``recall_context(query)`` tool adapter (R0079 / R0078
Revision 2 §5): maps Gemini's function-call round-trip onto
``ContextEngine.recall()`` — the provider-neutral Core recall entry mode
(``nexa.core.context``) — and back onto a minimal, privacy-folded
``FunctionResponse``.

Lives under ``nexa.realtime.gemini``, never ``nexa.core`` — Gemini-specific
tool-schema/``FunctionCallParams`` shapes never appear inside Core, and
Core never imports this module (the ``nexa.core -> never nexa.realtime``
invariant, unchanged).

Nothing here becomes a second memory authority: :class:`RecallExecutor`
owns exactly one long-lived :class:`~nexa.bootstrap.ContextRuntime`
(the same composition root ``apps/nexa_chat.py`` already uses via
``build_default_context_runtime()``), constructed once, reused for every
``recall_context`` call for the adapter's lifetime — never rebuilt per
call, never a Gemini-owned ``MemoryService``/``ContextEngine``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ...bootstrap import ContextRuntime, build_default_context_runtime
from ...core.context.models import RecallOutcome, RecallRequest, RecallResult
from ...core.privacy import CloudEligibility

logger = logging.getLogger(__name__)

RECALL_TOOL_NAME = "recall_context"

#: R0079 §17 — concise, does not force recall on every turn, does not
#: expose internal namespaces. Final wording is expected to be iterated
#: against the live tool-use compliance corpus (R0079 §18) once real model
#: access is available; this is the starting point, not a claim of
#: measured effectiveness.
RECALL_TOOL_USE_INSTRUCTION = (
    "Use NeXa Core recall when answering requires personal, project-specific, "
    "remembered, or user-specific information that is not already present in "
    "the current session context. Call the recall_context tool with a short "
    "plain-language query. Do not guess such information. Do not use recall "
    "for ordinary general-world knowledge that does not depend on NeXa's "
    "stored state."
)

#: R0079 §10/§11 — Core-owned bound on how long one recall() call may take
#: before the adapter gives up waiting and returns UNAVAILABLE. Separate
#: from (and defense-in-depth alongside) Pipecat's own
#: ``register_function(..., timeout_secs=...)`` — this one is owned by the
#: adapter itself, not dependent on any particular provider's own timeout
#: mechanism. R0077 measured ContextEngine's own build at ~1.4ms; this
#: bound is generous headroom, not a tuned SLA.
DEFAULT_RECALL_TIMEOUT_SECS = 3.0


def recall_tool_schema() -> Any:
    """Builds the Gemini function-declaration schema for ``recall_context``
    — exactly ONE caller-controlled field (``query``), per R0079 §11/§14.
    No namespace/privacy/budget/statuses/scope field is ever exposed to
    the provider; every other retrieval concern is Core-decided (see
    ``ContextEngine.recall()``/``RecallRequest``)."""
    from pipecat.adapters.schemas.function_schema import FunctionSchema

    return FunctionSchema(
        name=RECALL_TOOL_NAME,
        description=(
            "Ask NeXa Core to recall a specific fact or piece of context "
            "relevant to answering the current question."
        ),
        properties={
            "query": {
                "type": "string",
                "description": "What you need to recall, in plain language.",
            }
        },
        required=["query"],
    )


def to_wire_response(result: RecallResult) -> dict[str, Any]:
    """``RecallResult`` -> minimal Gemini ``FunctionResponse`` dict
    (R0079 §12/§13/§15).

    Privacy folding happens HERE, at the cloud wire boundary — never
    inside Core: only ``CLOUD_SAFE`` items ever cross; ``LOCAL_ONLY`` and
    ``CLOUD_WITH_USER_APPROVAL`` items are excluded and the result is the
    SAME wire shape as a genuine ``NO_MATCH`` (R0078 Revision 2 §10) — the
    provider cannot distinguish "nothing found" from "something private
    was found and withheld," and never learns the count, existence, or
    privacy label of an excluded item. Never sends namespaces, descriptor
    IDs, Memory IDs, source refs, trace, or privacy labels — ``facts`` is
    plain recalled text only."""
    if result.outcome is RecallOutcome.FOUND:
        safe_facts = [
            item.content
            for item in result.items
            if item.cloud_eligibility is CloudEligibility.CLOUD_SAFE
        ]
        if not safe_facts:
            # Everything Core selected was non-cloud-eligible -- wire-
            # indistinguishable from NO_MATCH, on purpose (R0078 Rev2 §10).
            return {"status": "no_match"}
        return {"status": "found", "facts": safe_facts}
    if result.outcome is RecallOutcome.UNAVAILABLE:
        return {"status": "unavailable"}
    # NO_MATCH and PERMISSION_REQUIRED both fold to no_match at the wire
    # boundary -- Core may still distinguish them internally via
    # result.outcome for future local/UI approval handling, but the
    # provider never learns which one it was.
    return {"status": "no_match"}


class RecallExecutor:
    """Owns ONE background thread and the SQLite-backed
    :class:`~nexa.bootstrap.ContextRuntime` constructed on it (R0079 §10/§22).

    Why a dedicated single-thread executor, not a bare
    ``asyncio.to_thread()``: the SQLite connection
    ``nexa.core.storage.sqlite.connect()`` opens is thread-affine
    (``check_same_thread`` defaults ``True``) — it must be both CREATED
    and USED on the same OS thread for its whole lifetime.
    ``asyncio.to_thread()`` draws from Python's shared default thread
    pool, which does NOT guarantee the same worker thread across calls, so
    a bare ``asyncio.to_thread(engine.recall, ...)`` would intermittently
    touch the connection from a thread that didn't create it and raise
    ``sqlite3.ProgrammingError``. A ``ThreadPoolExecutor(max_workers=1)``
    is a single, persistent OS thread for its entire lifetime (standard
    library, documented behavior) — so BOTH construction
    (``build_default_context_runtime()``, inside :meth:`start`) and every
    subsequent :meth:`recall` call are submitted to that exact one thread
    via ``loop.run_in_executor()``, which is what makes
    ``asyncio.wait_for()`` meaningful here: the await point actually
    yields control back to the event loop while the background thread
    runs, unlike a bare synchronous call (which ``asyncio.wait_for()``
    could never preempt, since asyncio cancellation only fires at await
    points).

    Known limitation, documented rather than hidden: a timeout only stops
    the ADAPTER from waiting — Python cannot forcibly kill a running
    thread, so an unusually slow ``recall()`` call keeps occupying the one
    worker thread and would delay whichever call is queued behind it.
    Acceptable for V1: local SQLite reads here are fast (``ContextEngine``
    measured ~1.4ms end-to-end in R0077); this is not a general-purpose
    executor.
    """

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nexa-recall")
        self._runtime: ContextRuntime | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._runtime = await loop.run_in_executor(
            self._executor, build_default_context_runtime
        )

    @property
    def started(self) -> bool:
        return self._runtime is not None

    async def recall(
        self, request: RecallRequest, *, timeout_secs: float = DEFAULT_RECALL_TIMEOUT_SECS
    ) -> RecallResult:
        if self._runtime is None:
            raise RuntimeError("RecallExecutor.start() must be awaited before recall()")
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self._executor, self._runtime.context_engine.recall, request
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout_secs)
        except TimeoutError:
            logger.warning(
                "nexa.realtime.gemini.core_recall_tool: recall() exceeded %.1fs -- "
                "returning UNAVAILABLE (R0079 §10, fail-safe)",
                timeout_secs,
            )
            return RecallResult(outcome=RecallOutcome.UNAVAILABLE)

    def close(self) -> None:
        """Best-effort teardown. Never raises -- mirrors
        ``CloudRealtimeConversationAdapter.stop()``'s own best-effort
        convention.

        The connection close is submitted to the SAME worker thread that
        created it (via the raw ``concurrent.futures.Executor.submit()``,
        not ``asyncio`` — ``close()`` is a plain sync method, callable
        from outside a running event loop) and waited on synchronously —
        calling ``connection.close()`` directly from whatever thread calls
        ``close()`` would be the exact thread-affinity violation this
        whole class exists to avoid (caught by
        ``test_gemini_core_recall_tool.py``'s own regression test)."""
        if self._runtime is not None:
            runtime = self._runtime
            self._runtime = None
            try:
                self._executor.submit(runtime.connection.close).result(timeout=5.0)
            except Exception:  # noqa: BLE001 -- best-effort teardown
                logger.warning(
                    "nexa.realtime.gemini.core_recall_tool: error closing connection",
                    exc_info=True,
                )
        self._executor.shutdown(wait=False)


def make_recall_handler(
    executor: RecallExecutor, *, timeout_secs: float = DEFAULT_RECALL_TIMEOUT_SECS
) -> Callable[[Any], Coroutine[Any, Any, None]]:
    """Builds the Pipecat ``FunctionCallHandler`` for ``recall_context``.

    Treats every provider-supplied argument as untrusted (R0079 §11):
    reads ONLY ``arguments["query"]``; any other key is ignored. A blank
    or oversized query is a normal, expected "nothing useful to recall"
    case (folded to ``no_match``), not an adapter failure — bounded/
    validated entirely by ``RecallRequest.__post_init__`` itself, no
    duplicated validation here."""

    async def _handle_recall(params: Any) -> None:
        raw_query = params.arguments.get("query", "")
        try:
            request = RecallRequest(query_text=str(raw_query))
        except ValueError:
            await params.result_callback(
                to_wire_response(RecallResult(outcome=RecallOutcome.NO_MATCH))
            )
            return

        try:
            result = await executor.recall(request, timeout_secs=timeout_secs)
        except Exception:
            logger.warning(
                "nexa.realtime.gemini.core_recall_tool: recall_context handler failed",
                exc_info=True,
            )
            result = RecallResult(outcome=RecallOutcome.UNAVAILABLE)

        await params.result_callback(to_wire_response(result))

    return _handle_recall


def register_recall_tool(
    llm: Any, executor: RecallExecutor, *, timeout_secs: float = DEFAULT_RECALL_TIMEOUT_SECS
) -> None:
    """Registers ``recall_context`` on an already-constructed
    ``GeminiLiveLLMService``.

    ``cancel_on_interruption=True`` (Pipecat's own documented default,
    left explicit here rather than relying on it implicitly) is the
    BLOCKING/synchronous-equivalent mode R0079 §2/§8 requires: the LLM
    waits for the result before continuing, rather than proceeding
    immediately and receiving the result later as a developer message.
    See R0078 Revision 2 §8 / R0079 §2 for the cited SDK evidence this
    choice is based on (installed Pipecat 1.8.1,
    ``GeminiLiveLLMService.register_function``'s own documented default)
    — implementation must still re-verify against the exact configured
    model before this is trusted in production (R0079's own §2
    requirement; nothing in this module substitutes for that check)."""
    llm.register_function(
        RECALL_TOOL_NAME,
        make_recall_handler(executor, timeout_secs=timeout_secs),
        cancel_on_interruption=True,
        timeout_secs=timeout_secs,
    )
