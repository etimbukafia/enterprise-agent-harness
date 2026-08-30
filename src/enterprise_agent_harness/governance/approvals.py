"""Application-owned approval policy and decision boundaries."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol
from uuid import uuid4

from ..contracts import (
    ActionProposal,
    AgentLifecycleStatus,
    ApprovalDecision,
    ApprovalDecisionStatus,
    ApprovalPolicy,
    ApprovalPolicyDecision,
    ApprovalPolicyRule,
    ApprovalRequest,
    ExecutionContext,
    PrincipalContext,
)
from ..tools.definitions import ToolDefinition


class ApprovalPolicyEvaluator(Protocol):
    """Application boundary that determines whether a call needs approval."""

    def evaluate(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        action: ActionProposal,
    ) -> ApprovalPolicyDecision:
        """Return the immutable approval requirement for one exact action."""


class ApprovalBroker(Protocol):
    """Application boundary that stores and resolves approval requests."""

    def submit(self, request: ApprovalRequest) -> ApprovalDecision | None:
        """Register a request and return an existing decision when available."""

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Return a submitted request by ID."""

    def get_decision(self, request_id: str) -> ApprovalDecision | None:
        """Return the current decision for a request, if one exists."""


class DeclarativeApprovalPolicyEngine:
    """Evaluate versioned approval rules without reducing trusted authority."""

    def __init__(
        self,
        policies: Sequence[ApprovalPolicy] = (),
        *,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self.policies = tuple(policies)
        policy_keys = [(policy.policy_id, policy.version) for policy in self.policies]
        if len(policy_keys) != len(set(policy_keys)):
            raise ValueError("approval policy identity and version pairs must be unique")
        self._id = id_factory or (lambda prefix: f"{prefix}_{uuid4().hex[:10]}")
        self._decisions: list[ApprovalPolicyDecision] = []
        self._lock = RLock()

    @property
    def decisions(self) -> list[ApprovalPolicyDecision]:
        """Return a copy of approval policy decisions made by this engine."""

        with self._lock:
            return deepcopy(self._decisions)

    def evaluate(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        action: ActionProposal,
    ) -> ApprovalPolicyDecision:
        """Evaluate all active rules and keep the most restrictive requirement."""

        del principal
        required = False
        selected_policy: ApprovalPolicy | None = None
        matched_rule_ids: list[str] = []
        expiry_values: list[float] = []

        for policy in self.policies:
            if policy.lifecycle != AgentLifecycleStatus.ACTIVE:
                continue
            policy_required = policy.default_requires_approval
            policy_rule_ids: list[str] = []
            policy_expiry_values: list[float] = []
            for rule in policy.rules:
                if not _rule_matches(rule, execution=execution, tool=tool, action=action):
                    continue
                if rule.requires_approval:
                    policy_required = True
                    policy_rule_ids.append(rule.rule_id)
                    if rule.expiry_seconds is not None:
                        policy_expiry_values.append(rule.expiry_seconds)
            if policy_required:
                required = True
                if selected_policy is None:
                    selected_policy = policy
                matched_rule_ids.extend(policy_rule_ids)
                if policy_expiry_values:
                    expiry_values.extend(policy_expiry_values)
                elif policy.default_expiry_seconds is not None:
                    expiry_values.append(policy.default_expiry_seconds)

        decision = ApprovalPolicyDecision(
            decision_id=self._id("approval_policy_decision"),
            required=required,
            reason_code=("approval_required_by_policy" if required else "no_approval_policy_match"),
            policy_id=selected_policy.policy_id if selected_policy is not None else None,
            policy_version=selected_policy.version if selected_policy is not None else None,
            matched_rule_ids=matched_rule_ids,
            expiry_seconds=min(expiry_values) if expiry_values else None,
        )
        with self._lock:
            self._decisions.append(decision.model_copy(deep=True))
        return decision


class InMemoryApprovalBroker:
    """Thread-safe approval broker for local runs and deterministic tests."""

    def __init__(
        self,
        *,
        policy_engine: ApprovalPolicyEvaluator | None = None,
        policies: Sequence[ApprovalPolicy] = (),
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        if policy_engine is not None and policies:
            raise ValueError("policy_engine cannot be combined with policies")
        self.policy_engine = policy_engine or DeclarativeApprovalPolicyEngine(policies)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id = id_factory or (lambda prefix: f"{prefix}_{uuid4().hex[:10]}")
        self._requests: dict[str, ApprovalRequest] = {}
        self._decisions: dict[str, ApprovalDecision] = {}
        self._lock = RLock()

    @property
    def requests(self) -> list[ApprovalRequest]:
        """Return submitted requests in insertion order."""

        with self._lock:
            return deepcopy(list(self._requests.values()))

    @property
    def decisions(self) -> list[ApprovalDecision]:
        """Return recorded decisions in insertion order."""

        with self._lock:
            return deepcopy(list(self._decisions.values()))

    @property
    def pending_requests(self) -> list[ApprovalRequest]:
        """Return requests that still need a decision."""

        with self._lock:
            return deepcopy(
                [
                    request
                    for request_id, request in self._requests.items()
                    if self._decision_locked(request_id) is None
                ]
            )

    def submit(self, request: ApprovalRequest) -> ApprovalDecision | None:
        """Store an exact request and keep duplicate submissions idempotent."""

        with self._lock:
            existing = self._requests.get(request.request_id)
            if existing is not None and existing != request:
                raise ValueError("approval request ID cannot be reused for a different action")
            if existing is None:
                self._requests[request.request_id] = request.model_copy(deep=True)
            return deepcopy(self._decision_locked(request.request_id))

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Return a copy of one exact request."""

        with self._lock:
            request = self._requests.get(request_id)
            return deepcopy(request)

    def get_decision(self, request_id: str) -> ApprovalDecision | None:
        """Return a decision and materialize expiry for an unanswered request."""

        with self._lock:
            return deepcopy(self._decision_locked(request_id))

    def decide(
        self,
        request_id: str,
        *,
        decision: ApprovalDecisionStatus | str,
        decided_by: str,
        reason_code: str | None = None,
        expires_at: datetime | None = None,
    ) -> ApprovalDecision:
        """Record one immutable approve, reject, or request-change decision."""

        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                raise KeyError(f"unknown approval request: {request_id}")
            existing = self._decision_locked(request_id)
            if existing is not None:
                return deepcopy(existing)
            status = _normalize_decision_status(decision)
            now = self._clock()
            if _expired(request.expires_at, now):
                return deepcopy(self._expire_locked(request_id, request))
            resolved_expiry = expires_at
            if status == ApprovalDecisionStatus.APPROVED:
                if resolved_expiry is None:
                    resolved_expiry = request.expires_at
                if request.expires_at is not None and (
                    resolved_expiry is None or resolved_expiry > request.expires_at
                ):
                    raise ValueError("approval expiry cannot exceed request expiry")
                if _expired(resolved_expiry, now):
                    return deepcopy(self._expire_locked(request_id, request))
            result = ApprovalDecision(
                approval_id=self._id("approval"),
                request_id=request.request_id,
                action_digest=request.action_digest,
                approved=status == ApprovalDecisionStatus.APPROVED,
                decision=status,
                decided_by=decided_by,
                reason_code=reason_code or _default_reason(status),
                expires_at=resolved_expiry,
                decided_at=now,
            )
            self._decisions[request_id] = result
            return deepcopy(result)

    def record_decision(self, decision: ApprovalDecision) -> ApprovalDecision:
        """Record externally created evidence after checking its exact request."""

        with self._lock:
            request = self._requests.get(decision.request_id or "")
            if request is None:
                raise KeyError("approval decision must reference a submitted request")
            self._validate_decision_against_request(request, decision)
            existing = self._decision_locked(request.request_id)
            if existing is not None:
                return deepcopy(existing)
            self._decisions[request.request_id] = decision.model_copy(deep=True)
            return deepcopy(decision)

    def approve(
        self,
        request_id: str,
        *,
        decided_by: str,
        reason_code: str = "approved",
        expires_at: datetime | None = None,
    ) -> ApprovalDecision:
        """Convenience method for an approving reviewer."""

        return self.decide(
            request_id,
            decision=ApprovalDecisionStatus.APPROVED,
            decided_by=decided_by,
            reason_code=reason_code,
            expires_at=expires_at,
        )

    def reject(
        self,
        request_id: str,
        *,
        decided_by: str,
        reason_code: str = "rejected",
    ) -> ApprovalDecision:
        """Convenience method for a rejecting reviewer."""

        return self.decide(
            request_id,
            decision=ApprovalDecisionStatus.REJECTED,
            decided_by=decided_by,
            reason_code=reason_code,
        )

    def request_changes(
        self,
        request_id: str,
        *,
        decided_by: str,
        reason_code: str = "changes_requested",
    ) -> ApprovalDecision:
        """Convenience method for a reviewer who needs a changed proposal."""

        return self.decide(
            request_id,
            decision=ApprovalDecisionStatus.REQUEST_CHANGES,
            decided_by=decided_by,
            reason_code=reason_code,
        )

    def _decision_locked(self, request_id: str) -> ApprovalDecision | None:
        existing = self._decisions.get(request_id)
        if existing is not None:
            return existing
        request = self._requests.get(request_id)
        if request is not None and _expired(request.expires_at, self._clock()):
            return self._expire_locked(request_id, request)
        return None

    def _expire_locked(self, request_id: str, request: ApprovalRequest) -> ApprovalDecision:
        result = ApprovalDecision(
            approval_id=self._id("approval"),
            request_id=request.request_id,
            action_digest=request.action_digest,
            approved=False,
            decision=ApprovalDecisionStatus.EXPIRED,
            decided_by="system",
            reason_code="approval_expired",
            expires_at=request.expires_at,
            decided_at=self._clock(),
        )
        self._decisions[request_id] = result
        return result

    @staticmethod
    def _validate_decision_against_request(
        request: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> None:
        if decision.request_id != request.request_id:
            raise ValueError("approval decision request ID does not match")
        if decision.action_digest != request.action_digest:
            raise ValueError("approval decision action digest does not match")
        if (
            decision.status == ApprovalDecisionStatus.APPROVED
            and request.expires_at is not None
            and decision.expires_at is not None
            and decision.expires_at > request.expires_at
        ):
            raise ValueError("approval expiry cannot exceed request expiry")


DefaultApprovalBroker = InMemoryApprovalBroker


def _rule_matches(
    rule: ApprovalPolicyRule,
    *,
    execution: ExecutionContext,
    tool: ToolDefinition,
    action: ActionProposal,
) -> bool:
    if rule.tool_ids and tool.tool_id not in rule.tool_ids:
        return False
    if rule.action_ids and action.action_id not in rule.action_ids:
        return False
    if rule.action_kinds and tool.kind not in rule.action_kinds:
        return False
    if rule.risk_levels and tool.risk_level not in rule.risk_levels:
        return False
    return not (rule.environments and execution.environment not in rule.environments)


def _normalize_decision_status(value: ApprovalDecisionStatus | str) -> ApprovalDecisionStatus:
    if isinstance(value, ApprovalDecisionStatus):
        return value
    aliases = {
        "approve": ApprovalDecisionStatus.APPROVED,
        "approved": ApprovalDecisionStatus.APPROVED,
        "reject": ApprovalDecisionStatus.REJECTED,
        "rejected": ApprovalDecisionStatus.REJECTED,
        "request_change": ApprovalDecisionStatus.REQUEST_CHANGES,
        "request_changes": ApprovalDecisionStatus.REQUEST_CHANGES,
        "requested_changes": ApprovalDecisionStatus.REQUEST_CHANGES,
        "expired": ApprovalDecisionStatus.EXPIRED,
    }
    try:
        return aliases[value.lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported approval decision: {value}") from exc


def _default_reason(status: ApprovalDecisionStatus) -> str:
    return {
        ApprovalDecisionStatus.APPROVED: "approved",
        ApprovalDecisionStatus.REJECTED: "rejected",
        ApprovalDecisionStatus.REQUEST_CHANGES: "changes_requested",
        ApprovalDecisionStatus.EXPIRED: "approval_expired",
    }[status]


def _expired(value: datetime | None, now: datetime) -> bool:
    return value is not None and now >= value


__all__ = [
    "ApprovalBroker",
    "ApprovalPolicyEvaluator",
    "DeclarativeApprovalPolicyEngine",
    "DefaultApprovalBroker",
    "InMemoryApprovalBroker",
]
