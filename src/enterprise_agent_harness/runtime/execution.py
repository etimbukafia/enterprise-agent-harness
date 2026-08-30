"""Bounded provider-to-tool execution for general agent workflows."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from inspect import Parameter, signature
from threading import RLock
from typing import cast
from uuid import uuid4

from ..capabilities import CapabilityDefinition
from ..contracts import (
    ActionProposal,
    AgentOutcome,
    AgentPlan,
    ApprovalDecision,
    ApprovalDecisionStatus,
    ApprovalPolicyDecision,
    ApprovalRequest,
    CompiledContext,
    ExecutionCheckpoint,
    ExecutionContext,
    ExecutionState,
    ExecutionStateStatus,
    OutcomeProposal,
    OutcomeStatus,
    PermissionDecision,
    PlanStep,
    PolicyDecision,
    PrincipalContext,
    RecoveryAction,
    ResourceContext,
    RiskLevel,
    RuntimeConfig,
    SafetyFlag,
    ToolCall,
    ToolCallRecord,
    ToolExecutionRecord,
    ToolResult,
    ToolResultStatus,
    VerificationResult,
)
from ..errors import (
    ExecutionCancelledError,
    ExecutionTimeoutError,
    HarnessError,
    ProviderOutputError,
    ProviderTimeoutError,
)
from ..evaluation.contracts import RunTrace, TraceEvent
from ..governance.approvals import (
    ApprovalBroker,
    ApprovalPolicyEvaluator,
)
from ..governance.permissions import DefaultPermissionBroker, PermissionBroker
from ..governance.safety import SafetyDecision, SafetyPolicy, indirect_injection_matches
from ..memory.strategies import MemoryStrategy
from ..observability.audit import AuditLogger, AuditSink, ListAuditSink
from ..observability.tracing import ListTraceSink, TraceRecorder, TraceSink, digest_mapping
from ..providers.base import ProviderAdapter
from ..providers.contracts import (
    CompositionRequest,
    CompositionResponse,
    InterpretationRequest,
    InterpretationResponse,
    PlanningRequest,
    PlanningResponse,
    ProviderOperation,
)
from ..providers.invocation import (
    DefaultProviderCallPolicy,
    ProviderCallPolicy,
    ProviderInvocationResult,
    invoke_provider_call,
)
from ..providers.normalization import (
    normalize_composition,
    normalize_interpretation,
    normalize_plan,
)
from ..state.store import InMemoryStateStore, StateConflictError, StateStore
from ..tools.definitions import ToolDefinition, ToolInvocationError
from ..tools.registry import ToolRegistry
from ..verification.outcomes import verify_outcome
from .context import ContextCompiler
from .control import CancellationSignal, ExecutionControl


@dataclass
class _ActiveExecution:
    """State needed to close a run when a control interrupts it."""

    principal: PrincipalContext
    execution: ExecutionContext
    state: ExecutionState
    trace: TraceRecorder
    tool_calls: list[ToolCallRecord]
    current_step: PlanStep | None = None
    current_tool: ToolDefinition | None = None


@dataclass
class _PendingApproval:
    """In-memory continuation data for one paused approval gate."""

    principal: PrincipalContext
    execution: ExecutionContext
    input_text: str
    resource: ResourceContext | None
    plan: AgentPlan
    resume_plan: AgentPlan
    tool_calls: list[ToolCallRecord]
    tool_results: list[ToolResult]
    highest_risk: RiskLevel
    request: ApprovalRequest
    outcome: AgentOutcome
    trace: TraceRecorder
    trace_prefix: RunTrace | None = None
    trace_prefix_event_count: int = 0


class _ControlledProviderCallPolicy:
    """Apply the run retry budget to an application provider policy."""

    def __init__(self, delegate: ProviderCallPolicy, control: ExecutionControl) -> None:
        self._delegate = delegate
        self._control = control

    def timeout_seconds(self, operation: ProviderOperation) -> float | None:
        """Return the provider's per-call timeout."""

        return self._delegate.timeout_seconds(operation)

    def max_attempts(self, operation: ProviderOperation) -> int:
        """Return the provider's configured attempt ceiling."""

        return self._delegate.max_attempts(operation)

    def should_retry(
        self,
        *,
        operation: ProviderOperation,
        error: BaseException,
        attempt: int,
    ) -> bool:
        """Retry only when both policies and the run budget allow it."""

        if not self._delegate.should_retry(operation=operation, error=error, attempt=attempt):
            return False
        return self._control.admit_retry()

    def backoff_seconds(
        self,
        *,
        operation: ProviderOperation,
        error: BaseException,
        attempt: int,
    ) -> float:
        """Return the provider policy's retry delay."""

        return self._delegate.backoff_seconds(operation=operation, error=error, attempt=attempt)


