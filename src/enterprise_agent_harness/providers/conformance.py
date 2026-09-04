"""Small public-boundary provider conformance probe."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..contracts import (
    AgentPlan,
    CompiledContext,
    ExecutionContext,
    OutcomeProposal,
    PromptDefinition,
    SkillDefinition,
    ToolDescriptor,
)
from .base import ProviderAdapter
from .contracts import (
    CompositionRequest,
    InterpretationRequest,
    InterpretationResponse,
    PlanningRequest,
)
from .normalization import normalize_composition, normalize_interpretation, normalize_plan


@dataclass(frozen=True)
class ProviderConformanceResult:
    """Typed results from one provider boundary probe."""

    interpretation: InterpretationResponse
    plan: AgentPlan
    outcome_proposal: OutcomeProposal


def run_conformance_probe(
    provider: ProviderAdapter,
    *,
    context: CompiledContext,
    execution: ExecutionContext,
    prompt: PromptDefinition | None = None,
    skills: Sequence[SkillDefinition] = (),
    tools: Sequence[ToolDescriptor] = (),
) -> ProviderConformanceResult:
    """Verify all provider operations return the public typed contracts."""

    interpretation = normalize_interpretation(
        provider.interpret(
            request=InterpretationRequest(
                request_id="conformance:interpret",
                context=context,
                execution=execution,
                prompt=prompt.model_copy(deep=True) if prompt is not None else None,
                skills=[skill.model_copy(deep=True) for skill in skills],
                tools=list(tools),
            )
        )
    )
    plan_response = normalize_plan(
        provider.plan(
            request=PlanningRequest(
                request_id="conformance:plan",
                context=context,
                execution=execution,
                prompt=prompt.model_copy(deep=True) if prompt is not None else None,
                skills=[skill.model_copy(deep=True) for skill in skills],
                tools=list(tools),
                interpretation=interpretation,
            )
        )
    )
    composition = normalize_composition(
        provider.compose(
            request=CompositionRequest(
                request_id="conformance:compose",
                context=context,
                execution=execution,
                plan=plan_response.plan,
                tool_results=[],
            )
        )
    )
    return ProviderConformanceResult(
        interpretation=interpretation,
        plan=plan_response.plan,
        outcome_proposal=composition.proposal,
    )
