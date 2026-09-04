"""Public-boundary regressions for approval and live lifecycle authority."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentConfig,
    AgentDefinition,
    AgentFactory,
    AgentLifecycleStatus,
    AgentRegistry,
    AgentTemplate,
    AgentVersion,
    ComponentReference,
    ComponentType,
    DeterministicProvider,
    FactoryAuthorizationError,
    FactoryTemplateError,
    IncompatibleRegistrationError,
    InMemoryApprovalBroker,
    OutcomeStatus,
    PolicyDefinition,
    PolicyEffect,
    PolicyRule,
    PrincipalContext,
    PromptDefinition,
    PromptRegistry,
    ProviderProfile,
    RiskLevel,
    RuntimeAuthorizationError,
    SkillDefinition,
    SkillRegistry,
    ToolDefinition,
    ToolKind,
    ToolRegistry,
)


class RecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


class RecordOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def principal(name: str = "audit-owner") -> PrincipalContext:
    return PrincipalContext(
        principal_id=name,
        tenant_id="audit-tenant",
        session_id=f"session-{name}",
    )


def tool(
    *,
    kind: ToolKind,
    requires_approval: bool = False,
    calls: list[str] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        tool_id="write-record" if kind != ToolKind.READ else "read-record",
        version="1.0.0",
        description="Read or write one record.",
        input_model=RecordInput,
        output_model=RecordOutput,
        handler=lambda _context, arguments: (
            (calls.append(arguments.value) if calls is not None else None)
            or RecordOutput(value=arguments.value)
        ),
        kind=kind,
        risk_level=RiskLevel.HIGH if kind != ToolKind.READ else RiskLevel.LOW,
        requires_approval=requires_approval,
    )


def skill(*, tool_id: str, lifecycle: AgentLifecycleStatus) -> SkillDefinition:
    risk = RiskLevel.HIGH if tool_id == "write-record" else RiskLevel.LOW
    return SkillDefinition(
        skill_id="record-operation",
        version="1.0.0",
        name="Record operation",
        description="Operate on records.",
        supported_operations=("read", "write"),
        supported_intents=("record_operation",),
        supported_languages=("en",),
        required_tool_refs=(
            ComponentReference(
                component_type=ComponentType.TOOL,
                component_id=tool_id,
                version="1.0.0",
            ),
        ),
        risk_level=risk,
        lifecycle=lifecycle,
    )


def approval_policy() -> PolicyDefinition:
    return PolicyDefinition(
        policy_id="record-approval",
        version="1.0.0",
        description="Require review before writing records.",
        default_effect=PolicyEffect.DENY,
        rules=[
            PolicyRule(
                rule_id="approve-record-write",
                effect=PolicyEffect.ALLOW,
                tool_ids=["write-record"],
                requires_approval=True,
            )
        ],
        lifecycle=AgentLifecycleStatus.ACTIVE,
    )


def make_factory(
    registered_tool: ToolDefinition,
    *,
    registered_skill: SkillDefinition | None = None,
    policies: list[PolicyDefinition] | None = None,
    approval_broker: InMemoryApprovalBroker | None = None,
) -> tuple[AgentFactory, AgentRegistry]:
    tools = ToolRegistry([registered_tool])
    skills = SkillRegistry(tools=tools)
    if registered_skill is not None:
        skills.register(registered_skill)
    prompts = PromptRegistry(
        [
            PromptDefinition(
                prompt_id="audit-prompt",
                version="1.0.0",
                purpose="Operate on records safely.",
                instructions="Use exact configured references and obey policy.",
            )
        ]
    )
    registry = AgentRegistry(
        prompts=prompts,
        skills=skills,
        tools=tools,
        policies=policies or [],
    )
    return (
        AgentFactory(
            agent_registry=registry,
            providers={
                ("deterministic", "1.0.0"): DeterministicProvider(tool_id=registered_tool.tool_id)
            },
            approval_broker=approval_broker,
        ),
        registry,
    )


def config(
    *,
    agent_id: str = "record-agent",
    tool_id: str,
    include_skill: bool = False,
    include_policy: bool = False,
    template: AgentTemplate | None = None,
    approval_requirements: list[str] | None = None,
    risk_level: RiskLevel = RiskLevel.HIGH,
) -> AgentConfig:
    return AgentConfig(
        identity=AgentVersion(agent_id=agent_id, version="1.0.0"),
        goal="Operate on records safely.",
        supported_intents=["record_operation"],
        supported_languages=["en"],
        prompt_ref=ComponentReference(
            component_type=ComponentType.PROMPT,
            component_id="audit-prompt",
            version="1.0.0",
        ),
        skill_refs=(
            [
                ComponentReference(
                    component_type=ComponentType.SKILL,
                    component_id="record-operation",
                    version="1.0.0",
                )
            ]
            if include_skill
            else []
        ),
        tool_refs=[
            ComponentReference(
                component_type=ComponentType.TOOL,
                component_id=tool_id,
                version="1.0.0",
            )
        ],
        policy_refs=(
            [
                ComponentReference(
                    component_type=ComponentType.POLICY,
                    component_id="record-approval",
                    version="1.0.0",
                )
            ]
            if include_policy
            else []
        ),
        provider_profile=ProviderProfile(
            provider_id="deterministic",
            version="1.0.0",
            model="test-model",
        ),
        risk_level=risk_level,
        approval_requirements=approval_requirements or [],
        template=template,
    )


def agent_definition(
    *,
    agent_id: str,
    lifecycle: AgentLifecycleStatus,
) -> AgentDefinition:
    return AgentDefinition(
        identity=AgentVersion(agent_id=agent_id, version="1.0.0"),
        goal="Operate on records safely.",
        prompt_ref=ComponentReference(
            component_type=ComponentType.PROMPT,
            component_id="audit-prompt",
            version="1.0.0",
        ),
        skill_refs=[
            ComponentReference(
                component_type=ComponentType.SKILL,
                component_id="record-operation",
                version="1.0.0",
            )
        ],
        tool_refs=[
            ComponentReference(
                component_type=ComponentType.TOOL,
                component_id="read-record",
                version="1.0.0",
            )
        ],
        provider_profile=ProviderProfile(
            provider_id="deterministic",
            version="1.0.0",
            model="test-model",
        ),
        risk_level=RiskLevel.LOW,
        lifecycle=lifecycle,
    )


def test_approval_metadata_does_not_protect_an_unprotected_side_effect() -> None:
    registered_tool = tool(kind=ToolKind.ACTION)
    factory, _registry = make_factory(registered_tool)

    with pytest.raises(FactoryTemplateError, match="every side-effecting tool"):
        factory.build(
            config(
                tool_id=registered_tool.tool_id,
                template=AgentTemplate.APPROVAL_GATED_OPERATOR,
                approval_requirements=["record.write"],
            )
        )


def test_approval_gated_factory_pauses_then_runs_only_after_exact_approval() -> None:
    calls: list[str] = []
    registered_tool = tool(
        kind=ToolKind.ACTION,
        requires_approval=True,
        calls=calls,
    )
    broker = InMemoryApprovalBroker()
    factory, _registry = make_factory(registered_tool, approval_broker=broker)
    built = factory.build(
        config(
            tool_id=registered_tool.tool_id,
            template=AgentTemplate.APPROVAL_GATED_OPERATOR,
        )
    )

    paused = built.execute(principal(), "Publish record-1")

    assert paused.status == OutcomeStatus.ESCALATED
    assert built.runtime is not None
    assert calls == []
    request = broker.pending_requests[0]
    broker.approve(request.request_id, decided_by="reviewer")

    resumed = built.runtime.resume(paused.execution_id)

    assert resumed.status == OutcomeStatus.COMPLETED
    assert calls == ["Publish record-1"]


def test_approval_gated_factory_accepts_a_matching_allow_rule() -> None:
    registered_tool = tool(kind=ToolKind.ACTION)
    factory, _registry = make_factory(
        registered_tool,
        policies=[approval_policy()],
    )

    built = factory.build(
        config(
            tool_id=registered_tool.tool_id,
            include_policy=True,
            template=AgentTemplate.APPROVAL_GATED_OPERATOR,
        )
    )

    assert built.runtime is not None


def test_approval_gated_runtime_fails_closed_without_an_approval_service() -> None:
    calls: list[str] = []
    registered_tool = tool(
        kind=ToolKind.ACTION,
        requires_approval=True,
        calls=calls,
    )
    factory, _registry = make_factory(registered_tool)
    built = factory.build(
        config(
            tool_id=registered_tool.tool_id,
            template=AgentTemplate.APPROVAL_GATED_OPERATOR,
        )
    )

    outcome = built.execute(principal(), "Publish record-2")

    assert outcome.status == OutcomeStatus.ESCALATED
    assert calls == []


def test_active_agent_rejects_validated_dependency_but_validated_agent_may_use_it() -> None:
    registered_tool = tool(kind=ToolKind.READ)
    tools = ToolRegistry([registered_tool])
    skills = SkillRegistry(tools=tools)
    skills.register(
        skill(
            tool_id=registered_tool.tool_id,
            lifecycle=AgentLifecycleStatus.VALIDATED,
        )
    )
    registry = AgentRegistry(
        prompts=PromptRegistry(
            [
                PromptDefinition(
                    prompt_id="audit-prompt",
                    version="1.0.0",
                    purpose="Operate on records safely.",
                    instructions="Use exact configured references and obey policy.",
                )
            ]
        ),
        skills=skills,
        tools=tools,
    )

    with pytest.raises(IncompatibleRegistrationError, match="skill is not usable"):
        registry.register(
            agent_definition(
                agent_id="active-agent",
                lifecycle=AgentLifecycleStatus.ACTIVE,
            )
        )

    validated = registry.register(
        agent_definition(
            agent_id="validated-agent",
            lifecycle=AgentLifecycleStatus.VALIDATED,
        )
    )

    assert validated.lifecycle == AgentLifecycleStatus.VALIDATED


def test_built_agent_rechecks_live_skill_dependencies() -> None:
    registered_tool = tool(kind=ToolKind.READ)
    registered_skill = skill(
        tool_id=registered_tool.tool_id,
        lifecycle=AgentLifecycleStatus.ACTIVE,
    )
    factory, registry = make_factory(
        registered_tool,
        registered_skill=registered_skill,
    )
    built = factory.build(
        config(
            agent_id="skill-dependent-agent",
            tool_id=registered_tool.tool_id,
            include_skill=True,
            risk_level=RiskLevel.LOW,
        )
    )
    assert built.execute(principal(), "Read record-1").status == OutcomeStatus.COMPLETED

    registry.skills.suspend("record-operation", "1.0.0")

    with pytest.raises(FactoryAuthorizationError, match="not active"):
        built.execute(principal(), "Read record-2")


def test_runtime_guard_rejects_a_suspended_tool_dependency() -> None:
    registered_tool = tool(kind=ToolKind.READ)
    factory, registry = make_factory(registered_tool)
    built = factory.build(config(tool_id=registered_tool.tool_id, risk_level=RiskLevel.LOW))
    assert built.runtime is not None

    registry.tools.disable(registered_tool.tool_id, registered_tool.version)

    with pytest.raises(RuntimeAuthorizationError, match="not active"):
        built.runtime.execute(
            principal(),
            "Read record-2",
            agent_id="record-agent",
            agent_version="1.0.0",
            authorized_tool_ids=[registered_tool.tool_id],
        )


def test_factory_runtime_is_bound_to_identity_and_live_agent_lifecycle() -> None:
    registered_tool = tool(kind=ToolKind.READ)
    factory, registry = make_factory(registered_tool)
    built = factory.build(config(tool_id=registered_tool.tool_id, risk_level=RiskLevel.LOW))
    assert built.runtime is not None

    with pytest.raises(RuntimeAuthorizationError, match="different agent identity"):
        built.runtime.execute(
            principal(),
            "Read another agent's record",
            agent_id="other-agent",
            agent_version="1.0.0",
            authorized_tool_ids=[registered_tool.tool_id],
        )

    registry.suspend("record-agent", "1.0.0")

    with pytest.raises(FactoryAuthorizationError, match="not active"):
        built.execute(principal(), "Read record-3")
    with pytest.raises(RuntimeAuthorizationError, match="not active"):
        built.runtime.execute(
            principal(),
            "Read record-4",
            agent_id="record-agent",
            agent_version="1.0.0",
            authorized_tool_ids=[registered_tool.tool_id],
        )


def test_runtime_guard_runs_before_resume_after_dependency_suspension() -> None:
    calls: list[str] = []
    registered_tool = tool(
        kind=ToolKind.ACTION,
        requires_approval=True,
        calls=calls,
    )
    registered_skill = skill(
        tool_id=registered_tool.tool_id,
        lifecycle=AgentLifecycleStatus.ACTIVE,
    )
    broker = InMemoryApprovalBroker()
    factory, registry = make_factory(
        registered_tool,
        registered_skill=registered_skill,
        approval_broker=broker,
    )
    built = factory.build(
        config(
            tool_id=registered_tool.tool_id,
            include_skill=True,
            template=AgentTemplate.APPROVAL_GATED_OPERATOR,
        )
    )
    paused = built.execute(principal(), "Publish record-3")
    request = broker.pending_requests[0]
    broker.approve(request.request_id, decided_by="reviewer")
    registry.skills.suspend("record-operation", "1.0.0")

    assert built.runtime is not None
    with pytest.raises(RuntimeAuthorizationError, match="not active"):
        built.runtime.resume(paused.execution_id)
    assert calls == []
