"""Acceptance tests for Phase 12 observability, audit, and cost controls."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentPlan,
    AgentRuntime,
    DefaultRedactor,
    DeterministicProvider,
    EventEnvelope,
    InMemoryApprovalBroker,
    InMemoryStateStore,
    ListAuditSink,
    ListObservabilityFailureReporter,
    ListTraceSink,
    OutcomeProposal,
    OutcomeStatus,
    PlanStep,
    PrincipalContext,
    RiskLevel,
    RuntimeConfig,
    SafetyFlag,
    StaticTokenCostModel,
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


class FailingAuditSink:
    def append(self, event: Any) -> None:
        raise RuntimeError("audit sink failure")


class FailingTraceSink:
    def append(self, event: Any) -> None:
        raise RuntimeError("trace sink failure")


def principal(name: str = "obs-principal") -> PrincipalContext:
    return PrincipalContext(
        principal_id=name,
        tenant_id="tenant-obs",
        session_id=f"session-{name}",
    )


def tool(**kwargs: Any) -> ToolDefinition:
    return ToolDefinition(
        tool_id="lookup",
        version="1.0.0",
        description="Look up a record.",
        input_model=Input,
        output_model=Output,
        handler=lambda _context, arguments: Output(value=arguments.value),
        kind=ToolKind.READ,
        **kwargs,
    )


def step() -> PlanStep:
    return PlanStep(
        step_id="lookup-step",
        tool_id="lookup",
        tool_version="1.0.0",
        purpose="Look up a record.",
        arguments={"value": "record-1"},
    )


def approval_tool() -> ToolDefinition:
    return ToolDefinition(
        tool_id="publish",
        version="1.0.0",
        description="Publish a record.",
        input_model=Input,
        output_model=Output,
        handler=lambda _context, arguments: Output(value=arguments.value),
        kind=ToolKind.ACTION,
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        idempotency_required=True,
    )


def make_runtime(
    *,
    provider: Any,
    tools: list[ToolDefinition],
    config: RuntimeConfig | None = None,
    trace_sink: Any = None,
    audit_sink: Any = None,
    cost_model: Any = None,
    redactor: Any = None,
    approval_broker: Any = None,
    failure_reporter: Any = None,
    state_store: Any = None,
) -> AgentRuntime:
    return AgentRuntime(
        tools=ToolRegistry(tools),
        provider=provider,
        state_store=state_store,
        config=config,
        trace_sink=trace_sink,
        audit_sink=audit_sink,
        approval_broker=approval_broker,
        cost_model=cost_model,
        redactor=redactor,
        failure_reporter=failure_reporter,
    )


def test_provider_tool_policy_approval_trace_emission() -> None:
    trace_sink = ListTraceSink()
    audit_sink = ListAuditSink()
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup"),
        tools=[tool()],
        trace_sink=trace_sink,
        audit_sink=audit_sink,
    )
    outcome = governed.execute(
        principal(),
        "Look up the record",
        authorized_tool_ids=["lookup"],
        execution_id="obs-execution",
    )

    assert outcome.status == OutcomeStatus.COMPLETED
    event_types = {event.event_type for event in trace_sink.events}
    assert "provider_call_started" in event_types
    assert "provider_call_completed" in event_types
    assert "tool_result_recorded" in event_types
    assert "execution_terminal" in event_types
    assert any(event.event_type == "policy_decision" for event in audit_sink.events)


def test_state_transition_tracing() -> None:
    trace_sink = ListTraceSink()
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup"),
        tools=[tool()],
        trace_sink=trace_sink,
    )
    governed.execute(
        principal(),
        "Look up the record",
        authorized_tool_ids=["lookup"],
        execution_id="state-execution",
    )

    assert any(event.event_type == "state_transitioned" for event in trace_sink.events)


def test_audit_and_trace_sink_behavior() -> None:
    trace_sink = ListTraceSink()
    audit_sink = ListAuditSink()
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup"),
        tools=[tool()],
        trace_sink=trace_sink,
        audit_sink=audit_sink,
    )
    governed.execute(
        principal(),
        "Look up the record",
        authorized_tool_ids=["lookup"],
        execution_id="sink-execution",
    )

    assert len(trace_sink.events) > 0
    assert len(audit_sink.events) > 0
    assert all(event.execution_id == "sink-execution" for event in audit_sink.events)


def test_sink_failure_does_not_abort_execution() -> None:
    failure_reporter = ListObservabilityFailureReporter()
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup"),
        tools=[tool()],
        trace_sink=FailingTraceSink(),
        audit_sink=FailingAuditSink(),
        failure_reporter=failure_reporter,
    )
    outcome = governed.execute(
        principal(),
        "Look up the record",
        authorized_tool_ids=["lookup"],
        execution_id="sink-failure-execution",
    )

    assert outcome.status == OutcomeStatus.COMPLETED
    assert failure_reporter.failures
    assert {failure.operation for failure in failure_reporter.failures} == {
        "audit_append",
        "trace_append",
    }


def test_latency_and_token_metrics_collected() -> None:
    trace_sink = ListTraceSink()
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup", input_tokens=100, output_tokens=50),
        tools=[tool()],
        trace_sink=trace_sink,
    )
    governed.execute(
        principal(),
        "Look up the record",
        authorized_tool_ids=["lookup"],
        execution_id="metrics-execution",
    )

    trace = governed.trace_for("metrics-execution")
    assert trace.metrics is not None
    assert trace.metrics.provider_calls == 3  # interpret, plan, compose
    assert trace.metrics.total_input_tokens == 300
    assert trace.metrics.total_output_tokens == 150
    assert trace.metrics.total_tokens == 450
    assert trace.metrics.execution_latency_ms >= 0.0


def test_provider_cost_aggregation() -> None:
    trace_sink = ListTraceSink()
    cost_model = StaticTokenCostModel(input_cost_per_1k=2.0, output_cost_per_1k=4.0)
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup", input_tokens=100, output_tokens=50),
        tools=[tool()],
        trace_sink=trace_sink,
        cost_model=cost_model,
    )
    governed.execute(
        principal(),
        "Look up the record",
        authorized_tool_ids=["lookup"],
        execution_id="cost-execution",
    )

    trace = governed.trace_for("cost-execution")
    assert trace.metrics is not None
    # 3 calls * (100/1000 * 2.0 + 50/1000 * 4.0) = 3 * 0.4 = 1.2
    assert abs(trace.metrics.total_cost - 1.2) < 1e-6


def test_per_tool_metrics() -> None:
    trace_sink = ListTraceSink()
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup"),
        tools=[tool()],
        trace_sink=trace_sink,
    )
    governed.execute(
        principal(),
        "Look up the record",
        authorized_tool_ids=["lookup"],
        execution_id="tool-metrics-execution",
    )

    trace = governed.trace_for("tool-metrics-execution")
    assert trace.metrics is not None
    assert trace.metrics.tool_invocations == 1
    assert len(trace.metrics.tools) == 1
    assert trace.metrics.tools[0].tool_id == "lookup"


def test_execution_budget_token_limit_enforced() -> None:
    trace_sink = ListTraceSink()
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup", input_tokens=1000),
        tools=[tool()],
        trace_sink=trace_sink,
        config=RuntimeConfig(max_total_tokens=500),
    )
    outcome = governed.execute(
        principal(),
        "Look up the record",
        authorized_tool_ids=["lookup"],
        execution_id="budget-execution",
    )

    assert outcome.status == OutcomeStatus.FAILED
    assert SafetyFlag.BUDGET_EXHAUSTED in outcome.safety_flags


def test_execution_budget_cost_limit_enforced() -> None:
    trace_sink = ListTraceSink()
    cost_model = StaticTokenCostModel(input_cost_per_1k=10.0)
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup", input_tokens=1000),
        tools=[tool()],
        trace_sink=trace_sink,
        cost_model=cost_model,
        config=RuntimeConfig(max_cost=5.0),
    )
    outcome = governed.execute(
        principal(),
        "Look up the record",
        authorized_tool_ids=["lookup"],
        execution_id="cost-budget-execution",
    )

    assert outcome.status == OutcomeStatus.FAILED
    assert SafetyFlag.BUDGET_EXHAUSTED in outcome.safety_flags


def test_execution_budget_tool_invocation_limit_enforced() -> None:
    trace_sink = ListTraceSink()
    provider = DeterministicProvider(tool_id="lookup")
    governed = make_runtime(
        provider=provider,
        tools=[tool()],
        trace_sink=trace_sink,
        config=RuntimeConfig(max_tool_invocations=1),
    )
    # A two-step plan exceeds the one-invocation budget.
    two_step_provider = TwoStepProvider()
    governed = make_runtime(
        provider=two_step_provider,
        tools=[tool()],
        trace_sink=trace_sink,
        config=RuntimeConfig(max_tool_invocations=1),
    )
    outcome = governed.execute(
        principal(),
        "Look up records",
        authorized_tool_ids=["lookup"],
        execution_id="tool-budget-execution",
    )

    assert outcome.status == OutcomeStatus.FAILED
    assert SafetyFlag.BUDGET_EXHAUSTED in outcome.safety_flags


class TwoStepProvider:
    def plan(self, *, request: Any) -> AgentPlan:
        del request
        return AgentPlan(
            steps=[
                PlanStep(
                    step_id="step-1",
                    tool_id="lookup",
                    tool_version="1.0.0",
                    purpose="First lookup.",
                    arguments={"value": "a"},
                ),
                PlanStep(
                    step_id="step-2",
                    tool_id="lookup",
                    tool_version="1.0.0",
                    purpose="Second lookup.",
                    arguments={"value": "b"},
                ),
            ]
        )

    def compose(self, *, request: Any) -> OutcomeProposal:
        del request
        return OutcomeProposal(summary="done", confidence=1.0)


def test_redaction_blocks_sensitive_metadata_keys() -> None:
    audit_sink = ListAuditSink()
    redactor = DefaultRedactor(sensitive_field_names=("api_key",))
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup"),
        tools=[tool(sensitive_argument_fields=("value",))],
        audit_sink=audit_sink,
        redactor=redactor,
    )
    governed.execute(
        principal(),
        "Look up the record",
        authorized_tool_ids=["lookup"],
        execution_id="redaction-execution",
    )

    for event in audit_sink.events:
        for key in event.metadata:
            assert "api_key" not in key.lower()
            assert "secret" not in key.lower()


def test_correlation_across_retries_stable() -> None:
    event = EventEnvelope(
        event_id="event-1",
        event_type="records.created",
        source="records",
        correlation_id="corr-stable",
        principal_id=principal().principal_id,
        tenant_id=principal().tenant_id,
    )
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup"),
        tools=[tool()],
    )
    outcome = governed.execute_event(
        event,
        principal=principal(),
        input_text="Correlate",
        authorized_tool_ids=["lookup"],
        execution_id="corr-execution",
        attempt=2,
    )

    assert outcome.status == OutcomeStatus.COMPLETED
    trace = governed.trace_for("corr-execution")
    assert trace.correlation_id == "corr-stable"
    assert trace.attempt == 2


def test_approval_resume_preserves_event_identity_and_aggregates_metrics() -> None:
    event = EventEnvelope(
        event_id="approval-event",
        event_type="records.created",
        source="records",
        correlation_id="approval-correlation",
        causation_id="parent-event",
        principal_id=principal().principal_id,
        tenant_id=principal().tenant_id,
    )
    broker = InMemoryApprovalBroker()
    state_store = InMemoryStateStore()
    cost_model = StaticTokenCostModel(input_cost_per_1k=2.0, output_cost_per_1k=4.0)
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="publish", input_tokens=100, output_tokens=50),
        tools=[approval_tool()],
        approval_broker=broker,
        cost_model=cost_model,
        state_store=state_store,
    )

    paused = governed.execute_event(
        event,
        principal=principal(),
        authorized_tool_ids=["publish"],
        input_text="Publish the record",
        execution_id="approval-event-execution",
        attempt=2,
    )
    request = broker.pending_requests[0]
    broker.approve(request.request_id, decided_by="reviewer")
    restarted = make_runtime(
        provider=DeterministicProvider(tool_id="publish", input_tokens=100, output_tokens=50),
        tools=[approval_tool()],
        approval_broker=broker,
        cost_model=cost_model,
        state_store=state_store,
    )
    resumed = restarted.resume(paused.execution_id, principal=principal())
    trace = restarted.trace_for(resumed.execution_id)

    assert paused.status == OutcomeStatus.ESCALATED
    assert resumed.status == OutcomeStatus.COMPLETED
    assert trace.event_id == event.event_id
    assert trace.trigger_id == event.trigger_id
    assert trace.causation_id == event.causation_id
    assert trace.attempt == 2
    assert trace.metrics is not None
    assert trace.metrics.provider_calls == 3
    assert trace.metrics.total_input_tokens == 300
    assert trace.metrics.total_output_tokens == 150
    assert abs(trace.metrics.total_cost - 1.2) < 1e-6


def test_trace_bundle_export_is_deterministic_serialization() -> None:
    trace_sink = ListTraceSink()
    governed = make_runtime(
        provider=DeterministicProvider(tool_id="lookup"),
        tools=[tool()],
        trace_sink=trace_sink,
    )
    governed.execute(
        principal(),
        "Look up the record",
        authorized_tool_ids=["lookup"],
        execution_id="export-execution",
    )

    trace = governed.trace_for("export-execution")
    first = trace.model_dump(mode="json")
    second = trace.model_dump(mode="json")
    assert first == second
    assert first["schema_version"] == "agent-run-trace.v1"
    assert "metrics" in first
