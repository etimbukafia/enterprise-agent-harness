"""Acceptance tests for the Phase 3 tool runtime and registry."""

from __future__ import annotations

import time

import pytest
from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentLifecycleStatus,
    ExecutionContext,
    PrincipalContext,
    ToolDefinition,
    ToolInvocationError,
    ToolKind,
    ToolRegistry,
    ToolResult,
    ToolResultStatus,
    ToolRetryPolicy,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def context(execution_id: str = "execution-1") -> ExecutionContext:
    owner = PrincipalContext(
        principal_id="principal-1",
        tenant_id="tenant-1",
        session_id="session-1",
    )
    return ExecutionContext(
        execution_id=execution_id,
        agent_id="agent-1",
        agent_version="1.0.0",
        principal=owner,
        authorized_tool_ids=("lookup", "write"),
        state_id="state-1",
    )


def tool(
    tool_id: str = "lookup",
    handler=None,
    **kwargs,
) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        version="1.0.0",
        description="Run a typed test operation.",
        input_model=Input,
        output_model=Output,
        handler=handler or (lambda _context, arguments: Output(value=arguments.value)),
        **kwargs,
    )


def test_registry_registers_resolves_lists_and_lifecycle_controls_versions() -> None:
    registry = ToolRegistry([tool()])

    assert registry.resolve("lookup", "1.0.0").tool_id == "lookup"
    assert registry.version("lookup").version == "1.0.0"
    assert registry.versions("lookup") == ("1.0.0",)
    assert [item.tool_id for item in registry.list()] == ["lookup"]
    assert registry.descriptors()[0].lifecycle == AgentLifecycleStatus.ACTIVE

    deprecated = registry.deprecate("lookup", "1.0.0")
    assert deprecated.lifecycle == AgentLifecycleStatus.DEPRECATED
    assert registry.list() == []
    assert registry.list(include_inactive=True)[0].lifecycle == AgentLifecycleStatus.DEPRECATED
    with pytest.raises(ToolInvocationError, match="deprecated"):
        registry.resolve("lookup", "1.0.0")

    registry.activate("lookup", "1.0.0")
    registry.disable("lookup", "1.0.0")
    with pytest.raises(ToolInvocationError, match="disabled"):
        registry.resolve("lookup", "1.0.0")

    registry.activate("lookup", "1.0.0")
    registry.retire("lookup", "1.0.0")
    with pytest.raises(ToolInvocationError, match="retired"):
        registry.resolve("lookup", "1.0.0")


def test_tool_descriptor_exposes_risk_timeout_retry_dependency_owner_and_tags() -> None:
    registered = tool(
        kind=ToolKind.WRITE,
        risk_level="medium",
        owner_id="records-team",
        tags=("records", "write"),
        timeout_seconds=2.5,
        retryable=True,
        max_attempts=3,
        retry_backoff_seconds=0.1,
        dependencies=("records-api@1.0.0",),
        allowed_environments=("development", "staging"),
    )

    descriptor = registered.descriptor
    assert descriptor.kind == ToolKind.WRITE
    assert descriptor.risk_level.value == "medium"
    assert descriptor.timeout_seconds == 2.5
    assert descriptor.retryable is True
    assert descriptor.max_attempts == 3
    assert descriptor.dependencies == ["records-api@1.0.0"]
    assert descriptor.allowed_environments == ["development", "staging"]
    assert descriptor.owner_id == "records-team"
    assert descriptor.tags == ["records", "write"]


def test_destructive_action_exposes_critical_risk_and_approval_metadata() -> None:
    destructive = tool(
        tool_id="delete-record",
        kind=ToolKind.ACTION,
        risk_level="critical",
        requires_approval=True,
    )

    assert destructive.descriptor.kind == ToolKind.ACTION
    assert destructive.descriptor.risk_level.value == "critical"
    assert destructive.descriptor.requires_approval is True


