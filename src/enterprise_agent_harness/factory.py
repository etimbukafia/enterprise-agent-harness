"""Declarative construction of approved, version-pinned agents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import ValidationError

from .contracts import (
    AgentConfig,
    AgentDefinition,
    AgentLifecycleStatus,
    AgentOutcome,
    AgentTemplate,
    ComponentReference,
    PolicyDefinition,
    PolicyEffect,
    PolicyRule,
    PrincipalContext,
    PromptDefinition,
    ProviderProfile,
    ResolvedAgentManifest,
    RiskLevel,
    RuntimeConfig,
    RuntimeProfile,
    SkillDefinition,
    ToolKind,
)
from .errors import RuntimeAuthorizationError
from .governance.approvals import ApprovalBroker, ApprovalPolicyEvaluator
from .governance.permissions import (
    DefaultPermissionBroker,
    PermissionBroker,
)
from .governance.safety import SafetyPolicy
from .memory.strategies import MemoryStrategy
from .observability.audit import AuditSink
from .observability.failures import ObservabilityFailureReporter
from .observability.tracing import TraceSink
from .providers.base import ProviderAdapter
from .providers.invocation import ProviderCallPolicy
from .registries import AgentRegistry, RegistryError
from .runtime.execution import AgentRuntime
from .state.store import StateStore
from .tools.definitions import ToolDefinition, ToolInvocationError

if TYPE_CHECKING:
    from .evaluation.contracts import RunTrace


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
class _ManifestAuthority:
    """Immutable build-time authority used independently of the public manifest."""

    manifest_digest: str
    agent_id: str
    agent_version: str
    allowed_tool_ids: tuple[str, ...]
    allowed_tool_versions: tuple[str, ...]
    tool_permissions: tuple[tuple[str, tuple[str, ...]], ...]
    risk_level: RiskLevel
    max_plan_steps: int


@dataclass(frozen=True)
class BuiltAgent:
    """A resolved manifest and its governed runtime, when activation is enabled."""

    manifest: ResolvedAgentManifest
    runtime: AgentRuntime | None
    registered: bool
    dry_run: bool
    registry: AgentRegistry | None = None
    _authority: _ManifestAuthority | None = None

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
        self._assert_manifest_integrity()
        authority = self._require_authority()
        if self.registry is not None:
            try:
                self.registry.resolve(authority.agent_id, authority.agent_version)
            except RegistryError as exc:
                raise FactoryAuthorizationError(
                    "the registered agent version is not active"
                ) from exc

        allowed_versions = authority.allowed_tool_versions
        allowed_ids = authority.allowed_tool_ids
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
        effective_risk = authority.risk_level
        if max_risk_level is not None:
            if _risk_exceeds(max_risk_level, effective_risk):
                raise FactoryAuthorizationError("risk authority is outside the manifest")
            effective_risk = max_risk_level
        return self.runtime.execute(
            principal,
            input_text,
            agent_id=authority.agent_id,
            agent_version=authority.agent_version,
            authorized_tool_ids=selected_ids,
            authorized_tool_versions=selected_versions,
            max_risk_level=effective_risk,
            **kwargs,
        )

    def trace_for(self, execution_id: str) -> RunTrace:
        """Return stable trace evidence for an execution of this built agent."""

        if self.runtime is None:
            raise FactoryError("the agent is dry-run only or has not been activated")
        return self.runtime.trace_for(execution_id)

    def _assert_manifest_integrity(self) -> None:
        """Reject changes to the public manifest before provider execution."""

        authority = self._require_authority()
        try:
            calculated_digest = _calculate_manifest_digest(
                manifest_id=self.manifest.manifest_id,
                source=self.manifest.source,
                agent=self.manifest.agent,
                prompt_ref=self.manifest.prompt_ref,
                skill_refs=self.manifest.skill_refs,
                tool_refs=self.manifest.tool_refs,
                policy_refs=self.manifest.policy_refs,
                registry_snapshot_id=self.manifest.registry_snapshot_id,
                provider_profile=self.manifest.provider_profile,
                runtime_profile=self.manifest.runtime_profile,
                runtime_limits=self.manifest.runtime_limits,
                template=self.manifest.template,
            )
        except Exception as exc:
            raise FactoryAuthorizationError("resolved manifest integrity check failed") from exc
        if (
            self.manifest.manifest_digest != authority.manifest_digest
            or calculated_digest != authority.manifest_digest
        ):
            raise FactoryAuthorizationError("resolved manifest integrity check failed")

    def _require_authority(self) -> _ManifestAuthority:
        if self._authority is None:
            raise FactoryAuthorizationError("built agent has no trusted authority snapshot")
        return self._authority

    @property
    def _trusted_agent_identity(self) -> tuple[str, str]:
        """Return the exact agent identity captured at build time."""

        authority = self._require_authority()
        return authority.agent_id, authority.agent_version

    @property
    def _trusted_allowed_tool_ids(self) -> tuple[str, ...]:
        """Return the build-time tool identity ceiling."""

        return self._require_authority().allowed_tool_ids

    @property
    def _trusted_allowed_tool_versions(self) -> tuple[str, ...]:
        """Return the build-time exact tool version ceiling."""

        return self._require_authority().allowed_tool_versions

    @property
    def _trusted_tool_permissions(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return build-time permission requirements for exact tools."""

        return self._require_authority().tool_permissions

    @property
    def _trusted_risk_level(self) -> RiskLevel:
        """Return the build-time agent risk ceiling."""

        return self._require_authority().risk_level

    @property
    def _trusted_max_plan_steps(self) -> int:
        """Return the build-time plan-step ceiling."""

        return self._require_authority().max_plan_steps


