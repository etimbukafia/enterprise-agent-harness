"""Shortest runnable governed-agent path.

Run from the repository root with:

    python -m examples.quickstart
"""

from __future__ import annotations

from ._support import principal
from .read_only_analyst import build_agent


def main() -> None:
    """Build, run, and inspect one governed agent."""

    agent = build_agent()
    outcome = agent.execute(
        principal(),
        "Review record-42",
        execution_id="quickstart-execution",
    )
    trace = agent.trace_for(outcome.execution_id)
    print(f"status: {outcome.status.value}")
    print(f"summary: {outcome.summary}")
    print(f"trace: {trace.trace_id}")


if __name__ == "__main__":
    main()