def test_registry_rejects_invalid_arguments_before_handler_execution() -> None:
    called = False

    def handler(_context, _arguments):
        nonlocal called
        called = True
        return Output(value="should not run")

    events: list[tuple[str, dict[str, str]]] = []
    registry = ToolRegistry([tool(handler=handler)])

    with pytest.raises(ToolInvocationError) as error:
        registry.invoke(
            "lookup",
            context(),
            {"unexpected": "argument"},
            trace_callback=lambda event, metadata: events.append((event, metadata)),
        )

    assert error.value.code == "invalid_arguments"
    assert called is False
    assert registry.execution_records[0].status == ToolResultStatus.INVALID_ARGUMENTS
    assert events[-1][0] == "tool_execution_failed"


def test_registry_rejects_invalid_typed_results_after_handler_execution() -> None:
    registry = ToolRegistry([tool(handler=lambda _context, _arguments: {"wrong": "shape"})])

    with pytest.raises(ToolInvocationError) as error:
        registry.invoke("lookup", context(), {"value": "record-1"})

    assert error.value.code == "invalid_output"
    assert registry.execution_records[0].status == ToolResultStatus.FAILED


def test_retryable_read_tool_retries_and_records_attempts() -> None:
    attempts = 0

    def flaky(_context, arguments):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("temporary failure")
        return Output(value=arguments.value)

    events: list[str] = []
    registry = ToolRegistry(
        [
            tool(
                handler=flaky,
                retry_policy=ToolRetryPolicy(max_attempts=2),
            )
        ]
    )

    result = registry.invoke(
        "lookup",
        context(),
        {"value": "record-1"},
        trace_callback=lambda event, _metadata: events.append(event),
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.metadata["retry_count"] == "1"
    assert attempts == 2
    assert registry.execution_records[0].attempts == 2
    assert "tool_retry_scheduled" in events


def test_write_retry_requires_an_idempotency_key_and_prevents_duplicate_execution() -> None:
    attempts = 0

    def write(_context, arguments):
        nonlocal attempts
        attempts += 1
        return Output(value=arguments.value)

    registry = ToolRegistry(
        [
            tool(
                tool_id="write",
                handler=write,
                kind=ToolKind.WRITE,
                idempotency_required=True,
                retryable=True,
                max_attempts=2,
            )
        ]
    )

    with pytest.raises(ToolInvocationError, match="idempotency key") as error:
        registry.invoke("write", context(), {"value": "record-1"})
    assert error.value.code == "idempotency_key_required"
    assert attempts == 0

    first = registry.invoke(
        "write",
        context("execution-1"),
        {"value": "record-1"},
        idempotency_key="key-1",
    )
    replay = registry.invoke(
        "write",
        context("execution-2"),
        {"value": "record-1"},
        idempotency_key="key-1",
    )
    assert first.status == ToolResultStatus.SUCCEEDED
    assert replay.status == ToolResultStatus.SUCCEEDED
    assert replay.execution_id == "execution-2"
    assert replay.metadata["idempotent_replay"] == "true"
    assert attempts == 1

    with pytest.raises(ToolInvocationError, match="reused") as error:
        registry.invoke(
            "write",
            context("execution-3"),
            {"value": "different-record"},
            idempotency_key="key-1",
        )
    assert error.value.code == "idempotency_key_reused"
    assert "key-1" not in registry.execution_records[0].model_dump_json()


def test_tool_timeout_returns_a_structured_failed_execution_without_waiting_for_handler() -> None:
    def slow(_context, _arguments):
        time.sleep(0.05)
        return Output(value="late")

    registry = ToolRegistry([tool(handler=slow, timeout_seconds=0.005)])
    started = time.perf_counter()

    with pytest.raises(ToolInvocationError) as error:
        registry.invoke("lookup", context(), {"value": "record-1"})

    elapsed = time.perf_counter() - started
    assert error.value.code == "tool_timeout"
    assert elapsed < 0.04
    assert registry.execution_records[0].error_code == "tool_timeout"


def test_registry_validates_enveloped_result_identity() -> None:
    registry = ToolRegistry(
        [
            tool(
                handler=lambda _context, _arguments: ToolResult(
                    tool_id="other-tool",
                    status=ToolResultStatus.SUCCEEDED,
                    output={"value": "wrong"},
                )
            )
        ]
    )

    with pytest.raises(ToolInvocationError) as error:
        registry.invoke("lookup", context(), {"value": "record-1"})

    assert error.value.code == "invalid_result_identity"
