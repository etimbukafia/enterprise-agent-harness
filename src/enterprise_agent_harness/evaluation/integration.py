"""Public helpers for external evaluation integration.

This module exports recorded evidence. It does not define evaluation policy or
invoke live tools during replay.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar

from ..contracts import ResolvedAgentManifest
from ..factory import BuiltAgent
from .contracts import (
    EvaluationEvidence,
    EvaluationSubject,
    RecordedReplay,
    RunTrace,
    TestCaseAdapter,
)

TestCaseT = TypeVar("TestCaseT")


def export_run_trace(trace: RunTrace) -> dict[str, Any]:
    """Return the stable JSON-safe representation of one run trace."""

    return trace.model_dump(mode="json")


def export_agent_manifest(manifest: ResolvedAgentManifest) -> dict[str, Any]:
    """Return the stable JSON-safe representation of one resolved manifest."""

    return manifest.model_dump(mode="json")


def execute_test_case(
    agent: BuiltAgent,
    test_case: TestCaseT,
    *,
    adapter: TestCaseAdapter[TestCaseT],
    role: Literal["baseline", "candidate"],
) -> EvaluationEvidence:
    """Run one adapted case through a built agent and export its evidence."""

    request = adapter.adapt(test_case)
    outcome = agent.execute(
        request.principal,
        request.input_text,
        execution_id=request.execution_id,
        correlation_id=request.correlation_id,
        resource=request.resource,
    )
    return EvaluationEvidence(
        case_id=request.case_id,
        subject=EvaluationSubject.from_manifest(agent.manifest, role=role),
        run_trace=export_run_trace(agent.trace_for(outcome.execution_id)),
        manifest=export_agent_manifest(agent.manifest),
    )


class RecordedReplayAdapter:
    """Reconstruct recorded evidence without invoking providers or tools."""

    def replay(self, exported_trace: RunTrace | Mapping[str, Any]) -> RecordedReplay:
        """Validate and return a recorded trace without performing live actions."""

        trace = (
            exported_trace.model_copy(deep=True)
            if isinstance(exported_trace, RunTrace)
            else RunTrace.model_validate(exported_trace)
        )
        return RecordedReplay(
            source_trace_id=trace.trace_id,
            source_execution_id=trace.execution_id,
            trace=trace,
        )
