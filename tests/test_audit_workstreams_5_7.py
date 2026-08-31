from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from enterprise_agent_harness import (
    AgentComposer,
    ApprovalDecision,
    DelegationRequest,
    ExecutionState,
    InMemoryApprovalBroker,
    InMemoryStateStore,
    StateConflictError,
)
from enterprise_agent_harness.composition import DelegationError
from enterprise_agent_harness.state import SQLiteStateStore
from tests.test_phase_6_approval import (
    ApprovalProvider,
    Output,
    action_tool,
    make_runtime,
    one_step,
    principal,
)
from tests.test_phase_10_composition import build_child, make_factory, parent_context


def test_approval_decision_requires_exact_request_id() -> None:
    with pytest.raises(ValidationError, match="request_id"):
        ApprovalDecision(
            approval_id="approval-1",
            action_digest="digest",
            decision="approved",
            decided_by="reviewer",
            reason_code="approved",
        )


def test_approval_request_cannot_be_resumed_or_consumed_twice() -> None:
    tool = action_tool(lambda _context, arguments: Output(value=arguments.value))
    broker = InMemoryApprovalBroker()
    broker_runtime = make_runtime(ApprovalProvider([one_step()]), tool, broker)
    paused = broker_runtime.execute(
        principal("replay"), "publish", authorized_tool_ids=[tool.tool_id]
    )
    request = broker.pending_requests[0]
    broker.approve(request.request_id, decided_by="reviewer")
    assert broker_runtime.resume(paused.execution_id).status.value == "completed"
    with pytest.raises(KeyError, match="unknown or non-paused"):
        broker_runtime.resume(paused.execution_id)


@pytest.mark.parametrize("expected_version", [1])
def test_in_memory_state_creation_rejects_nonzero_or_invalid_expected_version(
    expected_version: int,
) -> None:
    store = InMemoryStateStore()
    state = ExecutionState(
        state_id="state-1",
        execution_id="execution-1",
        agent_id="agent",
        agent_version="1.0.0",
        principal_id="principal",
        tenant_id="tenant",
        session_id="session",
        version=0,
    )
    with pytest.raises(StateConflictError):
        store.save(state.model_copy(update={"version": expected_version}), expected_version=None)
    with pytest.raises(StateConflictError):
        store.save(state, expected_version=2)


def test_sqlite_state_creation_rejects_nonzero_and_invalid_expected_version(
    tmp_path: Path,
) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite")
    state = ExecutionState(
        state_id="state-1",
        execution_id="execution-1",
        agent_id="agent",
        agent_version="1.0.0",
        principal_id="principal",
        tenant_id="tenant",
        session_id="session",
    )
    with pytest.raises(StateConflictError):
        store.save(state.model_copy(update={"version": 1}), expected_version=None)
    with pytest.raises(StateConflictError):
        store.save(state, expected_version=2)
    store.close()


def test_delegation_ids_and_child_identities_are_single_use_and_unique() -> None:
    factory, _traces, _audits = make_factory()
    build_child(factory, "child-agent")
    composer = AgentComposer(factory, id_factory=lambda prefix: prefix)
    request = DelegationRequest(
        delegation_id="delegation-1",
        parent_execution_id="parent-execution",
        parent_agent_id="parent-agent",
        parent_agent_version="1.0.0",
        child_agent_id="child-agent",
        child_agent_version="1.0.0",
        input_text="review",
        reason="test",
        requested_tool_ids=("records-read",),
    )
    first = composer.delegate(parent_context(), request)
    second = composer.delegate(
        parent_context(), request.model_copy(update={"delegation_id": "delegation-2"})
    )
    assert first.child_execution_id != second.child_execution_id
    assert first.context.child_execution_id != second.context.child_execution_id
    with pytest.raises(DelegationError, match="cannot be reused"):
        composer.delegate(parent_context(), request)
