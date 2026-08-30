"""Bounded provider-to-tool execution for general agent workflows."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from inspect import Parameter, signature
from threading import RLock
from typing import cast
from uuid import uuid4

from ..capabilities import CapabilityDefinition
from ..contracts import (
    AgentOutcome,
    AgentPlan,
    CompiledContext,
    ExecutionContext,
    ExecutionState,
    ExecutionStateStatus,
    OutcomeProposal,
    OutcomeStatus,
    PermissionDecision,
    PrincipalContext,
    RecoveryAction,
    RiskLevel,
    RuntimeConfig,
    SafetyFlag,
    ToolCallRecord,
    ToolResult,
    ToolResultStatus,
    VerificationResult,
)
from ..errors import HarnessError, ProviderOutputError, ProviderTimeoutError
from ..evaluation.contracts import RunTrace
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
from ..tools.definitions import ToolInvocationError
from ..tools.registry import ToolRegistry
from ..verification.outcomes import verify_outcome
from .context import ContextCompiler


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
        )
        trace = TraceRecorder(
            execution=execution,
            input_text=text,
            sink=self.trace_sink,
            id_factory=self._id,
            clock=self._clock,
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
        interpretation: InterpretationResponse | None = None
        interpretation_fn = getattr(self.provider, "interpret", None)
        if callable(interpretation_fn):
            interpretation_request = InterpretationRequest(
                request_id=self._id("provider_request"),
                context=context,
                execution=execution,
                capabilities=[capability.model_copy(deep=True) for capability in self.capabilities],
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
                )
                interpretation = normalize_interpretation(invocation.value)
                interpretation = cast(
                    InterpretationResponse,
                    _with_invocation_metadata(interpretation, invocation),
                )
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
            )
            planning_response = normalize_plan(invocation.value)
            planning_response = cast(
                PlanningResponse,
                _with_invocation_metadata(planning_response, invocation),
            )
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
        tool_results: list[ToolResult] = []
        tool_calls: list[ToolCallRecord] = []
        highest_risk = RiskLevel.LOW
        for step in plan.steps:
            tool = self.tools.get(step.tool_id, step.tool_version)
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

            permission = self.permission_broker.authorize(
                principal=principal,
                execution=execution,
                tool=tool,
                arguments=step.arguments,
            )
            permission = PermissionDecision.model_validate(permission)
            if tool.tool_id not in execution.authorized_tool_ids:
                permission = permission.model_copy(
                    update={
                        "allowed": False,
                        "reason_code": "tool_not_authorized",
                        "approval_required": False,
                    }
                )
            elif self.capabilities and tool.tool_id not in self._capability_tool_ids:
                permission = permission.model_copy(
                    update={
                        "allowed": False,
                        "reason_code": "tool_not_in_capability",
                        "approval_required": False,
                    }
                )
            if (
                tool.requires_approval
                and tool.action_digest(step.arguments) not in execution.approved_action_digests
            ):
                permission = permission.model_copy(
                    update={
                        "allowed": False,
                        "reason_code": "approval_required",
                        "approval_required": True,
                    }
                )
            if not permission.allowed:
                result_status = (
                    ToolResultStatus.APPROVAL_REQUIRED
                    if permission.approval_required
                    else ToolResultStatus.PERMISSION_DENIED
                )
                tool_calls.append(
                    ToolCallRecord(
                        call_id=self._id("call"),
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
                outcome = self._decision_outcome(
                    execution=execution,
                    status=(
                        OutcomeStatus.ESCALATED
                        if permission.approval_required
                        else OutcomeStatus.REFUSED
                    ),
                    summary=(
                        "The action requires exact human approval before execution."
                        if permission.approval_required
                        else "The proposed tool call is not authorized for this execution."
                    ),
                    flags=[
                        SafetyFlag.APPROVAL_REQUIRED
                        if permission.approval_required
                        else SafetyFlag.PERMISSION_DENIED
                    ],
                    tool_calls=tool_calls,
                    recovery=(
                        RecoveryAction.ESCALATE
                        if permission.approval_required
                        else RecoveryAction.REFUSE
                    ),
                    human_review_required=permission.approval_required,
                    error_code=permission.reason_code,
                )
                return self._finish(
                    principal=principal,
                    execution=execution,
                    state=state,
                    outcome=outcome,
                    trace=trace,
                )

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
                result = tool.invoke(execution, step.arguments)
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
            latency_ms = (time.perf_counter() - started) * 1000.0
            tool_results.append(result)
            tool_calls.append(
                ToolCallRecord(
                    call_id=self._id("call"),
                    step_id=step.step_id,
                    tool_id=tool.tool_id,
                    tool_version=tool.version,
                    arguments=tool.redact_arguments(step.arguments),
                    result_status=result.status,
                    evidence_ids=[item.evidence_id for item in result.evidence],
                    latency_ms=latency_ms,
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

        result_context = self._compile_context(
            principal,
            execution,
            state,
            text,
            tool_results=tool_results,
        )
        composition_request = CompositionRequest(
            request_id=self._id("provider_request"),
            context=result_context,
            execution=execution,
            plan=plan,
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
            )
            composition_response = normalize_composition(invocation.value)
            composition_response = cast(
                CompositionResponse,
                _with_invocation_metadata(composition_response, invocation),
            )
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
            flags=list(decision.flags),
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
    ) -> ProviderInvocationResult:
        return invoke_provider_call(
            operation=operation,
            call=call,
            policy=self.provider_call_policy,
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
        tool_calls: list[ToolCallRecord] | None = None,
    ) -> AgentOutcome:
        error_code = _provider_error_code(error)
        flags = [SafetyFlag.PROVIDER_FAILURE]
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
                outcome = outcome.model_copy(
                    update={
                        "status": OutcomeStatus.FAILED,
                        "summary": "The runtime could not commit workflow state safely.",
                        "error_code": "state_conflict",
                        "recovery": RecoveryAction.ABORT,
                    }
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
