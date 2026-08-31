"""Safe reporting for observability sink failures."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from threading import RLock
from typing import Literal, Protocol

from pydantic import Field, model_validator

from ..contracts import ContractModel, utc_now


class ObservabilityFailure(ContractModel):
    """Safe evidence that an audit or trace sink could not persist a record."""

    schema_version: Literal["agent-observability-failure.v1"] = "agent-observability-failure.v1"
    failure_id: str = Field(min_length=1)
    sink_type: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    error_type: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def timestamp_is_aware(self) -> ObservabilityFailure:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include timezone information")
        return self


class ObservabilityFailureReporter(Protocol):
    """Storage boundary for safe observability failure evidence."""

    def record(self, failure: ObservabilityFailure) -> None:
        """Persist one safe sink-failure record."""


class ListObservabilityFailureReporter:
    """Thread-safe in-memory failure reporter for tests and local use."""

    def __init__(self) -> None:
        self.failures: list[ObservabilityFailure] = []
        self._lock = RLock()

    def record(self, failure: ObservabilityFailure) -> None:
        with self._lock:
            self.failures.append(deepcopy(failure))


def report_observability_failure(
    reporter: ObservabilityFailureReporter,
    *,
    id_factory: Callable[[str], str],
    clock: Callable[[], datetime],
    sink: object,
    operation: str,
    execution_id: str,
    correlation_id: str,
    error: BaseException,
) -> None:
    """Send safe failure evidence to a separate reporter.

    A failure reporter is also an extension boundary. Its own failure is
    swallowed so observability can never change runtime governance.
    """

    failure = ObservabilityFailure(
        failure_id=id_factory("observability_failure"),
        sink_type=type(sink).__name__,
        operation=operation,
        execution_id=execution_id,
        correlation_id=correlation_id,
        error_type=type(error).__name__,
        occurred_at=clock(),
    )
    try:
        reporter.record(failure)
    except Exception:  # noqa: BLE001 - failure reporting must remain best effort.
        return


__all__ = [
    "ListObservabilityFailureReporter",
    "ObservabilityFailure",
    "ObservabilityFailureReporter",
    "report_observability_failure",
]