class AgentRuntime:
    """Coordinate one bounded execution while retaining application authority.

    A provider can propose plan steps and an outcome summary. It cannot set the
    principal, add tools, approve actions, or choose the final outcome state.
    """

    def __init__(
        self,
        *,
        tools: ToolRegistry,
        provider: ProviderAdapter,
        capabilities: Sequence[CapabilityDefinition] = (),
        state_store: StateStore | None = None,
        memory: MemoryStrategy | None = None,
        permission_broker: PermissionBroker | None = None,
        approval_broker: ApprovalBroker | None = None,
        approval_policy: ApprovalPolicyEvaluator | None = None,
        safety_policy: SafetyPolicy | None = None,
        trace_sink: TraceSink | None = None,
        audit_sink: AuditSink | None = None,
        config: RuntimeConfig | None = None,
        provider_call_policy: ProviderCallPolicy | None = None,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.tools = tools
        self.provider = provider
        self.capabilities = tuple(capabilities)
        self._capability_tool_ids = frozenset(
            tool_id for capability in self.capabilities for tool_id in capability.allowed_tool_ids
        )
        self.state_store = state_store or InMemoryStateStore()
        self.memory = memory
        self.permission_broker = permission_broker or DefaultPermissionBroker()
        self.approval_broker = approval_broker
        self.approval_policy: ApprovalPolicyEvaluator | None = approval_policy
        if (
            self.approval_policy is None
            and approval_broker is not None
            and hasattr(approval_broker, "policy_engine")
        ):
            self.approval_policy = cast(
                ApprovalPolicyEvaluator,
                getattr(approval_broker, "policy_engine"),  # noqa: B009
            )
        self.config = config or RuntimeConfig()
        self.provider_call_policy = provider_call_policy or DefaultProviderCallPolicy(
            timeout_seconds_value=self.config.provider_timeout_seconds,
            max_attempts_value=self.config.provider_max_attempts,
            retry_backoff_seconds_value=self.config.provider_retry_backoff_seconds,
        )
        self.safety_policy = safety_policy or SafetyPolicy(self.config)
        self.trace_sink = trace_sink or ListTraceSink()
        self.audit_sink = audit_sink or ListAuditSink()
        self._id = id_factory or (lambda prefix: f"{prefix}_{uuid4().hex[:10]}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self.audit_logger = AuditLogger(
            sink=self.audit_sink,
            id_factory=self._id,
            clock=self._clock,
        )
        self._traces: dict[str, RunTrace] = {}
        self._trace_lock = RLock()
        self._active: dict[str, _ActiveExecution] = {}
        self._active_lock = RLock()
        self._pending_approvals: dict[str, _PendingApproval] = {}
        self._pending_lock = RLock()

    def execute(
        self,
        principal: PrincipalContext,
        input_text: str,
        *,
        agent_id: str = "agent",
        agent_version: str = "1.0.0",
        authorized_tool_ids: Sequence[str] = (),
        granted_permissions: Sequence[str] = (),
        approved_action_digests: Sequence[str] = (),
        state_id: str | None = None,
        execution_id: str | None = None,
        environment: str | None = None,
        max_risk_level: RiskLevel | None = None,
        resource: ResourceContext | None = None,
        timeout_seconds: float | None = None,
        cancellation_event: CancellationSignal | None = None,
        cancel_event: CancellationSignal | None = None,
        _plan_override: AgentPlan | None = None,
        _composition_plan_override: AgentPlan | None = None,
        _initial_tool_calls: Sequence[ToolCallRecord] = (),
        _initial_tool_results: Sequence[ToolResult] = (),
        _initial_highest_risk: RiskLevel = RiskLevel.LOW,
    ) -> AgentOutcome:
        """Execute one bounded run with cooperative timeout and cancellation."""

        if cancellation_event is not None and cancel_event is not None:
            raise ValueError("provide only one cancellation signal")
        control = ExecutionControl(
            timeout_seconds=(
                self.config.execution_timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
            max_retries=self.config.max_retries,
            cancellation_signal=cancellation_event or cancel_event,
        )
        try:
            return self._execute(
                principal,
                input_text,
                agent_id=agent_id,
                agent_version=agent_version,
                authorized_tool_ids=authorized_tool_ids,
                granted_permissions=granted_permissions,
                approved_action_digests=approved_action_digests,
                state_id=state_id,
                execution_id=execution_id,
                environment=environment,
                max_risk_level=max_risk_level,
                resource=resource,
                control=control,
                _plan_override=_plan_override,
                _composition_plan_override=_composition_plan_override,
                _initial_tool_calls=_initial_tool_calls,
                _initial_tool_results=_initial_tool_results,
                _initial_highest_risk=_initial_highest_risk,
            )
        except (ExecutionTimeoutError, ExecutionCancelledError) as exc:
            with self._active_lock:
                active = self._active.get(control.execution_id or "")
                if active is None:
                    raise
            return self._control_failure(active=active, control_error=exc)
        finally:
            if control.execution_id is not None:
                with self._active_lock:
                    self._active.pop(control.execution_id, None)

    def approval_request_for(self, execution_id: str) -> ApprovalRequest | None:
        """Return the exact pending approval request for an execution."""

        with self._pending_lock:
            pending = self._pending_approvals.get(execution_id)
            return pending.request.model_copy(deep=True) if pending is not None else None

    def pending_approval(self, execution_id: str) -> ApprovalRequest | None:
        """Alias for :meth:`approval_request_for` used by integrations."""

        return self.approval_request_for(execution_id)

    def resume(
        self,
        execution_id: str,
        approval_decision: ApprovalDecision | None = None,
        *,
        approval: ApprovalDecision | None = None,
        principal: PrincipalContext | None = None,
    ) -> AgentOutcome:
        """Resume a paused execution after its exact request is decided."""

        if approval_decision is not None and approval is not None:
            raise ValueError("provide only one approval decision")
        with self._pending_lock:
            pending = self._pending_approvals.get(execution_id)
        if pending is None and principal is not None:
            pending = self._restore_pending_approval(principal, execution_id)
        if pending is None:
            raise KeyError(f"unknown or non-paused execution: {execution_id}")
        if principal is not None and pending.principal != principal:
            raise ValueError("resume principal does not own the execution")

        decision = approval_decision or approval
        if decision is None and self.approval_broker is not None:
            raw_decision = self.approval_broker.get_decision(pending.request.request_id)
            decision = (
                ApprovalDecision.model_validate(raw_decision) if raw_decision is not None else None
            )
        if decision is None:
            return pending.outcome.model_copy(deep=True)

        status, error_code = self._validate_approval_decision(pending.request, decision)
        if error_code is not None:
            return self._close_pending_approval(
                pending=pending,
                status=OutcomeStatus.REFUSED,
                summary="The approval evidence does not match the reviewed action.",
                error_code=error_code,
                event_type="approval_stale",
                decision=decision,
            )
        if status == ApprovalDecisionStatus.REJECTED:
            return self._close_pending_approval(
                pending=pending,
                status=OutcomeStatus.REFUSED,
                summary="The reviewer rejected the proposed action.",
                error_code="approval_rejected",
                event_type="approval_rejected",
                decision=decision,
            )
        if status == ApprovalDecisionStatus.REQUEST_CHANGES:
            return self._close_pending_approval(
                pending=pending,
                status=OutcomeStatus.NEEDS_INPUT,
                summary="The reviewer requested changes before the action can run.",
                error_code="approval_changes_requested",
                event_type="approval_changes_requested",
                decision=decision,
            )
        if status == ApprovalDecisionStatus.EXPIRED:
            return self._close_pending_approval(
                pending=pending,
                status=OutcomeStatus.ESCALATED,
                summary="The approval expired before the action could run.",
                error_code="approval_expired",
                event_type="approval_expired",
                decision=decision,
                recovery=RecoveryAction.ESCALATE,
                human_review_required=True,
            )

        pending.trace.record(
            stage="approval",
            event_type="approval_approved",
            metadata=self._approval_metadata(pending.request, decision),
        )
        self._audit(
            event_type="approval_approved",
            principal=pending.principal,
            execution=pending.execution,
            tool_ids=[pending.request.action.tool_call.tool_id],
            metadata=self._approval_metadata(pending.request, decision),
        )
        with self._pending_lock:
            self._pending_approvals.pop(execution_id, None)
        try:
            outcome = self.execute(
                pending.principal,
                pending.input_text,
                agent_id=pending.execution.agent_id,
                agent_version=pending.execution.agent_version,
                authorized_tool_ids=pending.execution.authorized_tool_ids,
                granted_permissions=pending.execution.granted_permissions,
                approved_action_digests=(
                    *pending.execution.approved_action_digests,
                    pending.request.action_digest,
                ),
                state_id=pending.execution.state_id,
                execution_id=pending.execution.execution_id,
                environment=pending.execution.environment,
                max_risk_level=pending.execution.max_risk_level,
                resource=pending.resource,
                _plan_override=pending.resume_plan,
                _composition_plan_override=pending.plan,
                _initial_tool_calls=pending.tool_calls,
                _initial_tool_results=pending.tool_results,
                _initial_highest_risk=pending.highest_risk,
            )
        except Exception:
            with self._pending_lock:
                self._pending_approvals[execution_id] = pending
            raise
        return self._merge_resumed_trace(pending, outcome)

    def _execute(
        self,
        principal: PrincipalContext,
        input_text: str,
        *,
        agent_id: str = "agent",
        agent_version: str = "1.0.0",
        authorized_tool_ids: Sequence[str] = (),
        granted_permissions: Sequence[str] = (),
        approved_action_digests: Sequence[str] = (),
        state_id: str | None = None,
        execution_id: str | None = None,
        environment: str | None = None,
        max_risk_level: RiskLevel | None = None,
        resource: ResourceContext | None = None,
        control: ExecutionControl,
        _plan_override: AgentPlan | None = None,
        _composition_plan_override: AgentPlan | None = None,
        _initial_tool_calls: Sequence[ToolCallRecord] = (),
        _initial_tool_results: Sequence[ToolResult] = (),
        _initial_highest_risk: RiskLevel = RiskLevel.LOW,
    ) -> AgentOutcome:
        """Execute one caller-supplied input through the bounded runtime."""

        text = input_text.strip()
        if not text:
            raise ValueError("input_text must not be empty")

        state = self.state_store.get_or_create(
            principal,
            agent_id=agent_id,
            agent_version=agent_version,
            state_id=state_id,
        )
        resolved_execution_id = execution_id or self._id("execution")
        with self._pending_lock:
            if resolved_execution_id in self._pending_approvals:
                raise ValueError("execution has a pending approval; call resume instead of execute")
        control.bind_execution(resolved_execution_id)
        execution = ExecutionContext(
            execution_id=resolved_execution_id,
            agent_id=agent_id,
            agent_version=agent_version,
            principal=principal,
            authorized_tool_ids=tuple(authorized_tool_ids),
            granted_permissions=tuple(granted_permissions),
            approved_action_digests=tuple(approved_action_digests),
            max_steps=self.config.max_plan_steps,
            state_id=state.state_id,
            environment=environment or self.config.environment,
            max_risk_level=max_risk_level or self.config.max_risk_level,
        )
        trace = TraceRecorder(
            execution=execution,
            input_text=text,
            sink=self.trace_sink,
            id_factory=self._id,
            clock=self._clock,
        )
        tool_calls: list[ToolCallRecord] = [
            call.model_copy(deep=True) for call in _initial_tool_calls
        ]
        tool_results: list[ToolResult] = [
            result.model_copy(deep=True) for result in _initial_tool_results
        ]
        with self._active_lock:
            self._active[resolved_execution_id] = _ActiveExecution(
                principal=principal,
                execution=execution,
                state=state,
                trace=trace,
                tool_calls=tool_calls,
            )
        trace.record(
            stage="runtime",
            event_type="execution_started",
            metadata={"input_length": str(len(text))},
        )
        self._audit(
            event_type="execution_started",
            principal=principal,
            execution=execution,
            metadata={"input_length": str(len(text))},
        )

        control.check()
        input_decision = self.safety_policy.inspect_input(text)
        if input_decision is not None:
            trace.record(
                stage="safety",
                event_type="direct_injection_refused",
                metadata={"reason_code": input_decision.escalation_code or "direct_injection"},
            )
            outcome = self._decision_outcome(
                execution=execution,
                status=input_decision.status,
                summary="The input is outside the runtime safety boundary.",
                flags=list(input_decision.flags),
                recovery=RecoveryAction.REFUSE,
                human_review_required=input_decision.human_review_required,
                error_code=input_decision.escalation_code,
            )
            return self._finish(
                principal=principal,
                execution=execution,
                state=state,
                outcome=outcome,
                trace=trace,
                save_state=False,
            )

        context = self._compile_context(principal, execution, state, text)
        trace.record(
            stage="context",
            event_type="context_compiled",
            metadata={
                "trusted_block_count": str(len(context.trusted_blocks)),
                "untrusted_block_count": str(len(context.untrusted_blocks)),
                "dropped_block_count": str(len(context.dropped_block_ids)),
            },
        )
        interpretation: InterpretationResponse | None = None
        if _plan_override is None:
            interpretation_fn = getattr(self.provider, "interpret", None)
            if callable(interpretation_fn):
                interpretation_request = InterpretationRequest(
                    request_id=self._id("provider_request"),
                    context=context,
                    execution=execution,
                    capabilities=[
                        capability.model_copy(deep=True) for capability in self.capabilities
                    ],
                    tools=self.tools.descriptors(self._visible_tool_ids(execution)),
                )
                trace.record(
                    stage="interpretation",
                    event_type="provider_call_started",
                    metadata={"operation": ProviderOperation.INTERPRET.value},
                )
                try:
                    invocation = self._invoke_provider(
                        operation=ProviderOperation.INTERPRET,
                        call=lambda: cast(Callable[..., object], interpretation_fn)(
                            request=interpretation_request
                        ),
                        control=control,
                    )
                    interpretation = normalize_interpretation(invocation.value)
                    interpretation = cast(
                        InterpretationResponse,
                        _with_invocation_metadata(interpretation, invocation),
                    )
                    control.check()
                    trace.record_provider_call(
                        operation=ProviderOperation.INTERPRET,
                        metadata=interpretation.metadata,
                    )
                    trace.record(
                        stage="interpretation",
                        event_type="provider_call_completed",
                        metadata={"operation": ProviderOperation.INTERPRET.value},
                    )
                except Exception as exc:  # noqa: BLE001 - provider code is an extension boundary.
                    return self._provider_failure(
                        principal=principal,
                        execution=execution,
                        state=state,
                        trace=trace,
                        operation=ProviderOperation.INTERPRET,
                        error=exc,
                        control=control,
                    )

            planning_request = PlanningRequest(
                request_id=self._id("provider_request"),
                context=context,
                execution=execution,
                capabilities=[capability.model_copy(deep=True) for capability in self.capabilities],
                tools=self.tools.descriptors(self._visible_tool_ids(execution)),
                interpretation=interpretation,
            )
            try:
                trace.record(
                    stage="planning",
                    event_type="provider_call_started",
                    metadata={"operation": ProviderOperation.PLAN.value},
                )
                invocation = self._invoke_provider(
                    operation=ProviderOperation.PLAN,
                    call=lambda: self._call_provider_operation(
                        method=cast(Callable[..., object], self.provider.plan),
                        request=planning_request,
                        legacy_kwargs={
                            "context": planning_request.context,
                            "execution": planning_request.execution,
                            "capabilities": planning_request.capabilities,
                            "tools": planning_request.tools,
                        },
                    ),
                    control=control,
                )
                planning_response = normalize_plan(invocation.value)
                planning_response = cast(
                    PlanningResponse,
                    _with_invocation_metadata(planning_response, invocation),
                )
                control.check()
                trace.record_provider_call(
                    operation=ProviderOperation.PLAN,
                    metadata=planning_response.metadata,
                )
                plan = planning_response.plan
                trace.record(
                    stage="planning",
                    event_type="provider_call_completed",
                    metadata={"operation": ProviderOperation.PLAN.value},
                )
            except Exception as exc:  # noqa: BLE001 - provider code is an extension boundary.
                return self._provider_failure(
                    principal=principal,
                    execution=execution,
                    state=state,
                    trace=trace,
                    operation=ProviderOperation.PLAN,
                    error=exc,
                    control=control,
                )
            composition_plan = plan.model_copy(deep=True)
        else:
            plan = _plan_override.model_copy(deep=True)
            composition_plan = (_composition_plan_override or plan).model_copy(deep=True)
            trace.record(
                stage="planning",
                event_type="plan_resumed",
                metadata={"step_count": str(len(plan.steps))},
            )

        try:
            self._validate_plan(plan)
        except ToolInvocationError as exc:
            trace.record(
                stage="planning",
                event_type="plan_rejected",
                metadata={"reason_code": exc.code},
            )
            outcome = self._decision_outcome(
                execution=execution,
                status=OutcomeStatus.REFUSED,
                summary="The proposed plan is outside the configured runtime boundary.",
                flags=[SafetyFlag.PLAN_VALIDATION_FAILED],
                recovery=RecoveryAction.REFUSE,
                error_code=exc.code,
            )
            return self._finish(
                principal=principal,
                execution=execution,
                state=state,
                outcome=outcome,
                trace=trace,
            )

        trace.record(
            stage="planning",
            event_type="plan_accepted",
            metadata={"step_count": str(len(plan.steps))},
        )
        if plan.stop_reason:
            trace.record(
                stage="planning",
                event_type="plan_stop_condition_declared",
                metadata={"reason_digest": digest_mapping({"reason": plan.stop_reason})},
            )
        if not plan.steps:
            trace.record(
                stage="planning",
                event_type="plan_stopped",
                metadata={
                    "reason_code": (
                        digest_mapping({"reason": plan.stop_reason})
                        if plan.stop_reason
                        else "no_steps"
                    )
                },
            )
            outcome = self._decision_outcome(
                execution=execution,
                status=OutcomeStatus.NEEDS_INPUT,
                summary="The provider returned no executable tool step.",
                flags=[SafetyFlag.NO_RESULT],
                recovery=RecoveryAction.REQUEST_INPUT,
                error_code="plan_stopped_without_tool_result",
            )
            return self._finish(
                principal=principal,
                execution=execution,
                state=state,
                outcome=outcome,
                trace=trace,
            )
        highest_risk = _initial_highest_risk
        for step_index, step in enumerate(plan.steps, start=1):
            control.check()
            trace.record(
                stage="execution",
                event_type="step_started",
                metadata={"step_id": step.step_id, "step_index": str(step_index)},
            )
            tool = self.tools.get(step.tool_id, step.tool_version)
            with self._active_lock:
                active = self._active.get(execution.execution_id)
                if active is not None:
                    active.current_step = step
                    active.current_tool = tool
            highest_risk = _higher_risk(highest_risk, tool.risk_level)
            trace.record(
                stage="tool",
                event_type="tool_proposed",
                metadata={
                    "tool_id": tool.tool_id,
                    "tool_version": tool.version,
                    "step_id": step.step_id,
                    "argument_keys": ",".join(sorted(step.arguments)),
                    "argument_digest": digest_mapping(step.arguments),
                },
            )

            permission = self._authorize(
                principal=principal,
                execution=execution,
                tool=tool,
                arguments=step.arguments,
                resource=resource,
            )
            approval_action = self._action_proposal(
                execution=execution,
                tool=tool,
                step=step,
            )
            approval_policy_decision = self._approval_policy_decision(
                principal=principal,
                execution=execution,
                tool=tool,
                action=approval_action,
            )
            if approval_policy_decision is not None:
                trace.record(
                    stage="approval",
                    event_type="approval_policy_decision",
                    metadata={
                        "required": str(approval_policy_decision.required).lower(),
                        "reason_code": approval_policy_decision.reason_code,
                        "policy_id": approval_policy_decision.policy_id or "none",
                        "matched_rule_count": str(len(approval_policy_decision.matched_rule_ids)),
                    },
                )
                self._audit(
                    event_type="approval_policy_decision",
                    principal=principal,
                    execution=execution,
                    tool_ids=[tool.tool_id],
                    metadata={
                        "required": str(approval_policy_decision.required).lower(),
                        "reason_code": approval_policy_decision.reason_code,
                        "policy_id": approval_policy_decision.policy_id or "none",
                    },
                )
            has_exact_approval = (
                tool.action_digest(step.arguments) in execution.approved_action_digests
            )
            if (
                permission.allowed
                and approval_policy_decision is not None
                and approval_policy_decision.required
                and not has_exact_approval
            ):
                permission = self._deny_permission(
                    permission=permission,
                    principal=principal,
                    execution=execution,
                    tool=tool,
                    reason_code="approval_required",
                    approval_required=True,
                )
            approval_request: ApprovalRequest | None = None
            approval_decision: ApprovalDecision | None = None
            approval_status: ApprovalDecisionStatus | None = None
            approval_error_code: str | None = None
            approval_call_id: str | None = None
            if (
                not permission.allowed
                and permission.approval_required
                and self.approval_broker is not None
            ):
                approval_call_id = self._id("call")
                approval_action = self._action_proposal(
                    execution=execution,
                    tool=tool,
                    step=step,
                    call_id=approval_call_id,
                )
                approval_request = self._approval_request(
                    execution=execution,
                    action=approval_action,
                    action_digest=tool.action_digest(step.arguments),
                    context=context,
                    reason=permission.reason_code,
                    policy_decision=approval_policy_decision,
                )
                approval_metadata = self._approval_metadata(approval_request)
                trace.record(
                    stage="approval",
                    event_type="approval_requested",
                    metadata=approval_metadata,
                )
                self._audit(
                    event_type="approval_requested",
                    principal=principal,
                    execution=execution,
                    tool_ids=[tool.tool_id],
                    metadata=approval_metadata,
                )
                raw_approval_decision = self.approval_broker.submit(
                    approval_request.model_copy(deep=True)
                )
                approval_decision = (
                    ApprovalDecision.model_validate(raw_approval_decision)
                    if raw_approval_decision is not None
                    else None
                )
                if approval_decision is not None:
                    approval_status, approval_error_code = self._validate_approval_decision(
                        approval_request,
                        approval_decision,
                    )
                    if (
                        approval_error_code is None
                        and approval_status == ApprovalDecisionStatus.APPROVED
                    ):
                        trace.record(
                            stage="approval",
                            event_type="approval_approved",
                            metadata=self._approval_metadata(
                                approval_request,
                                approval_decision,
                            ),
                        )
                        self._audit(
                            event_type="approval_approved",
                            principal=principal,
                            execution=execution,
                            tool_ids=[tool.tool_id],
                            metadata=self._approval_metadata(
                                approval_request,
                                approval_decision,
                            ),
                        )
                        execution = execution.model_copy(
                            update={
                                "approved_action_digests": tuple(
                                    dict.fromkeys(
                                        (
                                            *execution.approved_action_digests,
                                            approval_request.action_digest,
                                        )
                                    )
                                )
                            }
                        )
                        permission = self._authorize(
                            principal=principal,
                            execution=execution,
                            tool=tool,
                            arguments=step.arguments,
                            resource=resource,
                        )
                        if not permission.allowed:
                            approval_status = None
            if permission.policy_decision is not None:
                trace.record_policy_decision(permission.policy_decision)
                trace.record(
                    stage="policy",
                    event_type="policy_decision",
                    metadata={
                        "tool_id": tool.tool_id,
                        "allowed": str(permission.policy_decision.allowed).lower(),
                        "reason_code": permission.policy_decision.reason_code,
                    },
                )
                self._audit(
                    event_type="policy_decision",
                    principal=principal,
                    execution=execution,
                    tool_ids=[tool.tool_id],
                    safety_flags=(
                        [SafetyFlag.PERMISSION_DENIED]
                        if not permission.policy_decision.allowed
                        else []
                    ),
                    metadata={
                        "allowed": str(permission.policy_decision.allowed).lower(),
                        "reason_code": permission.policy_decision.reason_code,
                        "approval_required": str(
                            permission.policy_decision.approval_required
                        ).lower(),
                    },
                )
            if not permission.allowed:
                result_status = (
                    ToolResultStatus.APPROVAL_REQUIRED
                    if permission.approval_required
                    else ToolResultStatus.PERMISSION_DENIED
                )
                tool_calls.append(
                    ToolCallRecord(
                        call_id=approval_call_id or self._id("call"),
                        step_id=step.step_id,
                        tool_id=tool.tool_id,
                        tool_version=tool.version,
                        arguments=tool.redact_arguments(step.arguments),
                        result_status=result_status,
                        permission_reason_code=permission.reason_code,
                    )
                )
                trace.record(
                    stage="permission",
                    event_type="permission_denied",
                    metadata={
                        "tool_id": tool.tool_id,
                        "reason_code": permission.reason_code,
                    },
                )
                self._audit(
                    event_type="permission_denied",
                    principal=principal,
                    execution=execution,
                    tool_ids=[tool.tool_id],
                    safety_flags=[
                        SafetyFlag.APPROVAL_REQUIRED
                        if permission.approval_required
                        else SafetyFlag.PERMISSION_DENIED
                    ],
                    metadata={"reason_code": permission.reason_code},
                )
                outcome_status = (
                    OutcomeStatus.ESCALATED
                    if permission.approval_required
                    else OutcomeStatus.REFUSED
                )
                outcome_summary = (
                    "The action requires exact human approval before execution."
                    if permission.approval_required
                    else "The proposed tool call is not authorized for this execution."
                )
                outcome_recovery = (
                    RecoveryAction.ESCALATE
                    if permission.approval_required
                    else RecoveryAction.REFUSE
                )
                outcome_human_review = permission.approval_required
                outcome_error_code = permission.reason_code
                if approval_request is not None and approval_decision is None:
                    trace.record(
                        stage="runtime",
                        event_type="execution_paused",
                        metadata={
                            "reason_code": "approval_required",
                            "request_id": approval_request.request_id,
                        },
                    )
                elif approval_request is not None and (
                    approval_error_code is not None
                    or approval_status
                    in {
                        ApprovalDecisionStatus.REJECTED,
                        ApprovalDecisionStatus.REQUEST_CHANGES,
                        ApprovalDecisionStatus.EXPIRED,
                    }
                ):
                    transition = _approval_transition(
                        status=approval_status,
                        error_code=approval_error_code,
                    )
                    metadata = self._approval_metadata(approval_request, approval_decision)
                    trace.record(
                        stage="approval",
                        event_type=transition,
                        metadata={**metadata, "error_code": transition},
                    )
                    self._audit(
                        event_type=transition,
                        principal=principal,
                        execution=execution,
                        tool_ids=[tool.tool_id],
                        safety_flags=[SafetyFlag.APPROVAL_REQUIRED],
                        metadata={**metadata, "error_code": transition},
                    )
                    if approval_error_code is not None:
                        outcome_status = OutcomeStatus.REFUSED
                        outcome_summary = (
                            "The approval evidence does not match the reviewed action."
                        )
                        outcome_recovery = RecoveryAction.REFUSE
                        outcome_human_review = False
                        outcome_error_code = approval_error_code
                    elif approval_status == ApprovalDecisionStatus.REJECTED:
                        outcome_status = OutcomeStatus.REFUSED
                        outcome_summary = "The reviewer rejected the proposed action."
                        outcome_recovery = RecoveryAction.REFUSE
                        outcome_human_review = False
                        outcome_error_code = "approval_rejected"
                    elif approval_status == ApprovalDecisionStatus.REQUEST_CHANGES:
                        outcome_status = OutcomeStatus.NEEDS_INPUT
                        outcome_summary = (
                            "The reviewer requested changes before the action can run."
                        )
                        outcome_recovery = RecoveryAction.REQUEST_INPUT
                        outcome_human_review = True
                        outcome_error_code = "approval_changes_requested"
                    elif approval_status == ApprovalDecisionStatus.EXPIRED:
                        outcome_status = OutcomeStatus.ESCALATED
                        outcome_summary = "The approval expired before the action could run."
                        outcome_recovery = RecoveryAction.ESCALATE
                        outcome_human_review = True
                        outcome_error_code = "approval_expired"
                outcome = self._decision_outcome(
                    execution=execution,
                    status=outcome_status,
                    summary=outcome_summary,
                    flags=[
                        SafetyFlag.APPROVAL_REQUIRED
                        if permission.approval_required
                        else SafetyFlag.PERMISSION_DENIED
                    ],
                    tool_calls=tool_calls,
                    recovery=outcome_recovery,
                    human_review_required=outcome_human_review,
                    error_code=outcome_error_code,
                )
                finished = self._finish(
                    principal=principal,
                    execution=execution,
                    state=state,
                    outcome=outcome,
                    trace=trace,
                )
                if approval_request is not None and approval_decision is None:
                    self._remember_pending_approval(
                        principal=principal,
                        execution=execution,
                        input_text=text,
                        resource=resource,
                        plan=composition_plan,
                        resume_plan=AgentPlan(
                            steps=[
                                step.model_copy(deep=True) for step in plan.steps[step_index - 1 :]
                            ],
                            stop_reason=plan.stop_reason,
                        ),
                        tool_calls=tool_calls[:-1],
                        tool_results=tool_results,
                        highest_risk=highest_risk,
                        request=approval_request,
                        outcome=finished,
                        trace=trace,
                    )
                return finished

            trace.record(
                stage="permission",
                event_type="permission_allowed",
                metadata={
                    "tool_id": tool.tool_id,
                    "reason_code": permission.reason_code,
                },
            )

            if tool.idempotency_required and not step.idempotency_key:
                reason_code = "idempotency_key_required"
                tool_calls.append(
                    ToolCallRecord(
                        call_id=self._id("call"),
                        step_id=step.step_id,
                        tool_id=tool.tool_id,
                        tool_version=tool.version,
                        arguments=tool.redact_arguments(step.arguments),
                        result_status=ToolResultStatus.PERMISSION_DENIED,
                        permission_reason_code=reason_code,
                    )
                )
                trace.record_tool_execution(
                    ToolExecutionRecord(
                        execution_id=execution.execution_id,
                        tool_id=tool.tool_id,
                        tool_version=tool.version,
                        status=ToolResultStatus.PERMISSION_DENIED,
                        error_code=reason_code,
                    )
                )
                trace.record(
                    stage="tool",
                    event_type="tool_rejected",
                    metadata={"tool_id": tool.tool_id, "reason_code": reason_code},
                )
                outcome = self._decision_outcome(
                    execution=execution,
                    status=OutcomeStatus.REFUSED,
                    summary="The write operation does not have an idempotency key.",
                    flags=[SafetyFlag.PERMISSION_DENIED],
                    tool_calls=tool_calls,
                    recovery=RecoveryAction.REFUSE,
                    error_code=reason_code,
                )
                return self._finish(
                    principal=principal,
                    execution=execution,
                    state=state,
                    outcome=outcome,
                    trace=trace,
                )

            started = time.perf_counter()
            try:
                tool.validate_arguments(step.arguments)
                trace.record(
                    stage="tool",
                    event_type="tool_arguments_validated",
                    metadata={"tool_id": tool.tool_id, "step_id": step.step_id},
                )
                result = self.tools.invoke(
                    tool.tool_id,
                    execution,
                    step.arguments,
                    version=tool.version,
                    idempotency_key=step.idempotency_key,
                    trace_callback=lambda event_type, metadata: trace.record(
                        stage="tool",
                        event_type=event_type,
                        metadata=metadata,
                    ),
                    cancellation_check=control.is_cancelled,
                    deadline=control.deadline,
                    retry_admission=control.admit_retry,
                )
                result = _detect_indirect_injection(result)
            except ToolInvocationError as exc:
                trace.record(
                    stage="tool",
                    event_type="tool_invocation_failed",
                    metadata={"tool_id": tool.tool_id, "reason_code": exc.code},
                )
                status = (
                    ToolResultStatus.INVALID_ARGUMENTS
                    if exc.code == "invalid_arguments"
                    else ToolResultStatus.FAILED
                )
                result = ToolResult(
                    tool_id=tool.tool_id,
                    tool_version=tool.version,
                    execution_id=execution.execution_id,
                    status=status,
                    error_code=exc.code,
                )
                records = self.tools.execution_records
                if records and records[-1].execution_id == execution.execution_id:
                    record = records[-1]
                    result = result.model_copy(
                        update={
                            "metadata": {
                                "attempts": str(record.attempts),
                                "retry_count": str(record.retry_count),
                            }
                        }
                    )
            latency_ms = (time.perf_counter() - started) * 1000.0
            tool_results.append(result)
            tool_calls.append(
                ToolCallRecord(
                    call_id=approval_call_id or self._id("call"),
                    step_id=step.step_id,
                    tool_id=tool.tool_id,
                    tool_version=tool.version,
                    arguments=tool.redact_arguments(step.arguments),
                    result_status=result.status,
                    evidence_ids=[item.evidence_id for item in result.evidence],
                    latency_ms=latency_ms,
                    retry_count=_metadata_int(result.metadata, "retry_count"),
                )
            )
            trace.record_tool_execution(
                ToolExecutionRecord(
                    execution_id=execution.execution_id,
                    tool_id=tool.tool_id,
                    tool_version=tool.version,
                    status=result.status,
                    attempts=_metadata_int(result.metadata, "attempts", default=1),
                    retry_count=_metadata_int(result.metadata, "retry_count"),
                    latency_ms=latency_ms,
                    timeout_seconds=tool.timeout_seconds,
                    error_code=result.error_code,
                )
            )
            trace.record(
                stage="tool",
                event_type="tool_result_recorded",
                metadata={
                    "tool_id": tool.tool_id,
                    "result_status": result.status.value,
                    "evidence_count": str(len(result.evidence)),
                },
            )
            self._audit(
                event_type="tool_result_recorded",
                principal=principal,
                execution=execution,
                tool_ids=[tool.tool_id],
                safety_flags=(
                    [SafetyFlag.INDIRECT_PROMPT_INJECTION] if result.injection_flags else []
                ),
                metadata={
                    "result_status": result.status.value,
                    "evidence_count": str(len(result.evidence)),
                },
            )
            trace.record(
                stage="execution",
                event_type="step_completed",
                metadata={
                    "step_id": step.step_id,
                    "step_index": str(step_index),
                    "result_status": result.status.value,
                },
            )
            with self._active_lock:
                active = self._active.get(execution.execution_id)
                if active is not None:
                    active.current_step = None
                    active.current_tool = None
            control.check()

        trace.record(
            stage="execution",
            event_type="plan_steps_exhausted",
            metadata={"step_count": str(len(plan.steps))},
        )

        result_context = self._compile_context(
            principal,
            execution,
            state,
            text,
            tool_results=tool_results,
        )
        trace.record(
            stage="context",
            event_type="result_context_compiled",
            metadata={
                "trusted_block_count": str(len(result_context.trusted_blocks)),
                "untrusted_block_count": str(len(result_context.untrusted_blocks)),
                "dropped_block_count": str(len(result_context.dropped_block_ids)),
            },
        )
        control.check()
        composition_request = CompositionRequest(
            request_id=self._id("provider_request"),
            context=result_context,
            execution=execution,
            plan=composition_plan,
            tool_results=[result.model_copy(deep=True) for result in tool_results],
        )
        try:
            trace.record(
                stage="composition",
                event_type="provider_call_started",
                metadata={"operation": ProviderOperation.COMPOSE.value},
            )
            invocation = self._invoke_provider(
                operation=ProviderOperation.COMPOSE,
                call=lambda: self._call_provider_operation(
                    method=cast(Callable[..., object], self.provider.compose),
                    request=composition_request,
                    legacy_kwargs={
                        "context": composition_request.context,
                        "execution": composition_request.execution,
                        "plan": composition_request.plan,
                        "tool_results": composition_request.tool_results,
                    },
                ),
                control=control,
            )
            composition_response = normalize_composition(invocation.value)
            composition_response = cast(
                CompositionResponse,
                _with_invocation_metadata(composition_response, invocation),
            )
            control.check()
            trace.record_provider_call(
                operation=ProviderOperation.COMPOSE,
                metadata=composition_response.metadata,
            )
            proposal = composition_response.proposal
            trace.record(
                stage="composition",
                event_type="provider_call_completed",
                metadata={"operation": ProviderOperation.COMPOSE.value},
            )
        except Exception as exc:  # noqa: BLE001 - provider code is an extension boundary.
            return self._provider_failure(
                principal=principal,
                execution=execution,
                state=state,
                trace=trace,
                operation=ProviderOperation.COMPOSE,
                error=exc,
                control=control,
                tool_calls=tool_calls,
            )

        verification = verify_outcome(proposal=proposal, tool_results=tool_results)
        trace.record(
            stage="verification",
            event_type="outcome_verified",
            metadata={
                "supported": str(verification.supported).lower(),
                "evidence_coverage": f"{verification.evidence_coverage:.3f}",
            },
        )
        decision = self.safety_policy.decide(
            tool_results=tool_results,
            proposal=proposal,
            verification=verification,
            highest_risk=highest_risk,
            plan=plan,
        )
        outcome_flags = list(decision.flags)
        if (
            control.retry_budget_exhausted
            and SafetyFlag.RETRY_BUDGET_EXHAUSTED not in outcome_flags
        ):
            outcome_flags.append(SafetyFlag.RETRY_BUDGET_EXHAUSTED)
        outcome = self._decision_outcome(
            execution=execution,
            status=decision.status,
            summary=_summary_for_decision(proposal, decision),
            evidence_ids=(
                list(proposal.evidence_ids)
                if verification.supported
                and decision.status
                in {OutcomeStatus.COMPLETED, OutcomeStatus.PARTIAL, OutcomeStatus.ESCALATED}
                else []
            ),
            flags=outcome_flags,
            tool_calls=tool_calls,
            verification=verification,
            recovery=_recovery_for_status(decision.status),
            human_review_required=decision.human_review_required,
            confidence=proposal.confidence,
            error_code=decision.escalation_code,
        )
        return self._finish(
            principal=principal,
            execution=execution,
            state=state,
            outcome=outcome,
            trace=trace,
        )

    def _invoke_provider(
        self,
        *,
        operation: ProviderOperation,
        call: Callable[[], object],
        control: ExecutionControl,
    ) -> ProviderInvocationResult:
        return invoke_provider_call(
            operation=operation,
            call=call,
            policy=_ControlledProviderCallPolicy(self.provider_call_policy, control),
            cancellation_check=control.is_cancelled,
            deadline=control.deadline,
        )

    def _authorize(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        arguments: dict[str, object],
        resource: ResourceContext | None,
    ) -> PermissionDecision:
        """Call a broker and enforce the runtime authority ceiling."""

        broker = cast(Callable[..., object], self.permission_broker.authorize)
        try:
            parameters = signature(broker).parameters
        except (TypeError, ValueError):
            accepts_resource = True
        else:
            accepts_resource = "resource" in parameters or any(
                parameter.kind is Parameter.VAR_KEYWORD for parameter in parameters.values()
            )
        if accepts_resource:
            raw_permission = broker(
                principal=principal,
                execution=execution,
                tool=tool,
                arguments=arguments,
                resource=resource,
            )
        else:
            raw_permission = broker(
                principal=principal,
                execution=execution,
                tool=tool,
                arguments=arguments,
            )
        permission = PermissionDecision.model_validate(raw_permission)

        if (
            permission.principal_id != principal.principal_id
            or permission.tenant_id != principal.tenant_id
            or permission.tool_id != tool.tool_id
        ):
            return self._deny_permission(
                permission=permission,
                principal=principal,
                execution=execution,
                tool=tool,
                reason_code="invalid_permission_decision",
            )

        if permission.policy_decision is None:
            permission = permission.model_copy(
                update={
                    "policy_decision": self._policy_decision(
                        principal=principal,
                        execution=execution,
                        tool=tool,
                        allowed=permission.allowed,
                        reason_code=permission.reason_code,
                        approval_required=permission.approval_required,
                        resource=resource,
                    )
                }
            )
        elif not permission.policy_decision.allowed:
            return permission.model_copy(
                update={
                    "allowed": False,
                    "reason_code": permission.policy_decision.reason_code,
                    "approval_required": False,
                }
            )

        if not permission.allowed:
            return permission
        if tool.tool_id not in execution.authorized_tool_ids:
            return self._deny_permission(
                permission=permission,
                principal=principal,
                execution=execution,
                tool=tool,
                reason_code="tool_not_authorized",
            )
        if self.capabilities and tool.tool_id not in self._capability_tool_ids:
            return self._deny_permission(
                permission=permission,
                principal=principal,
                execution=execution,
                tool=tool,
                reason_code="tool_not_in_capability",
            )
        if set(tool.required_permissions).difference(execution.granted_permissions):
            return self._deny_permission(
                permission=permission,
                principal=principal,
                execution=execution,
                tool=tool,
                reason_code="required_permission_missing",
            )
        if tool.allowed_environments and execution.environment not in tool.allowed_environments:
            return self._deny_permission(
                permission=permission,
                principal=principal,
                execution=execution,
                tool=tool,
                reason_code="tool_not_allowed_in_environment",
            )
        if _risk_exceeds(tool.risk_level, execution.max_risk_level):
            return self._deny_permission(
                permission=permission,
                principal=principal,
                execution=execution,
                tool=tool,
                reason_code="risk_exceeds_execution_limit",
            )

        approval_required = permission.approval_required or tool.requires_approval
        if permission.policy_decision is not None:
            approval_required = approval_required or permission.policy_decision.approval_required
        if approval_required:
            if tool.action_digest(arguments) not in execution.approved_action_digests:
                return self._deny_permission(
                    permission=permission,
                    principal=principal,
                    execution=execution,
                    tool=tool,
                    reason_code="approval_required",
                    approval_required=True,
                )
            return permission.model_copy(
                update={"reason_code": "allowed_by_exact_approval", "approval_required": False}
            )
        return permission.model_copy(
            update={
                "agent_id": execution.agent_id,
                "environment": execution.environment,
                "risk_level": tool.risk_level,
            }
        )

    def _deny_permission(
        self,
        *,
        permission: PermissionDecision,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        reason_code: str,
        approval_required: bool = False,
    ) -> PermissionDecision:
        policy = permission.policy_decision
        if policy is None:
            policy = self._policy_decision(
                principal=principal,
                execution=execution,
                tool=tool,
                allowed=False,
                reason_code=reason_code,
                approval_required=approval_required,
                resource=None,
            )
        else:
            policy = policy.model_copy(
                update={
                    "allowed": False,
                    "reason_code": reason_code,
                    "approval_required": approval_required,
                }
            )
        return permission.model_copy(
            update={
                "allowed": False,
                "reason_code": reason_code,
                "approval_required": approval_required,
                "agent_id": execution.agent_id,
                "environment": execution.environment,
                "risk_level": tool.risk_level,
                "policy_decision": policy,
            }
        )

    def _policy_decision(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        allowed: bool,
        reason_code: str,
        approval_required: bool,
        resource: ResourceContext | None,
    ) -> PolicyDecision:
        return PolicyDecision(
            decision_id=self._id("policy_decision"),
            allowed=allowed,
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
            agent_id=execution.agent_id,
            tool_id=tool.tool_id,
            environment=execution.environment,
            risk_level=tool.risk_level,
            reason_code=reason_code,
            approval_required=approval_required,
            resource_type=resource.resource_type if resource is not None else None,
            resource_id=resource.resource_id if resource is not None else None,
        )

    @staticmethod
    def _call_provider_operation(
        *,
        method: Callable[..., object],
        request: object,
        legacy_kwargs: dict[str, object],
    ) -> object:
        """Call the request contract and retain compatibility with Phase 0A providers."""

        try:
            parameters = signature(method).parameters
        except (TypeError, ValueError):
            accepts_request = True
        else:
            accepts_request = "request" in parameters or any(
                parameter.kind is Parameter.VAR_KEYWORD for parameter in parameters.values()
            )
        return method(request=request) if accepts_request else method(**legacy_kwargs)

    def _provider_failure(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        state: ExecutionState,
        trace: TraceRecorder,
        operation: ProviderOperation,
        error: BaseException,
        control: ExecutionControl | None = None,
        tool_calls: list[ToolCallRecord] | None = None,
    ) -> AgentOutcome:
        if isinstance(error, (ExecutionTimeoutError, ExecutionCancelledError)):
            raise error
        error_code = _provider_error_code(error)
        flags = [SafetyFlag.PROVIDER_FAILURE]
        if control is not None and control.retry_budget_exhausted:
            flags.append(SafetyFlag.RETRY_BUDGET_EXHAUSTED)
        if isinstance(error, ProviderTimeoutError):
            flags.append(SafetyFlag.PROVIDER_TIMEOUT)
        if isinstance(error, ProviderOutputError):
            flags.append(SafetyFlag.PROVIDER_OUTPUT_INVALID)
        trace.record(
            stage=operation.value,
            event_type="provider_call_failed",
            metadata={
                "operation": operation.value,
                "error_code": error_code,
            },
        )
        self._audit(
            event_type="provider_call_failed",
            principal=principal,
            execution=execution,
            outcome_status=(
                OutcomeStatus.TIMED_OUT
                if isinstance(error, ProviderTimeoutError)
                else OutcomeStatus.FAILED
            ),
            safety_flags=flags,
            metadata={"operation": operation.value, "error_code": error_code},
        )
        outcome_status = (
            OutcomeStatus.TIMED_OUT
            if isinstance(error, ProviderTimeoutError)
            else OutcomeStatus.FAILED
        )
        outcome = self._decision_outcome(
            execution=execution,
            status=outcome_status,
            summary="The provider could not return a valid proposal.",
            flags=flags,
            tool_calls=tool_calls,
            recovery=RecoveryAction.ABORT,
            error_code=error_code,
        )
        return self._finish(
            principal=principal,
            execution=execution,
            state=state,
            outcome=outcome,
            trace=trace,
        )

    def _control_failure(
        self,
        *,
        active: _ActiveExecution,
        control_error: ExecutionTimeoutError | ExecutionCancelledError,
    ) -> AgentOutcome:
        """Close an interrupted run with a deterministic terminal outcome."""

        timed_out = isinstance(control_error, ExecutionTimeoutError)
        status = OutcomeStatus.TIMED_OUT if timed_out else OutcomeStatus.CANCELLED
        flag = SafetyFlag.EXECUTION_TIMEOUT if timed_out else SafetyFlag.EXECUTION_CANCELLED
        event_type = "execution_timed_out" if timed_out else "execution_cancelled"
        if active.current_step is not None and active.current_tool is not None:
            step = active.current_step
            tool = active.current_tool
            active.tool_calls.append(
                ToolCallRecord(
                    call_id=self._id("call"),
                    step_id=step.step_id,
                    tool_id=tool.tool_id,
                    tool_version=tool.version,
                    arguments=tool.redact_arguments(step.arguments),
                    result_status=ToolResultStatus.FAILED,
                    permission_reason_code=control_error.code.value,
                )
            )
            active.trace.record_tool_execution(
                ToolExecutionRecord(
                    execution_id=active.execution.execution_id,
                    tool_id=tool.tool_id,
                    tool_version=tool.version,
                    status=ToolResultStatus.FAILED,
                    timeout_seconds=tool.timeout_seconds,
                    error_code=control_error.code.value,
                )
            )
            active.trace.record(
                stage="execution",
                event_type="step_interrupted",
                metadata={"step_id": step.step_id, "tool_id": tool.tool_id},
            )
        active.trace.record(
            stage="runtime",
            event_type=event_type,
            metadata={
                "error_code": control_error.code.value,
            },
        )
        self._audit(
            event_type=event_type,
            principal=active.principal,
            execution=active.execution,
            outcome_status=status,
            safety_flags=[flag],
            tool_ids=[call.tool_id for call in active.tool_calls],
            metadata={"error_code": control_error.code.value},
        )
        outcome = self._decision_outcome(
            execution=active.execution,
            status=status,
            summary=(
                "The execution exceeded its configured time limit."
                if timed_out
                else "The execution was cancelled by its caller."
            ),
            flags=[flag],
            tool_calls=active.tool_calls,
            recovery=RecoveryAction.ABORT,
            error_code=control_error.code.value,
        )
        return self._finish(
            principal=active.principal,
            execution=active.execution,
            state=active.state,
            outcome=outcome,
            trace=active.trace,
        )

    def _approval_policy_decision(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        tool: ToolDefinition,
        action: ActionProposal,
    ) -> ApprovalPolicyDecision | None:
        if self.approval_policy is None:
            return None
        evaluator = cast(Callable[..., object], self.approval_policy.evaluate)
        return ApprovalPolicyDecision.model_validate(
            evaluator(
                principal=principal,
                execution=execution,
                tool=tool,
                action=action,
            )
        )

    def _action_proposal(
        self,
        *,
        execution: ExecutionContext,
        tool: ToolDefinition,
        step: PlanStep,
        call_id: str | None = None,
    ) -> ActionProposal:
        """Build approval data from trusted tool metadata and the exact step."""

        return ActionProposal(
            action_id=step.step_id,
            execution_id=execution.execution_id,
            tool_call=ToolCall(
                call_id=call_id,
                tool_id=tool.tool_id,
                tool_version=tool.version,
                arguments=step.arguments,
                purpose=step.purpose,
                idempotency_key=step.idempotency_key,
            ),
            risk_level=tool.risk_level,
            requires_approval=True,
            justification=step.purpose,
        )

    def _approval_request(
        self,
        *,
        execution: ExecutionContext,
        action: ActionProposal,
        action_digest: str,
        context: CompiledContext,
        reason: str,
        policy_decision: ApprovalPolicyDecision | None,
    ) -> ApprovalRequest:
        expiry_seconds = (
            policy_decision.expiry_seconds
            if policy_decision is not None and policy_decision.expiry_seconds is not None
            else self.config.approval_expiry_seconds
        )
        created_at = self._clock()
        expires_at = (
            created_at + timedelta(seconds=expiry_seconds) if expiry_seconds is not None else None
        )
        return ApprovalRequest(
            request_id=self._id("approval_request"),
            execution_id=execution.execution_id,
            agent_id=execution.agent_id,
            agent_version=execution.agent_version,
            principal_id=execution.principal.principal_id,
            tenant_id=execution.principal.tenant_id,
            action=action,
            action_digest=action_digest,
            reason=reason,
            context=context.model_copy(deep=True),
            approval_policy_id=policy_decision.policy_id if policy_decision else None,
            approval_policy_version=policy_decision.policy_version if policy_decision else None,
            approval_rule_ids=(
                list(policy_decision.matched_rule_ids) if policy_decision is not None else []
            ),
            expires_at=expires_at,
            created_at=created_at,
        )

    def _validate_approval_decision(
        self,
        request: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> tuple[ApprovalDecisionStatus | None, str | None]:
        """Validate request identity, digest, and approval lifetime."""

        if decision.request_id is not None and decision.request_id != request.request_id:
            return None, "approval_request_mismatch"
        if decision.action_digest != request.action_digest:
            return None, "approval_action_mismatch"
        if decision.status != ApprovalDecisionStatus.APPROVED:
            return decision.status, None
        now = self._clock()
        if request.expires_at is not None and now >= request.expires_at:
            return ApprovalDecisionStatus.EXPIRED, "approval_expired"
        if decision.expires_at is not None and now >= decision.expires_at:
            return ApprovalDecisionStatus.EXPIRED, "approval_expired"
        if decision.expires_at is not None and decision.expires_at <= decision.decided_at:
            return None, "approval_expiry_invalid"
        if request.expires_at is not None:
            if decision.expires_at is not None and decision.expires_at > request.expires_at:
                return None, "approval_expiry_invalid"
            if decision.decided_at >= request.expires_at:
                return ApprovalDecisionStatus.EXPIRED, "approval_expired"
        return decision.status, None

    @staticmethod
    def _approval_metadata(
        request: ApprovalRequest,
        decision: ApprovalDecision | None = None,
    ) -> dict[str, str]:
        metadata = {
            "request_id": request.request_id,
            "action_digest": request.action_digest,
            "tool_id": request.action.tool_call.tool_id,
        }
        if request.expires_at is not None:
            metadata["expires_at"] = request.expires_at.isoformat()
        if decision is not None:
            metadata.update(
                {
                    "approval_id": decision.approval_id,
                    "decision": decision.status.value,
                    "decided_by": decision.decided_by,
                }
            )
        return metadata

    def _remember_pending_approval(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        input_text: str,
        resource: ResourceContext | None,
        plan: AgentPlan,
        resume_plan: AgentPlan,
        tool_calls: Sequence[ToolCallRecord],
        tool_results: Sequence[ToolResult],
        highest_risk: RiskLevel,
        request: ApprovalRequest,
        outcome: AgentOutcome,
        trace: TraceRecorder,
        trace_prefix: RunTrace | None = None,
    ) -> None:
        with self._pending_lock:
            self._pending_approvals[execution.execution_id] = _PendingApproval(
                principal=principal,
                execution=execution,
                input_text=input_text,
                resource=resource,
                plan=plan.model_copy(deep=True),
                resume_plan=resume_plan.model_copy(deep=True),
                tool_calls=[call.model_copy(deep=True) for call in tool_calls],
                tool_results=[result.model_copy(deep=True) for result in tool_results],
                highest_risk=highest_risk,
                request=request.model_copy(deep=True),
                outcome=outcome.model_copy(deep=True),
                trace=trace,
                trace_prefix=trace_prefix.model_copy(deep=True) if trace_prefix else None,
            )
            pending = self._pending_approvals[execution.execution_id]
        self._persist_pending_checkpoint(pending)

    def _restore_pending_approval(
        self,
        principal: PrincipalContext,
        execution_id: str,
    ) -> _PendingApproval | None:
        """Hydrate a paused approval from a state-store checkpoint."""

        state = self.state_store.find_execution(principal, execution_id)
        if state is None or state.status != ExecutionStateStatus.PAUSED:
            return None
        if state.execution_id != execution_id:
            return None
        raw_checkpoint = state.data.get("checkpoint")
        if not isinstance(raw_checkpoint, dict):
            return None
        checkpoint = ExecutionCheckpoint.model_validate(raw_checkpoint)
        if checkpoint.execution.execution_id != execution_id:
            raise ValueError("checkpoint execution does not match the requested execution")
        if checkpoint.execution.principal != principal:
            raise ValueError("checkpoint principal does not own the execution")
        if (
            checkpoint.execution.agent_id != state.agent_id
            or checkpoint.execution.agent_version != state.agent_version
            or checkpoint.execution.state_id != state.state_id
        ):
            raise ValueError("checkpoint execution does not match stored state")
        initial_trace: RunTrace | None = None
        if checkpoint.trace is not None:
            initial_trace = RunTrace.model_validate(checkpoint.trace)
        trace = TraceRecorder(
            execution=checkpoint.execution,
            input_text=checkpoint.input_text,
            sink=self.trace_sink,
            id_factory=self._id,
            clock=self._clock,
            initial_trace=initial_trace,
        )
        pending = _PendingApproval(
            principal=principal,
            execution=checkpoint.execution,
            input_text=checkpoint.input_text,
            resource=checkpoint.resource,
            plan=checkpoint.plan,
            resume_plan=checkpoint.resume_plan,
            tool_calls=[call.model_copy(deep=True) for call in checkpoint.tool_calls],
            tool_results=[result.model_copy(deep=True) for result in checkpoint.tool_results],
            highest_risk=checkpoint.highest_risk,
            request=checkpoint.approval_request,
            outcome=checkpoint.outcome,
            trace=trace,
        )
        with self._pending_lock:
            current = self._pending_approvals.get(execution_id)
            if current is not None:
                return current
            self._pending_approvals[execution_id] = pending
        return pending

    def _persist_pending_checkpoint(
        self,
        pending: _PendingApproval,
        *,
        trace: RunTrace | None = None,
    ) -> None:
        """Store enough trusted continuation data to resume after a restart."""

        state = self.state_store.find_execution(pending.principal, pending.execution.execution_id)
        if state is None:
            raise StateConflictError("pending execution state is missing")
        if state.status not in {ExecutionStateStatus.ESCALATED, ExecutionStateStatus.PAUSED}:
            raise StateConflictError("pending execution state is no longer resumable")
        checkpoint = ExecutionCheckpoint(
            checkpoint_id=f"checkpoint:{pending.request.request_id}",
            execution=pending.execution,
            input_text=pending.input_text,
            resource=pending.resource,
            plan=pending.plan,
            resume_plan=pending.resume_plan,
            tool_calls=pending.tool_calls,
            tool_results=pending.tool_results,
            highest_risk=pending.highest_risk,
            approval_request=pending.request,
            outcome=pending.outcome,
            trace=(trace or pending.trace.export(final_status=pending.outcome.status)).model_dump(
                mode="json"
            ),
            created_at=pending.request.created_at,
            updated_at=self._clock(),
        )
        next_state = state.model_copy(
            update={
                "status": ExecutionStateStatus.PAUSED,
                "version": state.version + 1,
                "data": {
                    **state.data,
                    "checkpoint": checkpoint.model_dump(mode="json"),
                },
                "updated_at": self._clock(),
            },
            deep=True,
        )
        self.state_store.save(next_state, expected_version=state.version)

    def _close_pending_approval(
        self,
        *,
        pending: _PendingApproval,
        status: OutcomeStatus,
        summary: str,
        error_code: str,
        event_type: str,
        decision: ApprovalDecision,
        recovery: RecoveryAction | None = None,
        human_review_required: bool | None = None,
    ) -> AgentOutcome:
        metadata = self._approval_metadata(pending.request, decision)
        metadata["error_code"] = error_code
        pending.trace.record(
            stage="approval",
            event_type=event_type,
            metadata=metadata,
        )
        self._audit(
            event_type=event_type,
            principal=pending.principal,
            execution=pending.execution,
            outcome_status=status,
            safety_flags=[SafetyFlag.APPROVAL_REQUIRED],
            tool_ids=[pending.request.action.tool_call.tool_id],
            metadata=metadata,
        )
        state = self.state_store.get_or_create(
            pending.principal,
            agent_id=pending.execution.agent_id,
            agent_version=pending.execution.agent_version,
            state_id=pending.execution.state_id,
        )
        outcome = pending.outcome.model_copy(
            update={
                "outcome_id": self._id("outcome"),
                "status": status,
                "summary": summary,
                "recovery": recovery or _recovery_for_status(status),
                "human_review_required": (
                    human_review_required
                    if human_review_required is not None
                    else status == OutcomeStatus.ESCALATED
                ),
                "error_code": error_code,
            }
        )
        finished = self._finish(
            principal=pending.principal,
            execution=pending.execution,
            state=state,
            outcome=outcome,
            trace=pending.trace,
        )
        if pending.trace_prefix is not None:
            history = self._pending_trace_history(pending, final_status=finished.status)
            current = self.trace_for(finished.execution_id)
            merged = history.model_copy(
                update={
                    "trace_id": current.trace_id,
                    "final_status": current.final_status,
                    "generated_at": current.generated_at,
                }
            )
            with self._trace_lock:
                self._traces[finished.execution_id] = merged
            finished = finished.model_copy(update={"trace_id": merged.trace_id})
        with self._pending_lock:
            self._pending_approvals.pop(pending.execution.execution_id, None)
        return finished

    def _merge_resumed_trace(
        self,
        pending: _PendingApproval,
        outcome: AgentOutcome,
    ) -> AgentOutcome:
        """Keep the pause and resumed work in one ordered trace bundle."""

        previous = self._pending_trace_history(
            pending,
            final_status=pending.outcome.status,
        )
        merged = self._merge_trace_bundle(previous, outcome)
        merged_trace = self.trace_for(outcome.execution_id)
        next_pending: _PendingApproval | None
        with self._pending_lock:
            next_pending = self._pending_approvals.get(outcome.execution_id)
            if next_pending is not None:
                next_pending.trace_prefix = merged_trace
                next_pending.trace_prefix_event_count = len(next_pending.trace.export().events)
        if next_pending is not None:
            self._persist_pending_checkpoint(next_pending, trace=merged_trace)
        return merged

    def _pending_trace_history(
        self,
        pending: _PendingApproval,
        *,
        final_status: OutcomeStatus,
    ) -> RunTrace:
        """Return prior trace history plus events recorded in the current pause."""

        current = pending.trace.export(final_status=final_status)
        if pending.trace_prefix is None:
            return current
        extra_events = current.events[pending.trace_prefix_event_count :]
        if not extra_events:
            return pending.trace_prefix.model_copy(deep=True)
        return _append_trace_events(
            pending.trace_prefix,
            extra_events,
            generated_at=current.generated_at,
        )

    def _merge_trace_bundle(
        self,
        previous: RunTrace,
        outcome: AgentOutcome,
    ) -> AgentOutcome:
        """Prepend a prior approval cycle to the current execution trace."""

        current = self.trace_for(outcome.execution_id)
        offset = len(previous.events)
        resumed_events = [
            event.model_copy(update={"sequence": event.sequence + offset})
            for event in current.events
        ]
        merged = current.model_copy(
            update={
                "events": [*previous.events, *resumed_events],
                "provider_calls": [*previous.provider_calls, *current.provider_calls],
                "policy_decisions": [*previous.policy_decisions, *current.policy_decisions],
                "tool_executions": [*previous.tool_executions, *current.tool_executions],
            }
        )
        with self._trace_lock:
            self._traces[outcome.execution_id] = merged
        return outcome.model_copy(update={"trace_id": merged.trace_id})

    def trace_for(self, execution_id: str) -> RunTrace:
        """Return an exported trace for a completed execution."""

        with self._trace_lock:
            try:
                return self._traces[execution_id].model_copy(deep=True)
            except KeyError as exc:
                raise KeyError(f"unknown execution: {execution_id}") from exc

    def _compile_context(
        self,
        principal: PrincipalContext,
        execution: ExecutionContext,
        state: ExecutionState,
        input_text: str,
        *,
        tool_results: Sequence[ToolResult] = (),
    ) -> CompiledContext:
        memory = self.memory.select(principal) if self.memory is not None else ()
        return ContextCompiler(self.config).compile(
            principal=principal,
            execution=execution,
            input_text=input_text,
            state=state,
            capabilities=self.capabilities,
            memory=memory,
            tool_results=tool_results,
        )

    def _validate_plan(self, plan: AgentPlan) -> None:
        if len(plan.steps) > self.config.max_plan_steps:
            raise ToolInvocationError(
                "plan exceeds the configured step limit", code="plan_too_long"
            )
        for step in plan.steps:
            self.tools.get(step.tool_id, step.tool_version)

    def _visible_tool_ids(self, execution: ExecutionContext) -> tuple[str, ...]:
        """Return the intersection of caller authority and capability scope."""

        authorized = set(execution.authorized_tool_ids)
        if not self.capabilities:
            return tuple(sorted(authorized))
        return tuple(sorted(authorized.intersection(self._capability_tool_ids)))

    def _decision_outcome(
        self,
        *,
        execution: ExecutionContext,
        status: OutcomeStatus,
        summary: str,
        evidence_ids: list[str] | None = None,
        flags: list[SafetyFlag] | None = None,
        tool_calls: list[ToolCallRecord] | None = None,
        verification: VerificationResult | None = None,
        recovery: RecoveryAction = RecoveryAction.NONE,
        human_review_required: bool = False,
        confidence: float = 0.0,
        error_code: str | None = None,
    ) -> AgentOutcome:
        return AgentOutcome(
            outcome_id=self._id("outcome"),
            execution_id=execution.execution_id,
            agent_id=execution.agent_id,
            agent_version=execution.agent_version,
            session_id=execution.principal.session_id,
            principal_id=execution.principal.principal_id,
            tenant_id=execution.principal.tenant_id,
            status=status,
            summary=summary,
            evidence_ids=list(evidence_ids or []),
            safety_flags=list(flags or []),
            tool_calls=list(tool_calls or []),
            verification=verification,
            recovery=recovery,
            human_review_required=human_review_required,
            confidence=confidence,
            error_code=error_code,
            created_at=self._clock(),
        )

    def _finish(
        self,
        *,
        principal: PrincipalContext,
        execution: ExecutionContext,
        state: ExecutionState,
        outcome: AgentOutcome,
        trace: TraceRecorder,
        save_state: bool = True,
    ) -> AgentOutcome:
        if save_state:
            next_state = state.model_copy(
                update={
                    "execution_id": execution.execution_id,
                    "status": _state_status(outcome.status),
                    "version": state.version + 1,
                    "data": {
                        "last_outcome_id": outcome.outcome_id,
                        "last_status": outcome.status.value,
                        "last_tool_ids": ",".join(call.tool_id for call in outcome.tool_calls),
                    },
                    "updated_at": self._clock(),
                },
                deep=True,
            )
            try:
                self.state_store.save(next_state, expected_version=state.version)
                trace.record(
                    stage="state",
                    event_type="state_transitioned",
                    metadata={
                        "status": next_state.status.value,
                        "version": str(next_state.version),
                    },
                )
            except StateConflictError:
                trace.record(
                    stage="state",
                    event_type="state_transition_failed",
                    metadata={"reason_code": "state_conflict"},
                )
                outcome = outcome.model_copy(
                    update={
                        "status": OutcomeStatus.FAILED,
                        "summary": "The runtime could not commit workflow state safely.",
                        "error_code": "state_conflict",
                        "recovery": RecoveryAction.ABORT,
                    }
                )
        trace.record(
            stage="runtime",
            event_type="execution_terminal",
            metadata={"status": outcome.status.value},
        )
        trace.record(
            stage="outcome",
            event_type="outcome_decided",
            metadata={"status": outcome.status.value},
        )
        exported = trace.export(final_status=outcome.status)
        outcome = outcome.model_copy(update={"trace_id": exported.trace_id})
        with self._trace_lock:
            self._traces[execution.execution_id] = exported
        if {
            SafetyFlag.DIRECT_PROMPT_INJECTION,
            SafetyFlag.INDIRECT_PROMPT_INJECTION,
        }.intersection(outcome.safety_flags):
            self._audit(
                event_type="prompt_injection_flagged",
                principal=principal,
                execution=execution,
                safety_flags=outcome.safety_flags,
                metadata={"status": outcome.status.value},
            )
        self._audit(
            event_type="outcome_issued",
            principal=principal,
            execution=execution,
            outcome_status=outcome.status,
            safety_flags=outcome.safety_flags,
            tool_ids=[call.tool_id for call in outcome.tool_calls],
            metadata={"trace_id": exported.trace_id},
        )
        return outcome

    def _audit(
        self,
        *,
        event_type: str,
        principal: PrincipalContext,
        execution: ExecutionContext,
        outcome_status: OutcomeStatus | None = None,
        safety_flags: list[SafetyFlag] | tuple[SafetyFlag, ...] = (),
        tool_ids: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.audit_logger.record(
            event_type=event_type,
            principal=principal,
            execution_id=execution.execution_id,
            agent_id=execution.agent_id,
            outcome_status=outcome_status,
            safety_flags=safety_flags,
            tool_ids=tool_ids,
            metadata=metadata,
        )


def _detect_indirect_injection(result: ToolResult) -> ToolResult:
    """Label instruction-like output while keeping it as untrusted data."""

    if result.injection_flags:
        return result
    serialized = json.dumps(result.output, sort_keys=True, default=str)
    matches = indirect_injection_matches(serialized)
    return result.model_copy(update={"injection_flags": matches}) if matches else result


def _with_invocation_metadata(
    response: InterpretationResponse | PlanningResponse | CompositionResponse,
    invocation: ProviderInvocationResult,
) -> InterpretationResponse | PlanningResponse | CompositionResponse:
    metadata = response.metadata.model_copy(
        update={
            "latency_ms": invocation.latency_ms,
            "retry_count": invocation.attempts - 1,
        }
    )
    return response.model_copy(update={"metadata": metadata})


def _provider_error_code(error: BaseException) -> str:
    if isinstance(error, HarnessError):
        return error.code.value
    return "provider_failed"


def _higher_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
    order = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }
    return right if order[right] > order[left] else left


def _risk_exceeds(actual: RiskLevel, maximum: RiskLevel) -> bool:
    order = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }
    return order[actual] > order[maximum]


def _metadata_int(metadata: dict[str, str], key: str, *, default: int = 0) -> int:
    try:
        value = int(metadata.get(key, str(default)))
    except (TypeError, ValueError):
        return default
    return max(0, value)


def _approval_transition(
    *,
    status: ApprovalDecisionStatus | None,
    error_code: str | None,
) -> str:
    if error_code in {"approval_expired"} or status == ApprovalDecisionStatus.EXPIRED:
        return "approval_expired"
    if error_code is not None:
        return "approval_stale"
    return {
        ApprovalDecisionStatus.REJECTED: "approval_rejected",
        ApprovalDecisionStatus.REQUEST_CHANGES: "approval_changes_requested",
        ApprovalDecisionStatus.EXPIRED: "approval_expired",
        ApprovalDecisionStatus.APPROVED: "approval_approved",
        None: "approval_stale",
    }[status]


def _append_trace_events(
    trace: RunTrace,
    events: Sequence[TraceEvent],
    *,
    generated_at: datetime,
) -> RunTrace:
    """Append already-recorded events with contiguous trace sequence numbers."""

    offset = len(trace.events)
    appended = [event.model_copy(update={"sequence": event.sequence + offset}) for event in events]
    return trace.model_copy(
        update={
            "events": [*trace.events, *appended],
            "generated_at": generated_at,
        }
    )


def _state_status(status: OutcomeStatus) -> ExecutionStateStatus:
    return {
        OutcomeStatus.COMPLETED: ExecutionStateStatus.COMPLETED,
        OutcomeStatus.PARTIAL: ExecutionStateStatus.PAUSED,
        OutcomeStatus.NEEDS_INPUT: ExecutionStateStatus.PAUSED,
        OutcomeStatus.REFUSED: ExecutionStateStatus.REFUSED,
        OutcomeStatus.ESCALATED: ExecutionStateStatus.ESCALATED,
        OutcomeStatus.FAILED: ExecutionStateStatus.FAILED,
        OutcomeStatus.TIMED_OUT: ExecutionStateStatus.FAILED,
        OutcomeStatus.CANCELLED: ExecutionStateStatus.PAUSED,
    }[status]


def _recovery_for_status(status: OutcomeStatus) -> RecoveryAction:
    return {
        OutcomeStatus.COMPLETED: RecoveryAction.NONE,
        OutcomeStatus.PARTIAL: RecoveryAction.REQUEST_INPUT,
        OutcomeStatus.NEEDS_INPUT: RecoveryAction.REQUEST_INPUT,
        OutcomeStatus.REFUSED: RecoveryAction.REFUSE,
        OutcomeStatus.ESCALATED: RecoveryAction.ESCALATE,
        OutcomeStatus.FAILED: RecoveryAction.ABORT,
        OutcomeStatus.TIMED_OUT: RecoveryAction.ABORT,
        OutcomeStatus.CANCELLED: RecoveryAction.ABORT,
    }[status]


def _summary_for_decision(proposal: OutcomeProposal, decision: SafetyDecision) -> str:
    if decision.status == OutcomeStatus.NEEDS_INPUT:
        return (
            "The runtime needs more input or usable tool data before it can complete the execution."
        )
    if decision.status == OutcomeStatus.REFUSED:
        return "The runtime refused the execution because it crossed a configured safety boundary."
    if decision.status == OutcomeStatus.ESCALATED:
        return "The runtime requires human review before it can complete this execution."
    if decision.status == OutcomeStatus.FAILED:
        return "The execution failed before it reached a safe completed outcome."
    if decision.status == OutcomeStatus.PARTIAL:
        return f"{proposal.summary} The result is partial and requires review."
    return proposal.summary
