"""Deterministic event deduplication records.

Deduplication prevents re-running an event that already completed. It
complements write-tool idempotency: dedup guards the whole event, while a
write/action tool still carries its own idempotency key inside the governed
execution.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from enum import Enum
from threading import RLock
from typing import Protocol

from pydantic import Field

from ..contracts import AgentOutcome, ContractModel, utc_now


class DedupStatus(str, Enum):
    """Lifecycle of one deduplication record."""

    IN_PROGRESS = "in_progress"
    PENDING_APPROVAL = "pending_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class DeduplicationConflictError(RuntimeError):
    """Raised when a deduplication record would be overwritten unsafely."""


class DedupRecord(ContractModel):
    """One deduplication record for an event key."""

    schema_version: str = "agent-dedup.v1"
    deduplication_key: str
    status: DedupStatus = DedupStatus.IN_PROGRESS
    execution_id: str | None = None
    attempt: int = 0
    correlation_id: str | None = None
    outcome: AgentOutcome | None = None
    lease_owner: str | None = Field(default=None, min_length=1)
    lease_version: int | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class DeduplicationStore(Protocol):
    """Storage boundary for event deduplication records."""

    def get(self, deduplication_key: str) -> DedupRecord | None:
        """Return the current record for a key, if any."""

    def put(self, record: DedupRecord) -> None:
        """Create or replace a record."""

    def mark_in_progress(
        self,
        deduplication_key: str,
        *,
        execution_id: str,
        attempt: int,
        correlation_id: str,
        lease_owner: str,
        lease_version: int,
    ) -> None:
        """Mark a record as actively handled."""

    def mark_pending_approval(
        self,
        deduplication_key: str,
        *,
        execution_id: str,
        lease_owner: str,
        lease_version: int,
        outcome: AgentOutcome,
    ) -> None:
        """Keep a claimed event pending without allowing duplicate handling."""

    def mark_terminal(
        self,
        deduplication_key: str,
        *,
        status: DedupStatus,
        outcome: AgentOutcome | None,
        execution_id: str,
        lease_owner: str,
        lease_version: int,
    ) -> None:
        """Mark a claimed record terminal using its exact claim identity."""


class InMemoryDeduplicationStore:
    """Thread-safe, deterministic deduplication store for tests and local use."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._records: dict[str, DedupRecord] = {}
        self._lock = RLock()

    def get(self, deduplication_key: str) -> DedupRecord | None:
        with self._lock:
            record = self._records.get(deduplication_key)
            return deepcopy(record) if record is not None else None

    def put(self, record: DedupRecord) -> None:
        with self._lock:
            existing = self._records.get(record.deduplication_key)
            if (
                existing is not None
                and existing.status != record.status
                and existing.status
                in {
                    DedupStatus.COMPLETED,
                    DedupStatus.FAILED,
                    DedupStatus.DEAD_LETTERED,
                }
            ):
                raise DeduplicationConflictError(
                    "terminal deduplication record cannot be overwritten"
                )
            self._records[record.deduplication_key] = deepcopy(record)

    def mark_in_progress(
        self,
        deduplication_key: str,
        *,
        execution_id: str,
        attempt: int,
        correlation_id: str,
        lease_owner: str,
        lease_version: int,
    ) -> None:
        if not lease_owner.strip():
            raise ValueError("lease_owner must not be empty")
        if lease_version < 0:
            raise ValueError("lease_version must not be negative")
        with self._lock:
            existing = self._records.get(deduplication_key)
            if existing is not None and existing.status in {
                DedupStatus.PENDING_APPROVAL,
                DedupStatus.COMPLETED,
                DedupStatus.FAILED,
                DedupStatus.DEAD_LETTERED,
            }:
                raise DeduplicationConflictError("event cannot be marked in progress")
            if (
                existing is not None
                and existing.status == DedupStatus.IN_PROGRESS
                and existing.lease_version is not None
                and existing.lease_version > lease_version
            ):
                raise DeduplicationConflictError("stale lease cannot claim event")
            if (
                existing is not None
                and existing.status == DedupStatus.IN_PROGRESS
                and existing.lease_version == lease_version
                and existing.lease_owner not in {None, lease_owner}
            ):
                raise DeduplicationConflictError("deduplication claim belongs to another owner")
            self._records[deduplication_key] = DedupRecord(
                deduplication_key=deduplication_key,
                status=DedupStatus.IN_PROGRESS,
                execution_id=execution_id,
                attempt=attempt,
                correlation_id=correlation_id,
                lease_owner=lease_owner,
                lease_version=lease_version,
                updated_at=self._clock(),
            )

    def mark_pending_approval(
        self,
        deduplication_key: str,
        *,
        execution_id: str,
        lease_owner: str,
        lease_version: int,
        outcome: AgentOutcome,
    ) -> None:
        with self._lock:
            existing = self._require_claim(
                deduplication_key,
                execution_id=execution_id,
                lease_owner=lease_owner,
                lease_version=lease_version,
            )
            if existing.status not in {
                DedupStatus.IN_PROGRESS,
                DedupStatus.PENDING_APPROVAL,
            }:
                raise DeduplicationConflictError("only a claimed event can wait for approval")
            self._records[deduplication_key] = existing.model_copy(
                update={
                    "status": DedupStatus.PENDING_APPROVAL,
                    "outcome": outcome.model_copy(deep=True),
                    "updated_at": self._clock(),
                },
                deep=True,
            )

    def mark_terminal(
        self,
        deduplication_key: str,
        *,
        status: DedupStatus,
        outcome: AgentOutcome | None,
        execution_id: str,
        lease_owner: str,
        lease_version: int,
    ) -> None:
        if status in {DedupStatus.IN_PROGRESS, DedupStatus.PENDING_APPROVAL}:
            raise ValueError("mark_terminal requires a terminal deduplication status")
        with self._lock:
            existing = self._require_claim(
                deduplication_key,
                execution_id=execution_id,
                lease_owner=lease_owner,
                lease_version=lease_version,
            )
            if existing.status in {
                DedupStatus.COMPLETED,
                DedupStatus.FAILED,
                DedupStatus.DEAD_LETTERED,
            }:
                raise DeduplicationConflictError("event already has a terminal outcome")
            self._records[deduplication_key] = DedupRecord(
                deduplication_key=deduplication_key,
                status=status,
                execution_id=execution_id,
                attempt=existing.attempt,
                correlation_id=existing.correlation_id,
                outcome=outcome.model_copy(deep=True) if outcome is not None else None,
                lease_owner=lease_owner,
                lease_version=lease_version,
                updated_at=self._clock(),
            )

    def _require_claim(
        self,
        deduplication_key: str,
        *,
        execution_id: str,
        lease_owner: str,
        lease_version: int,
    ) -> DedupRecord:
        existing = self._records.get(deduplication_key)
        if existing is None:
            raise DeduplicationConflictError("event has no active deduplication claim")
        if (
            existing.execution_id != execution_id
            or existing.lease_owner != lease_owner
            or existing.lease_version != lease_version
        ):
            raise DeduplicationConflictError("deduplication claim does not match")
        return existing


__all__ = [
    "DedupRecord",
    "DedupStatus",
    "DeduplicationConflictError",
    "DeduplicationStore",
    "InMemoryDeduplicationStore",
]
