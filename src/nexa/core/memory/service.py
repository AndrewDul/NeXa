"""``MemoryService`` — the one canonical local NeXa Memory authority
(R0073). No cloud provider, no local/cloud LLM, ever owns memory; a model
may only PROPOSE an operation (as a ``DURABLE_CANDIDATE`` write), this
service alone decides and persists it.

Provider-neutral by design (R0073 final review §11): this module has no
knowledge of Gemini, ``CloudContextSnapshot``, or prompt construction — it
knows only privacy/export-eligibility metadata (``CloudEligibility``) and
how to filter/query by it. A future M3.3 Context Engine is the layer that
selects relevant memories and hands them to a provider-specific projection.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from ..privacy import CloudEligibility
from .errors import MemoryNotFoundError, MemoryValidationError
from .models import (
    HardDeleteReasonCode,
    MemoryCategory,
    MemoryEvidence,
    MemoryProvenance,
    MemoryRecord,
    MemoryRelation,
    MemoryStatus,
    MemoryWriteTrigger,
    Page,
    RelationStatus,
    RememberResult,
)
from .repository import MemoryRepository
from .validation import (
    validate_namespace,
    validate_payload,
    validate_reason_code,
    validate_record_type,
)

logger = logging.getLogger(__name__)

#: categories whose old value naturally becomes historically-closed by a
#: newer valid_from at supersession time (R0073 §5/§2) -- EPISODE/EVENT are
#: point-in-time and left alone (valid_until stays whatever it already was,
#: normally null/not-applicable).
_TEMPORAL_CLOSE_CATEGORIES = frozenset({MemoryCategory.FACT, MemoryCategory.STATE})


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MemoryService:
    def __init__(self, repository: MemoryRepository) -> None:
        self._repo = repository

    @classmethod
    def from_connection(cls, conn: sqlite3.Connection) -> MemoryService:
        return cls(MemoryRepository(conn))

    # ---- write policy (R0073 §P / final review §3) ----

    def remember(
        self,
        trigger: MemoryWriteTrigger,
        *,
        namespace: str,
        category: MemoryCategory,
        record_type: str,
        content: str,
        payload: dict | None = None,
        payload_version: int = 1,
        scope_type: str | None = None,
        scope_id: str | None = None,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        provenance: MemoryProvenance,
        source_kind: str | None = None,
        source_ref: str | None = None,
        confidence: float | None = None,
        observed_at: datetime | None = None,
        cloud_eligibility: CloudEligibility = CloudEligibility.LOCAL_ONLY,
    ) -> RememberResult:
        """Rejected outright: "every conversation sentence -> permanent
        memory." This is the only entry point that creates a new active
        memory, and it always requires an explicit trigger.

        ``DURABLE_CANDIDATE`` requires ``confidence`` -- an unattributed
        guess is never silently written as if certain.

        Dedup: an exact-match ``(namespace, record_type, scope_type,
        scope_id, content)`` hit against an ACTIVE record does NOT discard
        this call's evidence -- it ADDS a new, independent
        :class:`~.models.MemoryEvidence` row to the existing record and
        returns its id, ``was_new_memory=False`` (R0073 final review §3).
        """
        validate_namespace(namespace)
        validate_record_type(record_type, namespace=namespace)
        validate_payload(payload)  # raises on bad input; repository does the real serialization
        if trigger is MemoryWriteTrigger.DURABLE_CANDIDATE and confidence is None:
            raise MemoryValidationError(
                "DURABLE_CANDIDATE writes require an explicit confidence -- "
                "a model's guess is never silently written as certain"
            )

        now = _utcnow()
        with self._repo.conn:
            existing_id = self._repo.find_active_duplicate(
                namespace=namespace, record_type=record_type,
                scope_type=scope_type, scope_id=scope_id, content=content,
            )
            if existing_id is not None:
                evidence = MemoryEvidence(
                    id=_new_id(), memory_id=existing_id, provenance=provenance,
                    source_kind=source_kind, source_ref=source_ref,
                    confidence=confidence, observed_at=observed_at, created_at=now,
                    payload=None,
                )
                self._repo.insert_evidence(evidence)
                return RememberResult(
                    memory_id=existing_id, evidence_id=evidence.id, was_new_memory=False
                )

            record_id = _new_id()
            record = MemoryRecord(
                id=record_id, namespace=namespace, category=category, record_type=record_type,
                payload_version=payload_version, scope_type=scope_type, scope_id=scope_id,
                content=content, payload=payload, valid_from=valid_from, valid_until=valid_until,
                created_at=now, updated_at=now, cloud_eligibility=cloud_eligibility,
                status=MemoryStatus.ACTIVE, supersedes_id=None,
            )
            self._repo.insert_record(record)
            evidence = MemoryEvidence(
                id=_new_id(), memory_id=record_id, provenance=provenance,
                source_kind=source_kind, source_ref=source_ref, confidence=confidence,
                observed_at=observed_at, created_at=now, payload=None,
            )
            self._repo.insert_evidence(evidence)
            return RememberResult(
                memory_id=record_id, evidence_id=evidence.id, was_new_memory=True
            )

    def update_metadata(self, id_: str, *, cloud_eligibility: CloudEligibility) -> None:
        """The ONLY metadata mutation available -- content/namespace/
        record_type/etc. are never silently overwritten in place (use
        :meth:`supersede`). Record-level confidence does not exist in
        M3.2 (R0073 final review §2) -- evidence-level confidence is the
        only confidence value, and it is set once, at evidence creation,
        never mutated."""
        if self._repo.by_id(id_) is None:
            raise MemoryNotFoundError(id_)
        with self._repo.conn:
            self._repo.update_cloud_eligibility(id_, cloud_eligibility)

    def supersede(
        self,
        old_id: str,
        new_content: str,
        *,
        provenance: MemoryProvenance,
        payload: dict | None = None,
        payload_version: int = 1,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        source_kind: str | None = None,
        source_ref: str | None = None,
        confidence: float | None = None,
        observed_at: datetime | None = None,
        cloud_eligibility: CloudEligibility | None = None,
    ) -> RememberResult:
        """Content is never overwritten in place. Inserts a new ACTIVE
        record with ``supersedes_id=old_id``, its own first evidence row,
        and flips ``old_id`` to SUPERSEDED. For FACT/STATE categories with
        an explicit new ``valid_from``, the old record's ``valid_until`` is
        closed to that timestamp (R0073 §5) -- EPISODE/EVENT/PREFERENCE
        supersession leaves ``valid_until`` untouched (not applicable to a
        point-in-time record)."""
        old = self._repo.by_id(old_id)
        if old is None:
            raise MemoryNotFoundError(old_id)
        validate_payload(payload)

        now = _utcnow()
        with self._repo.conn:
            new_id = _new_id()
            record = MemoryRecord(
                id=new_id, namespace=old.namespace, category=old.category,
                record_type=old.record_type, payload_version=payload_version,
                scope_type=old.scope_type, scope_id=old.scope_id, content=new_content,
                payload=payload, valid_from=valid_from, valid_until=valid_until,
                created_at=now, updated_at=now,
                cloud_eligibility=(
                    cloud_eligibility if cloud_eligibility is not None else old.cloud_eligibility
                ),
                status=MemoryStatus.ACTIVE, supersedes_id=old_id,
            )
            self._repo.insert_record(record)
            evidence = MemoryEvidence(
                id=_new_id(), memory_id=new_id, provenance=provenance,
                source_kind=source_kind, source_ref=source_ref, confidence=confidence,
                observed_at=observed_at, created_at=now, payload=None,
            )
            self._repo.insert_evidence(evidence)

            should_close = (
                old.category in _TEMPORAL_CLOSE_CATEGORIES and valid_from is not None
            )
            close_until = valid_from if should_close else None
            self._repo.mark_superseded(old_id, valid_until=close_until)

        return RememberResult(memory_id=new_id, evidence_id=evidence.id, was_new_memory=True)

    # ---- lifecycle (R0073 final review §10) ----

    def retract(self, id_: str) -> None:
        """"Stop using this as active/historical knowledge" -- withdrawn
        or invalidated (e.g. the user said it was simply wrong), NOT
        merely "old" (an ordinary stale fact/preference is SUPERSEDED by
        its replacement instead, via :meth:`supersede`). The row still
        physically exists (audit-preserved); excluded from both default
        "current knowledge" queries AND ``valid_at()``'s historical
        default (R0073 final review §1) -- retrieve it only via an
        explicit ``statuses={MemoryStatus.RETRACTED}`` audit query."""
        if self._repo.by_id(id_) is None:
            raise MemoryNotFoundError(id_)
        with self._repo.conn:
            self._repo.mark_retracted(id_)
        logger.info("memory retract id=%s", id_)

    def hard_delete(self, id_: str, *, reason_code: str | HardDeleteReasonCode) -> None:
        """Explicit, durable, physical removal -- the "forget this"
        product path (R0073 final review §4/§10), distinct from
        :meth:`retract`. ``reason_code`` is a SHORT, VALIDATED, MACHINE-
        ORIENTED token (e.g. ``"user_requested"``) -- never free text.

        Logging discipline: the log line contains ONLY the memory id, the
        reason CODE, and a timestamp -- never ``content``, ``payload``,
        ``source_ref``, or any evidence payload. A user who asks NeXa to
        forget something precisely because it is sensitive must not find
        that information surviving in a debug log (R0073 final review §4).
        """
        code = reason_code.value if isinstance(reason_code, HardDeleteReasonCode) else reason_code
        validate_reason_code(code)
        if self._repo.by_id(id_) is None:
            raise MemoryNotFoundError(id_)
        with self._repo.conn:
            self._repo.physically_delete_record(id_)
        logger.info("memory hard_delete id=%s reason_code=%s", id_, code)

    def relate(self, source_id: str, relation_type: str, target_id: str) -> str:
        if self._repo.by_id(source_id) is None:
            raise MemoryNotFoundError(source_id)
        if self._repo.by_id(target_id) is None:
            raise MemoryNotFoundError(target_id)
        relation = MemoryRelation(
            id=_new_id(), source_id=source_id, relation_type=relation_type,
            target_id=target_id, created_at=_utcnow(), status=RelationStatus.ACTIVE,
        )
        with self._repo.conn:
            self._repo.insert_relation(relation)
        return relation.id

    def unrelate(self, relation_id: str) -> None:
        with self._repo.conn:
            self._repo.soft_delete_relation(relation_id)

    # ---- reads (thin passthrough -- MemoryService is the one entry point
    # external callers use; MemoryRepository stays an internal detail) ----

    def by_id(self, id_: str) -> MemoryRecord | None:
        return self._repo.by_id(id_)

    def by_ids(self, ids: Iterable[str]) -> tuple[MemoryRecord, ...]:
        return self._repo.by_ids(list(ids))

    def by_namespace(self, namespace: str, **kwargs) -> Page[MemoryRecord]:
        return self._repo.by_namespace(namespace, **kwargs)

    def by_scope(self, scope_type: str, scope_id: str, **kwargs) -> Page[MemoryRecord]:
        return self._repo.by_scope(scope_type, scope_id, **kwargs)

    def recent(self, limit: int, **kwargs) -> Page[MemoryRecord]:
        return self._repo.recent(limit, **kwargs)

    def valid_at(self, at: datetime, **kwargs) -> Page[MemoryRecord]:
        return self._repo.valid_at(at, **kwargs)

    def evidence_for(self, memory_id: str, **kwargs) -> Page[MemoryEvidence]:
        return self._repo.evidence_for(memory_id, **kwargs)

    def related_to(self, memory_id: str, **kwargs) -> tuple[MemoryRelation, ...]:
        return self._repo.related_to(memory_id, **kwargs)
