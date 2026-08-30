"""Acceptance tests for the Phase 4 permission and policy engine."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentPlan,
    AgentRuntime,
    DefaultPermissionBroker,
    DeterministicProvider,
    EnvironmentConstraint,
    OutcomeProposal,
    OutcomeStatus,
    PolicyDefinition,
    PolicyEffect,
    PolicyRule,
    PrincipalContext,
    ResourceContext,
    RiskLevel,
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


def principal(principal_id: str = "principal-1") -> PrincipalContext:
    return PrincipalContext(
        principal_id=principal_id,
        tenant_id="tenant-1",
        session_id=f"session-{principal_id}",
    )


def tool(
    tool_id: str = "lookup",
    *,
    kind: ToolKind = ToolKind.READ,
    risk_level: RiskLevel = RiskLevel.LOW,
) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        version="1.0.0",
        description="Run a governed test operation.",
        input_model=Input,
        output_model=Output,
        handler=lambda _context, arguments: Output(value=arguments.value),
        kind=kind,
        risk_level=risk_level,
    )


class FixedProvider:
    def __init__(self, tool_id: str = "lookup") -> None:
        self.tool_id = tool_id

    def plan(self, **_kwargs: Any) -> AgentPlan:
        return AgentPlan(
            steps=[
                {
                    "step_id": "step-1",
                    "tool_id": self.tool_id,
                    "tool_version": "1.0.0",
                    "purpose": "Run the proposed operation.",
                    "arguments": {"value": "record-1"},
                }
            ]
        )

    def compose(self, **_kwargs: Any) -> OutcomeProposal:
        return OutcomeProposal(summary="done", confidence=1.0)


def runtime(
    *,
    provider: Any | None = None,
    broker: DefaultPermissionBroker | None = None,
    tools: list[ToolDefinition] | None = None,
) -> AgentRuntime:
    return AgentRuntime(
        tools=ToolRegistry(tools or [tool()]),
        provider=provider or DeterministicProvider(),
        permission_broker=broker,
    )


def test_principal_and_agent_allowlists_intersect_execution_authority() -> None:
    broker = DefaultPermissionBroker(
        principal_tool_permissions={"principal-1": {"lookup"}},
        agent_tool_allowlists={"agent-1": {"lookup"}},
    )
    governed = runtime(broker=broker)
    allowed = governed.execute(
        principal(),
        "look up a record",
        authorized_tool_ids=["lookup"],
        agent_id="agent-1",
    )
    assert allowed.status == OutcomeStatus.COMPLETED

    denied_principal = governed.execute(
        principal("principal-2"),
        "look up a record",
        authorized_tool_ids=["lookup"],
        agent_id="agent-1",
    )
    assert denied_principal.status == OutcomeStatus.REFUSED
    assert denied_principal.error_code == "principal_tool_not_allowed"

    denied_agent = governed.execute(
        principal("principal-1"),
        "look up a record",
        authorized_tool_ids=["lookup"],
        agent_id="agent-2",
    )
    assert denied_agent.status == OutcomeStatus.REFUSED
    assert denied_agent.error_code == "agent_tool_not_allowed"


def test_declarative_policy_is_deny_by_default_and_deny_wins_over_allow() -> None:
    policy = PolicyDefinition(
        policy_id="records-policy",
        version="1.0.0",
        description="Control record operations.",
        lifecycle="active",
        rules=[
            PolicyRule(
                rule_id="allow-lookup",
                effect=PolicyEffect.ALLOW,
                tool_ids=["lookup"],
                agent_ids=["agent-1"],
            ),
            PolicyRule(
                rule_id="deny-production-lookup",
                effect=PolicyEffect.DENY,
                tool_ids=["lookup"],
                environments=["production"],
            ),
        ],
    )
    broker = DefaultPermissionBroker(policies=[policy])
    governed = runtime(broker=broker)

    allowed = governed.execute(
        principal(),
        "look up a record",
        authorized_tool_ids=["lookup"],
        agent_id="agent-1",
        environment="development",
    )
    assert allowed.status == OutcomeStatus.COMPLETED

    denied_by_default = governed.execute(
        principal("principal-2"),
        "look up a record",
        authorized_tool_ids=["lookup"],
        agent_id="agent-2",
        environment="development",
    )
    assert denied_by_default.status == OutcomeStatus.REFUSED
    assert denied_by_default.error_code == "policy_denied"

    denied_by_environment = governed.execute(
        principal("principal-3"),
        "look up a record",
        authorized_tool_ids=["lookup"],
        agent_id="agent-1",
        environment="production",
    )
    assert denied_by_environment.status == OutcomeStatus.REFUSED
    assert denied_by_environment.error_code == "policy_denied"


def test_environment_constraints_and_risk_tier_checks_run_before_handler() -> None:
    called = False

    def handler(_context, arguments):
        nonlocal called
        called = True
        return Output(value=arguments.value)

    action = ToolDefinition(
        tool_id="delete-record",
        version="1.0.0",
        description="Delete a record.",
        input_model=Input,
        output_model=Output,
        handler=handler,
        kind=ToolKind.ACTION,
        risk_level=RiskLevel.HIGH,
    )
    broker = DefaultPermissionBroker(
        environment_constraints={
            "production": EnvironmentConstraint(
                allowed_tool_ids=frozenset({"lookup"}),
                max_risk_level=RiskLevel.MEDIUM,
            )
        }
    )
    governed = runtime(
        broker=broker,
        provider=FixedProvider("delete-record"),
        tools=[action],
    )
    outcome = governed.execute(
        principal(),
        "delete a record",
        authorized_tool_ids=["delete-record"],
        environment="production",
    )

    assert outcome.status == OutcomeStatus.REFUSED
    assert outcome.error_code == "environment_tool_not_allowed"
    assert called is False

    limited = runtime(
        broker=DefaultPermissionBroker(max_risk_by_environment={"production": RiskLevel.MEDIUM}),
        provider=FixedProvider("delete-record"),
        tools=[action],
    )
    risk_denied = limited.execute(
        principal("principal-4"),
        "delete a record",
        authorized_tool_ids=["delete-record"],
        environment="production",
    )
    assert risk_denied.status == OutcomeStatus.REFUSED
    assert risk_denied.error_code == "risk_exceeds_environment_limit"
    assert called is False


def test_resource_policy_hook_can_deny_one_resource_without_granting_authority() -> None:
    def resource_policy(*, resource: ResourceContext | None, **_kwargs: Any) -> bool:
        return resource is not None and resource.resource_id == "record-1"

    broker = DefaultPermissionBroker(resource_policy_hooks=[resource_policy])
    governed = runtime(broker=broker)
    allowed = governed.execute(
        principal(),
        "look up a record",
        authorized_tool_ids=["lookup"],
        resource=ResourceContext(resource_type="record", resource_id="record-1"),
    )
    assert allowed.status == OutcomeStatus.COMPLETED

    denied = governed.execute(
        principal("principal-5"),
        "look up a record",
        authorized_tool_ids=["lookup"],
        resource=ResourceContext(resource_type="record", resource_id="record-2"),
    )
    assert denied.status == OutcomeStatus.REFUSED
    assert denied.error_code == "resource_policy_denied"


def test_all_resource_hooks_run_so_a_later_deny_cannot_be_skipped() -> None:
    observed: list[str] = []

    def approve(**_kwargs: Any) -> bool:
        observed.append("approve")
        return True

    def deny(**_kwargs: Any) -> bool:
        observed.append("deny")
        return False

    governed = runtime(
        broker=DefaultPermissionBroker(resource_policy_hooks=[approve, deny]),
    )
    outcome = governed.execute(
        principal("principal-approval-then-deny"),
        "look up a record",
        authorized_tool_ids=["lookup"],
        resource=ResourceContext(resource_type="record", resource_id="record-1"),
    )

    assert outcome.status == OutcomeStatus.REFUSED
    assert outcome.error_code == "resource_policy_denied"
    assert observed == ["approve", "deny"]


def test_policy_rule_can_require_exact_approval_before_an_action() -> None:
    action = tool("approve-record", kind=ToolKind.ACTION, risk_level=RiskLevel.HIGH)
    policy = PolicyDefinition(
        policy_id="approval-policy",
        version="1.0.0",
        description="Require approval for record actions.",
        lifecycle="active",
        rules=[
            PolicyRule(
                rule_id="approve-record-action",
                effect=PolicyEffect.ALLOW,
                tool_ids=["approve-record"],
                requires_approval=True,
            )
        ],
    )
    governed = runtime(
        broker=DefaultPermissionBroker(policies=[policy]),
        provider=FixedProvider("approve-record"),
        tools=[action],
    )

    pending = governed.execute(
        principal("principal-approval"),
        "approve a record",
        authorized_tool_ids=["approve-record"],
    )
    assert pending.status == OutcomeStatus.ESCALATED
    assert pending.error_code == "approval_required"

    approved = governed.execute(
        principal("principal-approval"),
        "approve a record",
        authorized_tool_ids=["approve-record"],
        approved_action_digests=[action.action_digest({"value": "record-1"})],
    )
    assert approved.status == OutcomeStatus.COMPLETED


def test_policy_decisions_are_exported_and_provider_cannot_grant_a_tool() -> None:
    called = False

    def handler(_context, arguments):
        nonlocal called
        called = True
        return Output(value=arguments.value)

    governed = runtime(
        provider=FixedProvider("write-record"),
        tools=[tool("write-record", kind=ToolKind.WRITE, risk_level=RiskLevel.MEDIUM)],
    )
    outcome = governed.execute(
        principal("principal-6"),
        "write a record",
        authorized_tool_ids=[],
    )
    trace = governed.trace_for(outcome.execution_id)

    assert outcome.status == OutcomeStatus.REFUSED
    assert SafetyFlag.PERMISSION_DENIED in outcome.safety_flags
    assert called is False
    assert trace.policy_decisions
    decision = trace.policy_decisions[0]
    assert decision.allowed is False
    assert decision.reason_code == "tool_not_in_execution_allowlist"
    assert decision.principal_id == "principal-6"
    assert decision.agent_id == "agent"
    assert any(event.event_type == "policy_decision" for event in trace.events)
    assert any(event.event_type == "policy_decision" for event in governed.audit_sink.events)


def test_custom_broker_cannot_bypass_required_permission_risk_or_approval_ceiling() -> None:
    class OvergrantingBroker:
        def authorize(self, **kwargs: Any):
            execution = kwargs["execution"]
            tool_value = kwargs["tool"]
            return {
                "allowed": True,
                "principal_id": execution.principal.principal_id,
                "tenant_id": execution.principal.tenant_id,
                "tool_id": tool_value.tool_id,
                "reason_code": "provider_says_allowed",
            }

    governed = AgentRuntime(
        tools=ToolRegistry([tool("write-record", kind=ToolKind.WRITE, risk_level=RiskLevel.HIGH)]),
        provider=FixedProvider("write-record"),
        permission_broker=OvergrantingBroker(),
    )
    outcome = governed.execute(
        principal("principal-7"),
        "write a record",
        authorized_tool_ids=[],
    )

    assert outcome.status == OutcomeStatus.REFUSED
    assert outcome.error_code == "tool_not_authorized"
