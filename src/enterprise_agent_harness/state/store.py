"""Principal-bound state storage for non-conversational workflows."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Protocol

from ..contracts import ExecutionState, PrincipalContext


class StateOwnershipError(RuntimeError):
    """Raised when a principal or tenant uses another owner's state."""


class StateConflictError(RuntimeError):
    """Raised when a state write uses a stale version."""


class StateStore(Protocol):
    """Storage boundary for versioned workflow state."""

    def get_or_create(
        self,
        principal: PrincipalContext,
        *,
        agent_id: str,
        agent_version: str,
        state_id: str | None = None,
    ) -> ExecutionState:
        """Return state owned by the supplied principal and tenant."""

    def save(self, state: ExecutionState, *, expected_version: int | None = None) -> None:
        """Save state and check its optimistic-concurrency version."""


class InMemoryStateStore:
    """Deterministic state store for local runs and tests.

    The store does not store provider prompts or raw tool output. A durable
    consumer must add retention, encryption, and deployment-level concurrency.
    """

    def __init__(self) -> None:
        self._states: dict[tuple[str, str, str, str], ExecutionState] = {}
        self._lock = RLock()

    def get_or_create(
        self,
        principal: PrincipalContext,
        *,
        agent_id: str,
        agent_version: str,
        state_id: str | None = None,
    ) -> ExecutionState:
        resolved_state_id = state_id or principal.session_id
        key = (principal.tenant_id, principal.session_id, agent_id, resolved_state_id)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = ExecutionState(
                    state_id=resolved_state_id,
                    execution_id=f"state_{resolved_state_id}",
                    agent_id=agent_id,
                    agent_version=agent_version,
                    principal_id=principal.principal_id,
                    tenant_id=principal.tenant_id,
                    session_id=principal.session_id,
                )
                self._states[key] = state
            self._assert_owner(state, principal)
            if state.agent_version != agent_version:
                raise StateConflictError("state agent version does not match the requested version")
            return deepcopy(state)

    def save(self, state: ExecutionState, *, expected_version: int | None = None) -> None:
        key = (state.tenant_id, state.session_id, state.agent_id, state.state_id)
        with self._lock:
            existing = self._states.get(key)
            if existing is None:
                if state.version != 0 and expected_version not in {None, 0}:
                    raise StateConflictError("new state must start at version zero")
            else:
                self._assert_same_owner(existing, state)
                if expected_version is not None and existing.version != expected_version:
                    raise StateConflictError("state version is stale")
                if expected_version is None and state.version < existing.version:
                    raise StateConflictError("state version cannot move backwards")
            self._states[key] = deepcopy(state)

    @staticmethod
    def _assert_owner(state: ExecutionState, principal: PrincipalContext) -> None:
        if (
            state.principal_id != principal.principal_id
            or state.tenant_id != principal.tenant_id
            or state.session_id != principal.session_id
        ):
            raise StateOwnershipError("state belongs to a different principal or tenant")

    @staticmethod
    def _assert_same_owner(left: ExecutionState, right: ExecutionState) -> None:
        if (
            left.principal_id != right.principal_id
            or left.tenant_id != right.tenant_id
            or left.session_id != right.session_id
        ):
            raise StateOwnershipError("state owner cannot change")
