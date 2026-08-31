"""Acceptance tests for Phase 11 event-driven and background execution."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Event, Thread
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentOutcome,
    AgentPlan,
    AgentRuntime,
    BackgroundJobRunner,
    BackgroundRetryPolicy,
    EventDisposition,
    EventEnvelope,
    InMemoryApprovalBroker,
    InMemoryDeduplicationStore,
    InMemoryLeaseStore,
    JobResult,
    LeaseConflictError,
    ListDeadLetterSink,
    OutcomeProposal,
    OutcomeStatus,
    PlanStep,
    PrincipalContext,
    RiskLevel,
    ToolDefinition,
    ToolKind,
    ToolRegistry,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def principal(name: str = "event-principal") -> PrincipalContext:
    return PrincipalContext(
        principal_id=name,
        tenant_id="tenant-events",
        session_id=f"session-{name}",
    )


def make_event(
    event_id: str = "event-1",
    *,
    event_type: str = "records.created",
    source: str = "records",
    payload: dict[str, Any] | None = None,
    deduplication_key: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        source=source,
        payload=payload or {"value": "event-payload"},
        deduplication_key=deduplication_key,
        correlation_id=correlation_id,
        causation_id=causation_id,
        principal_id=principal().principal_id,
        tenant_id=principal().tenant_id,
    )


class WorkflowProvider:
    def __init__(self, steps: list[PlanStep]) -> None:
        self.steps = steps

    def plan(self, *, request: Any) -> AgentPlan:
        del request
        return AgentPlan(steps=self.steps)

    def compose(self, *, request: Any) -> OutcomeProposal:
        del request
        return OutcomeProposal(summary="event complete", confidence=1.0)


def read_tool(handler: Any | None = None, **kwargs: Any) -> ToolDefinition:
    return ToolDefinition(
        tool_id="lookup",
        version="1.0.0",
        description="Read a record.",
        input_model=Input,
        output_model=Output,
        handler=handler or (lambda _context, arguments: Output(value=arguments.value)),
        kind=ToolKind.READ,
        **kwargs,
    )


def write_tool(handler: Any | None = None, *, idempotency_required: bool = True) -> ToolDefinition:
    return ToolDefinition(
        tool_id="publish",
        version="1.0.0",
        description="Publish a record.",
        input_model=Input,
        output_model=Output,
        handler=handler or (lambda _context, arguments: Output(value=arguments.value)),
        kind=ToolKind.WRITE,
        risk_level=RiskLevel.HIGH,
        idempotency_required=idempotency_required,
    )


def step(
    tool_id: str = "lookup", *, value: str = "record-1", idempotency_key: str | None = None
) -> PlanStep:
    return PlanStep(
        step_id=f"{tool_id}-step",
        tool_id=tool_id,
        tool_version="1.0.0",
        purpose="Handle the event.",
        arguments={"value": value},
        idempotency_key=idempotency_key,
    )


def test_event_triggered_execution_uses_the_normal_runtime() -> None:
    calls: list[str] = []
    tool = read_tool(
        lambda _context, arguments: calls.append(arguments.value) or Output(value=arguments.value)
    )
    governed = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=WorkflowProvider([step()]),
    )
    event = make_event()

    outcome = governed.execute_event(
        event,
        principal=principal(),
        authorized_tool_ids=[tool.tool_id],
        input_text="Process the event",
        execution_id="event-execution",
    )

    assert outcome.status == OutcomeStatus.COMPLETED
    assert calls == ["record-1"]
    trace = governed.trace_for("event-execution")
    assert trace.event_id == event.event_id
    assert trace.correlation_id == f"event:{event.event_id}"


def test_same_runtime_executes_interactive_and_event_driven_work() -> None:
    calls: list[str] = []
    tool = read_tool(
        lambda _context, arguments: calls.append(arguments.value) or Output(value=arguments.value)
    )
    governed = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=WorkflowProvider([step()]),
    )

    interactive = governed.execute(
        principal(),
        "Interactive call",
        authorized_tool_ids=[tool.tool_id],
        execution_id="interactive-execution",
    )
    event_outcome = governed.execute_event(
        make_event(event_id="event-2"),
        principal=principal(),
        authorized_tool_ids=[tool.tool_id],
        input_text="Event call",
        execution_id="event-execution-2",
    )

    assert interactive.status == OutcomeStatus.COMPLETED
    assert event_outcome.status == OutcomeStatus.COMPLETED
    assert len(calls) == 2


def test_duplicate_sequential_delivery_runs_once() -> None:
    calls: list[str] = []
    tool = write_tool(
        lambda _context, arguments: calls.append(arguments.value) or Output(value=arguments.value)
    )
    governed = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=WorkflowProvider([step("publish", idempotency_key="publish-key")]),
    )
    event = make_event(deduplication_key="dedup-1")

    def handler(
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
        return governed.execute_event(
            event,
            principal=principal,
            input_text=input_text,
            authorized_tool_ids=[tool.tool_id],
            execution_id=execution_id,
            attempt=attempt,
        )

    runner = BackgroundJobRunner(handler)
    first = runner.run(event, principal=principal(), input_text="Publish record")
    second = runner.run(event, principal=principal(), input_text="Publish record")

    assert first.disposition == EventDisposition.COMPLETED
    assert second.disposition == EventDisposition.DUPLICATE
    assert calls == ["record-1"]


def test_duplicate_concurrent_delivery_fails_closed_on_lease() -> None:
    calls: list[str] = []
    tool = read_tool(
        lambda _context, arguments: calls.append(arguments.value) or Output(value=arguments.value)
    )
    governed = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=WorkflowProvider([step()]),
    )
    event = make_event(deduplication_key="dedup-concurrent")

    def handler(
        principal,
        input_text,
        *,
        correlation_id,
        event_id,
        trigger_id,
        causation_id,
        attempt,
        execution_id,
    ):
        return governed.execute_event(
            event,
            principal=principal,
            input_text=input_text,
            authorized_tool_ids=[tool.tool_id],
            execution_id=execution_id,
            attempt=attempt,
        )

    lease_store = InMemoryLeaseStore()
    runner = BackgroundJobRunner(handler, lease_store=lease_store)

    # Simulate a held lease from another worker.
    lease_store.acquire(event.dedup_key, "other-worker", 60.0)
    result = runner.run(event, principal=principal(), input_text="Handle event")

    assert result.disposition == EventDisposition.LEASE_CONFLICT
    assert calls == []


def test_same_event_concurrent_delivery_runs_one_claimed_handler() -> None:
    calls: list[str] = []
    handler_started = Event()
    release_handler = Event()
    results: list[JobResult] = []

    def handler(
        principal,
        input_text,
        *,
        correlation_id,
        event_id,
        trigger_id,
        causation_id,
        attempt,
        execution_id,
    ):
        del input_text, correlation_id, event_id, trigger_id, causation_id, attempt
        calls.append(execution_id)
        handler_started.set()
        release_handler.wait(timeout=2.0)
        return AgentOutcome(
            outcome_id="outcome-concurrent",
            execution_id=execution_id,
            agent_id="agent",
            agent_version="1.0.0",
            session_id=principal.session_id,
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
            status=OutcomeStatus.COMPLETED,
            summary="completed",
        )

    event = make_event(deduplication_key="dedup-same-owner")
    runner = BackgroundJobRunner(handler)
    first_thread = Thread(
        target=lambda: results.append(
            runner.run(event, principal=principal(), input_text="Handle event")
        )
    )
    first_thread.start()
    assert handler_started.wait(timeout=2.0)

    second = runner.run(event, principal=principal(), input_text="Handle event")
    release_handler.set()
    first_thread.join(timeout=2.0)

    assert second.disposition == EventDisposition.LEASE_CONFLICT
    assert len(results) == 1
    assert results[0].disposition == EventDisposition.COMPLETED
    assert len(calls) == 1


def test_pending_approval_dedupes_until_runner_resolves_it() -> None:
    attempts = 0

    def handler(
        principal,
        input_text,
        *,
        correlation_id,
        event_id,
        trigger_id,
        causation_id,
        attempt,
        execution_id,
    ):
        del input_text, correlation_id, event_id, trigger_id, causation_id, attempt
        nonlocal attempts
        attempts += 1
        return AgentOutcome(
            outcome_id=f"pending-{attempts}",
            execution_id=execution_id,
            agent_id="agent",
            agent_version="1.0.0",
            session_id=principal.session_id,
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
            status=OutcomeStatus.ESCALATED,
            summary="approval required",
            human_review_required=True,
        )

    event = make_event(deduplication_key="dedup-pending")
    runner = BackgroundJobRunner(handler)
    first = runner.run(event, principal=principal(), input_text="Approve event")
    duplicate = runner.run(event, principal=principal(), input_text="Approve event")

    assert first.disposition == EventDisposition.PENDING_APPROVAL
    assert duplicate.disposition == EventDisposition.PENDING_APPROVAL
    assert attempts == 1

    assert first.execution_id is not None
    completed = AgentOutcome(
        outcome_id="approved-outcome",
        execution_id=first.execution_id,
        agent_id="agent",
        agent_version="1.0.0",
        session_id=principal().session_id,
        principal_id=principal().principal_id,
        tenant_id=principal().tenant_id,
        status=OutcomeStatus.COMPLETED,
        summary="approved",
    )
    resolved = runner.resolve_pending(event, completed, principal=principal())
    replay = runner.run(event, principal=principal(), input_text="Approve event")

    assert resolved.disposition == EventDisposition.COMPLETED
    assert replay.disposition == EventDisposition.DUPLICATE
    assert attempts == 1


def test_duplicate_after_success_does_not_create_new_success() -> None:
    calls: list[str] = []
    tool = write_tool(
        lambda _context, arguments: calls.append(arguments.value) or Output(value=arguments.value)
    )
    governed = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=WorkflowProvider([step("publish", idempotency_key="publish-key")]),
    )
    event = make_event(deduplication_key="dedup-success")

    def handler(
        principal,
        input_text,
        *,
        correlation_id,
        event_id,
        trigger_id,
        causation_id,
        attempt,
        execution_id,
    ):
        return governed.execute_event(
            event,
            principal=principal,
            input_text=input_text,
            authorized_tool_ids=[tool.tool_id],
            execution_id=execution_id,
            attempt=attempt,
        )

    runner = BackgroundJobRunner(handler)
    first = runner.run(event, principal=principal(), input_text="Publish")
    second = runner.run(event, principal=principal(), input_text="Publish")

    assert first.disposition == EventDisposition.COMPLETED
    assert second.disposition == EventDisposition.DUPLICATE
    assert second.outcome is not None
    assert second.outcome.execution_id == first.execution_id
    assert calls == ["record-1"]


def test_retryable_failure_and_bounded_retries() -> None:
    attempts = 0

    def handler(
        principal,
        input_text,
        *,
        correlation_id,
        event_id,
        trigger_id,
        causation_id,
        attempt,
        execution_id,
    ):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return AgentOutcome(
                outcome_id=f"outcome-{attempts}",
                execution_id=execution_id,
                agent_id="agent",
                agent_version="1.0.0",
                session_id=principal.session_id,
                principal_id=principal.principal_id,
                tenant_id=principal.tenant_id,
                status=OutcomeStatus.FAILED,
                summary="transient failure",
            )
        return AgentOutcome(
            outcome_id=f"outcome-{attempts}",
            execution_id=execution_id,
            agent_id="agent",
            agent_version="1.0.0",
            session_id=principal.session_id,
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
            status=OutcomeStatus.COMPLETED,
            summary="recovered",
        )

    event = make_event(deduplication_key="dedup-retry")
    runner = BackgroundJobRunner(
        handler,
        retry_policy=BackgroundRetryPolicy(max_attempts=3),
    )
    result = runner.run(event, principal=principal(), input_text="Retry")

    assert result.disposition == EventDisposition.COMPLETED
    assert attempts == 3


def test_permanent_failure_is_dead_lettered() -> None:
    def handler(
        principal,
        input_text,
        *,
        correlation_id,
        event_id,
        trigger_id,
        causation_id,
        attempt,
        execution_id,
    ):
        return AgentOutcome(
            outcome_id="outcome-perm",
            execution_id=execution_id,
            agent_id="agent",
            agent_version="1.0.0",
            session_id=principal.session_id,
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
            status=OutcomeStatus.REFUSED,
            summary="permanent refusal",
        )

    event = make_event(deduplication_key="dedup-perm")
    sink = ListDeadLetterSink()
    runner = BackgroundJobRunner(
        handler,
        dead_letter_sink=sink,
        retry_policy=BackgroundRetryPolicy(max_attempts=2),
    )
    result = runner.run(event, principal=principal(), input_text="Refuse")

    assert result.disposition == EventDisposition.DEAD_LETTERED
    assert len(sink.records) == 1
    assert sink.records[0].event_id == event.event_id


def test_process_restart_dedup_and_lease_are_empty() -> None:
    event = make_event(deduplication_key="dedup-restart")
    lease_store = InMemoryLeaseStore()
    InMemoryDeduplicationStore()

    lease_store.acquire(event.dedup_key, "worker", 60.0)
    # A new store simulates a restarted process with no prior state.
    restarted_lease = InMemoryLeaseStore()
    restarted_dedup = InMemoryDeduplicationStore()

    assert restarted_lease.get(event.dedup_key) is None
    assert restarted_dedup.get(event.dedup_key) is None


def test_stale_lease_can_be_taken_over() -> None:
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    lease_store = InMemoryLeaseStore(clock=lambda: now[0])
    event = make_event(deduplication_key="dedup-stale")

    first = lease_store.acquire(event.dedup_key, "worker-1", 10.0)
    now[0] = datetime(2026, 1, 1, 0, 0, 11, tzinfo=UTC)
    second = lease_store.acquire(event.dedup_key, "worker-2", 10.0)

    assert second.version > first.version


def test_lease_ownership_enforced() -> None:
    lease_store = InMemoryLeaseStore()
    lease_store.acquire("key-1", "owner-1", 60.0)

    try:
        lease_store.release("key-1", "owner-2")
        assert False, "expected LeaseConflictError"
    except LeaseConflictError:
        pass


def test_cancellation_produces_terminal_disposition() -> None:
    def handler(
        principal,
        input_text,
        *,
        correlation_id,
        event_id,
        trigger_id,
        causation_id,
        attempt,
        execution_id,
    ):
        return AgentOutcome(
            outcome_id="outcome-cancel",
            execution_id=execution_id,
            agent_id="agent",
            agent_version="1.0.0",
            session_id=principal.session_id,
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
            status=OutcomeStatus.CANCELLED,
            summary="cancelled",
        )

    event = make_event(deduplication_key="dedup-cancel")
    runner = BackgroundJobRunner(handler, retry_policy=BackgroundRetryPolicy(max_attempts=2))
    result = runner.run(event, principal=principal(), input_text="Cancel")

    assert result.disposition == EventDisposition.CANCELLED


def test_idempotent_irreversible_write_does_not_repeat() -> None:
    calls: list[str] = []
    tool = write_tool(
        lambda _context, arguments: calls.append(arguments.value) or Output(value=arguments.value)
    )
    governed = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=WorkflowProvider([step("publish", idempotency_key="publish-key")]),
    )
    event = make_event(deduplication_key="dedup-idem")

    def handler(
        principal,
        input_text,
        *,
        correlation_id,
        event_id,
        trigger_id,
        causation_id,
        attempt,
        execution_id,
    ):
        return governed.execute_event(
            event,
            principal=principal,
            input_text=input_text,
            authorized_tool_ids=[tool.tool_id],
            execution_id=execution_id,
            attempt=attempt,
        )

    runner = BackgroundJobRunner(handler)
    first = runner.run(event, principal=principal(), input_text="Publish")
    second = runner.run(event, principal=principal(), input_text="Publish")

    assert first.disposition == EventDisposition.COMPLETED
    assert second.disposition == EventDisposition.DUPLICATE
    assert calls == ["record-1"]


def test_event_audit_and_trace_correlation() -> None:
    tool = read_tool(lambda _context, arguments: Output(value=arguments.value))
    governed = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=WorkflowProvider([step()]),
    )
    event = make_event(correlation_id="corr-1", causation_id="cause-1")

    outcome = governed.execute_event(
        event,
        principal=principal(),
        authorized_tool_ids=[tool.tool_id],
        input_text="Correlate",
        execution_id="corr-execution",
    )

    assert outcome.status == OutcomeStatus.COMPLETED
    trace = governed.trace_for("corr-execution")
    assert trace.correlation_id == "corr-1"
    assert trace.event_id == event.event_id
    assert trace.causation_id == "cause-1"
    assert any(event.event_type == "execution_started" for event in governed.audit_sink.events)


def test_permission_enforcement_blocks_unauthorized_event_tool() -> None:
    tool = read_tool(lambda _context, arguments: Output(value=arguments.value))
    governed = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=WorkflowProvider([step()]),
    )
    event = make_event()

    outcome = governed.execute_event(
        event,
        principal=principal(),
        authorized_tool_ids=["some-other-tool"],
        input_text="Blocked",
        execution_id="blocked-execution",
    )

    assert outcome.status == OutcomeStatus.REFUSED


def test_approval_enforcement_blocks_event_side_effect_without_approval() -> None:
    calls: list[str] = []
    tool = ToolDefinition(
        tool_id="publish",
        version="1.0.0",
        description="Publish a record.",
        input_model=Input,
        output_model=Output,
        handler=lambda _context, arguments: (
            calls.append(arguments.value) or Output(value=arguments.value)
        ),
        kind=ToolKind.ACTION,
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
    )
    governed = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=WorkflowProvider([step("publish")]),
        approval_broker=InMemoryApprovalBroker(),
    )
    event = make_event()

    outcome = governed.execute_event(
        event,
        principal=principal(),
        authorized_tool_ids=[tool.tool_id],
        input_text="Approve me",
        execution_id="approval-execution",
    )

    assert outcome.status == OutcomeStatus.ESCALATED
    assert calls == []
