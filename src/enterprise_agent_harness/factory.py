"""Declarative construction of approved, version-pinned agents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .contracts import (
    AgentConfig,
    AgentDefinition,
    AgentLifecycleStatus,
    AgentOutcome,
    AgentTemplate,
    CapabilityDefinition,
    PolicyDefinition,
    PrincipalContext,
    ProviderProfile,
    ResolvedAgentManifest,
    RiskLevel,
    RuntimeConfig,
    RuntimeProfile,
    ToolDescriptor,
    ToolKind,
)
from .governance.approvals import ApprovalBroker, ApprovalPolicyEvaluator
from .governance.permissions import (
    DefaultPermissionBroker,
    PermissionBroker,
)
from .governance.safety import SafetyPolicy
from .memory.strategies import MemoryStrategy
from .observability.audit import AuditSink
from .observability.tracing import TraceSink
from .providers.base import ProviderAdapter
from .providers.invocation import ProviderCallPolicy
from .registries import AgentRegistry, RegistryError
from .runtime.execution import AgentRuntime
from .state.store import StateStore
from .tools.definitions import ToolDefinition, ToolInvocationError


class FactoryError(ValueError):
    """Base error for declarative factory failures."""


class FactoryValidationError(FactoryError):
    """Raised when a declarative agent configuration is invalid."""


class FactoryDependencyError(FactoryError):
    """Raised when a required registered component cannot be resolved."""


class FactoryTemplateError(FactoryError):
    """Raised when a configuration does not satisfy its selected template."""


class FactoryAuthorizationError(FactoryError):
    """Raised when a built agent is asked to exceed its resolved authority."""


class ProviderRegistry:
    """Resolve exact provider adapters for factory builds."""

    def __init__(
        self,
        providers: Mapping[tuple[str, str], ProviderAdapter] | None = None,
    ) -> None:
        self._providers: dict[tuple[str, str], ProviderAdapter] = {}
        self._lock = RLock()
        for (provider_id, version), provider in (providers or {}).items():
            self.register(
                ProviderProfile(
                    provider_id=provider_id,
                    version=version,
                    model="registered",
                ),
                provider,
            )

    def register(
        self,
        profile: ProviderProfile,
        provider: ProviderAdapter,
        *,
        replace_existing: bool = False,
    ) -> ProviderAdapter:
        """Register one exact provider adapter identity and version."""

        key = (profile.provider_id, profile.version)
        with self._lock:
            if key in self._providers:
                if replace_existing:
                    raise FactoryError("registered provider versions are immutable")
                raise FactoryError("provider identity and version pairs must be unique")
            self._providers[key] = provider
        return provider

    def resolve(self, profile: ProviderProfile) -> ProviderAdapter:
        """Return the provider adapter for an exact profile reference."""

        with self._lock:
            try:
                return self._providers[(profile.provider_id, profile.version)]
            except KeyError as exc:
                raise FactoryDependencyError(
                    f"provider is unavailable: {profile.provider_id}@{profile.version}"
                ) from exc


class RuntimeProfileRegistry:
    """Resolve reusable exact runtime-limit profiles."""

    def __init__(self, profiles: Sequence[RuntimeProfile] = ()) -> None:
        self._profiles: dict[tuple[str, str], RuntimeProfile] = {}
        self._lock = RLock()
        for profile in profiles:
            self.register(profile)

    def register(
        self,
        profile: RuntimeProfile,
        *,
        replace_existing: bool = False,
    ) -> RuntimeProfile:
        """Register one immutable runtime profile."""

        key = (profile.profile_id, profile.version)
        with self._lock:
            if key in self._profiles:
                if replace_existing:
                    raise FactoryError("registered runtime profiles are immutable")
                raise FactoryError("runtime profile identity and version pairs must be unique")
            self._profiles[key] = profile.model_copy(deep=True)
            return deepcopy(self._profiles[key])

    def resolve(self, profile_id: str, version: str) -> RuntimeProfile:
        """Return one active exact runtime profile."""

        with self._lock:
            try:
                profile = self._profiles[(profile_id, version)]
            except KeyError as exc:
                raise FactoryDependencyError(
                    f"runtime profile is unavailable: {profile_id}@{version}"
                ) from exc
            if profile.lifecycle != AgentLifecycleStatus.ACTIVE:
                raise FactoryDependencyError(
                    f"runtime profile is not active: {profile_id}@{version}"
                )
            return deepcopy(profile)


@dataclass(frozen=True)
class BuiltAgent:
    """A resolved manifest and its governed runtime, when activation is enabled."""

    manifest: ResolvedAgentManifest
    runtime: AgentRuntime | None
    registered: bool
    dry_run: bool
    registry: AgentRegistry | None = None

    def execute(
        self,
        principal: PrincipalContext,
        input_text: str,
        *,
        authorized_tool_ids: Sequence[str] | None = None,
        authorized_tool_versions: Sequence[str] | None = None,
        max_risk_level: RiskLevel | None = None,
        **kwargs: Any,
    ) -> AgentOutcome:
        """Execute through the resolved runtime without exceeding the manifest."""

        if self.runtime is None:
            raise FactoryError("the agent is dry-run only or has not been activated")
        if "agent_id" in kwargs or "agent_version" in kwargs:
            raise FactoryAuthorizationError("built agent identity cannot be overridden")
        if self.registry is not None:
            try:
                self.registry.resolve(self.manifest.agent.agent_id, self.manifest.agent.version)
            except RegistryError as exc:
                raise FactoryAuthorizationError(
                    "the registered agent version is not active"
                ) from exc

        allowed_versions = tuple(
            f"{reference.component_id}@{reference.version}"
            for reference in self.manifest.agent.allowed_tools
        )
        allowed_ids = tuple(
            dict.fromkeys(reference.component_id for reference in self.manifest.agent.allowed_tools)
        )
        if authorized_tool_ids is None:
            selected_ids = allowed_ids
        else:
            selected_ids = tuple(authorized_tool_ids)
            unauthorized_ids = set(selected_ids).difference(allowed_ids)
            if unauthorized_ids:
                raise FactoryAuthorizationError(
                    f"tool authority is outside the manifest: {sorted(unauthorized_ids)}"
                )
        if authorized_tool_versions is None:
            selected_versions = tuple(
                reference
                for reference in allowed_versions
                if reference.split("@", 1)[0] in selected_ids
            )
        else:
            selected_versions = tuple(authorized_tool_versions)
            unauthorized_versions = set(selected_versions).difference(allowed_versions)
            if unauthorized_versions:
                raise FactoryAuthorizationError(
                    "tool version authority is outside the manifest: "
                    f"{sorted(unauthorized_versions)}"
                )
            if any(
                reference.split("@", 1)[0] not in selected_ids for reference in selected_versions
            ):
                raise FactoryAuthorizationError(
                    "authorized tool versions must belong to authorized tool IDs"
                )
        effective_risk = self.manifest.agent.risk_level
        if max_risk_level is not None:
            if _risk_exceeds(max_risk_level, effective_risk):
                raise FactoryAuthorizationError("risk authority is outside the manifest")
            effective_risk = max_risk_level
        return self.runtime.execute(
            principal,
            input_text,
            agent_id=self.manifest.agent.agent_id,
            agent_version=self.manifest.agent.version,
            authorized_tool_ids=selected_ids,
            authorized_tool_versions=selected_versions,
            max_risk_level=effective_risk,
            **kwargs,
        )


@dataclass(frozen=True)
class _ResolvedComponents:
    config: AgentConfig
    definition: AgentDefinition
    provider: ProviderAdapter
    runtime_profile: RuntimeProfile | None
    runtime_limits: RuntimeConfig
    capabilities: tuple[CapabilityDefinition, ...]
    tools: tuple[ToolDefinition, ...]
    policies: tuple[PolicyDefinition, ...]
    memory: MemoryStrategy | None
    state_store: StateStore | None
    manifest: ResolvedAgentManifest


class AgentFactory:
    """Validate declarative configurations and assemble approved runtimes."""

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        providers: ProviderRegistry | Mapping[tuple[str, str], ProviderAdapter],
        runtime_profiles: RuntimeProfileRegistry
        | Mapping[tuple[str, str], RuntimeProfile]
        | None = None,
        memory_strategies: Mapping[str, MemoryStrategy] | None = None,
        state_stores: Mapping[str, StateStore] | None = None,
        default_state_store: StateStore | None = None,
        permission_broker: PermissionBroker | None = None,
        approval_broker: ApprovalBroker | None = None,
        approval_policy: ApprovalPolicyEvaluator | None = None,
        safety_policy: SafetyPolicy | None = None,
        trace_sink: TraceSink | None = None,
        audit_sink: AuditSink | None = None,
        provider_call_policy: ProviderCallPolicy | None = None,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.providers = (
            providers if isinstance(providers, ProviderRegistry) else ProviderRegistry(providers)
        )
        if runtime_profiles is None:
            self.runtime_profiles = RuntimeProfileRegistry()
        elif isinstance(runtime_profiles, RuntimeProfileRegistry):
            self.runtime_profiles = runtime_profiles
        else:
            self.runtime_profiles = RuntimeProfileRegistry(tuple(runtime_profiles.values()))
        self.memory_strategies = dict(memory_strategies or {})
        self.state_stores = dict(state_stores or {})
        self.default_state_store = default_state_store
        self.permission_broker = permission_broker
        self.approval_broker = approval_broker
        self.approval_policy = approval_policy
        self.safety_policy = safety_policy
        self.trace_sink = trace_sink
        self.audit_sink = audit_sink
        self.provider_call_policy = provider_call_policy
        self._id = id_factory or (lambda prefix: f"{prefix}_{uuid4().hex[:10]}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._built: dict[tuple[str, str], BuiltAgent] = {}
        self._lock = RLock()

    @staticmethod
    def template_config(template: AgentTemplate, **values: Any) -> AgentConfig:
        """Create a declarative configuration for one standard template."""

        return AgentConfig(template=template, **values)

    def validate(
        self,
        config: AgentConfig | Mapping[str, Any],
        *,
        activate: bool = True,
    ) -> ResolvedAgentManifest:
        """Resolve and validate without registering or constructing a runtime."""

        return self._resolve(self._coerce_config(config), activate=activate).manifest

    def build(
        self,
        config: AgentConfig | Mapping[str, Any],
        *,
        dry_run: bool = False,
        activate: bool = True,
        register: bool = True,
    ) -> BuiltAgent:
        """Build one exact agent, optionally registering and activating it."""

        resolved = self._resolve(self._coerce_config(config), activate=activate)
        registered = False
        if not dry_run and register:
            self.agent_registry.register(resolved.definition)
            registered = True
            if activate:
                self.agent_registry.activate(
                    resolved.definition.agent_id,
                    resolved.definition.version,
                )
        runtime: AgentRuntime | None = None
        if not dry_run and activate:
            runtime = AgentRuntime(
                tools=self.agent_registry.tools,
                provider=resolved.provider,
                capabilities=resolved.capabilities,
                state_store=resolved.state_store,
                memory=resolved.memory,
                permission_broker=self._permission_broker(resolved),
                approval_broker=self.approval_broker,
                approval_policy=self.approval_policy,
                safety_policy=self.safety_policy,
                trace_sink=self.trace_sink,
                audit_sink=self.audit_sink,
                config=resolved.runtime_limits,
                provider_call_policy=self.provider_call_policy,
                id_factory=self._id,
                clock=self._clock,
            )
        built = BuiltAgent(
            manifest=resolved.manifest,
            runtime=runtime,
            registered=registered,
            dry_run=dry_run,
            registry=self.agent_registry if registered else None,
        )
        if runtime is not None:
            with self._lock:
                self._built[(resolved.definition.agent_id, resolved.definition.version)] = built
        return built

    def runtime_for(self, agent_id: str, version: str) -> BuiltAgent:
        """Return a previously built active agent for delegation."""

        with self._lock:
            try:
                built = self._built[(agent_id, version)]
            except KeyError as exc:
                raise FactoryDependencyError(
                    f"agent runtime is not built: {agent_id}@{version}"
                ) from exc
        if built.runtime is None:
            raise FactoryDependencyError(f"agent runtime is not active: {agent_id}@{version}")
        return built

    def new_id(self, prefix: str) -> str:
        """Create an application-scoped identifier for a composed execution."""

        return self._id(prefix)

    def _resolve(self, config: AgentConfig, *, activate: bool) -> _ResolvedComponents:
        provider = self.providers.resolve(config.provider_profile)
        runtime_profile: RuntimeProfile | None = None
        if config.runtime_profile is not None:
            if config.runtime_limits is not None:
                raise FactoryValidationError(
                    "runtime_profile and runtime_limits cannot both be configured"
                )
            runtime_profile = self.runtime_profiles.resolve(
                config.runtime_profile.component_id,
                config.runtime_profile.version,
            )
            runtime_limits = runtime_profile.runtime_limits.model_copy(deep=True)
        else:
            runtime_limits = config.runtime_limits or RuntimeConfig()

        capabilities = tuple(
            self._resolve_capability(reference.component_id, reference.version)
            for reference in config.capabilities
        )
        tools = tuple(
            self._resolve_tool(reference.component_id, reference.version)
            for reference in config.allowed_tools
        )
        policies = tuple(
            self._resolve_policy(reference.component_id, reference.version)
            for reference in config.policies
        )
        target_lifecycle = (
            AgentLifecycleStatus.ACTIVE if activate else AgentLifecycleStatus.VALIDATED
        )
        definition = AgentDefinition(
            identity=config.identity,
            goal=config.goal,
            supported_intents=list(config.supported_intents),
            supported_languages=list(config.supported_languages),
            capabilities=[reference.model_copy(deep=True) for reference in config.capabilities],
            allowed_tools=[reference.model_copy(deep=True) for reference in config.allowed_tools],
            policies=[reference.model_copy(deep=True) for reference in config.policies],
            provider_profile=config.provider_profile.model_copy(deep=True),
            runtime_limits=runtime_limits,
            risk_level=config.risk_level,
            approval_requirements=list(config.approval_requirements),
            state_strategy=config.state_strategy,
            memory_strategy=config.memory_strategy,
            owner_id=config.owner_id,
            lifecycle=target_lifecycle,
            performance_metadata=deepcopy(config.performance_metadata),
        )
        try:
            self.agent_registry.check_compatibility(definition)
        except RegistryError as exc:
            raise FactoryDependencyError(str(exc)) from exc
        self._validate_template(config, tools=tools, policies=policies)
        memory = self._resolve_memory(config.memory_strategy)
        state_store = self._resolve_state(config.state_strategy)
        tool_descriptors = tuple(tool.descriptor for tool in tools)
        manifest = self._manifest(
            config=config,
            definition=definition,
            capabilities=capabilities,
            tools=tool_descriptors,
            policies=policies,
            runtime_profile=runtime_profile,
            runtime_limits=runtime_limits,
        )
        return _ResolvedComponents(
            config=config,
            definition=definition,
            provider=provider,
            runtime_profile=runtime_profile,
            runtime_limits=runtime_limits,
            capabilities=capabilities,
            tools=tools,
            policies=policies,
            memory=memory,
            state_store=state_store,
            manifest=manifest,
        )

    def _resolve_capability(self, capability_id: str, version: str) -> CapabilityDefinition:
        try:
            capability = self.agent_registry.capabilities.resolve(capability_id, version)
        except RegistryError as exc:
            raise FactoryDependencyError(str(exc)) from exc
        return capability

    def _resolve_tool(self, tool_id: str, version: str) -> ToolDefinition:
        try:
            return self.agent_registry.tools.get(tool_id, version)
        except ToolInvocationError as exc:
            raise FactoryDependencyError(f"tool is unavailable: {tool_id}@{version}") from exc

    def _resolve_policy(self, policy_id: str, version: str) -> PolicyDefinition:
        for policy in self.agent_registry.policies:
            if policy.policy_id == policy_id and policy.version == version:
                if policy.lifecycle != AgentLifecycleStatus.ACTIVE:
                    raise FactoryDependencyError(f"policy is not active: {policy_id}@{version}")
                return policy
        raise FactoryDependencyError(f"policy is unavailable: {policy_id}@{version}")

    def _resolve_memory(self, strategy_id: str | None) -> MemoryStrategy | None:
        if strategy_id is None:
            return None
        try:
            return self.memory_strategies[strategy_id]
        except KeyError as exc:
            raise FactoryDependencyError(f"memory strategy is unavailable: {strategy_id}") from exc

    def _resolve_state(self, strategy_id: str | None) -> StateStore | None:
        if strategy_id is None:
            return self.default_state_store
        try:
            return self.state_stores[strategy_id]
        except KeyError as exc:
            raise FactoryDependencyError(f"state strategy is unavailable: {strategy_id}") from exc

    def _permission_broker(self, resolved: _ResolvedComponents) -> PermissionBroker:
        if self.permission_broker is not None:
            return self.permission_broker
        return DefaultPermissionBroker(
            policies=resolved.policies,
            agent_tool_allowlists={
                resolved.definition.agent_id: [tool.tool_id for tool in resolved.tools]
            },
        )

    @staticmethod
    def _validate_template(
        config: AgentConfig,
        *,
        tools: Sequence[ToolDefinition],
        policies: Sequence[PolicyDefinition],
    ) -> None:
        template = config.template
        if template is None:
            return
        non_read_tools = [tool for tool in tools if tool.kind != ToolKind.READ]
        if template == AgentTemplate.READ_ONLY_ANALYST and non_read_tools:
            raise FactoryTemplateError("read_only_analyst cannot reference write or action tools")
        if template == AgentTemplate.ACTION_AGENT and not non_read_tools:
            raise FactoryTemplateError("action_agent requires a write or action tool")
        if template == AgentTemplate.APPROVAL_GATED_OPERATOR:
            policy_requires_approval = any(
                rule.requires_approval is True for policy in policies for rule in policy.rules
            )
            if not non_read_tools or not (
                config.approval_requirements
                or policy_requires_approval
                or any(tool.requires_approval for tool in tools)
            ):
                raise FactoryTemplateError(
                    "approval_gated_operator requires a side-effecting tool and approval control"
                )

    def _manifest(
        self,
        *,
        config: AgentConfig,
        definition: AgentDefinition,
        capabilities: Sequence[CapabilityDefinition],
        tools: Sequence[ToolDescriptor],
        policies: Sequence[PolicyDefinition],
        runtime_profile: RuntimeProfile | None,
        runtime_limits: RuntimeConfig,
    ) -> ResolvedAgentManifest:
        digest_payload = {
            "agent": definition.model_dump(mode="json"),
            "capabilities": [item.model_dump(mode="json") for item in capabilities],
            "tools": [item.model_dump(mode="json") for item in tools],
            "policies": [item.model_dump(mode="json") for item in policies],
            "provider_profile": config.provider_profile.model_dump(mode="json"),
            "runtime_profile": (
                runtime_profile.model_dump(mode="json") if runtime_profile is not None else None
            ),
            "runtime_limits": runtime_limits.model_dump(mode="json"),
            "template": config.template.value if config.template is not None else None,
        }
        encoded = json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return ResolvedAgentManifest(
            manifest_id=f"{definition.agent_id}@{definition.version}",
            manifest_digest=digest,
            source=config.model_copy(deep=True),
            agent=definition.model_copy(deep=True),
            capabilities=tuple(item.model_copy(deep=True) for item in capabilities),
            tools=tuple(item.model_copy(deep=True) for item in tools),
            policies=tuple(item.model_copy(deep=True) for item in policies),
            provider_profile=config.provider_profile.model_copy(deep=True),
            runtime_profile=(
                runtime_profile.model_copy(deep=True) if runtime_profile is not None else None
            ),
            runtime_limits=runtime_limits.model_copy(deep=True),
            state_strategy=config.state_strategy,
            memory_strategy=config.memory_strategy,
            template=config.template,
            resolved_at=self._clock(),
        )

    @staticmethod
    def _coerce_config(config: AgentConfig | Mapping[str, Any]) -> AgentConfig:
        if isinstance(config, AgentConfig):
            return config.model_copy(deep=True)
        try:
            return AgentConfig.model_validate(config)
        except ValidationError as exc:
            raise FactoryValidationError(str(exc)) from exc


def _risk_exceeds(actual: RiskLevel, maximum: RiskLevel) -> bool:
    order = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }
    return order[RiskLevel(actual)] > order[RiskLevel(maximum)]


__all__ = [
    "AgentFactory",
    "BuiltAgent",
    "FactoryAuthorizationError",
    "FactoryDependencyError",
    "FactoryError",
    "FactoryTemplateError",
    "FactoryValidationError",
    "ProviderRegistry",
    "RuntimeProfileRegistry",
]
