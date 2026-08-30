"""Provider-neutral adapter boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ..contracts import (
    AgentPlan,
    OutcomeProposal,
)
from .contracts import (
    CompositionRequest,
    CompositionResponse,
    InterpretationProposal,
    InterpretationRequest,
    InterpretationResponse,
    PlanningRequest,
    PlanningResponse,
)

InterpretationReturn = InterpretationResponse | InterpretationProposal | Mapping[str, object]
PlanningReturn = PlanningResponse | AgentPlan | Mapping[str, object]
CompositionReturn = CompositionResponse | OutcomeProposal | Mapping[str, object]


class ProviderAdapter(Protocol):
    """Provider boundary for untrusted interpretation and execution proposals."""

    def plan(
        self,
        *,
        request: PlanningRequest,
    ) -> PlanningReturn:
        """Propose bounded tool calls. The runtime validates the result."""

    def interpret(
        self,
        *,
        request: InterpretationRequest,
    ) -> InterpretationReturn:
        """Propose a generic interpretation. The runtime treats it as data."""

    def compose(
        self,
        *,
        request: CompositionRequest,
    ) -> CompositionReturn:
        """Propose an outcome summary. The runtime chooses the final state."""
