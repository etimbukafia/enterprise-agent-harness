"""Structured audit event creation and storage."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from threading import RLock
from typing import Protocol

from pydantic import Field, model_validator

from ..contracts import ContractModel, OutcomeStatus, PrincipalContext, SafetyFlag
from .failures import (
    ListObservabilityFailureReporter,
    ObservabilityFailureReporter,
    report_observability_failure,
)
from .redaction import DefaultRedactor, Redactor


class AuditEvent(ContractModel):
    """Audit record without raw caller input or tool output."""

    schema_version: str = "agent-audit.v1"
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    occurred_at: datetime
    execution_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    correlation_id: str = Field(default="root", min_length=1)
    parent_execution_id: str | None = Field(default=None, min_length=1)
    delegation_id: str | None = Field(default=None, min_length=1)
    delegation_depth: int = Field(default=0, ge=0, le=100)
    source_event_id: str | None = Field(default=None, min_length=1)
    trigger_id: str | None = Field(default=None, min_length=1)
    causation_id: str | None = Field(default=None, min_length=1)
    attempt: int = Field(default=0, ge=0)
    outcome_status: OutcomeStatus | None = None
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def timestamp_is_aware(self) -> AuditEvent:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include timezone information")
        if self.parent_execution_id is None and self.delegation_depth != 0:
            raise ValueError("root audit event cannot have a delegation depth")
        if self.parent_execution_id is not None and self.delegation_depth < 1:
            raise ValueError("delegated audit event must have a positive delegation depth")
        return self


class AuditSink(Protocol):
    """Storage boundary for audit events."""

    def append(self, event: AuditEvent) -> None:
        """Persist one event."""


class ListAuditSink:
    """Thread-safe in-memory sink for deterministic runs and tests."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self._lock = RLock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self.events.append(deepcopy(event))

    def by_execution(self, execution_id: str) -> list[AuditEvent]:
        with self._lock:
            return [deepcopy(event) for event in self.events if event.execution_id == execution_id]


class AuditLogger:
    """Write identity-, action-, and outcome-bound audit records."""

    def __init__(
        self,
        *,
        sink: AuditSink,
        id_factory: Callable[[str], str],
        clock: Callable[[], datetime],
        redactor: Redactor | None = None,
        failure_reporter: ObservabilityFailureReporter | None = None,
    ) -> None:
        self.sink = sink
        self._id = id_factory
        self._clock = clock
        self._redactor = redactor or DefaultRedactor()
        self.failure_reporter = failure_reporter or ListObservabilityFailureReporter()

    def record(
        self,
        *,
        event_type: str,
        principal: PrincipalContext,
        execution_id: str,
        agent_id: str,
        correlation_id: str = "root",
        parent_execution_id: str | None = None,
        delegation_id: str | None = None,
        delegation_depth: int = 0,
        source_event_id: str | None = None,
        trigger_id: str | None = None,
        causation_id: str | None = None,
        attempt: int = 0,
        outcome_status: OutcomeStatus | None = None,
        safety_flags: list[SafetyFlag] | tuple[SafetyFlag, ...] = (),
        tool_ids: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Write one redacted structured event without failing the caller."""

        safe_metadata = {
            key: self._redactor.redact_value(key, value)
            for key, value in (metadata or {}).items()
            if key and value and not self._redactor.redact_key(key)
        }
        event = AuditEvent(
            event_id=self._id("audit"),
            event_type=event_type,
            occurred_at=self._clock(),
            execution_id=execution_id,
            agent_id=agent_id,
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
            session_id=principal.session_id,
            correlation_id=correlation_id,
            parent_execution_id=parent_execution_id,
            delegation_id=delegation_id,
            delegation_depth=delegation_depth,
            source_event_id=source_event_id,
            trigger_id=trigger_id,
            causation_id=causation_id,
            attempt=attempt,
            outcome_status=outcome_status,
            safety_flags=list(safety_flags),
            tool_ids=list(tool_ids or []),
            metadata=safe_metadata,
        )
        _append_safely(
            self.sink,
            event,
            reporter=self.failure_reporter,
            id_factory=self._id,
            clock=self._clock,
        )


def _append_safely(
    sink: AuditSink,
    event: AuditEvent,
    *,
    reporter: ObservabilityFailureReporter,
    id_factory: Callable[[str], str],
    clock: Callable[[], datetime],
) -> None:
    """Persist one audit event without letting a sink failure change governance."""

    try:
        sink.append(event)
    except Exception as exc:  # noqa: BLE001 - observability persistence is best effort.
        report_observability_failure(
            reporter,
            id_factory=id_factory,
            clock=clock,
            sink=sink,
            operation="audit_append",
            execution_id=event.execution_id,
            correlation_id=event.correlation_id,
            error=exc,
        )
