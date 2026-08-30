"""Normalize provider-specific proposal shapes into runtime contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts import (
    AgentPlan,
    OutcomeProposal,
    PlanStep,
    ToolCall,
)
from ..errors import ProviderOutputError
from .contracts import (
    CompositionResponse,
    InterpretationProposal,
    InterpretationResponse,
    PlanningResponse,
    ProviderCallMetadata,
)


def normalize_interpretation(value: object) -> InterpretationResponse:
    """Return a validated interpretation response from a provider value."""

    if isinstance(value, InterpretationResponse):
        return InterpretationResponse.model_validate(value)
    if isinstance(value, InterpretationProposal):
        return InterpretationResponse(proposal=value)
    mapping = _mapping(value, "interpretation response")
    metadata = _metadata(mapping.get("metadata"))
    proposal_value = mapping.get(
        "proposal",
        {key: item for key, item in mapping.items() if key != "metadata"},
    )
    try:
        proposal = InterpretationProposal.model_validate(proposal_value)
    except Exception as exc:  # Pydantic supplies the detailed boundary error.
        raise ProviderOutputError("provider interpretation output is invalid") from exc
    return InterpretationResponse(proposal=proposal, metadata=metadata)


def normalize_plan(value: object) -> PlanningResponse:
    """Return a validated planning response with canonical tool-call steps."""

    if isinstance(value, PlanningResponse):
        return PlanningResponse(
            plan=AgentPlan(
                steps=normalize_tool_calls(value.plan.steps),
                stop_reason=value.plan.stop_reason,
            ),
            metadata=ProviderCallMetadata.model_validate(value.metadata),
        )
    if isinstance(value, AgentPlan):
        return PlanningResponse(
            plan=AgentPlan(
                steps=normalize_tool_calls(value.steps),
                stop_reason=value.stop_reason,
            )
        )

    if isinstance(value, Mapping):
        metadata = _metadata(value.get("metadata"))
        if "plan" in value:
            plan_value = value["plan"]
            if isinstance(plan_value, AgentPlan):
                return PlanningResponse(
                    plan=AgentPlan(
                        steps=normalize_tool_calls(plan_value),
                        stop_reason=plan_value.stop_reason,
                    ),
                    metadata=metadata,
                )
            stop_reason = None
            if isinstance(plan_value, Mapping):
                stop_reason_value = plan_value.get("stop_reason")
                stop_reason = str(stop_reason_value) if stop_reason_value is not None else None
            if stop_reason is None:
                stop_reason_value = value.get("stop_reason")
                stop_reason = str(stop_reason_value) if stop_reason_value is not None else None
            steps = normalize_tool_calls(plan_value)
            return PlanningResponse(
                plan=AgentPlan(steps=steps, stop_reason=stop_reason),
                metadata=metadata,
            )
        stop_reason_value = value.get("stop_reason")
        return PlanningResponse(
            plan=AgentPlan(
                steps=normalize_tool_calls(value),
                stop_reason=str(stop_reason_value) if stop_reason_value is not None else None,
            ),
            metadata=metadata,
        )

    return PlanningResponse(plan=AgentPlan(steps=normalize_tool_calls(value)))


def normalize_composition(value: object) -> CompositionResponse:
    """Return a validated composition response from a provider value."""

    if isinstance(value, CompositionResponse):
        return CompositionResponse.model_validate(value)
    if isinstance(value, OutcomeProposal):
        return CompositionResponse(proposal=value)
    mapping = _mapping(value, "composition response")
    metadata = _metadata(mapping.get("metadata"))
    proposal_value = mapping.get(
        "proposal",
        {key: item for key, item in mapping.items() if key != "metadata"},
    )
    try:
        proposal = OutcomeProposal.model_validate(proposal_value)
    except Exception as exc:  # Pydantic supplies the detailed boundary error.
        raise ProviderOutputError("provider composition output is invalid") from exc
    return CompositionResponse(proposal=proposal, metadata=metadata)


def normalize_tool_calls(value: object) -> list[PlanStep]:
    """Normalize common provider tool-call shapes into `PlanStep` values."""

    if isinstance(value, AgentPlan):
        values: object = value.steps
    elif isinstance(value, PlanningResponse):
        values = value.plan.steps
    elif isinstance(value, Mapping):
        values = _tool_call_collection(value)
    else:
        values = value

    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ProviderOutputError("provider tool calls must be a sequence")
    return [normalize_tool_call(item, index=index) for index, item in enumerate(values, start=1)]


def normalize_tool_call(value: object, *, index: int) -> PlanStep:
    """Normalize one provider tool-call item into one canonical plan step."""

    if isinstance(value, PlanStep):
        return PlanStep.model_validate(value)
    if isinstance(value, ToolCall):
        return PlanStep(
            step_id=value.call_id or f"step_{index}",
            tool_id=value.tool_id,
            tool_version=value.tool_version,
            purpose=value.purpose,
            arguments=value.arguments,
            idempotency_key=value.idempotency_key,
        )

    raw = _mapping(value, "tool call")
    function = raw.get("function", raw.get("function_call", raw.get("functionCall")))
    function_mapping = function if isinstance(function, Mapping) else {}

    tool_id = _first_string(
        raw.get("tool_id"),
        raw.get("tool_name"),
        raw.get("name"),
        raw.get("tool"),
        function_mapping.get("name"),
    )
    if tool_id is None:
        raise ProviderOutputError("provider tool call has no tool identity")

    arguments_value: object = raw.get(
        "arguments",
        raw.get("args", raw.get("input", raw.get("parameters", {}))),
    )
    if "arguments" not in raw and "arguments" in function_mapping:
        arguments_value = function_mapping["arguments"]
    elif "arguments" not in raw and "input" in function_mapping:
        arguments_value = function_mapping["input"]
    elif "arguments" not in raw and "args" in function_mapping:
        arguments_value = function_mapping["args"]
    arguments = _arguments(arguments_value)
    step_id = _first_string(raw.get("step_id"), raw.get("call_id"), raw.get("id"))
    purpose = _first_string(raw.get("purpose"), raw.get("reason"), raw.get("description"))
    version = _first_string(raw.get("tool_version"), raw.get("version"))
    idempotency_key = _first_string(raw.get("idempotency_key"))
    return PlanStep(
        step_id=step_id or f"step_{index}",
        tool_id=tool_id,
        tool_version=version,
        purpose=purpose or "Provider-proposed tool call.",
        arguments=arguments,
        idempotency_key=idempotency_key,
    )


def _tool_call_collection(value: Mapping[str, object]) -> object:
    for key in ("steps", "tool_calls", "calls", "content", "output"):
        if key in value:
            return value[key]
    for key in ("function_call", "functionCall", "tool_call"):
        if key in value:
            return [value[key]]
    choices = value.get("choices")
    if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes, bytearray)):
        calls: list[object] = []
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            message = choice.get("message", choice)
            if not isinstance(message, Mapping):
                continue
            try:
                nested = _tool_call_collection(message)
            except ProviderOutputError:
                continue
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes, bytearray)):
                calls.extend(nested)
            else:
                calls.append(nested)
        if calls:
            return calls
    if any(
        key in value
        for key in ("tool_id", "tool_name", "name", "tool", "function", "function_call")
    ):
        return [value]
    raise ProviderOutputError("provider plan has no tool-call collection")


def _arguments(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ProviderOutputError("provider tool arguments are not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ProviderOutputError("provider tool arguments must be an object")
    return dict(value)


def _first_string(*values: object) -> str | None:
    return next(
        (value.strip() for value in values if isinstance(value, str) and value.strip()), None
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProviderOutputError(f"provider {label} must be an object")
    return value


def _metadata(value: object) -> ProviderCallMetadata:
    if value is None:
        return ProviderCallMetadata()
    try:
        return ProviderCallMetadata.model_validate(value)
    except Exception as exc:
        raise ProviderOutputError("provider call metadata is invalid") from exc


__all__ = [
    "normalize_composition",
    "normalize_interpretation",
    "normalize_plan",
    "normalize_tool_call",
    "normalize_tool_calls",
]
