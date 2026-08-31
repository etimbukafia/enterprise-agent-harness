"""Versioned agent and capability registries with deterministic discovery."""

from __future__ import annotations

import builtins
from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from packaging.version import InvalidVersion, Version

from .contracts import (
    AgentDefinition,
    AgentLifecycleStatus,
    CapabilityDefinition,
    PolicyDefinition,
    RegistryDependency,
    RegistrySnapshot,
    RiskLevel,
    ToolDescriptor,
)
from .registry_audit import ListRegistryAuditSink, RegistryAuditEvent, RegistryAuditSink
from .tools.definitions import ToolDefinition, ToolInvocationError
from .tools.registry import ToolRegistry


class RegistryError(ValueError):
    """Base error for invalid registration or registry resolution."""


class DuplicateRegistrationError(RegistryError):
    """Raised when an exact component identity and version already exists."""


class IncompatibleRegistrationError(RegistryError):
    """Raised when a component references missing or incompatible dependencies."""


class StaleRegistrationError(RegistryError):
    """Raised when a caller attempts to replace an immutable version."""


class RegistryLifecycleError(RegistryError):
    """Raised when a lifecycle transition is not allowed."""


class CapabilityRegistry:
    """Store and discover immutable, versioned capability definitions."""

    registry_id = "capabilities"

    def __init__(
        self,
        *,
        tools: ToolRegistry | None = None,
        audit_sink: RegistryAuditSink | None = None,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.tools = tools
        self.audit_sink = audit_sink or ListRegistryAuditSink()
        self._id = id_factory or (lambda prefix: f"{prefix}_{uuid4().hex[:10]}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._capabilities: dict[tuple[str, str], CapabilityDefinition] = {}
        self._events: list[RegistryAuditEvent] = []
        self._revision = 0
        self._lock = RLock()

    @property
    def revision(self) -> int:
        """Return the monotonic registry revision."""

        with self._lock:
            return self._revision

    @property
    def events(self) -> list[RegistryAuditEvent]:
        """Return registry events without exposing sink storage."""

        with self._lock:
            return deepcopy(self._events)

    def register(
        self,
        capability: CapabilityDefinition,
        *,
        replace_existing: bool = False,
    ) -> CapabilityDefinition:
        """Register one exact capability version."""

        candidate = capability.model_copy(deep=True)
        key = (candidate.capability_id, candidate.version)
        with self._lock:
            if key in self._capabilities:
                if replace_existing:
                    raise StaleRegistrationError("registered capability versions are immutable")
                raise DuplicateRegistrationError(
                    "capability identity and version pairs must be unique"
                )
            if candidate.lifecycle in {
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
            }:
                self._validate_capability(candidate)
            self._capabilities[key] = candidate
            self._changed(
                operation="registered",
                component_kind="capability",
                component_id=candidate.capability_id,
                version=candidate.version,
                to_status=candidate.lifecycle,
            )
            return deepcopy(candidate)

    def get(self, capability_id: str, version: str) -> CapabilityDefinition:
        """Return an exact capability version, including inactive records."""

        with self._lock:
            try:
                return deepcopy(self._capabilities[(capability_id, version)])
            except KeyError as exc:
                raise RegistryError(f"unknown capability: {capability_id}@{version}") from exc

    def resolve(self, capability_id: str, version: str | None = None) -> CapabilityDefinition:
        """Resolve one active capability with explicit version selection."""

        with self._lock:
            if version is not None:
                capability = self._capabilities.get((capability_id, version))
                if capability is None:
                    raise RegistryError(f"unknown capability: {capability_id}@{version}")
                if capability.lifecycle != AgentLifecycleStatus.ACTIVE:
                    raise RegistryError(f"capability is not active: {capability_id}@{version}")
                return deepcopy(capability)
            active = [
                capability
                for (candidate_id, _), capability in self._capabilities.items()
                if candidate_id == capability_id
                and capability.lifecycle == AgentLifecycleStatus.ACTIVE
            ]
            if not active:
                raise RegistryError(f"unknown active capability: {capability_id}")
            if len(active) > 1:
                raise RegistryError(f"capability version is required: {capability_id}")
            return deepcopy(active[0])

    def list(self, *, include_inactive: bool = False) -> list[CapabilityDefinition]:
        """List capabilities in deterministic identity and version order."""

        with self._lock:
            values = list(self._capabilities.values())
        if not include_inactive:
            values = [
                capability
                for capability in values
                if capability.lifecycle == AgentLifecycleStatus.ACTIVE
            ]
        return deepcopy(sorted(values, key=_component_sort_key))

    def versions(self, capability_id: str, *, include_inactive: bool = True) -> tuple[str, ...]:
        """Return versions for one capability identity."""

        return tuple(
            capability.version
            for capability in self.list(include_inactive=include_inactive)
            if capability.capability_id == capability_id
        )

    def search(
        self,
        query: str | None = None,
        *,
        intent: str | None = None,
        tool_id: str | None = None,
        language: str | None = None,
        risk_level: RiskLevel | None = None,
        include_inactive: bool = False,
    ) -> builtins.list[CapabilityDefinition]:
        """Find reusable capabilities by stable metadata."""

        query_text = query.lower() if query is not None else None
        intent_text = intent.lower() if intent is not None else None
        language_text = language.lower() if language is not None else None
        values = self.list(include_inactive=include_inactive)
        return [
            capability
            for capability in values
            if (
                query_text is None
                or query_text in capability.capability_id.lower()
                or query_text in capability.description.lower()
                or any(query_text in value.lower() for value in capability.supported_operations)
                or any(query_text in value.lower() for value in capability.supported_intents)
                or any(query_text in value.lower() for value in capability.tags)
            )
            and (
                intent_text is None
                or any(intent_text == value.lower() for value in capability.supported_intents)
                or any(intent_text == value.lower() for value in capability.supported_operations)
            )
            and (tool_id is None or tool_id in capability.allowed_tool_ids)
            and (
                language_text is None
                or any(language_text == value.lower() for value in capability.supported_languages)
            )
            and (risk_level is None or capability.risk_level == RiskLevel(risk_level))
        ]

    def validate(self, capability_id: str, version: str) -> CapabilityDefinition:
        """Validate dependencies and move a draft to validated."""

        with self._lock:
            current = self._require(capability_id, version)
            self._validate_capability(current)
            if current.lifecycle == AgentLifecycleStatus.DRAFT:
                return self._transition(current, AgentLifecycleStatus.VALIDATED, "validated")
            if current.lifecycle in {
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
            }:
                return deepcopy(current)
            raise RegistryLifecycleError(
                f"cannot validate capability in state {current.lifecycle.value}"
            )

    def activate(self, capability_id: str, version: str) -> CapabilityDefinition:
        """Validate and activate one capability version."""

        with self._lock:
            current = self._require(capability_id, version)
            self._validate_capability(current)
            if current.lifecycle == AgentLifecycleStatus.DRAFT:
                current = self._transition(current, AgentLifecycleStatus.VALIDATED, "validated")
            if current.lifecycle == AgentLifecycleStatus.VALIDATED:
                return self._transition(current, AgentLifecycleStatus.ACTIVE, "activated")
            if current.lifecycle == AgentLifecycleStatus.SUSPENDED:
                return self._transition(current, AgentLifecycleStatus.ACTIVE, "reactivated")
            if current.lifecycle == AgentLifecycleStatus.ACTIVE:
                return deepcopy(current)
            raise RegistryLifecycleError(
                f"cannot activate capability in state {current.lifecycle.value}"
            )

    def suspend(self, capability_id: str, version: str) -> CapabilityDefinition:
        """Suspend new use of one active capability version."""

        return self._transition_by_key(
            capability_id,
            version,
            AgentLifecycleStatus.SUSPENDED,
            "suspended",
            allowed_from={AgentLifecycleStatus.ACTIVE},
        )

    def deprecate(self, capability_id: str, version: str) -> CapabilityDefinition:
        """Mark one capability version deprecated."""

        return self._transition_by_key(
            capability_id,
            version,
            AgentLifecycleStatus.DEPRECATED,
            "deprecated",
            allowed_from={
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
                AgentLifecycleStatus.SUSPENDED,
            },
        )

    def retire(self, capability_id: str, version: str) -> CapabilityDefinition:
        """Retire one capability version permanently."""

        return self._transition_by_key(
            capability_id,
            version,
            AgentLifecycleStatus.RETIRED,
            "retired",
            allowed_from={
                AgentLifecycleStatus.DRAFT,
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
                AgentLifecycleStatus.SUSPENDED,
                AgentLifecycleStatus.DEPRECATED,
            },
        )

    def snapshot(self, *, include_inactive: bool = False) -> RegistrySnapshot:
        """Return a versioned capability snapshot for deterministic planning."""

        capabilities = self.list(include_inactive=include_inactive)
        tools = (
            self.tools.descriptors(include_inactive=include_inactive)
            if self.tools is not None
            else []
        )
        tool_revision = self.tools.revision if self.tools is not None else 0
        dependencies = _capability_dependencies(capabilities, tools)
        snapshot = RegistrySnapshot(
            snapshot_id=self._id("registry_snapshot"),
            revision=self.revision + tool_revision,
            tool_registry_revision=tool_revision,
            capabilities=capabilities,
            tools=tools,
            dependencies=dependencies,
        )
        self._record_snapshot(snapshot, component_kind="capability")
        return snapshot

    def _require(self, capability_id: str, version: str) -> CapabilityDefinition:
        try:
            return self._capabilities[(capability_id, version)]
        except KeyError as exc:
            raise RegistryError(f"unknown capability: {capability_id}@{version}") from exc

    def _validate_capability(self, capability: CapabilityDefinition) -> None:
        if self.tools is None:
            if capability.allowed_tool_ids:
                raise IncompatibleRegistrationError(
                    f"capability {capability.capability_id}@{capability.version} has no tool registry"
                )
            return
        active_tools = self.tools.list(include_inactive=False)
        for tool_id in capability.allowed_tool_ids:
            matches = [tool for tool in active_tools if tool.tool_id == tool_id]
            if not matches:
                raise IncompatibleRegistrationError(
                    f"capability references unavailable tool: {tool_id}"
                )
            if any(_risk_exceeds(tool.risk_level, capability.risk_level) for tool in matches):
                raise IncompatibleRegistrationError(
                    f"capability risk is below tool risk: {tool_id}"
                )

    def _transition_by_key(
        self,
        capability_id: str,
        version: str,
        target: AgentLifecycleStatus,
        operation: str,
        *,
        allowed_from: set[AgentLifecycleStatus],
    ) -> CapabilityDefinition:
        with self._lock:
            current = self._require(capability_id, version)
            if current.lifecycle not in allowed_from:
                raise RegistryLifecycleError(
                    f"cannot move capability from {current.lifecycle.value} to {target.value}"
                )
            return self._transition(current, target, operation)

    def _transition(
        self,
        current: CapabilityDefinition,
        target: AgentLifecycleStatus,
        operation: str,
    ) -> CapabilityDefinition:
        updated = current.model_copy(update={"lifecycle": target}, deep=True)
        self._capabilities[(current.capability_id, current.version)] = updated
        self._changed(
            operation=operation,
            component_kind="capability",
            component_id=current.capability_id,
            version=current.version,
            from_status=current.lifecycle,
            to_status=target,
        )
        return deepcopy(updated)

    def _changed(
        self,
        *,
        operation: str,
        component_kind: str,
        component_id: str,
        version: str,
        from_status: AgentLifecycleStatus | None = None,
        to_status: AgentLifecycleStatus | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self._revision += 1
        event = RegistryAuditEvent(
            event_id=self._id("registry_event"),
            registry_id=self.registry_id,
            operation=operation,
            component_kind=component_kind,
            component_id=component_id,
            version=version,
            from_status=from_status,
            to_status=to_status,
            occurred_at=self._clock(),
            metadata=metadata or {},
        )
        self._events.append(event)
        self.audit_sink.append(event)

    def _record_snapshot(self, snapshot: RegistrySnapshot, *, component_kind: str) -> None:
        with self._lock:
            self._events.append(
                RegistryAuditEvent(
                    event_id=self._id("registry_event"),
                    registry_id=self.registry_id,
                    operation="snapshot_created",
                    component_kind=component_kind,
                    component_id=snapshot.snapshot_id,
                    version=str(snapshot.revision),
                    occurred_at=self._clock(),
                    metadata={
                        "capability_count": str(len(snapshot.capabilities)),
                        "tool_count": str(len(snapshot.tools)),
                    },
                )
            )
            self.audit_sink.append(self._events[-1])


class AgentRegistry:
    """Store, validate, and discover exact agent definitions."""

    registry_id = "agents"

    def __init__(
        self,
        *,
        capabilities: CapabilityRegistry | None = None,
        tools: ToolRegistry | None = None,
        policies: Sequence[PolicyDefinition] = (),
        audit_sink: RegistryAuditSink | None = None,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if tools is None and capabilities is not None and capabilities.tools is not None:
            self.tools = capabilities.tools
        else:
            self.tools = tools or ToolRegistry()
        if capabilities is None:
            self.capabilities = CapabilityRegistry(tools=self.tools)
        else:
            self.capabilities = capabilities
            if capabilities.tools is None:
                capabilities.tools = self.tools
            elif capabilities.tools is not self.tools:
                raise ValueError("agent and capability registries must share one tool registry")
        policy_keys = [(policy.policy_id, policy.version) for policy in policies]
        if len(policy_keys) != len(set(policy_keys)):
            raise DuplicateRegistrationError("policy identity and version pairs must be unique")
        self._policies = {
            (policy.policy_id, policy.version): policy.model_copy(deep=True) for policy in policies
        }
        self.audit_sink = audit_sink or ListRegistryAuditSink()
        self._id = id_factory or (lambda prefix: f"{prefix}_{uuid4().hex[:10]}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._agents: dict[tuple[str, str], AgentDefinition] = {}
        self._events: list[RegistryAuditEvent] = []
        self._revision = 0
        self._lock = RLock()

    @property
    def revision(self) -> int:
        """Return the monotonic registry revision."""

        with self._lock:
            return self._revision

    @property
    def events(self) -> list[RegistryAuditEvent]:
        """Return registry events without exposing internal storage."""

        with self._lock:
            return deepcopy(self._events)

    @property
    def policies(self) -> list[PolicyDefinition]:
        """Return the configured policy catalog."""

        with self._lock:
            return deepcopy(list(self._policies.values()))

    def register(
        self,
        agent: AgentDefinition,
        *,
        replace_existing: bool = False,
    ) -> AgentDefinition:
        """Register one exact agent version."""

        candidate = agent.model_copy(deep=True)
        key = (candidate.agent_id, candidate.version)
        with self._lock:
            if key in self._agents:
                if replace_existing:
                    raise StaleRegistrationError("registered agent versions are immutable")
                raise DuplicateRegistrationError("agent identity and version pairs must be unique")
            if candidate.lifecycle in {
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
            }:
                required_statuses = (
                    {AgentLifecycleStatus.ACTIVE}
                    if candidate.lifecycle == AgentLifecycleStatus.ACTIVE
                    else {
                        AgentLifecycleStatus.VALIDATED,
                        AgentLifecycleStatus.ACTIVE,
                    }
                )
                self._validate_agent(
                    candidate,
                    required_dependency_statuses=required_statuses,
                )
            self._agents[key] = candidate
            self._changed(
                operation="registered",
                component_kind="agent",
                component_id=candidate.agent_id,
                version=candidate.version,
                to_status=candidate.lifecycle,
            )
            return deepcopy(candidate)

    def get(self, agent_id: str, version: str) -> AgentDefinition:
        """Return an exact agent version, including inactive records."""

        with self._lock:
            try:
                return deepcopy(self._agents[(agent_id, version)])
            except KeyError as exc:
                raise RegistryError(f"unknown agent: {agent_id}@{version}") from exc

    def resolve(self, agent_id: str, version: str | None = None) -> AgentDefinition:
        """Resolve one active agent with explicit version selection."""

        with self._lock:
            if version is not None:
                agent = self._agents.get((agent_id, version))
                if agent is None:
                    raise RegistryError(f"unknown agent: {agent_id}@{version}")
                if agent.lifecycle != AgentLifecycleStatus.ACTIVE:
                    raise RegistryError(f"agent is not active: {agent_id}@{version}")
                self._validate_agent(
                    agent,
                    required_dependency_statuses={AgentLifecycleStatus.ACTIVE},
                )
                return deepcopy(agent)
            active = [
                agent
                for (candidate_id, _), agent in self._agents.items()
                if candidate_id == agent_id and agent.lifecycle == AgentLifecycleStatus.ACTIVE
            ]
            if not active:
                raise RegistryError(f"unknown active agent: {agent_id}")
            if len(active) > 1:
                raise RegistryError(f"agent version is required: {agent_id}")
            self._validate_agent(
                active[0],
                required_dependency_statuses={AgentLifecycleStatus.ACTIVE},
            )
            return deepcopy(active[0])

    def list(self, *, include_inactive: bool = False) -> list[AgentDefinition]:
        """List agents in deterministic identity and version order."""

        with self._lock:
            values = list(self._agents.values())
        if not include_inactive:
            values = [agent for agent in values if agent.lifecycle == AgentLifecycleStatus.ACTIVE]
        return deepcopy(sorted(values, key=_component_sort_key))

    def versions(self, agent_id: str, *, include_inactive: bool = True) -> tuple[str, ...]:
        """Return versions for one agent identity."""

        return tuple(
            agent.version
            for agent in self.list(include_inactive=include_inactive)
            if agent.agent_id == agent_id
        )

    def check_compatibility(
        self,
        agent: AgentDefinition,
        *,
        require_active_dependencies: bool = True,
    ) -> None:
        """Check an unregistered agent against the exact registry dependencies.

        Factory validation needs the same public-boundary checks as registration,
        but must be able to run them before mutating this registry.
        """

        candidate = agent.model_copy(deep=True)
        required_statuses = (
            {AgentLifecycleStatus.ACTIVE}
            if require_active_dependencies
            else {
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
            }
        )
        with self._lock:
            self._validate_agent(
                candidate,
                required_dependency_statuses=required_statuses,
            )

    def search(
        self,
        query: str | None = None,
        *,
        intent: str | None = None,
        language: str | None = None,
        risk_level: RiskLevel | None = None,
        include_inactive: bool = False,
    ) -> builtins.list[AgentDefinition]:
        """Find reusable agents by stable declarative metadata."""

        query_text = query.lower() if query is not None else None
        intent_text = intent.lower() if intent is not None else None
        language_text = language.lower() if language is not None else None
        values = self.list(include_inactive=include_inactive)
        return [
            agent
            for agent in values
            if (
                query_text is None
                or query_text in agent.agent_id.lower()
                or query_text in agent.goal.lower()
                or any(query_text in value.lower() for value in agent.supported_intents)
            )
            and (
                intent_text is None
                or any(intent_text == value.lower() for value in agent.supported_intents)
            )
            and (
                language_text is None
                or any(language_text == value.lower() for value in agent.supported_languages)
            )
            and (risk_level is None or agent.risk_level == RiskLevel(risk_level))
        ]

    def validate(self, agent_id: str, version: str) -> AgentDefinition:
        """Validate exact dependencies and move a draft to validated."""

        with self._lock:
            current = self._require(agent_id, version)
            self._validate_agent(
                current,
                required_dependency_statuses={
                    AgentLifecycleStatus.VALIDATED,
                    AgentLifecycleStatus.ACTIVE,
                },
            )
            if current.lifecycle == AgentLifecycleStatus.DRAFT:
                return self._transition(current, AgentLifecycleStatus.VALIDATED, "validated")
            if current.lifecycle in {
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
            }:
                return deepcopy(current)
            raise RegistryLifecycleError(
                f"cannot validate agent in state {current.lifecycle.value}"
            )

    def activate(self, agent_id: str, version: str) -> AgentDefinition:
        """Validate and activate one agent version."""

        with self._lock:
            current = self._require(agent_id, version)
            self._validate_agent(
                current,
                required_dependency_statuses={AgentLifecycleStatus.ACTIVE},
            )
            if current.lifecycle == AgentLifecycleStatus.DRAFT:
                current = self._transition(current, AgentLifecycleStatus.VALIDATED, "validated")
            if current.lifecycle == AgentLifecycleStatus.VALIDATED:
                return self._transition(current, AgentLifecycleStatus.ACTIVE, "activated")
            if current.lifecycle == AgentLifecycleStatus.SUSPENDED:
                return self._transition(current, AgentLifecycleStatus.ACTIVE, "reactivated")
            if current.lifecycle == AgentLifecycleStatus.ACTIVE:
                return deepcopy(current)
            raise RegistryLifecycleError(
                f"cannot activate agent in state {current.lifecycle.value}"
            )

    def suspend(self, agent_id: str, version: str) -> AgentDefinition:
        """Suspend new use of one active agent version."""

        return self._transition_by_key(
            agent_id,
            version,
            AgentLifecycleStatus.SUSPENDED,
            "suspended",
            allowed_from={AgentLifecycleStatus.ACTIVE},
        )

    def deprecate(self, agent_id: str, version: str) -> AgentDefinition:
        """Mark one agent version deprecated."""

        return self._transition_by_key(
            agent_id,
            version,
            AgentLifecycleStatus.DEPRECATED,
            "deprecated",
            allowed_from={
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
                AgentLifecycleStatus.SUSPENDED,
            },
        )

    def retire(self, agent_id: str, version: str) -> AgentDefinition:
        """Retire one agent version permanently."""

        return self._transition_by_key(
            agent_id,
            version,
            AgentLifecycleStatus.RETIRED,
            "retired",
            allowed_from={
                AgentLifecycleStatus.DRAFT,
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
                AgentLifecycleStatus.SUSPENDED,
                AgentLifecycleStatus.DEPRECATED,
            },
        )

    def snapshot(self, *, include_inactive: bool = False) -> RegistrySnapshot:
        """Return exact active registry records for deterministic planning."""

        agents = self.list(include_inactive=include_inactive)
        capabilities = self.capabilities.list(include_inactive=include_inactive)
        tools = self.tools.descriptors(include_inactive=include_inactive)
        policies = sorted(
            [
                policy
                for policy in self.policies
                if include_inactive or policy.lifecycle == AgentLifecycleStatus.ACTIVE
            ],
            key=lambda policy: (policy.policy_id, policy.version),
        )
        tool_revision = self.tools.revision
        dependencies = _agent_dependencies(agents, capabilities, tools)
        snapshot = RegistrySnapshot(
            snapshot_id=self._id("registry_snapshot"),
            revision=self.revision + self.capabilities.revision + tool_revision,
            tool_registry_revision=tool_revision,
            agents=agents,
            capabilities=capabilities,
            tools=tools,
            policies=policies,
            dependencies=dependencies,
        )
        self._record_snapshot(snapshot)
        return snapshot

    def _require(self, agent_id: str, version: str) -> AgentDefinition:
        try:
            return self._agents[(agent_id, version)]
        except KeyError as exc:
            raise RegistryError(f"unknown agent: {agent_id}@{version}") from exc

    def _validate_agent(
        self,
        agent: AgentDefinition,
        *,
        required_dependency_statuses: set[AgentLifecycleStatus],
    ) -> None:
        capability_values: list[CapabilityDefinition] = []
        for reference in agent.capabilities:
            try:
                capability = self.capabilities.get(reference.component_id, reference.version)
            except RegistryError as exc:
                raise IncompatibleRegistrationError(str(exc)) from exc
            if capability.lifecycle not in required_dependency_statuses:
                raise IncompatibleRegistrationError(
                    f"capability is not usable: {capability.capability_id}@{capability.version}"
                )
            if _risk_exceeds(capability.risk_level, agent.risk_level):
                raise IncompatibleRegistrationError(
                    f"capability risk exceeds agent risk: {capability.capability_id}"
                )
            capability_values.append(capability)

        tools_by_reference: list[ToolDefinition] = []
        for reference in agent.allowed_tools:
            try:
                tool = self.tools.get(
                    reference.component_id,
                    reference.version,
                    include_inactive=True,
                )
            except ToolInvocationError as exc:
                raise IncompatibleRegistrationError(str(exc)) from exc
            if tool.lifecycle not in required_dependency_statuses:
                raise IncompatibleRegistrationError(
                    f"tool is not usable: {tool.tool_id}@{tool.version}"
                )
            if _risk_exceeds(tool.risk_level, agent.risk_level):
                raise IncompatibleRegistrationError(
                    f"tool risk exceeds agent risk: {tool.tool_id}@{tool.version}"
                )
            tools_by_reference.append(tool)
            for dependency in tool.dependencies:
                if not any(
                    candidate.tool_id == dependency
                    and candidate.lifecycle in required_dependency_statuses
                    for candidate in self.tools.list(include_inactive=True)
                ):
                    raise IncompatibleRegistrationError(
                        f"tool dependency is unavailable: {dependency}"
                    )

        policy_by_key = self._policies
        for reference in agent.policies:
            policy = policy_by_key.get((reference.component_id, reference.version))
            if policy is None:
                raise IncompatibleRegistrationError(
                    f"policy is unavailable: {reference.component_id}@{reference.version}"
                )
            if policy.lifecycle not in required_dependency_statuses:
                raise IncompatibleRegistrationError(
                    f"policy is not usable: {reference.component_id}@{reference.version}"
                )

        tool_ids = {tool.tool_id for tool in tools_by_reference}
        for capability in capability_values:
            if capability.allowed_tool_ids and not tool_ids.intersection(
                capability.allowed_tool_ids
            ):
                raise IncompatibleRegistrationError(
                    f"agent exposes no tool for capability: {capability.capability_id}"
                )

    def _transition_by_key(
        self,
        agent_id: str,
        version: str,
        target: AgentLifecycleStatus,
        operation: str,
        *,
        allowed_from: set[AgentLifecycleStatus],
    ) -> AgentDefinition:
        with self._lock:
            current = self._require(agent_id, version)
            if current.lifecycle not in allowed_from:
                raise RegistryLifecycleError(
                    f"cannot move agent from {current.lifecycle.value} to {target.value}"
                )
            return self._transition(current, target, operation)

    def _transition(
        self,
        current: AgentDefinition,
        target: AgentLifecycleStatus,
        operation: str,
    ) -> AgentDefinition:
        updated = current.model_copy(update={"lifecycle": target}, deep=True)
        self._agents[(current.agent_id, current.version)] = updated
        self._changed(
            operation=operation,
            component_kind="agent",
            component_id=current.agent_id,
            version=current.version,
            from_status=current.lifecycle,
            to_status=target,
        )
        return deepcopy(updated)

    def _changed(
        self,
        *,
        operation: str,
        component_kind: str,
        component_id: str,
        version: str,
        from_status: AgentLifecycleStatus | None = None,
        to_status: AgentLifecycleStatus | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self._revision += 1
        event = RegistryAuditEvent(
            event_id=self._id("registry_event"),
            registry_id=self.registry_id,
            operation=operation,
            component_kind=component_kind,
            component_id=component_id,
            version=version,
            from_status=from_status,
            to_status=to_status,
            occurred_at=self._clock(),
            metadata=metadata or {},
        )
        self._events.append(event)
        self.audit_sink.append(event)

    def _record_snapshot(self, snapshot: RegistrySnapshot) -> None:
        with self._lock:
            event = RegistryAuditEvent(
                event_id=self._id("registry_event"),
                registry_id=self.registry_id,
                operation="snapshot_created",
                component_kind="snapshot",
                component_id=snapshot.snapshot_id,
                version=str(snapshot.revision),
                occurred_at=self._clock(),
                metadata={
                    "agent_count": str(len(snapshot.agents)),
                    "capability_count": str(len(snapshot.capabilities)),
                    "tool_count": str(len(snapshot.tools)),
                    "policy_count": str(len(snapshot.policies)),
                },
            )
            self._events.append(event)
            self.audit_sink.append(event)


def _component_sort_key(
    component: AgentDefinition | CapabilityDefinition,
) -> tuple[str, tuple[int, Version | str]]:
    identifier = (
        component.agent_id if isinstance(component, AgentDefinition) else component.capability_id
    )
    version = component.version
    try:
        parsed: Version | str = Version(version)
        group = 0
    except InvalidVersion:
        parsed = version
        group = 1
    return identifier, (group, parsed)


def _risk_exceeds(actual: RiskLevel, maximum: RiskLevel) -> bool:
    order = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }
    return order[RiskLevel(actual)] > order[RiskLevel(maximum)]


def _capability_dependencies(
    capabilities: Sequence[CapabilityDefinition],
    tools: Sequence[ToolDescriptor],
) -> list[RegistryDependency]:
    return [
        RegistryDependency(
            source_kind="capability",
            source_id=capability.capability_id,
            source_version=capability.version,
            target_kind="tool",
            target_id=tool.tool_id,
            target_version=tool.version,
            relation="allows_tool",
        )
        for capability in capabilities
        for tool in tools
        if tool.tool_id in capability.allowed_tool_ids
    ]


def _agent_dependencies(
    agents: Sequence[AgentDefinition],
    capabilities: Sequence[CapabilityDefinition],
    tools: Sequence[ToolDescriptor],
) -> list[RegistryDependency]:
    dependencies: list[RegistryDependency] = []
    capability_keys = {
        (capability.capability_id, capability.version) for capability in capabilities
    }
    tool_keys = {(tool.tool_id, tool.version) for tool in tools}
    for agent in agents:
        for reference in agent.capabilities:
            if (reference.component_id, reference.version) in capability_keys:
                dependencies.append(
                    RegistryDependency(
                        source_kind="agent",
                        source_id=agent.agent_id,
                        source_version=agent.version,
                        target_kind="capability",
                        target_id=reference.component_id,
                        target_version=reference.version,
                        relation="uses_capability",
                    )
                )
        for reference in agent.allowed_tools:
            if (reference.component_id, reference.version) in tool_keys:
                dependencies.append(
                    RegistryDependency(
                        source_kind="agent",
                        source_id=agent.agent_id,
                        source_version=agent.version,
                        target_kind="tool",
                        target_id=reference.component_id,
                        target_version=reference.version,
                        relation="allows_tool",
                    )
                )
        for reference in agent.policies:
            dependencies.append(
                RegistryDependency(
                    source_kind="agent",
                    source_id=agent.agent_id,
                    source_version=agent.version,
                    target_kind="policy",
                    target_id=reference.component_id,
                    target_version=reference.version,
                    relation="uses_policy",
                )
            )
    dependencies.extend(_capability_dependencies(capabilities, tools))
    for tool in tools:
        for dependency in tool.dependencies:
            for target in tools:
                if target.tool_id == dependency:
                    dependencies.append(
                        RegistryDependency(
                            source_kind="tool",
                            source_id=tool.tool_id,
                            source_version=tool.version,
                            target_kind="tool",
                            target_id=target.tool_id,
                            target_version=target.version,
                            relation="depends_on",
                        )
                    )
    return sorted(
        dependencies,
        key=lambda item: (
            item.source_kind,
            item.source_id,
            item.source_version,
            item.target_kind,
            item.target_id,
            item.target_version,
            item.relation,
        ),
    )


__all__ = [
    "AgentRegistry",
    "CapabilityRegistry",
    "DuplicateRegistrationError",
    "IncompatibleRegistrationError",
    "ListRegistryAuditSink",
    "RegistryAuditEvent",
    "RegistryAuditSink",
    "RegistryError",
    "RegistryLifecycleError",
    "StaleRegistrationError",
]
