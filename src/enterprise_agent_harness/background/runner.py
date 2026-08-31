"""Background job runner with bounded retry, lease, and dead-letter semantics."""

from __future__ import annotations

import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol
from uuid import uuid4

from pydantic import Field

from ..contracts import AgentOutcome, ContractModel, OutcomeStatus, PrincipalContext, utc_now
from ..observability.audit import AuditLogger, AuditSink, ListAuditSink
from ..observability.failures import (
    ListObservabilityFailureReporter,
    ObservabilityFailureReporter,
)
from .deduplication import (
    DeduplicationConflictError,
    DeduplicationStore,
    DedupRecord,
    DedupStatus,
    InMemoryDeduplicationStore,
)
from .events import EventDisposition, EventEnvelope, FailureCategory
from .leases import (
    InMemoryLeaseStore,
    Lease,
    LeaseConflictError,
    LeaseExpiredError,
    LeaseStore,
)


class JobHandler(Protocol):
    """Application boundary that executes one governed event attempt."""

    def __call__(
        self,
        principal: PrincipalContext,
        input_text: str,
        *,
        correlation_id: str,
        event_id: str,
        trigger_id: str,
        causation_id: str | None,
        attempt: int,
        execution_id: str,
    ) -> AgentOutcome:
        """Run one attempt through the governed runtime and return its outcome."""


class DeadLetterRecord(ContractModel):
    """Evidence retained when a background event reaches a terminal failure."""

    schema_version: str = "agent-dead-letter.v1"
    event_id: str
    trigger_id: str
    correlation_id: str
    execution_id: str | None = None
    attempt: int = 0
    failure_category: FailureCategory
    retry_decision: str
    final_disposition: EventDisposition
    occurred_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str] = Field(default_factory=dict)


class DeadLetterSink(Protocol):
    """Storage boundary for dead-letter evidence."""

    def record(self, record: DeadLetterRecord) -> None:
        """Persist one dead-letter record."""


class ListDeadLetterSink:
    """Thread-safe in-memory dead-letter sink for tests and local use."""

    def __init__(self) -> None:
        self.records: list[DeadLetterRecord] = []
        self._lock = RLock()

    def record(self, record: DeadLetterRecord) -> None:
        with self._lock:
            self.records.append(deepcopy(record))


@dataclass(frozen=True)
class BackgroundRetryPolicy:
    """Bounded retry policy for background event handling."""

    max_attempts: int = 3
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must not be negative")


class JobResult(ContractModel):
    """Structured terminal result of one background event."""

    schema_version: str = "agent-job-result.v1"
    disposition: EventDisposition
    event_id: str
    trigger_id: str
    correlation_id: str
    execution_id: str | None = None
    attempt: int = 0
    outcome: AgentOutcome | None = None
    failure_category: FailureCategory | None = None
    completed_at: datetime = Field(default_factory=utc_now)


