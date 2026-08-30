"""Structured audit event creation and storage."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from threading import RLock
from typing import Protocol

from pydantic import Field, model_validator

from ..contracts import ContractModel, OutcomeStatus, PrincipalContext, SafetyFlag


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
    outcome_status: OutcomeStatus | None = None
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def timestamp_is_aware(self) -> AuditEvent:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include timezone information")
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

    _BLOCKED_KEY_PARTS = (
        "message",
        "prompt",
        "text",
        "content",
        "output",
        "secret",
        "token",
        "credential",
        "password",
    )

    def __init__(
        self,
        *,
        sink: AuditSink,
        id_factory: Callable[[str], str],
        clock: Callable[[], datetime],
    ) -> None:
        self.sink = sink
        self._id = id_factory
        self._clock = clock

    def record(
        self,
        *,
        event_type: str,
        principal: PrincipalContext,
        execution_id: str,
        agent_id: str,
        outcome_status: OutcomeStatus | None = None,
        safety_flags: list[SafetyFlag] | tuple[SafetyFlag, ...] = (),
        tool_ids: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Write one redacted structured event."""

        safe_metadata = {
            key: value[:200]
            for key, value in (metadata or {}).items()
            if key and value and not any(part in key.lower() for part in self._BLOCKED_KEY_PARTS)
        }
        self.sink.append(
            AuditEvent(
                event_id=self._id("audit"),
                event_type=event_type,
                occurred_at=self._clock(),
                execution_id=execution_id,
                agent_id=agent_id,
                principal_id=principal.principal_id,
                tenant_id=principal.tenant_id,
                session_id=principal.session_id,
                outcome_status=outcome_status,
                safety_flags=list(safety_flags),
                tool_ids=list(tool_ids or []),
                metadata=safe_metadata,
            )
        )
