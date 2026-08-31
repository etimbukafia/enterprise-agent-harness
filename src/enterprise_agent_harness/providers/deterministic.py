"""Offline deterministic provider for runtime tests and local examples."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..contracts import (
    AgentPlan,
    CapabilityDefinition,
    CompiledContext,
    ExecutionContext,
    OutcomeProposal,
    PlanStep,
    ToolDescriptor,
    ToolResult,
    ToolResultStatus,
)
from .contracts import (
    CompositionRequest,
    CompositionResponse,
    InterpretationProposal,
    InterpretationRequest,
    InterpretationResponse,
    PlanningRequest,
    PlanningResponse,
    ProviderCallMetadata,
)

ArgumentBuilder = Callable[[CompiledContext, ToolDescriptor], dict[str, Any]]


class DeterministicProvider:
    """Choose one configured tool and compose a predictable outcome.

    This provider does not receive handlers, credentials, permissions, or
    approval controls. It is suitable for offline conformance and runtime
    tests.
    """

    def __init__(
        self,
        *,
        tool_id: str | None = None,
        argument_builder: ArgumentBuilder | None = None,
        provider_id: str = "deterministic",
        provider_version: str = "1.0.0",
        model: str = "deterministic-model",
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must not be negative")
        self.tool_id = tool_id
        self.argument_builder = argument_builder
        self.provider_id = provider_id
        self.provider_version = provider_version
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def interpret(
        self,
        *,
        request: InterpretationRequest | None = None,
        context: CompiledContext | None = None,
        execution: ExecutionContext | None = None,
        capabilities: Sequence[CapabilityDefinition] = (),
        tools: Sequence[ToolDescriptor] = (),
    ) -> InterpretationResponse:
        """Return a deterministic generic interpretation proposal."""

        resolved = _interpretation_request(
            request,
            context=context,
            execution=execution,
            capabilities=capabilities,
            tools=tools,
        )
        del execution, capabilities, tools
        return InterpretationResponse(
            proposal=InterpretationProposal(
                intent=resolved.context.input_text,
                confidence=1.0,
            ),
            metadata=self._metadata(resolved.request_id),
        )

    def plan(
        self,
        *,
        request: PlanningRequest | None = None,
        context: CompiledContext | None = None,
        execution: ExecutionContext | None = None,
        capabilities: Sequence[CapabilityDefinition] = (),
        tools: Sequence[ToolDescriptor] = (),
    ) -> PlanningResponse:
        """Choose one configured tool and return a typed planning response."""

        resolved = _planning_request(
            request,
            context=context,
            execution=execution,
            capabilities=capabilities,
            tools=tools,
        )
        selected = next((tool for tool in resolved.tools if tool.tool_id == self.tool_id), None)
        selected = selected or (resolved.tools[0] if resolved.tools else None)
        if selected is None:
            plan = AgentPlan(stop_reason="no_authorized_tool_available")
        else:
            arguments = (
                self.argument_builder(resolved.context, selected)
                if self.argument_builder is not None
                else _default_arguments(resolved.context, selected)
            )
            idempotency_key = (
                f"{resolved.context.execution_id}:step_1" if selected.idempotency_required else None
            )
            plan = AgentPlan(
                steps=[
                    PlanStep(
                        step_id="step_1",
                        tool_id=selected.tool_id,
                        tool_version=selected.version,
                        purpose="Execute the configured capability operation.",
                        arguments=arguments,
                        idempotency_key=idempotency_key,
                    )
                ]
            )
        return PlanningResponse(plan=plan, metadata=self._metadata(resolved.request_id))

    def compose(
        self,
        *,
        request: CompositionRequest | None = None,
        context: CompiledContext | None = None,
        execution: ExecutionContext | None = None,
        plan: AgentPlan | None = None,
        tool_results: Sequence[ToolResult] = (),
    ) -> CompositionResponse:
        """Compose a predictable typed outcome proposal."""

        resolved = _composition_request(
            request,
            context=context,
            execution=execution,
            plan=plan,
            tool_results=tool_results,
        )
        successful = [
            result
            for result in resolved.tool_results
            if result.status == ToolResultStatus.SUCCEEDED
        ]
        evidence_ids = [item.evidence_id for result in successful for item in result.evidence]
        confidence = min((result.confidence for result in successful), default=0.0)
        proposal = OutcomeProposal(
            summary=(
                f"Execution produced {len(successful)} successful tool result(s)."
                if successful
                else "Execution produced no successful tool result."
            ),
            confidence=confidence,
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            output={"successful_result_count": len(successful)},
        )
        return CompositionResponse(
            proposal=proposal,
            metadata=self._metadata(resolved.request_id),
        )

    def _metadata(self, request_id: str) -> ProviderCallMetadata:
        return ProviderCallMetadata(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            model=self.model,
            request_id=request_id,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.input_tokens + self.output_tokens,
        )


def _default_arguments(context: CompiledContext, descriptor: ToolDescriptor) -> dict[str, Any]:
    if not descriptor.input_fields:
        return {}
    preferred = next(
        (
            name
            for name in ("input_text", "input", "request", "query")
            if name in descriptor.input_fields
        ),
        descriptor.input_fields[0],
    )
    return {preferred: context.input_text}


def _interpretation_request(
    request: InterpretationRequest | None,
    *,
    context: CompiledContext | None,
    execution: ExecutionContext | None,
    capabilities: Sequence[CapabilityDefinition],
    tools: Sequence[ToolDescriptor],
) -> InterpretationRequest:
    if request is not None:
        return request
    if context is None or execution is None:
        raise ValueError("context and execution are required without a request")
    return InterpretationRequest(
        request_id=f"{execution.execution_id}:interpret",
        context=context,
        execution=execution,
        capabilities=list(capabilities),
        tools=list(tools),
    )


def _planning_request(
    request: PlanningRequest | None,
    *,
    context: CompiledContext | None,
    execution: ExecutionContext | None,
    capabilities: Sequence[CapabilityDefinition],
    tools: Sequence[ToolDescriptor],
) -> PlanningRequest:
    if request is not None:
        return request
    if context is None or execution is None:
        raise ValueError("context and execution are required without a request")
    return PlanningRequest(
        request_id=f"{execution.execution_id}:plan",
        context=context,
        execution=execution,
        capabilities=list(capabilities),
        tools=list(tools),
    )


def _composition_request(
    request: CompositionRequest | None,
    *,
    context: CompiledContext | None,
    execution: ExecutionContext | None,
    plan: AgentPlan | None,
    tool_results: Sequence[ToolResult],
) -> CompositionRequest:
    if request is not None:
        return request
    if context is None or execution is None or plan is None:
        raise ValueError("context, execution, and plan are required without a request")
    return CompositionRequest(
        request_id=f"{execution.execution_id}:compose",
        context=context,
        execution=execution,
        plan=plan,
        tool_results=list(tool_results),
    )


__all__ = ["DeterministicProvider"]