class BackgroundJobRunner:
    """Coordinate event handling with dedup, lease, retry, and dead-letter hooks.

    The runner calls an application ``JobHandler``, which is expected to route
    through ``AgentRuntime.execute_event``. It never bypasses the governed
    runtime. The in-memory stores are deterministic and single-process; durable
    queue, lock, and dedup storage are consumer boundaries.
    """

    def __init__(
        self,
        handler: JobHandler,
        *,
        lease_store: LeaseStore | None = None,
        dedup_store: DeduplicationStore | None = None,
        dead_letter_sink: DeadLetterSink | None = None,
        retry_policy: BackgroundRetryPolicy | None = None,
        audit_sink: AuditSink | None = None,
        failure_reporter: ObservabilityFailureReporter | None = None,
        lease_ttl_seconds: float = 30.0,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be greater than zero")
        self.handler = handler
        self.lease_store = lease_store or InMemoryLeaseStore()
        self.dedup_store = dedup_store or InMemoryDeduplicationStore()
        self.dead_letter_sink = dead_letter_sink or ListDeadLetterSink()
        self.retry_policy = retry_policy or BackgroundRetryPolicy()
        self.audit_sink = audit_sink or ListAuditSink()
        self.failure_reporter = failure_reporter or ListObservabilityFailureReporter()
        self.lease_ttl_seconds = lease_ttl_seconds
        self._id = id_factory or (lambda prefix: f"{prefix}_{uuid4().hex[:10]}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._audit = AuditLogger(
            sink=self.audit_sink,
            id_factory=self._id,
            clock=self._clock,
            failure_reporter=self.failure_reporter,
        )

    def run(
        self,
        event: EventEnvelope,
        *,
        principal: PrincipalContext,
        input_text: str | None = None,
        input_extractor: Callable[[EventEnvelope], str] | None = None,
    ) -> JobResult:
        """Handle one event through dedup, lease, bounded retry, and dead-letter."""

        dedup_key = event.dedup_key
        correlation_id = event.correlation_id or f"event:{event.event_id}"
        trigger_id = event.trigger_id

        existing = self.dedup_store.get(dedup_key)
        if existing is not None and existing.status in {
            DedupStatus.PENDING_APPROVAL,
            DedupStatus.COMPLETED,
            DedupStatus.FAILED,
            DedupStatus.DEAD_LETTERED,
        }:
            return self._stored_result(event, existing, principal)

        lease_owner = self._id("lease")
        lease: Lease | None = None
        try:
            lease = self.lease_store.acquire(
                dedup_key,
                lease_owner,
                self.lease_ttl_seconds,
            )
        except LeaseConflictError:
            return self._lease_conflict_result(event, principal)
        assert lease is not None

        try:
            first_execution_id = self._id("execution")
            try:
                self.dedup_store.mark_in_progress(
                    dedup_key,
                    execution_id=first_execution_id,
                    attempt=0,
                    correlation_id=correlation_id,
                    lease_owner=lease_owner,
                    lease_version=lease.version,
                )
            except DeduplicationConflictError:
                stored = self.dedup_store.get(dedup_key)
                if stored is not None and stored.status != DedupStatus.IN_PROGRESS:
                    return self._stored_result(event, stored, principal)
                return self._lease_conflict_result(event, principal)

            self._audit.record(
                event_type="event_received",
                principal=principal,
                execution_id=correlation_id,
                agent_id="background",
                correlation_id=correlation_id,
                source_event_id=event.event_id,
                trigger_id=trigger_id,
                causation_id=event.causation_id,
                metadata={"payload_digest": event.payload_digest},
            )

            resolved_input = input_text
            if resolved_input is None and input_extractor is not None:
                resolved_input = input_extractor(event)
            if resolved_input is None:
                resolved_input = f"{event.source}:{event.event_type}:{event.event_id}"

            attempt = 0
            last_outcome: AgentOutcome | None = None
            last_execution_id: str | None = None
            last_category = FailureCategory.PERMANENT

            while attempt < self.retry_policy.max_attempts:
                attempt += 1
                execution_id = first_execution_id if attempt == 1 else self._id("execution")
                last_execution_id = execution_id
                if attempt > 1:
                    try:
                        self.dedup_store.mark_in_progress(
                            dedup_key,
                            execution_id=execution_id,
                            attempt=attempt - 1,
                            correlation_id=correlation_id,
                            lease_owner=lease_owner,
                            lease_version=lease.version,
                        )
                    except DeduplicationConflictError:
                        return self._lease_conflict_result(event, principal)
                self._audit.record(
                    event_type="attempt_started",
                    principal=principal,
                    execution_id=execution_id,
                    agent_id="background",
                    correlation_id=correlation_id,
                    source_event_id=event.event_id,
                    trigger_id=trigger_id,
                    causation_id=event.causation_id,
                    attempt=attempt,
                )
                try:
                    outcome = self.handler(
                        principal,
                        resolved_input,
                        correlation_id=correlation_id,
                        event_id=event.event_id,
                        trigger_id=trigger_id,
                        causation_id=event.causation_id,
                        attempt=attempt - 1,
                        execution_id=execution_id,
                    )
                except Exception as exc:  # noqa: BLE001 - handler is an extension boundary.
                    last_category = _classify_exception(exc)
                    last_outcome = None
                    self._audit.record(
                        event_type="attempt_failed",
                        principal=principal,
                        execution_id=execution_id,
                        agent_id="background",
                        correlation_id=correlation_id,
                        source_event_id=event.event_id,
                        trigger_id=trigger_id,
                        causation_id=event.causation_id,
                        attempt=attempt,
                        metadata={"failure_category": last_category.value},
                    )
                else:
                    last_outcome = outcome
                    last_category = _classify_outcome(outcome)
                    if _is_success(outcome):
                        self._audit.record(
                            event_type="attempt_succeeded",
                            principal=principal,
                            execution_id=execution_id,
                            agent_id="background",
                            correlation_id=correlation_id,
                            source_event_id=event.event_id,
                            trigger_id=trigger_id,
                            causation_id=event.causation_id,
                            attempt=attempt,
                        )
                        try:
                            self.dedup_store.mark_terminal(
                                dedup_key,
                                status=DedupStatus.COMPLETED,
                                outcome=outcome,
                                execution_id=execution_id,
                                lease_owner=lease_owner,
                                lease_version=lease.version,
                            )
                        except DeduplicationConflictError:
                            return self._lease_conflict_result(event, principal)
                        return JobResult(
                            disposition=EventDisposition.COMPLETED,
                            event_id=event.event_id,
                            trigger_id=trigger_id,
                            correlation_id=correlation_id,
                            execution_id=execution_id,
                            attempt=attempt,
                            outcome=outcome,
                            completed_at=self._clock(),
                        )

                if (
                    last_category == FailureCategory.RETRYABLE_TRANSIENT
                    and attempt < self.retry_policy.max_attempts
                ):
                    self._audit.record(
                        event_type="retry_scheduled",
                        principal=principal,
                        execution_id=execution_id,
                        agent_id="background",
                        correlation_id=correlation_id,
                        source_event_id=event.event_id,
                        trigger_id=trigger_id,
                        causation_id=event.causation_id,
                        attempt=attempt,
                        metadata={"next_attempt": str(attempt + 1)},
                    )
                    if self.retry_policy.backoff_seconds > 0:
                        self._sleep(self.retry_policy.backoff_seconds)
                    continue

                break

            final_disposition, final_category = _final_disposition(last_outcome, last_category)

            if final_disposition == EventDisposition.PENDING_APPROVAL:
                if last_outcome is None or last_execution_id is None:
                    return self._lease_conflict_result(event, principal)
                try:
                    self.dedup_store.mark_pending_approval(
                        dedup_key,
                        execution_id=last_execution_id,
                        lease_owner=lease_owner,
                        lease_version=lease.version,
                        outcome=last_outcome,
                    )
                except DeduplicationConflictError:
                    return self._lease_conflict_result(event, principal)
            else:
                try:
                    self.dedup_store.mark_terminal(
                        dedup_key,
                        status=DedupStatus.DEAD_LETTERED,
                        outcome=last_outcome,
                        execution_id=last_execution_id or first_execution_id,
                        lease_owner=lease_owner,
                        lease_version=lease.version,
                    )
                except DeduplicationConflictError:
                    return self._lease_conflict_result(event, principal)
                record = DeadLetterRecord(
                    event_id=event.event_id,
                    trigger_id=trigger_id,
                    correlation_id=correlation_id,
                    execution_id=last_execution_id,
                    attempt=attempt,
                    failure_category=final_category,
                    retry_decision="no_retry",
                    final_disposition=final_disposition,
                    occurred_at=self._clock(),
                )
                self.dead_letter_sink.record(record)
                self._audit.record(
                    event_type="dead_lettered",
                    principal=principal,
                    execution_id=last_execution_id or correlation_id,
                    agent_id="background",
                    correlation_id=correlation_id,
                    source_event_id=event.event_id,
                    trigger_id=trigger_id,
                    causation_id=event.causation_id,
                    attempt=attempt,
                    metadata={"failure_category": record.failure_category.value},
                )

            return JobResult(
                disposition=final_disposition,
                event_id=event.event_id,
                trigger_id=trigger_id,
                correlation_id=correlation_id,
                execution_id=last_execution_id,
                attempt=attempt,
                outcome=last_outcome,
                failure_category=final_category,
                completed_at=self._clock(),
            )
        finally:
            if lease is not None:
                try:
                    self.lease_store.release(dedup_key, lease_owner)
                except (LeaseConflictError, LeaseExpiredError):
                    pass

    def resolve_pending(
        self,
        event: EventEnvelope,
        outcome: AgentOutcome,
        *,
        principal: PrincipalContext,
    ) -> JobResult:
        """Commit the result of an approval resume without re-running the event."""

        dedup_key = event.dedup_key
        existing = self.dedup_store.get(dedup_key)
        if existing is None or existing.status != DedupStatus.PENDING_APPROVAL:
            raise ValueError("event does not have a pending approval")
        if existing.execution_id != outcome.execution_id:
            raise ValueError("approval outcome does not match the pending execution")
        if (
            outcome.principal_id != principal.principal_id
            or outcome.tenant_id != principal.tenant_id
        ):
            raise ValueError("approval outcome principal does not match the resolver")
        if existing.lease_owner is None or existing.lease_version is None:
            raise ValueError("pending event has incomplete claim evidence")

        category = _classify_outcome(outcome)
        if outcome.status == OutcomeStatus.ESCALATED:
            self.dedup_store.mark_pending_approval(
                dedup_key,
                execution_id=existing.execution_id,
                lease_owner=existing.lease_owner,
                lease_version=existing.lease_version,
                outcome=outcome,
            )
            return self._pending_result(event, existing, principal, outcome=outcome)

        if _is_success(outcome):
            disposition = EventDisposition.COMPLETED
            status = DedupStatus.COMPLETED
        else:
            disposition = EventDisposition.DEAD_LETTERED
            status = DedupStatus.DEAD_LETTERED
        self.dedup_store.mark_terminal(
            dedup_key,
            status=status,
            outcome=outcome,
            execution_id=existing.execution_id,
            lease_owner=existing.lease_owner,
            lease_version=existing.lease_version,
        )
        if disposition == EventDisposition.DEAD_LETTERED:
            self.dead_letter_sink.record(
                DeadLetterRecord(
                    event_id=event.event_id,
                    trigger_id=event.trigger_id,
                    correlation_id=existing.correlation_id
                    or event.correlation_id
                    or event.event_id,
                    execution_id=outcome.execution_id,
                    attempt=existing.attempt + 1,
                    failure_category=category,
                    retry_decision="no_retry",
                    final_disposition=disposition,
                    occurred_at=self._clock(),
                )
            )
        self._audit.record(
            event_type="pending_event_resolved",
            principal=principal,
            execution_id=outcome.execution_id,
            agent_id=outcome.agent_id,
            correlation_id=existing.correlation_id or event.correlation_id or event.event_id,
            source_event_id=event.event_id,
            trigger_id=event.trigger_id,
            causation_id=event.causation_id,
            attempt=existing.attempt + 1,
            outcome_status=outcome.status,
            metadata={"disposition": disposition.value},
        )
        return JobResult(
            disposition=disposition,
            event_id=event.event_id,
            trigger_id=event.trigger_id,
            correlation_id=existing.correlation_id or event.correlation_id or event.event_id,
            execution_id=outcome.execution_id,
            attempt=existing.attempt + 1,
            outcome=outcome,
            failure_category=None if disposition == EventDisposition.COMPLETED else category,
            completed_at=self._clock(),
        )

    def _stored_result(
        self,
        event: EventEnvelope,
        existing: DedupRecord,
        principal: PrincipalContext,
    ) -> JobResult:
        if existing.status == DedupStatus.COMPLETED:
            return self._duplicate_result(event, existing, principal)
        if existing.status == DedupStatus.PENDING_APPROVAL:
            return self._pending_result(event, existing, principal)
        disposition = (
            EventDisposition.FAILED
            if existing.status == DedupStatus.FAILED
            else EventDisposition.DEAD_LETTERED
        )
        correlation_id = (
            existing.correlation_id or event.correlation_id or f"event:{event.event_id}"
        )
        self._audit.record(
            event_type="event_terminal_deduped",
            principal=principal,
            execution_id=existing.execution_id or event.dedup_key,
            agent_id="background",
            correlation_id=correlation_id,
            source_event_id=event.event_id,
            trigger_id=event.trigger_id,
            causation_id=event.causation_id,
            attempt=existing.attempt,
            metadata={"disposition": disposition.value},
        )
        return JobResult(
            disposition=disposition,
            event_id=event.event_id,
            trigger_id=event.trigger_id,
            correlation_id=correlation_id,
            execution_id=existing.execution_id,
            attempt=existing.attempt,
            outcome=existing.outcome,
            failure_category=(
                _classify_outcome(existing.outcome)
                if existing.outcome is not None
                else FailureCategory.PERMANENT
            ),
            completed_at=self._clock(),
        )

    def _pending_result(
        self,
        event: EventEnvelope,
        existing: DedupRecord,
        principal: PrincipalContext,
        *,
        outcome: AgentOutcome | None = None,
    ) -> JobResult:
        correlation_id = (
            existing.correlation_id or event.correlation_id or f"event:{event.event_id}"
        )
        self._audit.record(
            event_type="event_pending_approval_deduped",
            principal=principal,
            execution_id=existing.execution_id or event.dedup_key,
            agent_id="background",
            correlation_id=correlation_id,
            source_event_id=event.event_id,
            trigger_id=event.trigger_id,
            causation_id=event.causation_id,
            attempt=existing.attempt,
        )
        return JobResult(
            disposition=EventDisposition.PENDING_APPROVAL,
            event_id=event.event_id,
            trigger_id=event.trigger_id,
            correlation_id=correlation_id,
            execution_id=existing.execution_id,
            attempt=existing.attempt,
            outcome=outcome or existing.outcome,
            failure_category=FailureCategory.APPROVAL_REQUIRED,
            completed_at=self._clock(),
        )

    def _duplicate_result(
        self,
        event: EventEnvelope,
        existing: DedupRecord,
        principal: PrincipalContext,
    ) -> JobResult:
        correlation_id = (
            existing.correlation_id or event.correlation_id or f"event:{event.event_id}"
        )
        self._audit.record(
            event_type="event_duplicate_deduped",
            principal=principal,
            execution_id=existing.execution_id or event.dedup_key,
            agent_id="background",
            correlation_id=correlation_id,
            source_event_id=event.event_id,
            trigger_id=event.trigger_id,
            causation_id=event.causation_id,
            attempt=existing.attempt,
        )
        return JobResult(
            disposition=EventDisposition.DUPLICATE,
            event_id=event.event_id,
            trigger_id=event.trigger_id,
            correlation_id=correlation_id,
            execution_id=existing.execution_id,
            attempt=existing.attempt,
            outcome=existing.outcome,
            completed_at=self._clock(),
        )

    def _lease_conflict_result(
        self,
        event: EventEnvelope,
        principal: PrincipalContext,
    ) -> JobResult:
        correlation_id = event.correlation_id or f"event:{event.event_id}"
        self._audit.record(
            event_type="lease_conflict",
            principal=principal,
            execution_id=correlation_id,
            agent_id="background",
            correlation_id=correlation_id,
            source_event_id=event.event_id,
            trigger_id=event.trigger_id,
            causation_id=event.causation_id,
        )
        return JobResult(
            disposition=EventDisposition.LEASE_CONFLICT,
            event_id=event.event_id,
            trigger_id=event.trigger_id,
            correlation_id=correlation_id,
            failure_category=FailureCategory.LEASE_LOST,
            completed_at=self._clock(),
        )


def _is_success(outcome: AgentOutcome) -> bool:
    return outcome.status in {OutcomeStatus.COMPLETED, OutcomeStatus.PARTIAL}


def _classify_exception(exc: BaseException) -> FailureCategory:
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        raise exc
    retryable = bool(getattr(exc, "retryable", False))
    code = getattr(exc, "code", None)
    code_value = getattr(code, "value", None)
    if code_value == "execution_cancelled":
        return FailureCategory.CANCELLED
    if code_value == "budget_exhausted":
        return FailureCategory.BUDGET_EXHAUSTED
    if retryable:
        return FailureCategory.RETRYABLE_TRANSIENT
    return FailureCategory.PERMANENT


def _classify_outcome(outcome: AgentOutcome) -> FailureCategory:
    if outcome.status == OutcomeStatus.CANCELLED:
        return FailureCategory.CANCELLED
    if outcome.status == OutcomeStatus.ESCALATED:
        return FailureCategory.APPROVAL_REQUIRED
    if outcome.status == OutcomeStatus.FAILED and any(
        flag.value == "budget_exhausted" for flag in outcome.safety_flags
    ):
        return FailureCategory.BUDGET_EXHAUSTED
    if outcome.status == OutcomeStatus.FAILED:
        return FailureCategory.RETRYABLE_TRANSIENT
    if outcome.status in {OutcomeStatus.NEEDS_INPUT, OutcomeStatus.REFUSED}:
        return FailureCategory.PERMANENT
    return FailureCategory.PERMANENT


def _final_disposition(
    outcome: AgentOutcome | None,
    category: FailureCategory,
) -> tuple[EventDisposition, FailureCategory]:
    if category == FailureCategory.CANCELLED:
        return EventDisposition.CANCELLED, category
    if category == FailureCategory.BUDGET_EXHAUSTED:
        return EventDisposition.FAILED, category
    if category == FailureCategory.APPROVAL_REQUIRED:
        return EventDisposition.PENDING_APPROVAL, category
    return EventDisposition.DEAD_LETTERED, category


__all__ = [
    "BackgroundJobRunner",
    "BackgroundRetryPolicy",
    "DeadLetterRecord",
    "DeadLetterSink",
    "JobHandler",
    "JobResult",
    "ListDeadLetterSink",
]
