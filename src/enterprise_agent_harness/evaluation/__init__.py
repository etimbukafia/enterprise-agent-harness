"""Runtime-facing trace and replay contracts.

This package does not contain evaluation cases, graders, metrics, or baseline
comparison logic. Those belong to an external evaluation system.
"""

from .contracts import ReplayRequest, RunTrace, TraceEvent

__all__ = ["ReplayRequest", "RunTrace", "TraceEvent"]
