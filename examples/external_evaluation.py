"""Minimal external evaluation integration.

The application builds ``agent``. The external evaluator owns case data,
metrics, and the hard gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from enterprise_agent_harness import (
    BuiltAgent,
    EvaluationEvidence,
    EvaluationExecutionInput,
    PrincipalContext,
    RecordedReplayAdapter,
    execute_test_case,
)


@dataclass(frozen=True)
class LabCase:
    case_id: str
    prompt: str


class LabCaseAdapter:
    def adapt(self, test_case: LabCase) -> EvaluationExecutionInput:
        return EvaluationExecutionInput(
            case_id=test_case.case_id,
            principal=PrincipalContext(
                principal_id="evaluation-lab",
                tenant_id="evaluation-tenant",
                session_id="evaluation-session",
            ),
            input_text=test_case.prompt,
            execution_id=f"evaluation-{test_case.case_id}",
        )


def evaluate(agent: BuiltAgent, test_case: LabCase) -> tuple[dict[str, float], bool]:
    evidence = execute_test_case(
        agent,
        test_case,
        adapter=LabCaseAdapter(),
        role="candidate",
    )
    metrics = external_metrics(evidence)
    passed = external_hard_gate(evidence)

    # This is offline. It validates and reconstructs evidence only.
    replay = RecordedReplayAdapter().replay(evidence.run_trace)
    assert replay.live_actions_executed is False
    return metrics, passed


def external_metrics(evidence: EvaluationEvidence) -> dict[str, float]:
    """Replace this with a metric from an optional external evaluation package."""

    return {"completed": float(evidence.run_trace["final_status"] == "completed")}


def external_hard_gate(evidence: EvaluationEvidence) -> bool:
    """Replace this with an external hard-gate policy."""

    return evidence.run_trace["final_status"] == "completed"
