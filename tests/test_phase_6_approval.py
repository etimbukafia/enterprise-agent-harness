"""Acceptance tests for Phase 6 human approval gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentPlan,
    AgentRuntime,
    ApprovalDecision,
    ApprovalDecisionStatus,
    ApprovalPolicy,
    ApprovalPolicyRule,
    InMemoryApprovalBroker,
    OutcomeProposal,
    OutcomeStatus,
    PlanStep,
    PrincipalContext,
    RiskLevel,
    RuntimeConfig,
    SafetyFlag,
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


def principal(name: str = "approval-owner") -> PrincipalContext:
    return PrincipalContext(
        principal_id=name,
        tenant_id="tenant-1",
        session_id=f"session-{name}",
    )


class ApprovalProvider:
    def __init__(self, steps: list[PlanStep]) -> None:
        self.steps = steps

    def plan(self, *, request: Any) -> AgentPlan:
        del request
        return AgentPlan(steps=list(self.steps))

    def compose(self, *, request: Any) -> OutcomeProposal:
        del request
        return OutcomeProposal(summary="approved action complete", confidence=1.0)


def action_tool(handler: Any, *, requires_approval: bool = True) -> ToolDefinition:
    return ToolDefinition(
        tool_id="publish-record",
        version="1.0.0",
        description="Publish one reviewed record.",
        input_model=Input,
        output_model=Output,
        handler=handler,
        kind=ToolKind.ACTION,
        risk_level=RiskLevel.HIGH,
        requires_approval=requires_approval,
    )


def make_runtime(
    provider: ApprovalProvider,
    tool: ToolDefinition,
    broker: InMemoryApprovalBroker,
    *,
    clock: Any | None = None,
) -> AgentRuntime:
    return AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=provider,
        approval_broker=broker,
        config=RuntimeConfig(approval_expiry_seconds=30.0),
        clock=clock,
    )


def one_step(value: str = "record-1") -> PlanStep:
    return PlanStep(
        step_id="publish-step",
        tool_id="publish-record",
        tool_version="1.0.0",
        purpose="Publish the reviewed record.",
        arguments={"value": value},
        idempotency_key="publish-record-1",
    )


def test_approval_policy_matches_tool_action_risk_and_environment() -> None:
    policy = ApprovalPolicy(
        policy_id="production-actions",
        version="1.0.0",
        description="Require review for high-risk actions in production.",
        rules=[
            ApprovalPolicyRule(
                rule_id="high-risk-production-action",
                action_kinds=[ToolKind.ACTION],
                risk_levels=[RiskLevel.HIGH],
                environments=["production"],
                expiry_seconds=45.0,
            )
        ],
    )
    broker = InMemoryApprovalBroker(policies=[policy])
    decision = broker.policy_engine.evaluate(
        principal=principal(),
        execution=_execution("policy-execution", principal()),
        tool=action_tool(lambda _context, arguments: Output(value=arguments.value)),
        action=_action("policy-execution", one_step()),
    )

    assert decision.required is True
    assert decision.policy_id == "production-actions"
    assert decision.matched_rule_ids == ["high-risk-production-action"]
    assert decision.expiry_seconds == 45.0

    outside_environment = broker.policy_engine.evaluate(
        principal=principal(),
        execution=_execution("development-execution", principal(), environment="development"),
        tool=action_tool(lambda _context, arguments: Output(value=arguments.value)),
        action=_action("development-execution", one_step()),
    )
    assert outside_environment.required is False

    policy_tool = action_tool(
        lambda _context, arguments: Output(value=arguments.value),
        requires_approval=False,
    )
    governed = make_runtime(ApprovalProvider([one_step()]), policy_tool, broker)
    paused = governed.execute(
        principal("policy-runtime"),
        "Publish the reviewed record",
        authorized_tool_ids=[policy_tool.tool_id],
        environment="production",
        execution_id="policy-runtime-execution",
    )
    request = governed.approval_request_for(paused.execution_id)
    assert paused.status == OutcomeStatus.ESCALATED
    assert request is not None
    assert request.approval_policy_id == "production-actions"


def test_sensitive_action_pauses_with_exact_action_and_review_context() -> None:
    calls: list[str] = []
    tool = action_tool(
        lambda _context, arguments: calls.append(arguments.value) or Output(value=arguments.value)
    )
    broker = InMemoryApprovalBroker()
    governed = make_runtime(ApprovalProvider([one_step()]), tool, broker)

    paused = governed.execute(
        principal(),
        "Publish the reviewed record",
        authorized_tool_ids=[tool.tool_id],
        execution_id="approval-execution",
    )

    request = governed.approval_request_for(paused.execution_id)
    assert paused.status == OutcomeStatus.ESCALATED
    assert paused.error_code == "approval_required"
    assert paused.human_review_required is True
    assert paused.tool_calls[0].result_status.value == "approval_required"
    assert request is not None
    assert request.action.tool_call.arguments == {"value": "record-1"}
    assert request.action.requires_approval is True
    assert request.action_digest == tool.action_digest(request.action.tool_call.arguments)
    assert request.context is not None
    assert "Publish the reviewed record" in request.context.input_text
    assert governed.pending_approval(paused.execution_id) == request
    assert any(event.event_type == "approval_requested" for event in governed.audit_sink.events)
    assert calls == []


def test_approved_request_resumes_exact_action_and_keeps_transition_trace() -> None:
    calls: list[str] = []
    tool = action_tool(
        lambda _context, arguments: calls.append(arguments.value) or Output(value=arguments.value)
    )
    broker = InMemoryApprovalBroker()
    governed = make_runtime(ApprovalProvider([one_step()]), tool, broker)
    paused = governed.execute(
        principal("approved"),
        "Publish the reviewed record",
        authorized_tool_ids=[tool.tool_id],
        execution_id="approved-execution",
    )
    request = broker.pending_requests[0]
    broker.approve(request.request_id, decided_by="reviewer-1")

    resumed = governed.resume(paused.execution_id)
    trace = governed.trace_for(paused.execution_id)

    assert resumed.status == OutcomeStatus.COMPLETED
    assert calls == ["record-1"]
    assert governed.approval_request_for(paused.execution_id) is None
    assert trace.final_status == OutcomeStatus.COMPLETED
    assert any(event.event_type == "approval_requested" for event in trace.events)
    assert any(event.event_type == "approval_approved" for event in trace.events)
    assert any(event.event_type == "execution_terminal" for event in trace.events)
    assert any(event.event_type == "approval_approved" for event in governed.audit_sink.events)


def test_rejection_and_requested_changes_never_reach_the_handler() -> None:
    for reviewer_action, expected_status, expected_error in (
        ("reject", OutcomeStatus.REFUSED, "approval_rejected"),
        ("changes", OutcomeStatus.NEEDS_INPUT, "approval_changes_requested"),
    ):
        calls: list[str] = []
        tool = action_tool(
            lambda _context, arguments, calls=calls: (
                calls.append(arguments.value) or Output(value=arguments.value)
            )
        )
        broker = InMemoryApprovalBroker()
        governed = make_runtime(ApprovalProvider([one_step()]), tool, broker)
        paused = governed.execute(
            principal(reviewer_action),
            "Publish the reviewed record",
            authorized_tool_ids=[tool.tool_id],
            execution_id=f"{reviewer_action}-execution",
        )
        request = broker.pending_requests[0]
        if reviewer_action == "reject":
            broker.reject(request.request_id, decided_by="reviewer-1")
        else:
            broker.request_changes(request.request_id, decided_by="reviewer-1")

        result = governed.resume(paused.execution_id)

        assert result.status == expected_status
        assert result.error_code == expected_error
        assert calls == []


def test_expired_approval_is_materialized_and_cannot_run() -> None:
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    tool = action_tool(lambda _context, arguments: Output(value=arguments.value))
    broker = InMemoryApprovalBroker(clock=lambda: now[0])
    governed = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=ApprovalProvider([one_step()]),
        approval_broker=broker,
        config=RuntimeConfig(approval_expiry_seconds=5.0),
        clock=lambda: now[0],
    )

    paused = governed.execute(
        principal("expired"),
        "Publish the reviewed record",
        authorized_tool_ids=[tool.tool_id],
        execution_id="expired-execution",
    )
    request = broker.pending_requests[0]
    now[0] = request.expires_at + timedelta(seconds=1)  # type: ignore[operator]

    expired = governed.resume(paused.execution_id)

    assert expired.status == OutcomeStatus.ESCALATED
    assert expired.error_code == "approval_expired"
    assert SafetyFlag.APPROVAL_REQUIRED in expired.safety_flags
    assert broker.get_decision(request.request_id) is not None
    assert broker.get_decision(request.request_id).status == ApprovalDecisionStatus.EXPIRED  # type: ignore[union-attr]
    assert any(event.event_type == "approval_expired" for event in governed.audit_sink.events)


def test_stale_approval_digest_is_refused_before_handler_execution() -> None:
    calls: list[str] = []
    tool = action_tool(
        lambda _context, arguments: calls.append(arguments.value) or Output(value=arguments.value)
    )
    broker = InMemoryApprovalBroker()
    governed = make_runtime(ApprovalProvider([one_step()]), tool, broker)
    paused = governed.execute(
        principal("stale"),
        "Publish the reviewed record",
        authorized_tool_ids=[tool.tool_id],
        execution_id="stale-execution",
    )
    request = broker.pending_requests[0]
    stale = ApprovalDecision(
        approval_id="stale-approval",
        request_id=request.request_id,
        action_digest=tool.action_digest({"value": "changed-record"}),
        decision=ApprovalDecisionStatus.APPROVED,
        decided_by="reviewer-1",
        reason_code="approved",
    )

    result = governed.resume(paused.execution_id, stale)

    assert result.status == OutcomeStatus.REFUSED
    assert result.error_code == "approval_action_mismatch"
    assert calls == []
    assert any(event.event_type == "approval_stale" for event in governed.audit_sink.events)


def test_approved_resume_uses_the_stored_action_when_provider_payload_changes() -> None:
    calls: list[str] = []
    tool = action_tool(
        lambda _context, arguments: calls.append(arguments.value) or Output(value=arguments.value),
        requires_approval=True,
    )
    provider = ApprovalProvider([one_step("record-1")])
    broker = InMemoryApprovalBroker()
    governed = make_runtime(provider, tool, broker)
    paused = governed.execute(
        principal("changed-payload"),
        "Publish the reviewed record",
        authorized_tool_ids=[tool.tool_id],
        execution_id="changed-payload-execution",
    )
    original = broker.pending_requests[0]
    provider.steps = [one_step("record-2")]
    broker.approve(original.request_id, decided_by="reviewer-1")

    result = governed.resume(paused.execution_id)

    assert result.status == OutcomeStatus.COMPLETED
    assert calls == ["record-1"]
    assert governed.approval_request_for(paused.execution_id) is None
    trace = governed.trace_for(paused.execution_id)

    assert [event.event_type for event in trace.events].count("approval_requested") == 1
    assert [event.event_type for event in trace.events].count("approval_approved") == 1
    assert trace.final_status == OutcomeStatus.COMPLETED


def test_resume_does_not_replay_completed_steps_before_a_later_approval() -> None:
    calls: list[str] = []
    tool = action_tool(
        lambda _context, arguments: calls.append(arguments.value) or Output(value=arguments.value)
    )
    second_step = one_step("record-2").model_copy(
        update={"step_id": "publish-step-2", "idempotency_key": "publish-record-2"}
    )
    provider = ApprovalProvider([one_step(), second_step])
    broker = InMemoryApprovalBroker()
    governed = make_runtime(provider, tool, broker)

    first_pause = governed.execute(
        principal("multi-step"),
        "Publish both reviewed records",
        authorized_tool_ids=[tool.tool_id],
        execution_id="multi-step-execution",
    )
    first_request = broker.pending_requests[0]
    broker.approve(first_request.request_id, decided_by="reviewer-1")

    second_pause = governed.resume(first_pause.execution_id)

    assert second_pause.status == OutcomeStatus.ESCALATED
    assert calls == ["record-1"]
    second_request = broker.pending_requests[0]
    assert second_request.action.tool_call.arguments == {"value": "record-2"}
    broker.approve(second_request.request_id, decided_by="reviewer-1")

    completed = governed.resume(first_pause.execution_id)
    trace = governed.trace_for(first_pause.execution_id)

    assert completed.status == OutcomeStatus.COMPLETED
    assert calls == ["record-1", "record-2"]
    assert [event.event_type for event in trace.events].count("approval_requested") == 2
    assert [event.event_type for event in trace.events].count("approval_approved") == 2
    assert trace.final_status == OutcomeStatus.COMPLETED


def _execution(
    execution_id: str,
    owner: PrincipalContext,
    *,
    environment: str = "production",
):
    from enterprise_agent_harness import ExecutionContext

    return ExecutionContext(
        execution_id=execution_id,
        agent_id="agent",
        agent_version="1.0.0",
        principal=owner,
        authorized_tool_ids=("publish-record",),
        state_id=f"state-{execution_id}",
        environment=environment,
    )


def _action(execution_id: str, step: PlanStep):
    from enterprise_agent_harness import ActionProposal, ToolCall

    return ActionProposal(
        action_id=step.step_id,
        execution_id=execution_id,
        tool_call=ToolCall(
            tool_id=step.tool_id,
            tool_version=step.tool_version,
            arguments=step.arguments,
            purpose=step.purpose,
            idempotency_key=step.idempotency_key,
        ),
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        justification=step.purpose,
    )
