from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentPlan,
    AgentRuntime,
    CompiledContext,
    ContextCompiler,
    DeterministicProvider,
    ExecutionContext,
    ExecutionState,
    OutcomeProposal,
    OutcomeStatus,
    PlanStep,
    PrincipalContext,
    ProviderOperation,
    ProviderTimeoutError,
    RuntimeConfig,
    SafetyFlag,
    ToolDefinition,
    ToolRegistry,
    normalize_composition,
    normalize_plan,
    run_conformance_probe,
)
from enterprise_agent_harness.errors import ProviderOutputError
from enterprise_agent_harness.providers import (
    CompositionRequest,
    InterpretationRequest,
    OpenAIProviderAdapter,
    PlanningRequest,
)


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


class ProviderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def principal(session_id: str = "session-1") -> PrincipalContext:
    return PrincipalContext(
        principal_id="person-1",
        tenant_id="tenant-1",
        session_id=session_id,
    )


def execution(owner: PrincipalContext | None = None) -> ExecutionContext:
    owner = owner or principal()
    return ExecutionContext(
        execution_id="execution-1",
        agent_id="agent-1",
        agent_version="1.0.0",
        principal=owner,
        authorized_tool_ids=("lookup",),
        state_id=owner.session_id,
    )


def state(owner: PrincipalContext | None = None) -> ExecutionState:
    owner = owner or principal()
    return ExecutionState(
        state_id=owner.session_id,
        execution_id="state-execution-1",
        agent_id="agent-1",
        agent_version="1.0.0",
        principal_id=owner.principal_id,
        tenant_id=owner.tenant_id,
        session_id=owner.session_id,
    )


def tool(handler, *, required_permissions: tuple[str, ...] = ()) -> ToolDefinition:
    return ToolDefinition(
        tool_id="lookup",
        version="1.0.0",
        description="Look up one record.",
        input_model=ProviderInput,
        output_model=ProviderOutput,
        handler=handler,
        required_permissions=required_permissions,
    )


def context(owner: PrincipalContext | None = None) -> CompiledContext:
    owner = owner or principal()
    return ContextCompiler().compile(
        principal=owner,
        execution=execution(owner),
        state=state(owner),
        input_text="look up a record",
    )


def test_provider_normalization_accepts_openai_function_call_shape() -> None:
    response = normalize_plan(
        {
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {
                        "name": "lookup",
                        "arguments": json.dumps({"value": "record-1"}),
                    },
                }
            ]
        }
    )

    assert response.plan.steps[0].step_id == "call-1"
    assert response.plan.steps[0].tool_id == "lookup"
    assert response.plan.steps[0].arguments == {"value": "record-1"}


@pytest.mark.parametrize(
    "provider_shape",
    [
        {
            "content": [
                {
                    "type": "tool_use",
                    "id": "anthropic-call-1",
                    "name": "lookup",
                    "input": {"value": "record-1"},
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "chat-call-1",
                                "function": {
                                    "name": "lookup",
                                    "arguments": '{"value":"record-1"}',
                                },
                            }
                        ]
                    }
                }
            ]
        },
    ],
)
def test_provider_normalization_accepts_common_tool_call_shapes(
    provider_shape: dict[str, object],
) -> None:
    response = normalize_plan(provider_shape)

    assert response.plan.steps[0].tool_id == "lookup"
    assert response.plan.steps[0].arguments == {"value": "record-1"}


def test_provider_request_response_contracts_and_deterministic_provider_are_typed() -> None:
    owner = principal()
    compiled = context(owner)
    request = InterpretationRequest(
        request_id="request-interpret",
        context=compiled,
        execution=execution(owner),
        tools=[tool(lambda _context, arguments: ProviderOutput(value=arguments.value)).descriptor],
    )
    restored_request = InterpretationRequest.model_validate_json(request.model_dump_json())
    result = run_conformance_probe(
        DeterministicProvider(),
        context=compiled,
        execution=execution(owner),
        tools=[tool(lambda _context, arguments: ProviderOutput(value=arguments.value)).descriptor],
    )

    assert restored_request == request
    assert result.interpretation.proposal.intent == compiled.input_text
    assert result.plan.steps[0].tool_id == "lookup"
    assert result.outcome_proposal.confidence == 0.0


class FakeResponses:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = FakeResponses(responses)


def response(*, response_id: str, output: list[object] | None = None, text: str | None = None):
    return SimpleNamespace(
        id=response_id,
        model="test-openai-model",
        output=output or [],
        output_text=text,
        usage=SimpleNamespace(input_tokens=11, output_tokens=7, total_tokens=18),
    )


def test_openai_adapter_translates_responses_api_calls_without_core_dependency() -> None:
    fake_client = FakeClient(
        [
            response(
                response_id="response-plan",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        call_id="call-1",
                        name="lookup",
                        arguments='{"value":"record-1"}',
                    )
                ],
            ),
            response(
                response_id="response-interpret",
                text='{"intent":"lookup","parameters":{},"constraints":[],"confidence":1.0}',
            ),
            response(
                response_id="response-compose",
                text='{"summary":"done","confidence":1.0,"evidence_ids":[],"output":{}}',
            ),
        ]
    )
    adapter = OpenAIProviderAdapter(model="test-openai-model", client=fake_client)
    owner = principal()
    descriptor = tool(lambda _context, arguments: ProviderOutput(value=arguments.value)).descriptor
    compiled = context(owner)
    plan_request = PlanningRequest(
        request_id="plan-1",
        context=compiled,
        execution=execution(owner),
        tools=[descriptor],
    )
    interpretation_request = InterpretationRequest(
        request_id="interpret-1",
        context=compiled,
        execution=execution(owner),
        tools=[descriptor],
    )
    plan_response = adapter.plan(request=plan_request)
    interpretation_response = adapter.interpret(request=interpretation_request)
    composition_response = adapter.compose(
        request=CompositionRequest(
            request_id="compose-1",
            context=compiled,
            execution=execution(owner),
            plan=plan_response.plan,
        )
    )

    assert plan_response.plan.steps[0].arguments == {"value": "record-1"}
    assert interpretation_response.proposal.intent == "lookup"
    assert composition_response.proposal.summary == "done"
    assert plan_response.metadata.provider_id == "openai"
    assert plan_response.metadata.total_tokens == 18
    assert fake_client.responses.calls[0]["tools"][0]["strict"] is True
    assert fake_client.responses.calls[1]["text"]["format"]["type"] == "json_schema"


