"""Runtime-facing trace and external evaluation contracts.

This package does not contain evaluation cases, graders, metrics, or baseline
comparison logic. Those belong to an external evaluation system.
"""

from .contracts import (
    EvaluationEvidence,
    EvaluationExecutionInput,
    EvaluationSubject,
    HardGateHook,
    MetricHook,
    RecordedReplay,
    ReplayRequest,
    RunTrace,
    TestCaseAdapter,
    TraceEvent,
)

__all__ = [
    "EvaluationEvidence",
    "EvaluationExecutionInput",
    "EvaluationSubject",
    "HardGateHook",
    "MetricHook",
    "RecordedReplay",
    "ReplayRequest",
    "RunTrace",
    "TestCaseAdapter",
    "TraceEvent",
]
