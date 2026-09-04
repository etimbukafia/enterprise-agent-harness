"""Regression tests for tool registry and resolved manifest integrity."""

from __future__ import annotations

from typing import Any

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
    ExecutionContext,
    FactoryAuthorizationError,
    ListRegistryAuditSink,
    PolicyDefinition,
    PolicyEffect,
    PolicyRule,
    PrincipalContext,
    PromptDefinition,
    PromptRegistry,
    ProviderProfile,
    RiskLevel,
    RuntimeConfig,
    SkillDefinition,
    SkillRegistry,
    ToolDefinition,
    ToolInvocationError,
    ToolKind,
    ToolRegistry,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def make_tool(
    *,
    version: str = "1.0.0",
    handler: Any = None,
) -> ToolDefinition:
    return ToolDefinition(
        tool_id="records-read",
        version=version,
        description="Read one record.",
        input_model=Input,
        output_model=Output,
        handler=handler or (lambda _context, arguments: Output(value=arguments.value)),
        kind=ToolKind.READ,
        risk_level=RiskLevel.LOW,
        owner_id="records-team",
    )


def make_skill() -> SkillDefinition:
    return SkillDefinition(
        skill_id="record-review",
        version="1.0.0",
        name="Record review",
        description="Review records.",
        supported_operations=("read",),
        supported_intents=("review_records",),
        supported_languages=("en",),
        required_tool_refs=(
            ComponentReference(
                component_type=ComponentType.TOOL,
                component_id="records-read",
                version="1.0.0",
            ),
        ),
        risk_level=RiskLevel.LOW,
        owner_id="records-team",
        lifecycle=AgentLifecycleStatus.ACTIVE,
    )


def make_policy() -> PolicyDefinition:
    return PolicyDefinition(
        policy_id="records-policy",
        version="1.0.0",
        description="Allow record reads.",
        default_effect=PolicyEffect.DENY,
        rules=[
            PolicyRule(
                rule_id="allow-record-read",
                effect=PolicyEffect.ALLOW,
                tool_ids=["records-read"],
            )
        ],
        lifecycle=AgentLifecycleStatus.ACTIVE,
    )


def make_agent(*, lifecycle: AgentLifecycleStatus = AgentLifecycleStatus.DRAFT) -> AgentDefinition:
    return AgentDefinition(
        identity=AgentVersion(agent_id="records-agent", version="1.0.0"),
        goal="Review records.",
        supported_intents=["review_records"],
        supported_languages=["en"],
        prompt_ref=ComponentReference(
            component_type=ComponentType.PROMPT,
            component_id="records-prompt",
            version="1.0.0",
        ),
        skill_refs=[
            ComponentReference(
                component_type=ComponentType.SKILL,
                component_id="record-review",
                version="1.0.0",
            )
        ],
        tool_refs=[
            ComponentReference(
                component_type=ComponentType.TOOL,
                component_id="records-read",
                version="1.0.0",
            )
        ],
        policy_refs=[
            ComponentReference(
                component_type=ComponentType.POLICY,
                component_id="records-policy",
                version="1.0.0",
            )
        ],
        provider_profile=ProviderProfile(
            provider_id="deterministic",
            version="1.0.0",
            model="test-model",
        ),
        runtime_limits=RuntimeConfig(max_plan_steps=1),
        risk_level=RiskLevel.LOW,
        owner_id="platform-team",
        lifecycle=lifecycle,
    )


def make_config() -> AgentConfig:
    return AgentConfig(
        identity=AgentVersion(agent_id="records-agent", version="1.0.0"),
        goal="Review records.",
        supported_intents=["review_records"],
        supported_languages=["en"],
        prompt_ref=ComponentReference(
            component_type=ComponentType.PROMPT,
            component_id="records-prompt",
            version="1.0.0",
        ),
        skill_refs=[
            ComponentReference(
                component_type=ComponentType.SKILL,
                component_id="record-review",
                version="1.0.0",
            )
        ],
        tool_refs=[
            ComponentReference(
                component_type=ComponentType.TOOL,
                component_id="records-read",
                version="1.0.0",
            )
        ],
        policy_refs=[
            ComponentReference(
                component_type=ComponentType.POLICY,
                component_id="records-policy",
                version="1.0.0",
            )
        ],
        provider_profile=ProviderProfile(
            provider_id="deterministic",
            version="1.0.0",
            model="test-model",
        ),
        runtime_limits=RuntimeConfig(max_plan_steps=1),
        owner_id="platform-team",
        template=AgentTemplate.READ_ONLY_ANALYST,
    )


