"""Governed agent-to-agent delegation and declarative composition."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from threading import RLock
from typing import Final

from .contracts import (
    AgentOutcome,
    CompositionDefinition,
    CompositionPattern,
    CompositionResult,
    CompositionStep,
    DelegatedExecutionContext,
    DelegationRequest,
    DelegationResult,
    ExecutionContext,
    OutcomeStatus,
    RiskLevel,
)
from .factory import AgentFactory, BuiltAgent, FactoryDependencyError
from .registries import RegistryError


class DelegationError(ValueError):
    """Base error for invalid or unsafe child-agent invocation."""


class DelegationAuthorityError(DelegationError):
    """Raised when a child request exceeds the parent execution authority."""


class DelegationCycleError(DelegationError):
    """Raised when a child identity is already in the delegation path."""


class DelegationDepthError(DelegationError):
    """Raised when the configured delegation depth ceiling would be exceeded."""


class CompositionError(DelegationError):
    """Raised when a composition definition cannot be dispatched safely."""


class AgentComposer:
    """Invoke registered agents through :class:`AgentFactory` runtimes only."""

    _RISK_ORDER: Final[dict[RiskLevel, int]] = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }

    def __init__(
        self,
        factory: AgentFactory,
        *,
        max_delegation_depth: int = 3,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1 <= max_delegation_depth <= 100:
            raise ValueError("max_delegation_depth must be between one and one hundred")
        self.factory = factory
        self.max_delegation_depth = max_delegation_depth
        self._id = id_factory or factory.new_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._identity_lock = RLock()
        self._delegation_ids: dict[str, set[str]] = {}
        self._child_execution_ids: set[str] = set()
        self._child_state_ids: set[str] = set()
        self._identity_counter = 0
        self._composition_counter = 0

    def delegate(
        self,
        parent: ExecutionContext,
        request: DelegationRequest,
    ) -> DelegationResult:
        """Run one exact child agent under an intersection of authorities."""

        self._validate_parent_request(parent, request)
        if parent.delegation_depth >= self.max_delegation_depth:
            raise DelegationDepthError(
                f"delegation depth exceeds the configured maximum: {self.max_delegation_depth}"
            )

        parent_identity = f"{parent.agent_id}@{parent.agent_version}"
        child_identity = f"{request.child_agent_id}@{request.child_agent_version}"
        path = tuple(parent.delegation_path)
        if not path:
            path = (parent_identity,)
        elif parent_identity not in path:
            path = (*path, parent_identity)
        if child_identity in path:
            raise DelegationCycleError(f"delegation cycle detected for child {child_identity}")
        child_path = (*path, child_identity)

        try:
            self.factory.agent_registry.resolve(
                request.child_agent_id,
                request.child_agent_version,
            )
            child = self.factory.runtime_for(
                request.child_agent_id,
                request.child_agent_version,
            )
        except (FactoryDependencyError, RegistryError) as exc:
            raise DelegationAuthorityError(
                f"child agent is not an active built version: {child_identity}"
            ) from exc

        authorized_tool_ids, authorized_tool_versions = self._tool_ceiling(parent, child, request)
        granted_permissions = self._permission_ceiling(parent, request)
        self._validate_tool_permissions(child, authorized_tool_versions, granted_permissions)
        effective_risk = self._risk_ceiling(parent, child, request)
        max_steps = min(parent.max_steps, child._trusted_max_plan_steps)
        with self._identity_lock:
            parent_delegations = self._delegation_ids.setdefault(parent.execution_id, set())
            if request.delegation_id in parent_delegations:
                raise DelegationError("delegation ID cannot be reused within a parent execution")
            parent_delegations.add(request.delegation_id)
            self._identity_counter += 1
            suffix = str(self._identity_counter)
            child_execution_id = f"{self._id('child_execution')}-{suffix}"
            child_state_id = f"{self._id('child_state')}-{suffix}"
            if child_execution_id in self._child_execution_ids:
                raise DelegationError("child execution ID cannot be reused")
            if child_state_id in self._child_state_ids:
                raise DelegationError("child state ID cannot be reused")
            self._child_execution_ids.add(child_execution_id)
            self._child_state_ids.add(child_state_id)
        child_agent_id, child_agent_version = child._trusted_agent_identity
        delegated_context = DelegatedExecutionContext(
            delegation_id=request.delegation_id,
            correlation_id=parent.correlation_id,
            parent_execution_id=parent.execution_id,
            parent_agent_id=parent.agent_id,
            parent_agent_version=parent.agent_version,
            child_execution_id=child_execution_id,
            child_agent_id=child_agent_id,
            child_agent_version=child_agent_version,
            principal=parent.principal,
            authorized_tool_ids=authorized_tool_ids,
            authorized_tool_versions=authorized_tool_versions,
            granted_permissions=granted_permissions,
            max_steps=max_steps,
            max_risk_level=effective_risk,
            delegation_depth=parent.delegation_depth + 1,
            delegation_path=child_path,
        )

        outcome = child.execute(
            parent.principal,
            request.input_text,
            authorized_tool_ids=delegated_context.authorized_tool_ids,
            authorized_tool_versions=delegated_context.authorized_tool_versions,
            granted_permissions=delegated_context.granted_permissions,
            max_risk_level=delegated_context.max_risk_level,
            max_steps=delegated_context.max_steps,
            execution_id=delegated_context.child_execution_id,
            state_id=child_state_id,
            correlation_id=delegated_context.correlation_id,
            parent_execution_id=delegated_context.parent_execution_id,
            delegation_id=delegated_context.delegation_id,
            delegation_depth=delegated_context.delegation_depth,
            delegation_path=delegated_context.delegation_path,
        )
        return DelegationResult(
            delegation_id=request.delegation_id,
            parent_execution_id=parent.execution_id,
            child_execution_id=delegated_context.child_execution_id,
            correlation_id=delegated_context.correlation_id,
            delegation_depth=delegated_context.delegation_depth,
            context=delegated_context,
            outcome=outcome,
            created_at=self._clock(),
        )

    def compose(
        self,
        parent: ExecutionContext,
        definition: CompositionDefinition,
        input_text: str,
        *,
        selected_step_id: str | None = None,
    ) -> CompositionResult:
        """Dispatch a router, supervisor, specialist, or sequential workflow."""

        if not input_text.strip():
            raise CompositionError("input_text must not be empty")
        steps = self._steps_for_definition(definition, selected_step_id)
        outcomes: list[AgentOutcome] = []
        next_input = input_text
        for step in steps:
            with self._identity_lock:
                self._composition_counter += 1
                delegation_id = (
                    f"{self._id(f'delegation_{step.step_id}')}-{self._composition_counter}"
                )
            request = DelegationRequest(
                delegation_id=delegation_id,
                parent_execution_id=parent.execution_id,
                parent_agent_id=parent.agent_id,
                parent_agent_version=parent.agent_version,
                child_agent_id=step.agent_id,
                child_agent_version=step.agent_version,
                input_text=next_input,
                reason=f"composition:{definition.composition_id}:{step.step_id}",
                max_risk_level=parent.max_risk_level,
            )
            result = self.delegate(parent, request)
            outcomes.append(result.outcome)
            if definition.pattern == CompositionPattern.SEQUENTIAL:
                if result.outcome.status != OutcomeStatus.COMPLETED:
                    break
                next_input = result.outcome.summary

        if not outcomes:
            raise CompositionError("composition selected no executable steps")
        return CompositionResult(
            composition_id=definition.composition_id,
            composition_version=definition.version,
            pattern=definition.pattern,
            parent_execution_id=parent.execution_id,
            correlation_id=parent.correlation_id,
            outcomes=tuple(outcomes),
            final_outcome=_final_outcome(outcomes),
            created_at=self._clock(),
        )

    def _validate_parent_request(
        self,
        parent: ExecutionContext,
        request: DelegationRequest,
    ) -> None:
        if request.parent_execution_id != parent.execution_id:
            raise DelegationAuthorityError("delegation parent execution does not match context")
        if (
            request.parent_agent_id != parent.agent_id
            or request.parent_agent_version != parent.agent_version
        ):
            raise DelegationAuthorityError("delegation parent agent does not match context")
        if not request.input_text.strip():
            raise DelegationError("delegation input_text must not be empty")
        if request.requested_permissions and not set(request.requested_permissions).issubset(
            parent.granted_permissions
        ):
            raise DelegationAuthorityError("delegated permissions exceed parent grants")
        if request.max_risk_level is not None and self._exceeds(
            request.max_risk_level,
            parent.max_risk_level,
        ):
            raise DelegationAuthorityError("delegated risk ceiling exceeds parent risk ceiling")

    def _tool_ceiling(
        self,
        parent: ExecutionContext,
        child: BuiltAgent,
        request: DelegationRequest,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        child_references = {
            reference: reference.split("@", 1)[0]
            for reference in child._trusted_allowed_tool_versions
        }
        child_ids = set(child_references.values())
        requested_ids = set(request.requested_tool_ids)
        if not requested_ids.issubset(child_ids):
            raise DelegationAuthorityError("delegated tools are outside the child manifest")
        parent_ids = set(parent.authorized_tool_ids)
        if not requested_ids.issubset(parent_ids):
            raise DelegationAuthorityError("delegated tools exceed parent tool authority")
        selected_ids = requested_ids or child_ids.intersection(parent_ids)

        parent_versions = set(parent.authorized_tool_versions)
        if selected_ids and not parent_versions:
            raise DelegationAuthorityError(
                "delegated tool authority must include exact parent tool versions"
            )
        exact_versions = tuple(
            sorted(
                reference
                for reference, tool_id in child_references.items()
                if tool_id in selected_ids and reference in parent_versions
            )
        )
        if selected_ids and not exact_versions:
            raise DelegationAuthorityError("no exact child tool version is within parent authority")
        effective_ids = tuple(sorted({child_references[reference] for reference in exact_versions}))
        return effective_ids, exact_versions

    @staticmethod
    def _permission_ceiling(
        parent: ExecutionContext,
        request: DelegationRequest,
    ) -> tuple[str, ...]:
        if request.requested_permissions:
            return tuple(request.requested_permissions)
        return tuple(parent.granted_permissions)

    @staticmethod
    def _validate_tool_permissions(
        child: BuiltAgent,
        authorized_tool_versions: Sequence[str],
        granted_permissions: Sequence[str],
    ) -> None:
        allowed = set(authorized_tool_versions)
        required = {
            permission
            for reference, permissions in child._trusted_tool_permissions
            if reference in allowed
            for permission in permissions
        }
        if not required.issubset(granted_permissions):
            missing = sorted(required.difference(granted_permissions))
            raise DelegationAuthorityError(
                f"delegated tools require permissions outside parent grants: {missing}"
            )

    def _risk_ceiling(
        self,
        parent: ExecutionContext,
        child: BuiltAgent,
        request: DelegationRequest,
    ) -> RiskLevel:
        child_risk = child._trusted_risk_level
        if self._exceeds(child_risk, parent.max_risk_level):
            raise DelegationAuthorityError("child agent risk exceeds parent risk ceiling")
        requested = request.max_risk_level or child_risk
        if self._exceeds(requested, parent.max_risk_level):
            raise DelegationAuthorityError("delegated risk ceiling exceeds parent risk ceiling")
        return min(
            (child_risk, requested, parent.max_risk_level),
            key=lambda risk: self._RISK_ORDER[risk],
        )

    @staticmethod
    def _steps_for_definition(
        definition: CompositionDefinition,
        selected_step_id: str | None,
    ) -> tuple[CompositionStep, ...]:
        by_id = {step.step_id: step for step in definition.steps}
        if selected_step_id is not None and selected_step_id not in by_id:
            raise CompositionError(f"unknown composition step: {selected_step_id}")
        if definition.pattern in {CompositionPattern.ROUTER, CompositionPattern.SPECIALIST}:
            if selected_step_id is None and len(definition.steps) != 1:
                raise CompositionError(
                    f"{definition.pattern.value} composition requires one selected step"
                )
            selected = selected_step_id or definition.steps[0].step_id
            return (by_id[selected],)
        if selected_step_id is not None:
            raise CompositionError(
                "selected_step_id is only valid for router or specialist composition"
            )
        return tuple(definition.steps)

    @classmethod
    def _exceeds(cls, actual: RiskLevel, maximum: RiskLevel) -> bool:
        return cls._RISK_ORDER[RiskLevel(actual)] > cls._RISK_ORDER[RiskLevel(maximum)]


def _final_outcome(outcomes: Sequence[AgentOutcome]) -> AgentOutcome:
    for outcome in outcomes:
        if outcome.status != OutcomeStatus.COMPLETED:
            return outcome
    return outcomes[-1]


__all__ = [
    "AgentComposer",
    "CompositionError",
    "DelegationAuthorityError",
    "DelegationCycleError",
    "DelegationDepthError",
    "DelegationError",
]