@dataclass(frozen=True)
class _ResolvedComponents:
    config: AgentConfig
    definition: AgentDefinition
    provider: ProviderAdapter
    runtime_profile: RuntimeProfile | None
    runtime_limits: RuntimeConfig
    prompt: PromptDefinition
    skills: tuple[SkillDefinition, ...]
    tools: tuple[ToolDefinition, ...]
    policies: tuple[PolicyDefinition, ...]
    registry_snapshot_id: str
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
        failure_reporter: ObservabilityFailureReporter | None = None,
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
        self.failure_reporter = failure_reporter
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
                prompt=resolved.prompt,
                skills=resolved.skills,
                state_store=resolved.state_store,
                memory=resolved.memory,
                permission_broker=self._permission_broker(resolved),
                approval_broker=self.approval_broker,
                approval_policy=self.approval_policy,
                safety_policy=self.safety_policy,
                trace_sink=self.trace_sink,
                audit_sink=self.audit_sink,
                failure_reporter=self.failure_reporter,
                config=resolved.runtime_limits,
                provider_call_policy=self.provider_call_policy,
                id_factory=self._id,
                clock=self._clock,
                execution_guard=self._runtime_execution_guard,
                bound_agent_id=resolved.definition.agent_id,
                bound_agent_version=resolved.definition.version,
                manifest_id=resolved.manifest.manifest_id,
                manifest_digest=resolved.manifest.manifest_digest,
                registry_snapshot_id=resolved.registry_snapshot_id,
                prompt_ref=resolved.manifest.prompt_ref,
                skill_refs=resolved.manifest.skill_refs,
            )
        authority = _manifest_authority(
            manifest=resolved.manifest,
            definition=resolved.definition,
            tools=resolved.tools,
            runtime_limits=resolved.runtime_limits,
        )
        built = BuiltAgent(
            manifest=resolved.manifest,
            runtime=runtime,
            registered=registered,
            dry_run=dry_run,
            registry=self.agent_registry if runtime is not None else None,
            _authority=authority,
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
                config.runtime_profile.profile_id,
                config.runtime_profile.version,
            )
            runtime_limits = runtime_profile.runtime_limits.model_copy(deep=True)
        else:
            runtime_limits = config.runtime_limits or RuntimeConfig()

        prompt = self._resolve_prompt(
            config.prompt_ref.component_id,
            config.prompt_ref.version,
            require_active=activate,
        )
        skills = tuple(
            self._resolve_skill(
                reference.component_id,
                reference.version,
                require_active=activate,
            )
            for reference in config.skill_refs
        )
        tools = tuple(
            self._resolve_tool(
                reference.component_id,
                reference.version,
                require_active=activate,
            )
            for reference in config.tool_refs
        )
        policies = tuple(
            self._resolve_policy(
                reference.component_id,
                reference.version,
                require_active=activate,
            )
            for reference in config.policy_refs
        )
        target_lifecycle = (
            AgentLifecycleStatus.ACTIVE if activate else AgentLifecycleStatus.VALIDATED
        )
        definition = AgentDefinition(
            identity=config.identity,
            goal=config.goal,
            supported_intents=list(config.supported_intents),
            supported_languages=list(config.supported_languages),
            prompt_ref=config.prompt_ref.model_copy(deep=True),
            skill_refs=[reference.model_copy(deep=True) for reference in config.skill_refs],
            tool_refs=[reference.model_copy(deep=True) for reference in config.tool_refs],
            policy_refs=[reference.model_copy(deep=True) for reference in config.policy_refs],
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
            self.agent_registry.check_compatibility(
                definition,
                require_active_dependencies=activate,
            )
        except RegistryError as exc:
            raise FactoryDependencyError(str(exc)) from exc
        self._validate_template(config, tools=tools, policies=policies)
        memory = self._resolve_memory(config.memory_strategy)
        state_store = self._resolve_state(config.state_strategy)
        registry_snapshot = self.agent_registry.snapshot(
            include_inactive=not activate,
            agent_overrides=(definition,),
        )
        manifest = self._manifest(
            config=config,
            definition=definition,
            registry_snapshot_id=registry_snapshot.snapshot_id,
            runtime_profile=runtime_profile,
            runtime_limits=runtime_limits,
        )
        return _ResolvedComponents(
            config=config,
            definition=definition,
            provider=provider,
            runtime_profile=runtime_profile,
            runtime_limits=runtime_limits,
            prompt=prompt,
            skills=skills,
            tools=tools,
            policies=policies,
            registry_snapshot_id=registry_snapshot.snapshot_id,
            memory=memory,
            state_store=state_store,
            manifest=manifest,
        )

    def _resolve_prompt(
        self,
        prompt_id: str,
        version: str,
        *,
        require_active: bool,
    ) -> PromptDefinition:
        try:
            prompt = (
                self.agent_registry.prompts.resolve(prompt_id, version)
                if require_active
                else self.agent_registry.prompts.get(prompt_id, version)
            )
            if not require_active and prompt.lifecycle not in {
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
            }:
                raise RegistryError(f"prompt is not validated: {prompt_id}@{version}")
        except RegistryError as exc:
            raise FactoryDependencyError(str(exc)) from exc
        return prompt

    def _resolve_skill(
        self,
        skill_id: str,
        version: str,
        *,
        require_active: bool,
    ) -> SkillDefinition:
        try:
            skill = (
                self.agent_registry.skills.resolve(skill_id, version)
                if require_active
                else self.agent_registry.skills.get(skill_id, version)
            )
            if not require_active and skill.lifecycle not in {
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
            }:
                raise RegistryError(f"skill is not validated: {skill_id}@{version}")
        except RegistryError as exc:
            raise FactoryDependencyError(str(exc)) from exc
        return skill

    def _resolve_tool(
        self,
        tool_id: str,
        version: str,
        *,
        require_active: bool,
    ) -> ToolDefinition:
        try:
            tool = self.agent_registry.tools.get(
                tool_id,
                version,
                include_inactive=not require_active,
            )
            if not require_active and tool.lifecycle not in {
                AgentLifecycleStatus.VALIDATED,
                AgentLifecycleStatus.ACTIVE,
            }:
                raise ToolInvocationError(
                    f"tool is not validated: {tool_id}@{version}",
                    code="tool_not_validated",
                )
            return tool
        except ToolInvocationError as exc:
            raise FactoryDependencyError(f"tool is unavailable: {tool_id}@{version}") from exc

    def _resolve_policy(
        self,
        policy_id: str,
        version: str,
        *,
        require_active: bool,
    ) -> PolicyDefinition:
        allowed_lifecycles = (
            {AgentLifecycleStatus.ACTIVE}
            if require_active
            else {AgentLifecycleStatus.VALIDATED, AgentLifecycleStatus.ACTIVE}
        )
        for policy in self.agent_registry.policies:
            if policy.policy_id == policy_id and policy.version == version:
                if policy.lifecycle not in allowed_lifecycles:
                    raise FactoryDependencyError(f"policy is not usable: {policy_id}@{version}")
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

    def _runtime_execution_guard(self, agent_id: str, agent_version: str) -> None:
        """Reject new and resumed work when live registry authority is stale."""

        try:
            self.agent_registry.resolve(agent_id, agent_version)
        except RegistryError as exc:
            raise RuntimeAuthorizationError(
                f"agent or dependency is not active: {agent_id}@{agent_version}"
            ) from exc

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
            if not non_read_tools:
                raise FactoryTemplateError("approval_gated_operator requires a side-effecting tool")
            unprotected = [
                tool
                for tool in non_read_tools
                if not tool.requires_approval
                and not any(
                    AgentFactory._approval_rule_covers_tool(
                        config=config,
                        tool=tool,
                        rule=rule,
                    )
                    for policy in policies
                    for rule in policy.rules
                )
            ]
            if unprotected:
                references = ", ".join(f"{tool.tool_id}@{tool.version}" for tool in unprotected)
                raise FactoryTemplateError(
                    "approval_gated_operator requires an approval gate for every "
                    f"side-effecting tool: {references}"
                )

    @staticmethod
    def _approval_rule_covers_tool(
        *,
        config: AgentConfig,
        tool: ToolDefinition,
        rule: PolicyRule,
    ) -> bool:
        """Return whether a declarative allow rule gates this tool in every run."""

        if rule.effect != PolicyEffect.ALLOW or rule.requires_approval is not True:
            return False
        if rule.tool_ids and tool.tool_id not in rule.tool_ids:
            return False
        if rule.agent_ids and config.identity.agent_id not in rule.agent_ids:
            return False
        if rule.risk_levels and tool.risk_level not in rule.risk_levels:
            return False

        # These dimensions are supplied by the caller, provider, or resource
        # context at execution time. A constrained rule cannot prove that the
        # tool is gated for every possible invocation.
        return not any(
            (
                rule.principal_ids,
                rule.tenant_ids,
                rule.required_permissions,
                rule.environments,
                rule.resource_types,
                rule.resource_ids,
            )
        )

    def _manifest(
        self,
        *,
        config: AgentConfig,
        definition: AgentDefinition,
        registry_snapshot_id: str,
        runtime_profile: RuntimeProfile | None,
        runtime_limits: RuntimeConfig,
    ) -> ResolvedAgentManifest:
        manifest_id = f"{definition.agent_id}@{definition.version}"
        digest = _calculate_manifest_digest(
            manifest_id=manifest_id,
            source=config,
            agent=definition,
            prompt_ref=definition.prompt_ref,
            skill_refs=definition.skill_refs,
            tool_refs=definition.tool_refs,
            policy_refs=definition.policy_refs,
            registry_snapshot_id=registry_snapshot_id,
            provider_profile=config.provider_profile,
            runtime_profile=runtime_profile,
            runtime_limits=runtime_limits,
            template=config.template,
        )
        return ResolvedAgentManifest(
            manifest_id=manifest_id,
            manifest_digest=digest,
            source=config.model_copy(deep=True),
            agent=definition.model_copy(deep=True),
            prompt_ref=definition.prompt_ref.model_copy(deep=True),
            skill_refs=tuple(item.model_copy(deep=True) for item in definition.skill_refs),
            tool_refs=tuple(item.model_copy(deep=True) for item in definition.tool_refs),
            policy_refs=tuple(item.model_copy(deep=True) for item in definition.policy_refs),
            registry_snapshot_id=registry_snapshot_id,
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


def _manifest_authority(
    *,
    manifest: ResolvedAgentManifest,
    definition: AgentDefinition,
    tools: Sequence[ToolDefinition],
    runtime_limits: RuntimeConfig,
) -> _ManifestAuthority:
    """Capture only immutable authority data needed after a factory build."""

    return _ManifestAuthority(
        manifest_digest=manifest.manifest_digest,
        agent_id=definition.agent_id,
        agent_version=definition.version,
        allowed_tool_ids=tuple(
            dict.fromkeys(reference.component_id for reference in definition.tool_refs)
        ),
        allowed_tool_versions=tuple(
            f"{reference.component_id}@{reference.version}" for reference in definition.tool_refs
        ),
        tool_permissions=tuple(
            sorted(
                (
                    f"{tool.tool_id}@{tool.version}",
                    tuple(tool.required_permissions),
                )
                for tool in tools
            )
        ),
        risk_level=definition.risk_level,
        max_plan_steps=runtime_limits.max_plan_steps,
    )


def _calculate_manifest_digest(
    *,
    manifest_id: str,
    source: AgentConfig,
    agent: AgentDefinition,
    prompt_ref: ComponentReference,
    skill_refs: Sequence[ComponentReference],
    tool_refs: Sequence[ComponentReference],
    policy_refs: Sequence[ComponentReference],
    registry_snapshot_id: str,
    provider_profile: ProviderProfile,
    runtime_profile: RuntimeProfile | None,
    runtime_limits: RuntimeConfig,
    template: AgentTemplate | None,
) -> str:
    """Calculate the digest over every resolved manifest authority field."""

    digest_payload = {
        "manifest_id": manifest_id,
        "source": source.model_dump(mode="json"),
        "agent": agent.model_dump(mode="json"),
        "prompt_ref": prompt_ref.model_dump(mode="json"),
        "skill_refs": [item.model_dump(mode="json") for item in skill_refs],
        "tool_refs": [item.model_dump(mode="json") for item in tool_refs],
        "policy_refs": [item.model_dump(mode="json") for item in policy_refs],
        "registry_snapshot_id": registry_snapshot_id,
        "provider_profile": provider_profile.model_dump(mode="json"),
        "runtime_profile": (
            runtime_profile.model_dump(mode="json") if runtime_profile is not None else None
        ),
        "runtime_limits": runtime_limits.model_dump(mode="json"),
        "template": template.value if template is not None else None,
    }
    encoded = json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
