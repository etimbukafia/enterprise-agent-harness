"""Acceptance tests for durable workflow state and restartable execution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentPlan,
    AgentRuntime,
    ExecutionStateStatus,
    InMemoryApprovalBroker,
    InMemoryStateStore,
    OutcomeProposal,
    OutcomeStatus,
    PlanStep,
    PrincipalContext,
    RiskLevel,
    SQLiteStateStore,
    StateConflictError,
    StateOwnershipError,
    ToolDefinition,
    ToolKind,
    ToolRegistry,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


class Output(BaseModel):
    value: str


class RestartProvider:
    def __init__(self, step: PlanStep, *, reject_planning: bool = False) -> None:
        self.step = step
        self.reject_planning = reject_planning
        self.plan_calls = 0

    def plan(self, *, request: Any) -> AgentPlan:
        del request
        self.plan_calls += 1
        if self.reject_planning:
            raise AssertionError("a restart must use the stored checkpoint plan")
        return AgentPlan(steps=[self.step])

    def compose(self, *, request: Any) -> OutcomeProposal:
        del request
        return OutcomeProposal(summary="restart completed", confidence=1.0)


def owner(name: str = "state-owner") -> PrincipalContext:
    return PrincipalContext(
        principal_id=name,
        tenant_id="tenant-state",
        session_id="session-state",
    )


def state_step(value: str = "record-1") -> PlanStep:
    return PlanStep(
        step_id="publish-step",
        tool_id="publish-record",
        tool_version="1.0.0",
        purpose="Publish the reviewed record.",
        arguments={"value": value},
        idempotency_key="publish-record-1",
    )


def make_state(version: int = 0, *, status: ExecutionStateStatus = ExecutionStateStatus.PENDING):
    from enterprise_agent_harness import ExecutionState

    current = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=version)
    return ExecutionState(
        state_id="workflow-state",
        execution_id="execution-state",
        agent_id="records-agent",
        agent_version="1.0.0",
        principal_id="state-owner",
        tenant_id="tenant-state",
        session_id="session-state",
        status=status,
        version=version,
        data={"version": version},
        updated_at=current,
    )


def test_sqlite_state_survives_reopen_and_rejects_stale_or_foreign_writes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "workflow-state.sqlite"
    state_store = SQLiteStateStore(database)
    principal = owner()

    first = state_store.get_or_create(
        principal,
        agent_id="records-agent",
        agent_version="1.0.0",
        state_id="workflow-state",
    )
    updated = first.model_copy(
        update={
            "execution_id": "execution-1",
            "status": ExecutionStateStatus.PAUSED,
            "version": 1,
            "data": {"checkpoint": {"step": "publish"}},
            "updated_at": datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        }
    )
    state_store.save(updated, expected_version=0)
    state_store.close()

    reopened = SQLiteStateStore(database)
    restored = reopened.get_or_create(
        principal,
        agent_id="records-agent",
        agent_version="1.0.0",
        state_id="workflow-state",
    )
    assert restored == updated

    with pytest.raises(StateConflictError, match="stale"):
        reopened.save(restored.model_copy(update={"version": 2}), expected_version=0)

    foreign = PrincipalContext(
        principal_id="different-owner",
        tenant_id=principal.tenant_id,
        session_id=principal.session_id,
    )
    with pytest.raises(StateOwnershipError, match="different principal"):
        reopened.get_or_create(
            foreign,
            agent_id="records-agent",
            agent_version="1.0.0",
            state_id="workflow-state",
        )
    reopened.close()


def test_state_ttl_and_retention_hook_are_explicit_and_deterministic(tmp_path: Path) -> None:
    database = tmp_path / "retained-state.sqlite"
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    state_store = SQLiteStateStore(
        database,
        clock=lambda: now[0],
        ttl_seconds=10.0,
    )
    principal = owner("ttl-owner")
    state = state_store.get_or_create(
        principal,
        agent_id="records-agent",
        agent_version="1.0.0",
    )
    state_store.save(
        state.model_copy(update={"updated_at": now[0]}),
        expected_version=0,
    )
    now[0] += timedelta(seconds=11)

    assert state_store.purge_expired() == 1
    assert state_store.find_execution(principal, state.execution_id) is None
    state_store.close()

    memory_store = InMemoryStateStore(
        retention_hook=lambda value: value.status == ExecutionStateStatus.COMPLETED
    )
    retained = memory_store.get_or_create(
        owner("hook-owner"),
        agent_id="records-agent",
        agent_version="1.0.0",
    )
    assert memory_store.purge_expired() == 1
    assert memory_store.find_execution(owner("hook-owner"), retained.execution_id) is None


def test_sqlite_checkpoint_rehydrates_a_paused_execution_after_runtime_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "restartable-execution.sqlite"
    calls: list[str] = []
    tool = ToolDefinition(
        tool_id="publish-record",
        version="1.0.0",
        description="Publish one reviewed record.",
        input_model=Input,
        output_model=Output,
        handler=lambda _context, arguments: (
            calls.append(arguments.value) or Output(value=arguments.value)
        ),
        kind=ToolKind.ACTION,
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
    )
    principal = owner("restart-owner")
    broker = InMemoryApprovalBroker()
    provider = RestartProvider(state_step())
    first_store = SQLiteStateStore(database)
    first_runtime = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=provider,
        state_store=first_store,
        approval_broker=broker,
    )

    paused = first_runtime.execute(
        principal,
        "Publish the reviewed record",
        authorized_tool_ids=[tool.tool_id],
        execution_id="restartable-execution",
    )
    request = broker.pending_requests[0]
    assert paused.status == OutcomeStatus.ESCALATED
    checkpointed = first_store.find_execution(principal, paused.execution_id)
    assert checkpointed is not None
    assert checkpointed.status == ExecutionStateStatus.PAUSED
    assert "checkpoint" in checkpointed.data
    decision = broker.approve(request.request_id, decided_by="reviewer-1")
    first_store.close()

    restart_provider = RestartProvider(state_step(), reject_planning=True)
    second_store = SQLiteStateStore(database)
    restarted = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=restart_provider,
        state_store=second_store,
        approval_broker=InMemoryApprovalBroker(),
    )

    resumed = restarted.resume(
        paused.execution_id,
        approval_decision=decision,
        principal=principal,
    )
    restored_state = second_store.find_execution(principal, paused.execution_id)

    assert resumed.status == OutcomeStatus.COMPLETED
    assert calls == ["record-1"]
    assert restart_provider.plan_calls == 0
    assert restored_state is not None
    assert restored_state.status == ExecutionStateStatus.COMPLETED
    assert "checkpoint" not in restored_state.data
    trace = restarted.trace_for(paused.execution_id)
    assert any(event.event_type == "approval_requested" for event in trace.events)
    assert any(event.event_type == "approval_approved" for event in trace.events)
    assert trace.final_status == OutcomeStatus.COMPLETED
    second_store.close()


def test_restart_resume_rejects_a_principal_outside_the_checkpoint_owner(
    tmp_path: Path,
) -> None:
    database = tmp_path / "owner-bound-state.sqlite"
    tool = ToolDefinition(
        tool_id="publish-record",
        version="1.0.0",
        description="Publish one reviewed record.",
        input_model=Input,
        output_model=Output,
        handler=lambda _context, arguments: Output(value=arguments.value),
        kind=ToolKind.ACTION,
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
    )
    broker = InMemoryApprovalBroker()
    store = SQLiteStateStore(database)
    runtime = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=RestartProvider(state_step()),
        state_store=store,
        approval_broker=broker,
    )
    paused = runtime.execute(
        owner("protected-owner"),
        "Publish the reviewed record",
        authorized_tool_ids=[tool.tool_id],
        execution_id="protected-execution",
    )
    store.close()

    restarted = AgentRuntime(
        tools=ToolRegistry([tool]),
        provider=RestartProvider(state_step()),
        state_store=SQLiteStateStore(database),
        approval_broker=broker,
    )
    with pytest.raises(KeyError, match="unknown or non-paused"):
        restarted.resume(paused.execution_id, principal=owner("wrong-owner"))
    restarted.state_store.close()  # type: ignore[union-attr]
