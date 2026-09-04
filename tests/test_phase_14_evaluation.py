"""Public-boundary tests for Phase 14 external evaluation integration."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentConfig,
    AgentFactory,
    AgentLifecycleStatus,
    AgentRegistry,
    AgentTemplate,
    AgentVersion,
    ComponentReference,
    ComponentType,
    DeterministicProvider,
    EvaluationEvidence,
    EvaluationExecutionInput,
    EvaluationSubject,
    ListAuditSink,
    ListTraceSink,
    OutcomeStatus,
    PolicyDefinition,
    PolicyEffect,
    PolicyRule,
    PrincipalContext,
    PromptDefinition,
    PromptRegistry,
    ProviderProfile,
    RecordedReplayAdapter,
    RiskLevel,
    SkillDefinition,
    SkillRegistry,
    ToolDefinition,
    ToolKind,
    ToolRegistry,
    execute_test_case,
)


class RecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


class RecordOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


@dataclass(frozen=True)
class ExternalCase:
    case_id: str
    prompt: str


class ExternalCaseAdapter:
    """Code owned by an external evaluation system."""

    def adapt(self, test_case: ExternalCase) -> EvaluationExecutionInput:
        return EvaluationExecutionInput(
            case_id=test_case.case_id,
            principal=PrincipalContext(
                principal_id="evaluation-lab",
                tenant_id="evaluation-tenant",
                session_id="evaluation-session",
            ),
            input_text=test_case.prompt,
            execution_id=f"execution-{test_case.case_id}",
        )


def _factory(handler_calls: list[str]) -> AgentFactory:
    tool = ToolDefinition(
        tool_id="records-read",
        version="1.0.0",
        description="Read one record.",
        input_model=RecordInput,
        output_model=RecordOutput,
        handler=lambda _context, arguments: (
            handler_calls.append(arguments.value) or RecordOutput(value=arguments.value)
        ),
        kind=ToolKind.READ,
        risk_level=RiskLevel.LOW,
        owner_id="records-team",
    )
    tools = ToolRegistry([tool])
    skills = SkillRegistry(tools=tools)
    skills.register(
        SkillDefinition(
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
    )
    policy = PolicyDefinition(
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
    prompts = PromptRegistry(
        [
            PromptDefinition(
                prompt_id="records-prompt",
                version="1.0.0",
                purpose="Review records safely.",
                instructions="Use the record review skill.",
            )
        ]
    )
    registry = AgentRegistry(
        prompts=prompts,
        skills=skills,
        tools=tools,
        policies=[policy],
    )
    return AgentFactory(
        agent_registry=registry,
        providers={("deterministic", "1.0.0"): DeterministicProvider(tool_id="records-read")},
        trace_sink=ListTraceSink(),
        audit_sink=ListAuditSink(),
    )


def _config(version: str) -> AgentConfig:
    return AgentConfig(
        identity=AgentVersion(agent_id="records-agent", version=version),
        goal="Review records and report approved findings.",
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
        owner_id="platform-team",
        template=AgentTemplate.READ_ONLY_ANALYST,
    )


def test_external_evaluator_can_adapt_export_and_replay_without_live_actions() -> None:
    handler_calls: list[str] = []
    factory = _factory(handler_calls)
    baseline = factory.build(_config("1.0.0"))
    candidate = factory.build(_config("2.0.0"))

    evidence = execute_test_case(
        candidate,
        ExternalCase(case_id="record-1", prompt="Review record-1"),
        adapter=ExternalCaseAdapter(),
        role="candidate",
    )

    def metric(evidence: EvaluationEvidence) -> dict[str, float]:
        return {"completed": float(evidence.run_trace["final_status"] == "completed")}

    def hard_gate(evidence: EvaluationEvidence) -> bool:
        return evidence.run_trace["final_status"] == "completed"

    baseline_identity = EvaluationSubject.from_manifest(baseline.manifest, role="baseline")
    replay = RecordedReplayAdapter().replay(evidence.run_trace)

    assert handler_calls == ["Review record-1"]
    assert evidence.subject.role == "candidate"
    assert evidence.subject.manifest_id == candidate.manifest.manifest_id
    assert evidence.manifest["manifest_digest"] == candidate.manifest.manifest_digest
    assert baseline_identity.manifest_id != evidence.subject.manifest_id
    assert baseline_identity.manifest_digest != evidence.subject.manifest_digest
    assert metric(evidence) == {"completed": 1.0}
    assert hard_gate(evidence) is True
    assert replay.live_actions_executed is False
    assert replay.trace.final_status == OutcomeStatus.COMPLETED
    assert handler_calls == ["Review record-1"]
    assert json.loads(json.dumps(evidence.model_dump(mode="json"))) == evidence.model_dump(
        mode="json"
    )
