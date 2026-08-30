"""Deterministic permission and policy checks for proposed tool calls."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from inspect import Parameter, signature
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from ..contracts import (
    AgentLifecycleStatus,
    ExecutionContext,
    PermissionDecision,
    PolicyDecision,
    PolicyDefinition,
    PolicyEffect,
    PolicyRule,
    PrincipalContext,
    ResourceContext,
    RiskLevel,
)
from ..tools.definitions import ToolDefinition


class PermissionBroker(Protocol):
    """Application boundary that authorizes one proposed tool call."""

    def authorize(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        resource: ResourceContext | None = None,
    ) -> PermissionDecision:
        """Return a decision before the handler can run."""


class PolicyEvaluator(Protocol):
    """Boundary for deterministic policy evaluation."""

    def evaluate(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        resource: ResourceContext | None = None,
    ) -> PolicyDecision:
        """Return one explicit policy decision."""


ResourcePolicyHook = Callable[..., PolicyDecision | bool]


@dataclass(frozen=True)
class EnvironmentConstraint:
    """Optional environment-specific tool and risk limits."""

    allowed_tool_ids: frozenset[str] | None = None
    max_risk_level: RiskLevel = RiskLevel.CRITICAL

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_risk_level", RiskLevel(self.max_risk_level))
        if self.allowed_tool_ids is not None and any(
            not tool_id.strip() for tool_id in self.allowed_tool_ids
        ):
            raise ValueError("allowed_tool_ids must not contain empty values")


class DeclarativePolicyEngine:
    """Evaluate policy rules without allowing them to exceed runtime authority.

    The engine treats the trusted execution context as a ceiling. Policy rules,
    principal mappings, and resource hooks can deny work or require approval;
    none of them can add a tool or permission that the context does not carry.
    """

    def __init__(
        self,
        policies: Sequence[PolicyDefinition] = (),
        *,
        principal_tool_permissions: Mapping[str, Iterable[str]] | None = None,
        principal_tool_allowlists: Mapping[str, Iterable[str]] | None = None,
        principal_permissions: Mapping[str, Iterable[str]] | None = None,
        agent_tool_allowlists: Mapping[str, Iterable[str]] | None = None,
        environment_constraints: Mapping[str, EnvironmentConstraint] | None = None,
        environment_tool_allowlists: Mapping[str, Iterable[str]] | None = None,
        max_risk_by_environment: Mapping[str, RiskLevel] | None = None,
        resource_policy_hooks: Sequence[ResourcePolicyHook] = (),
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        if principal_tool_permissions is not None and principal_tool_allowlists is not None:
            raise ValueError("provide one principal tool mapping")
        self.policies = tuple(policies)
        policy_keys = [(policy.policy_id, policy.version) for policy in self.policies]
        if len(policy_keys) != len(set(policy_keys)):
            raise ValueError("policy identity and version pairs must be unique")
        self.principal_tool_permissions = _normalize_mapping(
            principal_tool_permissions
            if principal_tool_permissions is not None
            else principal_tool_allowlists
        )
        self.principal_permissions = _normalize_mapping(principal_permissions)
        self.agent_tool_allowlists = _normalize_mapping(agent_tool_allowlists)
        self.environment_constraints = dict(environment_constraints or {})
        self.environment_tool_allowlists = _normalize_mapping(environment_tool_allowlists)
        self.max_risk_by_environment = {
            name: RiskLevel(value) for name, value in (max_risk_by_environment or {}).items()
        }
        self.resource_policy_hooks = tuple(resource_policy_hooks)
        self._id = id_factory or (lambda prefix: f"{prefix}_{uuid4().hex[:10]}")
        self._decisions: list[PolicyDecision] = []
        self._lock = RLock()

    @property
    def decisions(self) -> list[PolicyDecision]:
        """Return a copy of all deterministic decisions made by this engine."""

        with self._lock:
            return deepcopy(self._decisions)

    def evaluate(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        resource: ResourceContext | None = None,
    ) -> PolicyDecision:
        """Evaluate one proposed call in a stable, deny-first order."""

        base = self._decision_context(
            principal=principal,
            execution=execution,
            tool=tool,
            resource=resource,
        )

        if execution.principal != principal:
            return self._finish(
                PolicyDecision(
                    **base,
                    allowed=False,
                    reason_code="principal_context_mismatch",
                )
            )
        if tool.lifecycle != AgentLifecycleStatus.ACTIVE:
            return self._finish(
                PolicyDecision(
                    **base,
                    allowed=False,
                    reason_code="tool_not_active",
                )
            )
        if tool.tool_id not in execution.authorized_tool_ids:
            return self._finish(
                PolicyDecision(
                    **base,
                    allowed=False,
                    reason_code="tool_not_in_execution_allowlist",
                )
            )
        if tool.allowed_environments and execution.environment not in tool.allowed_environments:
            return self._finish(
                PolicyDecision(
                    **base,
                    allowed=False,
                    reason_code="tool_not_allowed_in_environment",
                )
            )
        if self._mapping_denies(
            self.principal_tool_permissions,
            principal.principal_id,
            tool.tool_id,
        ):
            return self._finish(
                PolicyDecision(
                    **base,
                    allowed=False,
                    reason_code="principal_tool_not_allowed",
                )
            )
        if self._mapping_denies(
            self.agent_tool_allowlists,
            execution.agent_id,
            tool.tool_id,
        ):
            return self._finish(
                PolicyDecision(
                    **base,
                    allowed=False,
                    reason_code="agent_tool_not_allowed",
                )
            )
        if self._principal_permission_denies(principal, tool):
            return self._finish(
                PolicyDecision(
                    **base,
                    allowed=False,
                    reason_code="principal_permission_missing",
                )
            )
        environment_failure = self._environment_failure(execution, tool)
        if environment_failure is not None:
            return self._finish(
                PolicyDecision(**base, allowed=False, reason_code=environment_failure)
            )
        if _risk_exceeds(tool.risk_level, execution.max_risk_level):
            return self._finish(
                PolicyDecision(
                    **base,
                    allowed=False,
                    reason_code="risk_exceeds_execution_limit",
                )
            )
        missing_permissions = sorted(
            set(tool.required_permissions).difference(execution.granted_permissions)
        )
        if missing_permissions:
            return self._finish(
                PolicyDecision(
                    **base,
                    allowed=False,
                    reason_code="required_permission_missing",
                    metadata={"missing_permission_count": str(len(missing_permissions))},
                )
            )

        policy_decision = self._evaluate_definitions(
            principal=principal,
            execution=execution,
            tool=tool,
            resource=resource,
            base=base,
        )
        if not policy_decision.allowed:
            return self._finish(policy_decision)

        hook_decision = self._evaluate_resource_hooks(
            principal=principal,
            execution=execution,
            tool=tool,
            arguments=arguments,
            resource=resource,
            base=base,
        )
        if hook_decision is not None and not hook_decision.allowed:
            return self._finish(hook_decision)

        approval_required = (
            policy_decision.approval_required
            or tool.requires_approval
            or (hook_decision is not None and hook_decision.approval_required)
        )
        return self._finish(
            policy_decision.model_copy(
                update={
                    "allowed": True,
                    "approval_required": approval_required,
                    "reason_code": (
                        "policy_allows_with_approval"
                        if approval_required
                        else policy_decision.reason_code
                    ),
                }
            )
        )

    def _evaluate_definitions(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        resource: ResourceContext | None,
        base: dict[str, Any],
    ) -> PolicyDecision:
        if not self.policies:
            return PolicyDecision(
                **base,
                allowed=True,
                reason_code="allowed_by_execution_authority",
                approval_required=tool.requires_approval,
            )

        matching_allow: list[tuple[PolicyDefinition, PolicyRule]] = []
        matching_deny: list[tuple[PolicyDefinition, PolicyRule]] = []
        matched_ids: list[str] = []
        approval_required = tool.requires_approval
        inactive = [policy for policy in self.policies if policy.lifecycle.value not in {"active"}]
        if inactive:
            return PolicyDecision(
                **base,
                allowed=False,
                reason_code="policy_not_active",
                policy_id=inactive[0].policy_id,
                policy_version=inactive[0].version,
            )

        for policy in self.policies:
            matched = [
                rule
                for rule in policy.rules
                if _rule_matches(
                    rule,
                    principal=principal,
                    execution=execution,
                    tool=tool,
                    resource=resource,
                )
            ]
            matched_ids.extend(f"{policy.policy_id}:{rule.rule_id}" for rule in matched)
            for rule in matched:
                approval_required = approval_required or rule.requires_approval is True
                target = matching_allow if rule.effect == PolicyEffect.ALLOW else matching_deny
                target.append((policy, rule))
            if not matched:
                target = (
                    matching_allow if policy.default_effect == PolicyEffect.ALLOW else matching_deny
                )
                target.append((policy, _default_rule(policy)))

        if matching_deny:
            policy, rule = matching_deny[0]
            return PolicyDecision(
                **base,
                allowed=False,
                reason_code="policy_denied",
                policy_id=policy.policy_id,
                policy_version=policy.version,
                rule_id=rule.rule_id,
                matched_rule_ids=matched_ids,
                approval_required=approval_required,
            )
        policy, rule = matching_allow[0]
        return PolicyDecision(
            **base,
            allowed=True,
            reason_code="allowed_by_policy",
            policy_id=policy.policy_id,
            policy_version=policy.version,
            rule_id=rule.rule_id,
            matched_rule_ids=matched_ids,
            approval_required=approval_required,
        )

    def _evaluate_resource_hooks(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        resource: ResourceContext | None,
        base: dict[str, Any],
    ) -> PolicyDecision | None:
        approval_decision: PolicyDecision | None = None
        for index, hook in enumerate(self.resource_policy_hooks, start=1):
            try:
                result = _call_resource_hook(
                    hook,
                    principal=principal,
                    execution=execution,
                    tool=tool,
                    arguments=arguments,
                    resource=resource,
                )
            except Exception:  # noqa: BLE001 - resource policy is an extension boundary.
                return PolicyDecision(
                    **base,
                    allowed=False,
                    reason_code="resource_policy_error",
                    metadata={"resource_hook": str(index)},
                )
            if isinstance(result, PolicyDecision):
                decision = result.model_copy(
                    update={
                        **base,
                        "allowed": result.allowed,
                        "reason_code": result.reason_code,
                        "policy_id": result.policy_id,
                        "policy_version": result.policy_version,
                        "rule_id": result.rule_id,
                        "matched_rule_ids": result.matched_rule_ids,
                        "approval_required": result.approval_required,
                        "metadata": {
                            **result.metadata,
                            "resource_hook": str(index),
                        },
                    }
                )
            else:
                decision = PolicyDecision(
                    **base,
                    allowed=bool(result),
                    reason_code=("resource_policy_allowed" if result else "resource_policy_denied"),
                    metadata={"resource_hook": str(index)},
                )
            if not decision.allowed:
                return decision
            if decision.approval_required:
                approval_decision = approval_decision or decision
        return approval_decision

    def _environment_failure(
        self,
        execution: ExecutionContext,
        tool: ToolDefinition,
    ) -> str | None:
        constraint = self.environment_constraints.get(execution.environment)
        if self.environment_constraints and constraint is None:
            return "environment_not_configured"
        if constraint is not None:
            if (
                constraint.allowed_tool_ids is not None
                and tool.tool_id not in constraint.allowed_tool_ids
            ):
                return "environment_tool_not_allowed"
            if _risk_exceeds(tool.risk_level, constraint.max_risk_level):
                return "risk_exceeds_environment_limit"
        if self._mapping_denies(
            self.environment_tool_allowlists,
            execution.environment,
            tool.tool_id,
        ):
            return "environment_tool_not_allowed"
        configured_risk = self.max_risk_by_environment.get(execution.environment)
        if configured_risk is not None and _risk_exceeds(tool.risk_level, configured_risk):
            return "risk_exceeds_environment_limit"
        return None

    def _principal_permission_denies(
        self,
        principal: PrincipalContext,
        tool: ToolDefinition,
    ) -> bool:
        if self.principal_permissions is None:
            return False
        granted = self.principal_permissions.get(principal.principal_id)
        if granted is None:
            return bool(tool.required_permissions)
        return not set(tool.required_permissions).issubset(granted)

    def _decision_context(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        resource: ResourceContext | None,
    ) -> dict[str, Any]:
        return {
            "decision_id": self._id("policy_decision"),
            "principal_id": principal.principal_id,
            "tenant_id": principal.tenant_id,
            "agent_id": execution.agent_id,
            "tool_id": tool.tool_id,
            "environment": execution.environment,
            "risk_level": tool.risk_level,
            "resource_type": resource.resource_type if resource is not None else None,
            "resource_id": resource.resource_id if resource is not None else None,
        }

    def _finish(self, decision: PolicyDecision) -> PolicyDecision:
        with self._lock:
            self._decisions.append(decision.model_copy(deep=True))
        return decision

    @staticmethod
    def _mapping_denies(
        mapping: dict[str, frozenset[str]] | None,
        identity: str,
        tool_id: str,
    ) -> bool:
        if mapping is None:
            return False
        allowed = mapping.get(identity)
        return allowed is None or tool_id not in allowed


class DefaultPermissionBroker:
    """Deny-by-default broker backed by the declarative policy engine."""

    def __init__(
        self,
        *,
        policy_engine: PolicyEvaluator | None = None,
        policies: Sequence[PolicyDefinition] = (),
        principal_tool_permissions: Mapping[str, Iterable[str]] | None = None,
        principal_tool_allowlists: Mapping[str, Iterable[str]] | None = None,
        principal_permissions: Mapping[str, Iterable[str]] | None = None,
        agent_tool_allowlists: Mapping[str, Iterable[str]] | None = None,
        environment_constraints: Mapping[str, EnvironmentConstraint] | None = None,
        environment_tool_allowlists: Mapping[str, Iterable[str]] | None = None,
        max_risk_by_environment: Mapping[str, RiskLevel] | None = None,
        resource_policy_hooks: Sequence[ResourcePolicyHook] = (),
    ) -> None:
        if policy_engine is not None and any(
            _is_configured(value)
            for value in (
                policies,
                principal_tool_permissions,
                principal_tool_allowlists,
                principal_permissions,
                agent_tool_allowlists,
                environment_constraints,
                environment_tool_allowlists,
                max_risk_by_environment,
                resource_policy_hooks,
            )
        ):
            raise ValueError("policy_engine cannot be combined with policy configuration")
        self.policy_engine = policy_engine or DeclarativePolicyEngine(
            policies,
            principal_tool_permissions=principal_tool_permissions,
            principal_tool_allowlists=principal_tool_allowlists,
            principal_permissions=principal_permissions,
            agent_tool_allowlists=agent_tool_allowlists,
            environment_constraints=environment_constraints,
            environment_tool_allowlists=environment_tool_allowlists,
            max_risk_by_environment=max_risk_by_environment,
            resource_policy_hooks=resource_policy_hooks,
        )

    @property
    def decisions(self) -> list[PolicyDecision]:
        """Return policy records produced by the configured engine."""

        return (
            self.policy_engine.decisions
            if isinstance(self.policy_engine, DeclarativePolicyEngine)
            else []
        )

    def authorize(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        resource: ResourceContext | None = None,
    ) -> PermissionDecision:
        """Return the final pre-handler permission decision."""

        policy = self.policy_engine.evaluate(
            principal=principal,
            execution=execution,
            tool=tool,
            arguments=arguments,
            resource=resource,
        )
        if not policy.allowed:
            return PermissionDecision(
                allowed=False,
                principal_id=principal.principal_id,
                tenant_id=principal.tenant_id,
                tool_id=tool.tool_id,
                reason_code=policy.reason_code,
                approval_required=False,
                agent_id=execution.agent_id,
                environment=execution.environment,
                risk_level=tool.risk_level,
                policy_decision=policy,
            )

        if policy.approval_required:
            approved = tool.action_digest(arguments) in execution.approved_action_digests
            if not approved:
                return PermissionDecision(
                    allowed=False,
                    principal_id=principal.principal_id,
                    tenant_id=principal.tenant_id,
                    tool_id=tool.tool_id,
                    reason_code="approval_required",
                    approval_required=True,
                    agent_id=execution.agent_id,
                    environment=execution.environment,
                    risk_level=tool.risk_level,
                    policy_decision=policy,
                )

        return PermissionDecision(
            allowed=True,
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
            tool_id=tool.tool_id,
            reason_code=(
                "allowed_by_exact_approval" if policy.approval_required else policy.reason_code
            ),
            approval_required=False,
            agent_id=execution.agent_id,
            environment=execution.environment,
            risk_level=tool.risk_level,
            policy_decision=policy,
        )


PolicyEngine = DeclarativePolicyEngine


def _normalize_mapping(
    value: Mapping[str, Iterable[str]] | None,
) -> dict[str, frozenset[str]] | None:
    if value is None:
        return None
    return {key: frozenset(items) for key, items in value.items()}


def _is_configured(value: object) -> bool:
    """Return whether optional policy configuration contains a value."""

    return value is not None and bool(value)


def _risk_exceeds(actual: RiskLevel, maximum: RiskLevel) -> bool:
    order = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }
    return order[actual] > order[maximum]


def _rule_matches(
    rule: PolicyRule,
    *,
    principal: PrincipalContext,
    execution: ExecutionContext,
    tool: ToolDefinition,
    resource: ResourceContext | None,
) -> bool:
    if rule.tool_ids and tool.tool_id not in rule.tool_ids:
        return False
    if rule.agent_ids and execution.agent_id not in rule.agent_ids:
        return False
    if rule.principal_ids and principal.principal_id not in rule.principal_ids:
        return False
    if rule.tenant_ids and principal.tenant_id not in rule.tenant_ids:
        return False
    if rule.required_permissions and not set(rule.required_permissions).issubset(
        execution.granted_permissions
    ):
        return False
    if rule.environments and execution.environment not in rule.environments:
        return False
    if rule.risk_levels and tool.risk_level not in rule.risk_levels:
        return False
    if rule.resource_types and (
        resource is None or resource.resource_type not in rule.resource_types
    ):
        return False
    if rule.resource_ids and (resource is None or resource.resource_id not in rule.resource_ids):
        return False
    return not (rule.requires_approval is False and tool.requires_approval)


def _default_rule(policy: PolicyDefinition) -> PolicyRule:
    return PolicyRule(rule_id=f"default:{policy.policy_id}", effect=policy.default_effect)


def _call_resource_hook(
    hook: ResourcePolicyHook,
    *,
    principal: PrincipalContext,
    execution: ExecutionContext,
    tool: ToolDefinition,
    arguments: dict[str, Any],
    resource: ResourceContext | None,
) -> PolicyDecision | bool:
    """Call a hook with keyword support and a small compatibility adapter."""

    values = {
        "principal": principal,
        "execution": execution,
        "tool": tool,
        "arguments": arguments,
        "resource": resource,
    }
    try:
        parameters = signature(hook).parameters
    except (TypeError, ValueError):
        return hook(**values)
    if any(parameter.kind is Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return hook(**values)
    keyword_values = {name: value for name, value in values.items() if name in parameters}
    if keyword_values:
        return hook(**keyword_values)
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind in {Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD}
    ]
    if len(positional) == 1:
        return hook(resource)
    return hook(principal, execution, tool, arguments, resource)


__all__ = [
    "DeclarativePolicyEngine",
    "DefaultPermissionBroker",
    "EnvironmentConstraint",
    "PermissionBroker",
    "PolicyEngine",
    "PolicyEvaluator",
    "ResourcePolicyHook",
]
