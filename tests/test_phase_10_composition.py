"""Acceptance tests for bounded Phase 10 agent composition and delegation."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentComposer,
    AgentConfig,
    AgentFactory,
    AgentRegistry,
    AgentVersion,
    ComponentReference,
    ComponentType,
    CompositionDefinition,
    CompositionPattern,
    CompositionStep,
    DelegationAuthorityError,
    DelegationCycleError,
    DelegationDepthError,
    DelegationRequest,
    DeterministicProvider,
    ExecutionContext,
    InMemoryStateStore,
    ListAuditSink,
    ListTraceSink,
    OutcomeStatus,
    PrincipalContext,
    PromptDefinition,
    PromptRegistry,
    ProviderProfile,
    RiskLevel,
    RuntimeConfig,
    ToolDefinition,
    ToolKind,
    ToolRegistry,
)


class OperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


class OperationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def operation_tool(
    tool_id: str,
    *,
    kind: ToolKind = ToolKind.READ,
    required_permissions: tuple[str, ...] = (),
) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        version="1.0.0",
        description=f"Run {tool_id}.",
        input_model=OperationInput,
        output_model=OperationOutput,
        handler=lambda _context, arguments: OperationOutput(value=arguments.value),
        kind=kind,
        risk_level=RiskLevel.LOW,
        required_permissions=required_permissions,
    )


def make_factory() -> tuple[AgentFactory, ListTraceSink, ListAuditSink]:
    tools = ToolRegistry(
        [
            operation_tool("records-read"),
            operation_tool(
                "records-write",
                kind=ToolKind.WRITE,
                required_permissions=("records:write",),
            ),
        ]
    )
    prompts = PromptRegistry(
        [
            PromptDefinition(
                prompt_id="composition-prompt",
                version="1.0.0",
                purpose="Compose a bounded operation.",
                instructions="Use the explicitly authorized tools.",
            )
        ]
    )
    registry = AgentRegistry(
        prompts=prompts,
        tools=tools,
    )
    traces = ListTraceSink()
    audits = ListAuditSink()
    factory = AgentFactory(
        agent_registry=registry,
        providers={
            ("deterministic", "1.0.0"): DeterministicProvider(),
        },
        default_state_store=InMemoryStateStore(),
        trace_sink=traces,
        audit_sink=audits,
    )
    return factory, traces, audits


def config(agent_id: str, tool_id: str = "records-read") -> AgentConfig:
    return AgentConfig(
        identity=AgentVersion(agent_id=agent_id, version="1.0.0"),
        goal=f"Operate as {agent_id}.",
        prompt_ref=ComponentReference(
            component_type=ComponentType.PROMPT,
            component_id="composition-prompt",
            version="1.0.0",
        ),
        tool_refs=[
            ComponentReference(
                component_type=ComponentType.TOOL,
                component_id=tool_id,
                version="1.0.0",
            )
        ],
        provider_profile=ProviderProfile(
            provider_id="deterministic",
            version="1.0.0",
            model="test-model",
        ),
        runtime_limits=RuntimeConfig(max_plan_steps=2),
        risk_level=RiskLevel.LOW,
    )


def principal() -> PrincipalContext:
    return PrincipalContext(
        principal_id="composition-owner",
        tenant_id="tenant-composition",
        session_id="session-composition",
    )


def parent_context(
    *,
    authorized_tool_ids: tuple[str, ...] = ("records-read",),
    authorized_tool_versions: tuple[str, ...] = ("records-read@1.0.0",),
    granted_permissions: tuple[str, ...] = (),
    max_risk_level: RiskLevel = RiskLevel.LOW,
    delegation_depth: int = 0,
    parent_execution_id: str | None = None,
    delegation_path: tuple[str, ...] = (),
) -> ExecutionContext:
    return ExecutionContext(
        execution_id="parent-execution",
        agent_id="parent-agent",
        agent_version="1.0.0",
        principal=principal(),
        authorized_tool_ids=authorized_tool_ids,
        authorized_tool_versions=authorized_tool_versions,
        granted_permissions=granted_permissions,
        max_steps=2,
        state_id="parent-state",
        max_risk_level=max_risk_level,
        correlation_id="correlation-1",
        parent_execution_id=parent_execution_id,
        delegation_depth=delegation_depth,
        delegation_path=delegation_path,
    )


def build_child(factory: AgentFactory, agent_id: str, tool_id: str = "records-read") -> None:
    factory.build(config(agent_id, tool_id))


def test_delegation_runs_child_through_runtime_with_ceiling_and_trace_correlation() -> None:
    factory, traces, audits = make_factory()
    build_child(factory, "child-agent")
    composer = AgentComposer(factory, id_factory=lambda prefix: f"{prefix}-1")
    request = DelegationRequest(
        delegation_id="delegation-1",
        parent_execution_id="parent-execution",
        parent_agent_id="parent-agent",
        parent_agent_version="1.0.0",
        child_agent_id="child-agent",
        child_agent_version="1.0.0",
        input_text="review record-1",
        reason="specialist review",
        requested_tool_ids=("records-read",),
    )

    result = composer.delegate(parent_context(), request)

    assert result.outcome.status == OutcomeStatus.COMPLETED
    assert result.context.correlation_id == "correlation-1"
    assert result.context.delegation_depth == 1
    assert result.context.delegation_path == (
        "parent-agent@1.0.0",
        "child-agent@1.0.0",
    )
    assert result.context.authorized_tool_versions == ("records-read@1.0.0",)
    child = factory.runtime_for("child-agent", "1.0.0")
    assert child.runtime is not None
    trace = child.runtime.trace_for(result.child_execution_id)
    assert trace.correlation_id == "correlation-1"
    assert trace.parent_execution_id == "parent-execution"
    assert trace.delegation_depth == 1
    assert [event.event_type for event in traces.by_execution(result.child_execution_id)].count(
        "delegation_started"
    ) == 1
    assert [event.event_type for event in traces.by_execution(result.child_execution_id)].count(
        "delegation_completed"
    ) == 1
    child_audits = audits.by_execution(result.child_execution_id)
    assert child_audits
    assert all(event.correlation_id == "correlation-1" for event in child_audits)


def test_delegation_rejects_tool_and_permission_amplification() -> None:
    factory, _traces, _audits = make_factory()
    build_child(factory, "write-agent", "records-write")
    composer = AgentComposer(factory)

    with pytest.raises(DelegationAuthorityError, match="exceed parent tool authority"):
        composer.delegate(
            parent_context(),
            DelegationRequest(
                delegation_id="write-outside-parent",
                parent_execution_id="parent-execution",
                parent_agent_id="parent-agent",
                parent_agent_version="1.0.0",
                child_agent_id="write-agent",
                child_agent_version="1.0.0",
                input_text="write record-1",
                reason="write request",
                requested_tool_ids=("records-write",),
            ),
        )

    with pytest.raises(DelegationAuthorityError, match="permissions outside parent grants"):
        composer.delegate(
            parent_context(
                authorized_tool_ids=("records-write",),
                authorized_tool_versions=("records-write@1.0.0",),
            ),
            DelegationRequest(
                delegation_id="permission-outside-parent",
                parent_execution_id="parent-execution",
                parent_agent_id="parent-agent",
                parent_agent_version="1.0.0",
                child_agent_id="write-agent",
                child_agent_version="1.0.0",
                input_text="write record-1",
                reason="write request",
                requested_tool_ids=("records-write",),
            ),
        )


def test_delegation_rejects_cycles_and_depth_overflow() -> None:
    factory, _traces, _audits = make_factory()
    build_child(factory, "child-agent")

    composer = AgentComposer(factory, max_delegation_depth=2)
    request = DelegationRequest(
        delegation_id="cycle",
        parent_execution_id="parent-execution",
        parent_agent_id="parent-agent",
        parent_agent_version="1.0.0",
        child_agent_id="child-agent",
        child_agent_version="1.0.0",
        input_text="review",
        reason="cycle test",
        requested_tool_ids=("records-read",),
    )
    with pytest.raises(DelegationCycleError, match="cycle detected"):
        composer.delegate(
            parent_context(
                delegation_depth=1,
                parent_execution_id="grandparent-execution",
                delegation_path=("grandparent-agent@1.0.0", "child-agent@1.0.0"),
            ),
            request,
        )

    with pytest.raises(DelegationDepthError, match="maximum"):
        composer.delegate(
            parent_context(
                delegation_depth=2,
                parent_execution_id="grandparent-execution",
                delegation_path=("grandparent-agent@1.0.0", "parent-agent@1.0.0"),
            ),
            request.model_copy(update={"delegation_id": "depth"}),
        )


def test_composer_supports_router_supervisor_specialist_and_sequential_patterns() -> None:
    factory, _traces, _audits = make_factory()
    build_child(factory, "specialist-a")
    build_child(factory, "specialist-b")
    composer = AgentComposer(factory, id_factory=lambda prefix: f"{prefix}-composition")
    parent = parent_context()

    router = composer.compose(
        parent,
        CompositionDefinition(
            composition_id="router-flow",
            version="1.0.0",
            pattern=CompositionPattern.ROUTER,
            steps=[
                CompositionStep(
                    step_id="a",
                    agent_id="specialist-a",
                    agent_version="1.0.0",
                ),
                CompositionStep(
                    step_id="b",
                    agent_id="specialist-b",
                    agent_version="1.0.0",
                ),
            ],
        ),
        "route this",
        selected_step_id="b",
    )
    assert len(router.outcomes) == 1

    supervisor = composer.compose(
        parent,
        CompositionDefinition(
            composition_id="supervisor-flow",
            version="1.0.0",
            pattern=CompositionPattern.SUPERVISOR,
            steps=[
                CompositionStep(
                    step_id="a",
                    agent_id="specialist-a",
                    agent_version="1.0.0",
                ),
                CompositionStep(
                    step_id="b",
                    agent_id="specialist-b",
                    agent_version="1.0.0",
                ),
            ],
        ),
        "fan out",
    )
    assert len(supervisor.outcomes) == 2
    assert supervisor.final_outcome.status == OutcomeStatus.COMPLETED

    specialist = composer.compose(
        parent,
        CompositionDefinition(
            composition_id="specialist-flow",
            version="1.0.0",
            pattern=CompositionPattern.SPECIALIST,
            steps=[
                CompositionStep(
                    step_id="a",
                    agent_id="specialist-a",
                    agent_version="1.0.0",
                )
            ],
        ),
        "specialist task",
    )
    assert len(specialist.outcomes) == 1

    sequential = composer.compose(
        parent,
        CompositionDefinition(
            composition_id="sequential-flow",
            version="1.0.0",
            pattern=CompositionPattern.SEQUENTIAL,
            steps=[
                CompositionStep(
                    step_id="a",
                    agent_id="specialist-a",
                    agent_version="1.0.0",
                ),
                CompositionStep(
                    step_id="b",
                    agent_id="specialist-b",
                    agent_version="1.0.0",
                ),
            ],
        ),
        "step one",
    )
    assert len(sequential.outcomes) == 2
    assert sequential.correlation_id == "correlation-1"
