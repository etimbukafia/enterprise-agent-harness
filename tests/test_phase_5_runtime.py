"""Acceptance tests for the Phase 5 bounded execution runtime."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentPlan,
    AgentRuntime,
    CancellationToken,
    CompiledContext,
    ContextCompiler,
    ContextTrust,
    ExecutionContext,
    ExecutionState,
    OutcomeProposal,
    OutcomeStatus,
    PlanStep,
    PrincipalContext,
    ReplayRequest,
    RuntimeConfig,
    SafetyFlag,
    ToolDefinition,
    ToolRegistry,
    ToolResultStatus,
    ToolRetryPolicy,
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


class WorkflowProvider:
    def __init__(self, steps: list[PlanStep], *, summary: str = "workflow complete") -> None:
        self.steps = steps
        self.summary = summary

    def plan(self, *, request: Any) -> AgentPlan:
        del request
        return AgentPlan(steps=self.steps)

    def compose(self, *, request: Any) -> OutcomeProposal:
        del request
        return OutcomeProposal(summary=self.summary, confidence=1.0)


def tool(
    tool_id: str = "lookup",
    *,
    handler: Any | None = None,
    **kwargs: Any,
) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        version="1.0.0",
        description="Run a governed test operation.",
        input_model=Input,
        output_model=Output,
        handler=handler or (lambda _context, arguments: Output(value=arguments.value)),
        **kwargs,
    )


def runtime(
    provider: Any,
    tools: list[ToolDefinition],
    *,
    config: RuntimeConfig | None = None,
    id_factory: Any | None = None,
    clock: Any | None = None,
) -> AgentRuntime:
    return AgentRuntime(
        tools=ToolRegistry(tools),
        provider=provider,
        config=config,
        id_factory=id_factory,
        clock=clock,
    )


def test_context_compiles_trusted_and_untrusted_partitions_separately() -> None:
    owner = principal()
    execution = ExecutionContext(
        execution_id="execution-1",
        agent_id="agent-1",
        agent_version="1.0.0",
        principal=owner,
        state_id="state-1",
    )
    state = ExecutionState(
        state_id="state-1",
        execution_id="execution-1",
        agent_id="agent-1",
        agent_version="1.0.0",
        principal_id=owner.principal_id,
        tenant_id=owner.tenant_id,
        session_id=owner.session_id,
    )

    compiled = ContextCompiler().compile(
        principal=owner,
        execution=execution,
        state=state,
        input_text="Review the record",
    )

    assert isinstance(compiled, CompiledContext)
    assert all(block.trust == ContextTrust.TRUSTED for block in compiled.trusted_blocks)
    assert all(block.trust == ContextTrust.UNTRUSTED for block in compiled.untrusted_blocks)
    assert "principal_id=principal-1" in compiled.render_trusted()
    assert "Review the record" in compiled.render_untrusted()
    assert not {block.block_id for block in compiled.trusted_blocks}.intersection(
        block.block_id for block in compiled.untrusted_blocks
    )


def test_runtime_completes_bounded_read_write_workflow_and_exports_terminal_trace() -> None:
    calls: list[str] = []

    def lookup(_context: Any, arguments: Input) -> Output:
        calls.append("lookup")
        return Output(value=arguments.value)

    def save(_context: Any, arguments: Input) -> Output:
        calls.append("save")
        return Output(value=arguments.value)

    steps = [
        PlanStep(
            step_id="lookup-step",
            tool_id="lookup",
            tool_version="1.0.0",
            purpose="Read the record.",
            arguments={"value": "record-1"},
        ),
        PlanStep(
            step_id="save-step",
            tool_id="save",
            tool_version="1.0.0",
            purpose="Write the reviewed record.",
            arguments={"value": "record-1"},
            idempotency_key="save-record-1",
        ),
    ]
    governed = runtime(
        WorkflowProvider(steps),
        [
            tool(handler=lookup),
            tool(
                "save",
                handler=save,
                idempotency_required=True,
            ),
        ],
    )

    outcome = governed.execute(
        principal(),
        "Review and save the record",
        authorized_tool_ids=["lookup", "save"],
        execution_id="workflow-execution",
    )

    trace = governed.trace_for("workflow-execution")
    assert outcome.status == OutcomeStatus.COMPLETED
    assert calls == ["lookup", "save"]
    assert [call.result_status for call in outcome.tool_calls] == [
        ToolResultStatus.SUCCEEDED,
        ToolResultStatus.SUCCEEDED,
    ]
    assert [event.event_type for event in trace.events].count("step_started") == 2
    assert trace.events[-2].event_type == "execution_terminal"
    assert trace.events[-1].event_type == "outcome_decided"
    assert trace.final_status == OutcomeStatus.COMPLETED


def test_plan_step_limit_and_explicit_empty_plan_stop_are_terminal_and_traceable() -> None:
    too_long = runtime(
        WorkflowProvider(
            [
                PlanStep(
                    step_id=f"step-{index}",
                    tool_id="lookup",
                    purpose="Look up a record.",
                    arguments={"value": str(index)},
                )
                for index in range(3)
            ]
        ),
        [tool()],
        config=RuntimeConfig(max_plan_steps=2),
    )

    refused = too_long.execute(
        principal("limit"),
        "Run the bounded plan",
        authorized_tool_ids=["lookup"],
        execution_id="limit-execution",
    )

    assert refused.status == OutcomeStatus.REFUSED
    assert refused.error_code == "plan_too_long"
    assert too_long.trace_for("limit-execution").final_status == OutcomeStatus.REFUSED

    stopped = runtime(
        WorkflowProvider([], summary="must not compose"),
        [tool()],
    )
    stopped_outcome = stopped.execute(
        principal("empty-plan"),
        "No executable operation",
        authorized_tool_ids=["lookup"],
    )

    assert stopped_outcome.status == OutcomeStatus.NEEDS_INPUT
    assert stopped_outcome.error_code == "plan_stopped_without_tool_result"
    assert SafetyFlag.NO_RESULT in stopped_outcome.safety_flags
    assert any(
        event.event_type == "plan_stopped"
        for event in stopped.trace_for(stopped_outcome.execution_id).events
    )


def test_retry_budget_limits_handler_retries_across_the_execution() -> None:
    attempts = 0

    def always_fails(_context: Any, _arguments: Input) -> Output:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("temporary failure")

    governed = runtime(
        WorkflowProvider(
            [
                PlanStep(
                    step_id="retry-step",
                    tool_id="lookup",
                    purpose="Retry a temporary read failure.",
                    arguments={"value": "record-1"},
                )
            ]
        ),
        [
            tool(
                handler=always_fails,
                retry_policy=ToolRetryPolicy(max_attempts=5),
            )
        ],
        config=RuntimeConfig(max_retries=1),
    )

    outcome = governed.execute(
        principal("retry-budget"),
        "Read the record",
        authorized_tool_ids=["lookup"],
    )
    trace = governed.trace_for(outcome.execution_id)

    assert outcome.status == OutcomeStatus.FAILED
    assert attempts == 2
    assert SafetyFlag.RETRY_BUDGET_EXHAUSTED in outcome.safety_flags
    assert trace.tool_executions[0].attempts == 2
    assert any(event.event_type == "retry_budget_exhausted" for event in trace.events)


def test_execution_timeout_returns_terminal_outcome_without_composing() -> None:
    release = threading.Event()
    composed = False

    class SlowProvider:
        def plan(self, *, request: Any) -> AgentPlan:
            del request
            release.wait(1.0)
            return AgentPlan(
                steps=[
                    PlanStep(
                        step_id="slow-step",
                        tool_id="lookup",
                        purpose="Look up a record.",
                        arguments={"value": "record-1"},
                    )
                ]
            )

        def compose(self, *, request: Any) -> OutcomeProposal:
            nonlocal composed
            composed = True
            del request
            return OutcomeProposal(summary="unexpected", confidence=1.0)

    governed = runtime(
        SlowProvider(),
        [tool()],
        config=RuntimeConfig(execution_timeout_seconds=0.02),
    )
    result: list[Any] = []
    worker = threading.Thread(
        target=lambda: result.append(
            governed.execute(
                principal("timeout"),
                "Run slowly",
                authorized_tool_ids=["lookup"],
            )
        )
    )
    worker.start()
    worker.join(0.5)
    release.set()
    worker.join(0.5)

    assert not worker.is_alive()
    assert len(result) == 1
    assert result[0].status == OutcomeStatus.TIMED_OUT
    assert result[0].error_code == "execution_timeout"
    assert SafetyFlag.EXECUTION_TIMEOUT in result[0].safety_flags
    assert composed is False
    trace = governed.trace_for(result[0].execution_id)
    assert trace.final_status == OutcomeStatus.TIMED_OUT
    assert any(event.event_type == "execution_timed_out" for event in trace.events)


def test_execution_timeout_during_a_tool_preserves_the_interrupted_step_trace() -> None:
    release = threading.Event()

    def slow_handler(_context: Any, _arguments: Input) -> Output:
        release.wait(1.0)
        return Output(value="late")

    governed = runtime(
        WorkflowProvider(
            [
                PlanStep(
                    step_id="slow-tool-step",
                    tool_id="lookup",
                    purpose="Look up a record.",
                    arguments={"value": "record-1"},
                )
            ]
        ),
        [tool(handler=slow_handler)],
        config=RuntimeConfig(execution_timeout_seconds=0.02),
    )
    result: list[Any] = []
    worker = threading.Thread(
        target=lambda: result.append(
            governed.execute(
                principal("tool-timeout"),
                "Run the slow lookup",
                authorized_tool_ids=["lookup"],
            )
        )
    )
    worker.start()
    worker.join(0.5)
    release.set()
    worker.join(0.5)

    assert not worker.is_alive()
    assert result[0].status == OutcomeStatus.TIMED_OUT
    assert result[0].tool_calls[0].step_id == "slow-tool-step"
    trace = governed.trace_for(result[0].execution_id)
    assert trace.tool_executions[-1].error_code == "execution_timeout"
    assert any(event.event_type == "step_interrupted" for event in trace.events)


def test_cancellation_token_stops_a_run_at_the_provider_boundary() -> None:
    release = threading.Event()
    token = CancellationToken()

    class CancellableProvider:
        def plan(self, *, request: Any) -> AgentPlan:
            del request
            release.wait(1.0)
            return AgentPlan()

        def compose(self, *, request: Any) -> OutcomeProposal:
            raise AssertionError("compose must not run after cancellation")

    governed = runtime(
        CancellableProvider(),
        [],
        config=RuntimeConfig(execution_timeout_seconds=1.0),
    )
    result: list[Any] = []
    worker = threading.Thread(
        target=lambda: result.append(
            governed.execute(
                principal("cancel"),
                "Cancel this run",
                cancellation_event=token,
            )
        )
    )
    worker.start()
    time.sleep(0.03)
    token.cancel()
    worker.join(0.5)
    release.set()
    worker.join(0.5)

    assert not worker.is_alive()
    assert len(result) == 1
    assert result[0].status == OutcomeStatus.CANCELLED
    assert result[0].error_code == "execution_cancelled"
    assert SafetyFlag.EXECUTION_CANCELLED in result[0].safety_flags
    trace = governed.trace_for(result[0].execution_id)
    assert trace.final_status == OutcomeStatus.CANCELLED
    assert any(event.event_type == "execution_cancelled" for event in trace.events)


def test_partial_tool_failure_is_safe_and_deterministic() -> None:
    calls: list[str] = []

    def fails(_context: Any, _arguments: Input) -> Output:
        calls.append("fails")
        raise RuntimeError("bad dependency")

    def succeeds(_context: Any, arguments: Input) -> Output:
        calls.append("succeeds")
        return Output(value=arguments.value)

    provider = WorkflowProvider(
        [
            PlanStep(
                step_id="failed-step",
                tool_id="fails",
                purpose="Try the unavailable source.",
                arguments={"value": "record-1"},
            ),
            PlanStep(
                step_id="success-step",
                tool_id="succeeds",
                purpose="Use the fallback source.",
                arguments={"value": "record-1"},
            ),
        ]
    )
    governed = runtime(provider, [tool("fails", handler=fails), tool("succeeds", handler=succeeds)])

    outcome = governed.execute(
        principal("partial"),
        "Read from both sources",
        authorized_tool_ids=["fails", "succeeds"],
    )

    assert calls == ["fails", "succeeds"]
    assert outcome.status == OutcomeStatus.PARTIAL
    assert SafetyFlag.TOOL_FAILURE in outcome.safety_flags
    assert [call.result_status for call in outcome.tool_calls] == [
        ToolResultStatus.FAILED,
        ToolResultStatus.SUCCEEDED,
    ]


def test_fixed_provider_scenario_can_be_replayed_without_private_runtime_access() -> None:
    fixed_time = datetime(2026, 1, 1, tzinfo=UTC)

    def make_ids() -> Any:
        counters: dict[str, int] = {}

        def next_id(prefix: str) -> str:
            counters[prefix] = counters.get(prefix, 0) + 1
            return f"{prefix}-{counters[prefix]}"

        return next_id

    steps = [
        PlanStep(
            step_id="replay-step",
            tool_id="lookup",
            purpose="Look up a record.",
            arguments={"value": "record-1"},
        )
    ]
    traces = []
    for index in range(2):
        governed = runtime(
            WorkflowProvider(steps),
            [tool()],
            id_factory=make_ids(),
            clock=lambda: fixed_time,
        )
        outcome = governed.execute(
            principal(f"replay-{index}"),
            "Replay the same scenario",
            authorized_tool_ids=["lookup"],
            execution_id="replay-execution",
        )
        traces.append(governed.trace_for(outcome.execution_id))

    assert traces[0].input_fingerprint == traces[1].input_fingerprint
    assert [event.event_type for event in traces[0].events] == [
        event.event_type for event in traces[1].events
    ]
    assert [event.metadata for event in traces[0].events] == [
        event.metadata for event in traces[1].events
    ]
    replay_request = ReplayRequest(
        trace_id=traces[0].trace_id,
        execution_id=traces[0].execution_id,
        agent_id=traces[0].agent_id,
        agent_version=traces[0].agent_version,
        input_fingerprint=traces[0].input_fingerprint,
        state_id="replay-execution",
        state_version=1,
    )
    assert replay_request.schema_version == "agent-replay.v1"