def make_principal() -> PrincipalContext:
    return PrincipalContext(
        principal_id="principal-1",
        tenant_id="tenant-1",
        session_id="session-1",
    )


class CountingProvider(DeterministicProvider):
    """Record provider calls for the manifest integrity boundary test."""

    def __init__(self) -> None:
        super().__init__(tool_id="records-read")
        self.plan_calls = 0

    def plan(self, *, request):
        self.plan_calls += 1
        return super().plan(request=request)


def test_tool_registry_rejects_exact_replacement_and_preserves_original_handler() -> None:
    registry = ToolRegistry(
        [make_tool(handler=lambda _context, _arguments: Output(value="original"))]
    )

    with pytest.raises(ValueError, match="immutable"):
        registry.register(
            make_tool(handler=lambda _context, _arguments: Output(value="replacement")),
            replace_existing=True,
        )

    result = registry.invoke("records-read", _execution_context(), {"value": "record-1"})

    assert result.output == {"value": "original"}
    assert registry.revision == 1


def test_tool_registry_lifecycle_changes_are_audited_and_terminal_states_cannot_reactivate() -> (
    None
):
    sink = ListRegistryAuditSink()
    registry = ToolRegistry([make_tool()], audit_sink=sink)

    registry.deprecate("records-read", "1.0.0")
    with pytest.raises(ToolInvocationError, match="deprecated"):
        registry.activate("records-read", "1.0.0")
    registry.retire("records-read", "1.0.0")
    with pytest.raises(ToolInvocationError, match="retired"):
        registry.activate("records-read", "1.0.0")

    assert registry.revision == 3
    assert [event.operation for event in registry.events] == [
        "registered",
        "deprecated",
        "retired",
    ]
    assert [event.operation for event in sink.by_registry("tools")] == [
        "registered",
        "deprecated",
        "retired",
    ]


def test_skill_and_agent_snapshots_track_tool_registry_revision() -> None:
    tools = ToolRegistry([make_tool()])
    prompts = PromptRegistry(
        [
            PromptDefinition(
                prompt_id="records-prompt",
                version="1.0.0",
                purpose="Review records.",
                instructions="Use the record review skill.",
            )
        ]
    )
    skills = SkillRegistry(tools=tools)
    skills.register(make_skill())
    agents = AgentRegistry(
        prompts=prompts,
        skills=skills,
        tools=tools,
        policies=[make_policy()],
    )
    agents.register(make_agent())
    agents.activate("records-agent", "1.0.0")

    skill_before = skills.snapshot()
    agent_before = agents.snapshot()
    tools.disable("records-read", "1.0.0")
    skill_after = skills.snapshot()
    agent_after = agents.snapshot()

    assert skill_after.tool_registry_revision == tools.revision
    assert agent_after.tool_registry_revision == tools.revision
    assert skill_after.tool_registry_revision > skill_before.tool_registry_revision
    assert agent_after.tool_registry_revision > agent_before.tool_registry_revision
    assert skill_after.revision > skill_before.revision
    assert agent_after.revision > agent_before.revision


def test_manifest_tampering_fails_before_provider_or_tool_execution() -> None:
    calls = 0

    def handler(_context, arguments):
        nonlocal calls
        calls += 1
        return Output(value=arguments.value)

    tools = ToolRegistry([make_tool(handler=handler)])
    prompts = PromptRegistry(
        [
            PromptDefinition(
                prompt_id="records-prompt",
                version="1.0.0",
                purpose="Review records.",
                instructions="Use the record review skill.",
            )
        ]
    )
    skills = SkillRegistry(tools=tools)
    skills.register(make_skill())
    registry = AgentRegistry(
        prompts=prompts,
        skills=skills,
        tools=tools,
        policies=[make_policy()],
    )
    provider = CountingProvider()
    factory = AgentFactory(
        agent_registry=registry,
        providers={("deterministic", "1.0.0"): provider},
    )
    built = factory.build(make_config())
    original_digest = built.manifest.manifest_digest

    built.manifest.agent.tool_refs[0] = ComponentReference(
        component_type=ComponentType.TOOL,
        component_id="records-read",
        version="2.0.0",
    )

    with pytest.raises(FactoryAuthorizationError, match="integrity"):
        built.execute(make_principal(), "review record-1")

    assert built.manifest.manifest_digest == original_digest
    assert provider.plan_calls == 0
    assert calls == 0


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id="execution-1",
        agent_id="records-agent",
        agent_version="1.0.0",
        principal=make_principal(),
        authorized_tool_ids=("records-read",),
        state_id="state-1",
    )