def test_invalid_provider_structured_output_fails_at_normalization_boundary() -> None:
    with pytest.raises(ProviderOutputError):
        normalize_composition({"summary": "missing confidence"})


def test_runtime_records_provider_metadata_in_exported_trace() -> None:
    runtime = AgentRuntime(
        tools=ToolRegistry(
            [tool(lambda _context, arguments: ProviderOutput(value=arguments.value))]
        ),
        provider=DeterministicProvider(model="trace-model"),
    )
    outcome = runtime.execute(
        principal(),
        "look up a record",
        authorized_tool_ids=["lookup"],
        execution_id="trace-execution",
    )

    trace = runtime.trace_for("trace-execution")
    assert outcome.status == OutcomeStatus.COMPLETED
    assert [call.operation for call in trace.provider_calls] == [
        ProviderOperation.INTERPRET,
        ProviderOperation.PLAN,
        ProviderOperation.COMPOSE,
    ]
    assert all(call.metadata.model == "trace-model" for call in trace.provider_calls)
    assert all(call.metadata.latency_ms >= 0 for call in trace.provider_calls)


class MappingProvider:
    """A request-contract provider that returns an untrusted mapping shape."""

    def plan(self, *, request):
        del request
        return {
            "tool_calls": [
                {
                    "id": "legacy-call-1",
                    "name": "lookup",
                    "arguments": {"value": "record-1"},
                }
            ]
        }

    def compose(self, *, request):
        del request
        return {"summary": "done", "confidence": 1.0}


def test_swapping_provider_shapes_does_not_change_permission_boundary() -> None:
    outcomes = []
    for index, provider in enumerate((DeterministicProvider(), MappingProvider()), start=1):
        called = False

        def handler(_context, arguments):
            nonlocal called
            called = True
            return ProviderOutput(value=arguments.value)

        runtime = AgentRuntime(
            tools=ToolRegistry([tool(handler, required_permissions=("records:read",))]),
            provider=provider,
        )
        outcome = runtime.execute(
            principal(f"provider-swap-{index}"),
            "look up a record",
            authorized_tool_ids=["lookup"],
        )
        outcomes.append((outcome, called))

    assert all(outcome.status == OutcomeStatus.REFUSED for outcome, _ in outcomes)
    assert all(outcome.safety_flags == [SafetyFlag.PERMISSION_DENIED] for outcome, _ in outcomes)
    assert all(called is False for _, called in outcomes)


class FlakyProvider:
    def __init__(self) -> None:
        self.plan_attempts = 0

    def plan(self, **_kwargs: object) -> AgentPlan:
        self.plan_attempts += 1
        if self.plan_attempts == 1:
            raise ConnectionError("temporary provider failure")
        return AgentPlan(
            steps=[
                PlanStep(
                    step_id="step-1",
                    tool_id="lookup",
                    tool_version="1.0.0",
                    purpose="Look up a record.",
                    arguments={"value": "record-1"},
                )
            ]
        )

    def compose(self, **_kwargs: object) -> OutcomeProposal:
        return OutcomeProposal(summary="done", confidence=1.0)


def test_runtime_retries_configured_transient_provider_failure_and_traces_retry_count() -> None:
    provider = FlakyProvider()
    runtime = AgentRuntime(
        tools=ToolRegistry(
            [tool(lambda _context, arguments: ProviderOutput(value=arguments.value))]
        ),
        provider=provider,
        config=RuntimeConfig(provider_max_attempts=2),
    )

    outcome = runtime.execute(
        principal("retry-session"),
        "look up a record",
        authorized_tool_ids=["lookup"],
    )

    trace = runtime.trace_for(outcome.execution_id)
    planning_call = next(
        call for call in trace.provider_calls if call.operation == ProviderOperation.PLAN
    )
    assert outcome.status == OutcomeStatus.COMPLETED
    assert provider.plan_attempts == 2
    assert planning_call.metadata.retry_count == 1


class SlowProvider:
    def plan(self, **_kwargs: object) -> AgentPlan:
        time.sleep(0.05)
        return AgentPlan()

    def compose(self, **_kwargs: object) -> OutcomeProposal:
        raise AssertionError("compose must not run after a provider timeout")


def test_runtime_converts_provider_timeout_to_terminal_failed_outcome() -> None:
    runtime = AgentRuntime(
        tools=ToolRegistry([]),
        provider=SlowProvider(),
        config=RuntimeConfig(provider_timeout_seconds=0.005),
    )

    outcome = runtime.execute(principal("timeout-session"), "run")

    assert outcome.status == OutcomeStatus.TIMED_OUT
    assert SafetyFlag.PROVIDER_FAILURE in outcome.safety_flags
    assert SafetyFlag.PROVIDER_TIMEOUT in outcome.safety_flags
    assert outcome.error_code == ProviderTimeoutError().code.value
