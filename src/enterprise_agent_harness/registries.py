"""Versioned agent, prompt, skill, and dependency registries."""

from __future__ import annotations

import hashlib
import json
from builtins import list as BuiltinList
from collections.abc import Callable, Iterable, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from packaging.version import InvalidVersion, Version

from .contracts import (
    AgentDefinition,
    AgentLifecycleStatus,
    ComponentReference,
    ComponentType,
    PolicyDefinition,
    PromptDefinition,
    RegistryDependency,
    RegistrySnapshot,
    RiskLevel,
    SkillDefinition,
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


class PromptRegistry:
    """Store and discover immutable, versioned prompt definitions."""

    registry_id = "prompts"

    def __init__(
        self,
        prompts: Iterable[PromptDefinition] = (),
        *,
        audit_sink: RegistryAuditSink | None = None,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.audit_sink = audit_sink or ListRegistryAuditSink()
        self._id = id_factory or (lambda prefix: f"{prefix}_{uuid4().hex[:10]}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._prompts: dict[tuple[str, str], PromptDefinition] = {}
        self._events: list[RegistryAuditEvent] = []
        self._revision = 0
        self._lock = RLock()
        for prompt in prompts:
            self.register(prompt)

    @property
    def revision(self) -> int:
        """Return the monotonic registry revision."""

        with self._lock:
            return self._revision

    @property
    def events(self) -> list[RegistryAuditEvent]:
        """Return prompt registry events as safe copies."""

        with self._lock:
            return deepcopy(self._events)

    def register(
        self,
        prompt: PromptDefinition,
        *,
        replace_existing: bool = False,
    ) -> PromptDefinition:
        """Register one exact prompt version."""

        candidate = prompt.model_copy(deep=True)
        key = (candidate.prompt_id, candidate.version)
        with self._lock:
            if key in self._prompts:
                if replace_existing:
                    raise StaleRegistrationError("registered prompt versions are immutable")
                raise DuplicateRegistrationError("prompt identity and version pairs must be unique")
            self._prompts[key] = candidate
            self._changed(
                operation="registered",
                component_kind=ComponentType.PROMPT.value,
                component_id=candidate.prompt_id,
                version=candidate.version,
                to_status=candidate.lifecycle,
            )
            return deepcopy(candidate)

    def get(self, prompt_id: str, version: str) -> PromptDefinition:
        """Return an exact prompt version, including inactive records."""

        with self._lock:
            try:
                return deepcopy(self._prompts[(prompt_id, version)])
            except KeyError as exc:
                raise RegistryError(f"unknown prompt: {prompt_id}@{version}") from exc

    def resolve(self, prompt_id: str, version: str | None = None) -> PromptDefinition:
        """Resolve one active prompt with explicit version selection."""

        with self._lock:
            if version is not None:
                prompt = self._prompts.get((prompt_id, version))
                if prompt is None:
                    raise RegistryError(f"unknown prompt: {prompt_id}@{version}")
                if prompt.lifecycle != AgentLifecycleStatus.ACTIVE:
                    raise RegistryError(f"prompt is not active: {prompt_id}@{version}")
                return deepcopy(prompt)
            active = [
                prompt
                for (candidate_id, _), prompt in self._prompts.items()
                if candidate_id == prompt_id and prompt.lifecycle == AgentLifecycleStatus.ACTIVE
            ]
            if not active:
                raise RegistryError(f"unknown active prompt: {prompt_id}")
            if len(active) > 1:
                raise RegistryError(f"prompt version is required: {prompt_id}")
            return deepcopy(active[0])

    def list(self, *, include_inactive: bool = False) -> list[PromptDefinition]:
        """List prompts in deterministic identity and version order."""

        with self._lock:
            values = list(self._prompts.values())
        if not include_inactive:
            values = [
                prompt for prompt in values if prompt.lifecycle == AgentLifecycleStatus.ACTIVE
            ]
        return deepcopy(sorted(values, key=_component_sort_key))

    def versions(self, prompt_id: str, *, include_inactive: bool = True) -> tuple[str, ...]:
        """Return registered versions for one prompt identity."""

        return tuple(
            prompt.version
            for prompt in self.list(include_inactive=include_inactive)
            if prompt.prompt_id == prompt_id
        )

    def search(
        self,
        query: str | None = None,
        *,
        purpose: str | None = None,
        owner_id: str | None = None,
        include_inactive: bool = False,
    ) -> BuiltinList[PromptDefinition]:
        """Find prompts by stable metadata without granting runtime authority."""

        query_text = query.lower() if query is not None else None
        purpose_text = purpose.lower() if purpose is not None else None
        values = self.list(include_inactive=include_inactive)
        return [
            prompt
            for prompt in values
            if (
                query_text is None
                or query_text in prompt.prompt_id.lower()
                or query_text in prompt.purpose.lower()
                or query_text in prompt.instructions.lower()
                or any(query_text in str(value).lower() for value in prompt.metadata.values())
            )
            and (purpose_text is None or purpose_text in prompt.purpose.lower())
            and (owner_id is None or prompt.owner_id == owner_id)
        ]

    def validate(self, prompt_id: str, version: str) -> PromptDefinition:
        """Move a draft prompt to validated after contract validation."""

        with self._lock:
            current = self._require(prompt_id, version)
            if current.lifecycle == AgentLifecycleStatus.DRAFT:
                return self._transition(current, AgentLifecycleStatus.VALIDATED, "validated")
            if current.lifecycle in {
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
            }:
                return deepcopy(current)
            raise RegistryLifecycleError(
                f"cannot validate prompt in state {current.lifecycle.value}"
            )

    def activate(self, prompt_id: str, version: str) -> PromptDefinition:
        """Validate and activate one prompt version."""

        with self._lock:
            current = self._require(prompt_id, version)
            if current.lifecycle == AgentLifecycleStatus.DRAFT:
                current = self._transition(current, AgentLifecycleStatus.VALIDATED, "validated")
            if current.lifecycle == AgentLifecycleStatus.VALIDATED:
                return self._transition(current, AgentLifecycleStatus.ACTIVE, "activated")
            if current.lifecycle == AgentLifecycleStatus.SUSPENDED:
                return self._transition(current, AgentLifecycleStatus.ACTIVE, "reactivated")
            if current.lifecycle == AgentLifecycleStatus.ACTIVE:
                return deepcopy(current)
            raise RegistryLifecycleError(
                f"cannot activate prompt in state {current.lifecycle.value}"
            )

    def suspend(self, prompt_id: str, version: str) -> PromptDefinition:
        """Suspend new use of one active prompt version."""

        return self._transition_by_key(
            prompt_id,
            version,
            AgentLifecycleStatus.SUSPENDED,
            "suspended",
            allowed_from={AgentLifecycleStatus.ACTIVE},
        )

    def deprecate(self, prompt_id: str, version: str) -> PromptDefinition:
        """Deprecate one prompt version."""

        return self._transition_by_key(
            prompt_id,
            version,
            AgentLifecycleStatus.DEPRECATED,
            "deprecated",
            allowed_from={
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
                AgentLifecycleStatus.SUSPENDED,
            },
        )

    def retire(self, prompt_id: str, version: str) -> PromptDefinition:
        """Retire one prompt version permanently."""

        return self._transition_by_key(
            prompt_id,
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
        """Return a deterministic snapshot of prompt records."""

        prompts = self.list(include_inactive=include_inactive)
        snapshot = RegistrySnapshot(
            snapshot_id=_stable_snapshot_id(
                "prompt_registry",
                {"prompts": [prompt.model_dump(mode="json") for prompt in prompts]},
            ),
            revision=self.revision,
            prompt_registry_revision=self.revision,
            prompts=prompts,
        )
        self._record_snapshot(snapshot)
        return snapshot

    def _require(self, prompt_id: str, version: str) -> PromptDefinition:
        try:
            return self._prompts[(prompt_id, version)]
        except KeyError as exc:
            raise RegistryError(f"unknown prompt: {prompt_id}@{version}") from exc

    def _transition_by_key(
        self,
        prompt_id: str,
        version: str,
        target: AgentLifecycleStatus,
        operation: str,
        *,
        allowed_from: set[AgentLifecycleStatus],
    ) -> PromptDefinition:
        with self._lock:
            current = self._require(prompt_id, version)
            if current.lifecycle not in allowed_from:
                raise RegistryLifecycleError(
                    f"cannot move prompt from {current.lifecycle.value} to {target.value}"
                )
            return self._transition(current, target, operation)

    def _transition(
        self,
        current: PromptDefinition,
        target: AgentLifecycleStatus,
        operation: str,
    ) -> PromptDefinition:
        updated = current.model_copy(update={"lifecycle": target}, deep=True)
        self._prompts[(current.prompt_id, current.version)] = updated
        self._changed(
            operation=operation,
            component_kind=ComponentType.PROMPT.value,
            component_id=current.prompt_id,
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
                metadata={"prompt_count": str(len(snapshot.prompts))},
            )
            self._events.append(event)
            self.audit_sink.append(event)


class SkillRegistry:
    """Store and discover immutable, versioned skill definitions."""

    registry_id = "skills"

    def __init__(
        self,
        skills: Iterable[SkillDefinition] = (),
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
        self._skills: dict[tuple[str, str], SkillDefinition] = {}
        self._events: list[RegistryAuditEvent] = []
        self._revision = 0
        self._lock = RLock()
        for skill in skills:
            self.register(skill)

    @property
    def revision(self) -> int:
        """Return the monotonic registry revision."""

        with self._lock:
            return self._revision

    @property
    def events(self) -> list[RegistryAuditEvent]:
        """Return skill registry events as safe copies."""

        with self._lock:
            return deepcopy(self._events)

    def register(
        self,
        skill: SkillDefinition,
        *,
        replace_existing: bool = False,
    ) -> SkillDefinition:
        """Register one exact skill version."""

        candidate = skill.model_copy(deep=True)
        key = (candidate.skill_id, candidate.version)
        with self._lock:
            if key in self._skills:
                if replace_existing:
                    raise StaleRegistrationError("registered skill versions are immutable")
                raise DuplicateRegistrationError("skill identity and version pairs must be unique")
            if candidate.lifecycle in {
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
            }:
                allowed = (
                    {AgentLifecycleStatus.ACTIVE}
                    if candidate.lifecycle == AgentLifecycleStatus.ACTIVE
                    else {AgentLifecycleStatus.VALIDATED, AgentLifecycleStatus.ACTIVE}
                )
                self._validate_skill(candidate, required_dependency_statuses=allowed)
            self._skills[key] = candidate
            self._changed(
                operation="registered",
                component_kind=ComponentType.SKILL.value,
                component_id=candidate.skill_id,
                version=candidate.version,
                to_status=candidate.lifecycle,
            )
            return deepcopy(candidate)

    def get(self, skill_id: str, version: str) -> SkillDefinition:
        """Return an exact skill version, including inactive records."""

        with self._lock:
            try:
                return deepcopy(self._skills[(skill_id, version)])
            except KeyError as exc:
                raise RegistryError(f"unknown skill: {skill_id}@{version}") from exc

    def resolve(self, skill_id: str, version: str | None = None) -> SkillDefinition:
        """Resolve one active skill with explicit version selection."""

        with self._lock:
            if version is not None:
                skill = self._skills.get((skill_id, version))
                if skill is None:
                    raise RegistryError(f"unknown skill: {skill_id}@{version}")
                if skill.lifecycle != AgentLifecycleStatus.ACTIVE:
                    raise RegistryError(f"skill is not active: {skill_id}@{version}")
                self._validate_skill(
                    skill,
                    required_dependency_statuses={AgentLifecycleStatus.ACTIVE},
                )
                return deepcopy(skill)
            active = [
                skill
                for (candidate_id, _), skill in self._skills.items()
                if candidate_id == skill_id and skill.lifecycle == AgentLifecycleStatus.ACTIVE
            ]
            if not active:
                raise RegistryError(f"unknown active skill: {skill_id}")
            if len(active) > 1:
                raise RegistryError(f"skill version is required: {skill_id}")
            self._validate_skill(
                active[0],
                required_dependency_statuses={AgentLifecycleStatus.ACTIVE},
            )
            return deepcopy(active[0])

    def list(self, *, include_inactive: bool = False) -> list[SkillDefinition]:
        """List skills in deterministic identity and version order."""

        with self._lock:
            values = list(self._skills.values())
        if not include_inactive:
            values = [skill for skill in values if skill.lifecycle == AgentLifecycleStatus.ACTIVE]
        return deepcopy(sorted(values, key=_component_sort_key))

    def versions(self, skill_id: str, *, include_inactive: bool = True) -> tuple[str, ...]:
        """Return registered versions for one skill identity."""

        return tuple(
            skill.version
            for skill in self.list(include_inactive=include_inactive)
            if skill.skill_id == skill_id
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
    ) -> BuiltinList[SkillDefinition]:
        """Find reusable skills by stable metadata."""

        query_text = query.lower() if query is not None else None
        intent_text = intent.lower() if intent is not None else None
        language_text = language.lower() if language is not None else None
        values = self.list(include_inactive=include_inactive)
        return [
            skill
            for skill in values
            if (
                query_text is None
                or query_text in skill.skill_id.lower()
                or query_text in skill.name.lower()
                or query_text in skill.description.lower()
                or any(query_text in value.lower() for value in skill.supported_operations)
                or any(query_text in value.lower() for value in skill.supported_intents)
                or any(query_text in value.lower() for value in skill.tags)
            )
            and (
                intent_text is None
                or any(intent_text == value.lower() for value in skill.supported_intents)
                or any(intent_text == value.lower() for value in skill.supported_operations)
            )
            and (tool_id is None or any(ref.component_id == tool_id for ref in skill.tool_refs))
            and (
                language_text is None
                or any(language_text == value.lower() for value in skill.supported_languages)
            )
            and (risk_level is None or skill.risk_level == RiskLevel(risk_level))
        ]

    def tools_for_skill(
        self,
        skill_id: str,
        version: str,
        *,
        include_inactive: bool = False,
    ) -> BuiltinList[ToolDescriptor]:
        """Return exact tool metadata linked to one skill for discovery."""

        skill = self.get(skill_id, version)
        if self.tools is None:
            if skill.required_tool_refs:
                raise RegistryError(f"skill has no tool registry: {skill_id}@{version}")
            return []
        descriptors: list[ToolDescriptor] = []
        for reference in skill.tool_refs:
            try:
                tool = self.tools.get(
                    reference.component_id,
                    reference.version,
                    include_inactive=include_inactive,
                )
            except ToolInvocationError:
                if reference in skill.optional_tool_refs:
                    continue
                raise RegistryError(
                    f"skill tool is unavailable: {reference.component_id}@{reference.version}"
                ) from None
            descriptors.append(tool.descriptor)
        return sorted(descriptors, key=lambda item: (item.tool_id, item.version))

    def validate(self, skill_id: str, version: str) -> SkillDefinition:
        """Validate dependencies and move a draft skill to validated."""

        with self._lock:
            current = self._require(skill_id, version)
            self._validate_skill(
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
                f"cannot validate skill in state {current.lifecycle.value}"
            )

    def activate(self, skill_id: str, version: str) -> SkillDefinition:
        """Validate and activate one skill version."""

        with self._lock:
            current = self._require(skill_id, version)
            self._validate_skill(
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
                f"cannot activate skill in state {current.lifecycle.value}"
            )

    def suspend(self, skill_id: str, version: str) -> SkillDefinition:
        """Suspend new use of one active skill version."""

        return self._transition_by_key(
            skill_id,
            version,
            AgentLifecycleStatus.SUSPENDED,
            "suspended",
            allowed_from={AgentLifecycleStatus.ACTIVE},
        )

    def deprecate(self, skill_id: str, version: str) -> SkillDefinition:
        """Deprecate one skill version."""

        return self._transition_by_key(
            skill_id,
            version,
            AgentLifecycleStatus.DEPRECATED,
            "deprecated",
            allowed_from={
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
                AgentLifecycleStatus.SUSPENDED,
            },
        )

    def retire(self, skill_id: str, version: str) -> SkillDefinition:
        """Retire one skill version permanently."""

        return self._transition_by_key(
            skill_id,
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
        """Return a deterministic snapshot of skills and linked tools."""

        skills = self.list(include_inactive=include_inactive)
        tools = (
            self.tools.descriptors(include_inactive=include_inactive)
            if self.tools is not None
            else []
        )
        dependencies = sorted(
            [*_skill_dependencies(skills, tools), *_tool_dependencies(tools)],
            key=_dependency_sort_key,
        )
        snapshot = RegistrySnapshot(
            snapshot_id=_stable_snapshot_id(
                "skill_registry",
                {
                    "skills": [skill.model_dump(mode="json") for skill in skills],
                    "tools": [tool.model_dump(mode="json") for tool in tools],
                    "dependencies": [
                        dependency.model_dump(mode="json") for dependency in dependencies
                    ],
                },
            ),
            revision=self.revision + (self.tools.revision if self.tools is not None else 0),
            skill_registry_revision=self.revision,
            tool_registry_revision=self.tools.revision if self.tools is not None else 0,
            skills=skills,
            tools=tools,
            dependencies=dependencies,
        )
        self._record_snapshot(snapshot)
        return snapshot

    def _require(self, skill_id: str, version: str) -> SkillDefinition:
        try:
            return self._skills[(skill_id, version)]
        except KeyError as exc:
            raise RegistryError(f"unknown skill: {skill_id}@{version}") from exc

    def _validate_skill(
        self,
        skill: SkillDefinition,
        *,
        required_dependency_statuses: set[AgentLifecycleStatus],
    ) -> None:
        if self.tools is None:
            if skill.required_tool_refs:
                raise IncompatibleRegistrationError(
                    f"skill {skill.skill_id}@{skill.version} has no tool registry"
                )
            return
        for reference in skill.required_tool_refs:
            tool = _get_tool_for_reference(self.tools, reference)
            if tool.lifecycle not in required_dependency_statuses:
                raise IncompatibleRegistrationError(
                    f"skill tool is not usable: {reference.component_id}@{reference.version}"
                )
            if _risk_exceeds(tool.risk_level, skill.risk_level):
                raise IncompatibleRegistrationError(
                    f"skill risk is below tool risk: {reference.component_id}@{reference.version}"
                )
            _validate_tool_dependencies(
                self.tools,
                tool,
                required_dependency_statuses=required_dependency_statuses,
                maximum_risk=skill.risk_level,
            )
        for reference in skill.optional_tool_refs:
            try:
                tool = _get_tool_for_reference(self.tools, reference)
            except IncompatibleRegistrationError:
                continue
            if tool.lifecycle not in required_dependency_statuses:
                continue
            if _risk_exceeds(tool.risk_level, skill.risk_level):
                raise IncompatibleRegistrationError(
                    f"skill risk is below optional tool risk: {reference.component_id}@{reference.version}"
                )
            _validate_tool_dependencies(
                self.tools,
                tool,
                required_dependency_statuses=required_dependency_statuses,
                maximum_risk=skill.risk_level,
            )

    def _transition_by_key(
        self,
        skill_id: str,
        version: str,
        target: AgentLifecycleStatus,
        operation: str,
        *,
        allowed_from: set[AgentLifecycleStatus],
    ) -> SkillDefinition:
        with self._lock:
            current = self._require(skill_id, version)
            if current.lifecycle not in allowed_from:
                raise RegistryLifecycleError(
                    f"cannot move skill from {current.lifecycle.value} to {target.value}"
                )
            return self._transition(current, target, operation)

    def _transition(
        self,
        current: SkillDefinition,
        target: AgentLifecycleStatus,
        operation: str,
    ) -> SkillDefinition:
        updated = current.model_copy(update={"lifecycle": target}, deep=True)
        self._skills[(current.skill_id, current.version)] = updated
        self._changed(
            operation=operation,
            component_kind=ComponentType.SKILL.value,
            component_id=current.skill_id,
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
                    "skill_count": str(len(snapshot.skills)),
                    "tool_count": str(len(snapshot.tools)),
                },
            )
            self._events.append(event)
            self.audit_sink.append(event)


class AgentRegistry:
    """Store, validate, and discover exact agent definitions."""

    registry_id = "agents"

    def __init__(
        self,
        *,
        skills: SkillRegistry | None = None,
        prompts: PromptRegistry | None = None,
        tools: ToolRegistry | None = None,
        policies: Sequence[PolicyDefinition] = (),
        audit_sink: RegistryAuditSink | None = None,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if tools is None and skills is not None and skills.tools is not None:
            self.tools = skills.tools
        else:
            self.tools = tools or ToolRegistry()
        if skills is None:
            self.skills = SkillRegistry(tools=self.tools)
        else:
            self.skills = skills
            if skills.tools is None:
                skills.tools = self.tools
            elif skills.tools is not self.tools:
                raise ValueError("agent and skill registries must share one tool registry")
        self.prompts = prompts or PromptRegistry()
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
        """Return agent registry events as safe copies."""

        with self._lock:
            return deepcopy(self._events)

    @property
    def policies(self) -> list[PolicyDefinition]:
        """Return the configured policy catalog as safe copies."""

        with self._lock:
            return deepcopy(sorted(self._policies.values(), key=_component_sort_key))

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
                    else {AgentLifecycleStatus.VALIDATED, AgentLifecycleStatus.ACTIVE}
                )
                self._validate_agent(
                    candidate,
                    required_dependency_statuses=required_statuses,
                )
            self._agents[key] = candidate
            self._changed(
                operation="registered",
                component_kind=ComponentType.AGENT.value,
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
        """Return registered versions for one agent identity."""

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
        """Check an unregistered agent against exact registry dependencies."""

        candidate = agent.model_copy(deep=True)
        required_statuses = (
            {AgentLifecycleStatus.ACTIVE}
            if require_active_dependencies
            else {AgentLifecycleStatus.VALIDATED, AgentLifecycleStatus.ACTIVE}
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
    ) -> BuiltinList[AgentDefinition]:
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

    def skills_for_agent(
        self,
        agent_id: str,
        version: str,
        *,
        include_inactive: bool = False,
    ) -> BuiltinList[SkillDefinition]:
        """Return exact skills referenced by one agent for discovery."""

        agent = self.get(agent_id, version)
        skills: list[SkillDefinition] = []
        for reference in agent.skill_refs:
            skill = self.skills.get(reference.component_id, reference.version)
            if include_inactive or skill.lifecycle == AgentLifecycleStatus.ACTIVE:
                skills.append(skill)
        return sorted(skills, key=_component_sort_key)

    def tools_for_skill(
        self,
        skill_id: str,
        version: str,
        *,
        include_inactive: bool = False,
    ) -> BuiltinList[ToolDescriptor]:
        """Return exact tools linked to one skill for discovery."""

        return self.skills.tools_for_skill(
            skill_id,
            version,
            include_inactive=include_inactive,
        )

    def agents_using_skill(
        self,
        skill_id: str,
        version: str,
        *,
        include_inactive: bool = False,
    ) -> BuiltinList[AgentDefinition]:
        """Return agents that reference one exact skill version."""

        reference = (skill_id, version)
        return [
            agent
            for agent in self.list(include_inactive=include_inactive)
            if any((item.component_id, item.version) == reference for item in agent.skill_refs)
        ]

    def agents_using_tool(
        self,
        tool_id: str,
        version: str,
        *,
        include_inactive: bool = False,
    ) -> BuiltinList[AgentDefinition]:
        """Return agents that use one exact tool directly or through a skill."""

        reference = (tool_id, version)
        skills = self.skills.list(include_inactive=True)
        skill_tools = {
            (skill.skill_id, skill.version): {
                (item.component_id, item.version) for item in skill.tool_refs
            }
            for skill in skills
        }
        agents: list[AgentDefinition] = []
        for agent in self.list(include_inactive=include_inactive):
            direct = any((item.component_id, item.version) == reference for item in agent.tool_refs)
            linked = any(
                reference in skill_tools.get((skill.component_id, skill.version), set())
                for skill in agent.skill_refs
            )
            if direct or linked:
                agents.append(agent)
        return agents

    def agents_using_prompt(
        self,
        prompt_id: str,
        version: str,
        *,
        include_inactive: bool = False,
    ) -> BuiltinList[AgentDefinition]:
        """Return agents that reference one exact prompt version."""

        reference = (prompt_id, version)
        return [
            agent
            for agent in self.list(include_inactive=include_inactive)
            if (agent.prompt_ref.component_id, agent.prompt_ref.version) == reference
        ]

    def validate(self, agent_id: str, version: str) -> AgentDefinition:
        """Validate exact dependencies and move a draft agent to validated."""

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
        """Deprecate one agent version."""

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

    def snapshot(
        self,
        *,
        include_inactive: bool = False,
        agent_overrides: Sequence[AgentDefinition] = (),
    ) -> RegistrySnapshot:
        """Return the complete exact artifact graph in deterministic order."""

        agents = self.list(include_inactive=include_inactive)
        by_key = {(agent.agent_id, agent.version): agent for agent in agents}
        for override in agent_overrides:
            by_key[(override.agent_id, override.version)] = override.model_copy(deep=True)
        agents = sorted(by_key.values(), key=_component_sort_key)
        prompts = self.prompts.list(include_inactive=include_inactive)
        skills = self.skills.list(include_inactive=include_inactive)
        tools = self.tools.descriptors(include_inactive=include_inactive)
        policies = [
            policy
            for policy in self.policies
            if include_inactive or policy.lifecycle == AgentLifecycleStatus.ACTIVE
        ]
        dependencies = _agent_dependencies(agents, prompts, skills, tools, policies)
        prompt_revision = self.prompts.revision
        skill_revision = self.skills.revision
        tool_revision = self.tools.revision
        snapshot = RegistrySnapshot(
            snapshot_id=_stable_snapshot_id(
                "agent_registry",
                {
                    "agents": [agent.model_dump(mode="json") for agent in agents],
                    "prompts": [prompt.model_dump(mode="json") for prompt in prompts],
                    "skills": [skill.model_dump(mode="json") for skill in skills],
                    "tools": [tool.model_dump(mode="json") for tool in tools],
                    "policies": [policy.model_dump(mode="json") for policy in policies],
                    "dependencies": [
                        dependency.model_dump(mode="json") for dependency in dependencies
                    ],
                },
            ),
            revision=self.revision + prompt_revision + skill_revision + tool_revision,
            agent_registry_revision=self.revision,
            prompt_registry_revision=prompt_revision,
            skill_registry_revision=skill_revision,
            tool_registry_revision=tool_revision,
            agents=agents,
            prompts=prompts,
            skills=skills,
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
        prompt = self.prompts.get(agent.prompt_ref.component_id, agent.prompt_ref.version)
        if prompt.lifecycle not in required_dependency_statuses:
            raise IncompatibleRegistrationError(
                f"prompt is not usable: {prompt.prompt_id}@{prompt.version}"
            )

        skills: list[SkillDefinition] = []
        for reference in agent.skill_refs:
            try:
                skill = self.skills.get(reference.component_id, reference.version)
            except RegistryError as exc:
                raise IncompatibleRegistrationError(str(exc)) from exc
            if skill.lifecycle not in required_dependency_statuses:
                raise IncompatibleRegistrationError(
                    f"skill is not usable: {skill.skill_id}@{skill.version}"
                )
            if _risk_exceeds(skill.risk_level, agent.risk_level):
                raise IncompatibleRegistrationError(
                    f"skill risk exceeds agent risk: {skill.skill_id}@{skill.version}"
                )
            self.skills._validate_skill(
                skill,
                required_dependency_statuses=required_dependency_statuses,
            )
            skills.append(skill)

        tools_by_key: dict[tuple[str, str], ToolDefinition] = {}
        for reference in agent.tool_refs:
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
            tools_by_key[(tool.tool_id, tool.version)] = tool
            _validate_tool_dependencies(
                self.tools,
                tool,
                required_dependency_statuses=required_dependency_statuses,
                maximum_risk=agent.risk_level,
            )

        for reference in agent.policy_refs:
            policy = self._policies.get((reference.component_id, reference.version))
            if policy is None:
                raise IncompatibleRegistrationError(
                    f"policy is unavailable: {reference.component_id}@{reference.version}"
                )
            if policy.lifecycle not in required_dependency_statuses:
                raise IncompatibleRegistrationError(
                    f"policy is not usable: {reference.component_id}@{reference.version}"
                )

        direct_tools = set(tools_by_key)
        for skill in skills:
            missing_required = [
                reference
                for reference in skill.required_tool_refs
                if (reference.component_id, reference.version) not in direct_tools
            ]
            if missing_required:
                missing = ", ".join(
                    f"{reference.component_id}@{reference.version}"
                    for reference in missing_required
                )
                raise IncompatibleRegistrationError(
                    f"agent tool_refs do not include required skill tools: {missing}"
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
            component_kind=ComponentType.AGENT.value,
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
                    "prompt_count": str(len(snapshot.prompts)),
                    "skill_count": str(len(snapshot.skills)),
                    "tool_count": str(len(snapshot.tools)),
                    "policy_count": str(len(snapshot.policies)),
                },
            )
            self._events.append(event)
            self.audit_sink.append(event)


def _component_sort_key(
    component: AgentDefinition
    | PromptDefinition
    | SkillDefinition
    | ToolDescriptor
    | PolicyDefinition,
) -> tuple[str, tuple[int, Version | str]]:
    if isinstance(component, AgentDefinition):
        identifier = component.agent_id
    elif isinstance(component, PromptDefinition):
        identifier = component.prompt_id
    elif isinstance(component, SkillDefinition):
        identifier = component.skill_id
    elif isinstance(component, ToolDescriptor):
        identifier = component.tool_id
    else:
        identifier = component.policy_id
    version = component.version
    try:
        parsed: Version | str = Version(version)
        group = 0
    except InvalidVersion:
        parsed = version
        group = 1
    return identifier, (group, parsed)


def _stable_snapshot_id(prefix: str, payload: dict[str, object]) -> str:
    """Return a repeatable snapshot identity for one exact graph view."""

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _risk_exceeds(actual: RiskLevel, maximum: RiskLevel) -> bool:
    order = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }
    return order[RiskLevel(actual)] > order[RiskLevel(maximum)]


def _get_tool_for_reference(
    tools: ToolRegistry,
    reference: ComponentReference,
) -> ToolDefinition:
    try:
        return tools.get(reference.component_id, reference.version, include_inactive=True)
    except ToolInvocationError as exc:
        raise IncompatibleRegistrationError(
            f"skill references unavailable tool: {reference.component_id}@{reference.version}"
        ) from exc


def _validate_tool_dependencies(
    tools: ToolRegistry,
    root: ToolDefinition,
    *,
    required_dependency_statuses: set[AgentLifecycleStatus],
    maximum_risk: RiskLevel | None = None,
) -> None:
    """Validate the exact transitive tool graph without following cycles forever."""

    visiting: set[tuple[str, str]] = set()
    visited: set[tuple[str, str]] = set()

    def visit(tool: ToolDefinition) -> None:
        key = (tool.tool_id, tool.version)
        if key in visiting:
            raise IncompatibleRegistrationError(f"tool dependency cycle detected: {tool.tool_id}")
        if key in visited:
            return
        visiting.add(key)
        for reference in tool.dependencies:
            dependency = _get_tool_for_reference(tools, reference)
            if dependency.lifecycle not in required_dependency_statuses:
                raise IncompatibleRegistrationError(
                    f"tool dependency is not usable: {reference.component_id}@{reference.version}"
                )
            if maximum_risk is not None and _risk_exceeds(
                dependency.risk_level,
                maximum_risk,
            ):
                raise IncompatibleRegistrationError(
                    f"tool dependency risk exceeds limit: {reference.component_id}@{reference.version}"
                )
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    visit(root)


def _skill_dependencies(
    skills: Sequence[SkillDefinition],
    tools: Sequence[ToolDescriptor],
) -> list[RegistryDependency]:
    del tools
    dependencies: list[RegistryDependency] = []
    for skill in skills:
        for reference in skill.required_tool_refs:
            dependencies.append(
                RegistryDependency(
                    source_kind=ComponentType.SKILL.value,
                    source_id=skill.skill_id,
                    source_version=skill.version,
                    target_kind=ComponentType.TOOL.value,
                    target_id=reference.component_id,
                    target_version=reference.version,
                    relation="requires_tool",
                )
            )
        for reference in skill.optional_tool_refs:
            dependencies.append(
                RegistryDependency(
                    source_kind=ComponentType.SKILL.value,
                    source_id=skill.skill_id,
                    source_version=skill.version,
                    target_kind=ComponentType.TOOL.value,
                    target_id=reference.component_id,
                    target_version=reference.version,
                    relation="optional_tool",
                )
            )
    return dependencies


def _agent_dependencies(
    agents: Sequence[AgentDefinition],
    prompts: Sequence[PromptDefinition],
    skills: Sequence[SkillDefinition],
    tools: Sequence[ToolDescriptor],
    policies: Sequence[PolicyDefinition],
) -> list[RegistryDependency]:
    del prompts, policies
    dependencies: list[RegistryDependency] = []
    for agent in agents:
        dependencies.append(
            RegistryDependency(
                source_kind=ComponentType.AGENT.value,
                source_id=agent.agent_id,
                source_version=agent.version,
                target_kind=ComponentType.PROMPT.value,
                target_id=agent.prompt_ref.component_id,
                target_version=agent.prompt_ref.version,
                relation="uses_prompt",
            )
        )
        for reference in agent.skill_refs:
            dependencies.append(
                RegistryDependency(
                    source_kind=ComponentType.AGENT.value,
                    source_id=agent.agent_id,
                    source_version=agent.version,
                    target_kind=ComponentType.SKILL.value,
                    target_id=reference.component_id,
                    target_version=reference.version,
                    relation="uses_skill",
                )
            )
        for reference in agent.tool_refs:
            dependencies.append(
                RegistryDependency(
                    source_kind=ComponentType.AGENT.value,
                    source_id=agent.agent_id,
                    source_version=agent.version,
                    target_kind=ComponentType.TOOL.value,
                    target_id=reference.component_id,
                    target_version=reference.version,
                    relation="allows_tool",
                )
            )
        for reference in agent.policy_refs:
            dependencies.append(
                RegistryDependency(
                    source_kind=ComponentType.AGENT.value,
                    source_id=agent.agent_id,
                    source_version=agent.version,
                    target_kind=ComponentType.POLICY.value,
                    target_id=reference.component_id,
                    target_version=reference.version,
                    relation="uses_policy",
                )
            )
    dependencies.extend(_skill_dependencies(skills, tools))
    dependencies.extend(_tool_dependencies(tools))
    return sorted(dependencies, key=_dependency_sort_key)


def _tool_dependencies(tools: Sequence[ToolDescriptor]) -> list[RegistryDependency]:
    return [
        RegistryDependency(
            source_kind=ComponentType.TOOL.value,
            source_id=tool.tool_id,
            source_version=tool.version,
            target_kind=ComponentType.TOOL.value,
            target_id=reference.component_id,
            target_version=reference.version,
            relation="depends_on",
        )
        for tool in tools
        for reference in tool.dependencies
    ]


def _dependency_sort_key(item: RegistryDependency) -> tuple[str, ...]:
    return (
        item.source_kind,
        item.source_id,
        item.source_version,
        item.target_kind,
        item.target_id,
        item.target_version,
        item.relation,
    )


__all__ = [
    "AgentRegistry",
    "DuplicateRegistrationError",
    "IncompatibleRegistrationError",
    "ListRegistryAuditSink",
    "PromptRegistry",
    "RegistryAuditEvent",
    "RegistryAuditSink",
    "RegistryError",
    "RegistryLifecycleError",
    "SkillRegistry",
    "StaleRegistrationError",
]
