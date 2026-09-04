"""Structured trace recording for runtime decisions."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from threading import RLock
from typing import Any, Protocol

from ..contracts import (
    ComponentReference,
    ExecutionContext,
    OutcomeStatus,
    PolicyDecision,
    ToolExecutionRecord,
)
from ..evaluation.contracts import RunTrace, TraceEvent
from ..providers.contracts import ProviderCallMetadata, ProviderCallRecord, ProviderOperation
from .failures import (
    ListObservabilityFailureReporter,
    ObservabilityFailureReporter,
    report_observability_failure,
)
from .metrics import CostModel, aggregate_metrics
from .redaction import DefaultRedactor, Redactor


class TraceSink(Protocol):
    """Storage boundary for structured trace events."""

    def append(self, event: TraceEvent) -> None:
        """Persist one trace event."""


class ListTraceSink:
    """Thread-safe in-memory trace sink for deterministic runs and tests."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self._lock = RLock()

    def append(self, event: TraceEvent) -> None:
        with self._lock:
            self.events.append(deepcopy(event))

    def by_execution(self, execution_id: str) -> list[TraceEvent]:
        with self._lock:
            return [deepcopy(event) for event in self.events if event.execution_id == execution_id]


class TraceRecorder:
    """Record stages and export a stable, input-redacted run trace."""

    def __init__(
        self,
        *,
        execution: ExecutionContext,
        input_text: str,
        sink: TraceSink,
        id_factory: Callable[[str], str],
        clock: Callable[[], datetime],
        initial_trace: RunTrace | None = None,
        redactor: Redactor | None = None,
        cost_model: CostModel | None = None,
        failure_reporter: ObservabilityFailureReporter | None = None,
        manifest_id: str | None = None,
        manifest_digest: str | None = None,
        registry_snapshot_id: str | None = None,
        prompt_ref: ComponentReference | None = None,
        skill_refs: tuple[ComponentReference, ...] = (),
    ) -> None:
        self.execution = execution
        self.sink = sink
        self._id = id_factory
        self._clock = clock
        self._redactor = redactor or DefaultRedactor()
        self._cost_model = cost_model
        self.failure_reporter = failure_reporter or ListObservabilityFailureReporter()
        self._manifest_id = manifest_id
        self._manifest_digest = manifest_digest
        self._registry_snapshot_id = registry_snapshot_id
        self._prompt_ref = prompt_ref.model_copy(deep=True) if prompt_ref is not None else None
        self._skill_refs = tuple(reference.model_copy(deep=True) for reference in skill_refs)
        self._started_at = time.perf_counter()
        self._input_fingerprint = fingerprint(input_text)
        if initial_trace is not None and (
            initial_trace.execution_id != execution.execution_id
            or initial_trace.agent_id != execution.agent_id
            or initial_trace.agent_version != execution.agent_version
            or initial_trace.principal_id != execution.principal.principal_id
            or initial_trace.tenant_id != execution.principal.tenant_id
            or initial_trace.session_id != execution.principal.session_id
            or initial_trace.input_fingerprint != self._input_fingerprint
            or initial_trace.correlation_id != execution.correlation_id
            or initial_trace.parent_execution_id != execution.parent_execution_id
            or initial_trace.delegation_id != execution.delegation_id
            or initial_trace.delegation_depth != execution.delegation_depth
            or initial_trace.delegation_path != execution.delegation_path
            or initial_trace.event_id != execution.event_id
            or initial_trace.trigger_id != execution.trigger_id
            or initial_trace.causation_id != execution.causation_id
            or initial_trace.attempt != execution.attempt
            or initial_trace.manifest_id != self._manifest_id
            or initial_trace.manifest_digest != self._manifest_digest
            or initial_trace.registry_snapshot_id != self._registry_snapshot_id
            or initial_trace.prompt_ref != self._prompt_ref
            or initial_trace.skill_refs != self._skill_refs
        ):
            raise ValueError("initial trace does not match the execution")
        self._events: list[TraceEvent] = (
            deepcopy(initial_trace.events) if initial_trace is not None else []
        )
        self._provider_calls: list[ProviderCallRecord] = (
            deepcopy(initial_trace.provider_calls) if initial_trace is not None else []
        )
        self._policy_decisions: list[PolicyDecision] = (
            deepcopy(initial_trace.policy_decisions) if initial_trace is not None else []
        )
        self._tool_executions: list[ToolExecutionRecord] = (
            deepcopy(initial_trace.tool_executions) if initial_trace is not None else []
        )

    @property
    def input_fingerprint(self) -> str:
        """Return the non-reversible input fingerprint."""

        return self._input_fingerprint

    @property
    def elapsed_ms(self) -> float:
        """Return the measured execution latency in milliseconds."""

        return (time.perf_counter() - self._started_at) * 1000.0

    def record(
        self, *, stage: str, event_type: str, metadata: dict[str, str] | None = None
    ) -> None:
        """Append one redacted event with a contiguous sequence number."""

        safe_metadata = {
            key: self._redactor.redact_value(key, value)
            for key, value in (metadata or {}).items()
            if key and value and not self._redactor.redact_key(key)
        }
        event = TraceEvent(
            event_id=self._id("trace_event"),
            execution_id=self.execution.execution_id,
            sequence=len(self._events) + 1,
            stage=stage,
            event_type=event_type,
            occurred_at=self._clock(),
            metadata=safe_metadata,
        )
        self._events.append(event)
        _append_safely(
            self.sink,
            event,
            reporter=self.failure_reporter,
            id_factory=self._id,
            clock=self._clock,
            correlation_id=self.execution.correlation_id,
        )

    def record_provider_call(
        self,
        *,
        operation: ProviderOperation,
        metadata: ProviderCallMetadata,
    ) -> None:
        """Record provider identity, model, latency, retry, and token metadata."""

        self._provider_calls.append(
            ProviderCallRecord(
                operation=operation,
                metadata=metadata.model_copy(deep=True),
            )
        )

    def record_policy_decision(self, decision: PolicyDecision) -> None:
        """Record one explicit policy decision without raw action data."""

        self._policy_decisions.append(decision.model_copy(deep=True))

    def record_tool_execution(self, record: ToolExecutionRecord) -> None:
        """Record one registry execution summary without raw arguments."""

        self._tool_executions.append(record.model_copy(deep=True))

    def export(self, *, final_status: OutcomeStatus | None = None) -> RunTrace:
        """Build a machine-readable trace bundle without raw input or output."""

        metrics = aggregate_metrics(
            execution_id=self.execution.execution_id,
            correlation_id=self.execution.correlation_id,
            agent_id=self.execution.agent_id,
            agent_version=self.execution.agent_version,
            attempt=self.execution.attempt,
            execution_latency_ms=self.elapsed_ms,
            provider_calls=self._provider_calls,
            tool_executions=self._tool_executions,
            cost_model=self._cost_model,
        )
        return RunTrace(
            trace_id=self._id("trace"),
            execution_id=self.execution.execution_id,
            agent_id=self.execution.agent_id,
            agent_version=self.execution.agent_version,
            principal_id=self.execution.principal.principal_id,
            tenant_id=self.execution.principal.tenant_id,
            session_id=self.execution.principal.session_id,
            input_fingerprint=self._input_fingerprint,
            manifest_id=self._manifest_id,
            manifest_digest=self._manifest_digest,
            registry_snapshot_id=self._registry_snapshot_id,
            prompt_ref=self._prompt_ref.model_copy(deep=True) if self._prompt_ref else None,
            skill_refs=tuple(reference.model_copy(deep=True) for reference in self._skill_refs),
            correlation_id=self.execution.correlation_id,
            parent_execution_id=self.execution.parent_execution_id,
            delegation_id=self.execution.delegation_id,
            delegation_depth=self.execution.delegation_depth,
            delegation_path=self.execution.delegation_path,
            event_id=self.execution.event_id,
            trigger_id=self.execution.trigger_id,
            causation_id=self.execution.causation_id,
            attempt=self.execution.attempt,
            events=deepcopy(self._events),
            provider_calls=deepcopy(self._provider_calls),
            policy_decisions=deepcopy(self._policy_decisions),
            tool_executions=deepcopy(self._tool_executions),
            metrics=metrics,
            final_status=final_status,
            generated_at=self._clock(),
        )


def _append_safely(
    sink: TraceSink,
    event: TraceEvent,
    *,
    reporter: ObservabilityFailureReporter,
    id_factory: Callable[[str], str],
    clock: Callable[[], datetime],
    correlation_id: str,
) -> None:
    """Persist one trace event without letting a sink failure abort execution."""

    try:
        sink.append(event)
    except Exception as exc:  # noqa: BLE001 - observability persistence is best effort.
        report_observability_failure(
            reporter,
            id_factory=id_factory,
            clock=clock,
            sink=sink,
            operation="trace_append",
            execution_id=event.execution_id,
            correlation_id=correlation_id,
            error=exc,
        )


def fingerprint(value: str) -> str:
    """Return a stable non-reversible SHA-256 fingerprint."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def digest_mapping(value: dict[str, Any]) -> str:
    """Return a stable digest for arguments without recording their values."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
