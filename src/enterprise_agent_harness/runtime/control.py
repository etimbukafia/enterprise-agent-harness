"""Cooperative execution controls shared by the runtime boundaries."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event, Lock
from typing import Protocol

from ..errors import (
    BudgetExhaustedError,
    ExecutionCancelledError,
    ExecutionTimeoutError,
)


class CancellationSignal(Protocol):
    """Minimal caller-owned cancellation signal."""

    def is_set(self) -> bool:
        """Return whether cancellation was requested."""


class CancellationToken:
    """Thread-safe cancellation token for a synchronous runtime call."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        """Request cancellation at the next safe runtime boundary."""

        self._event.set()

    def is_cancelled(self) -> bool:
        """Return whether cancellation was requested."""

        return self._event.is_set()

    def is_set(self) -> bool:
        """Return whether cancellation was requested.

        This alias lets a token be passed anywhere a standard ``Event`` is
        accepted.
        """

        return self.is_cancelled()


class ExecutionControl:
    """Enforce a run deadline, cancellation signal, and retry budget."""

    def __init__(
        self,
        *,
        timeout_seconds: float | None,
        max_retries: int | None,
        cancellation_signal: CancellationSignal | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        max_total_tokens: int | None = None,
        max_cost: float | None = None,
        max_tool_invocations: int | None = None,
    ) -> None:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_retries is not None and max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if max_total_tokens is not None and max_total_tokens < 1:
            raise ValueError("max_total_tokens must be positive")
        if max_cost is not None and max_cost < 0:
            raise ValueError("max_cost must not be negative")
        if max_tool_invocations is not None and max_tool_invocations < 1:
            raise ValueError("max_tool_invocations must be positive")
        self._monotonic = monotonic
        self._started_at = monotonic()
        self._deadline = self._started_at + timeout_seconds if timeout_seconds is not None else None
        self._max_retries = max_retries
        self._remaining_retries = max_retries
        self._retry_count = 0
        self._retry_budget_exhausted = False
        self._retry_lock = Lock()
        self._cancellation_signal = cancellation_signal
        self._execution_id: str | None = None
        self._max_total_tokens = max_total_tokens
        self._max_cost = max_cost
        self._max_tool_invocations = max_tool_invocations
        self._total_tokens = 0
        self._total_cost = 0.0
        self._tool_invocations = 0
        self._budget_reason: str | None = None
        self._budget_lock = Lock()

    @property
    def execution_id(self) -> str | None:
        """Return the bound execution ID, if runtime setup has completed."""

        return self._execution_id

    def bind_execution(self, execution_id: str) -> None:
        """Bind the control to the runtime execution it governs."""

        if self._execution_id is not None and self._execution_id != execution_id:
            raise ValueError("execution control cannot be rebound")
        self._execution_id = execution_id

    @property
    def deadline(self) -> float | None:
        """Return the monotonic deadline, if one is configured."""

        return self._deadline

    @property
    def retry_count(self) -> int:
        """Return the number of admitted retries for this execution."""

        with self._retry_lock:
            return self._retry_count

    @property
    def max_retries(self) -> int | None:
        """Return the configured retry budget."""

        return self._max_retries

    @property
    def retries_remaining(self) -> int | None:
        """Return the remaining retry budget."""

        with self._retry_lock:
            return self._remaining_retries

    @property
    def retry_budget_exhausted(self) -> bool:
        """Return whether a requested retry was refused by the run budget."""

        with self._retry_lock:
            return self._retry_budget_exhausted

    @property
    def total_tokens(self) -> int:
        """Return the token usage recorded against this execution."""

        with self._budget_lock:
            return self._total_tokens

    @property
    def total_cost(self) -> float:
        """Return the cost recorded against this execution."""

        with self._budget_lock:
            return self._total_cost

    @property
    def tool_invocations(self) -> int:
        """Return the number of tool invocations recorded for this execution."""

        with self._budget_lock:
            return self._tool_invocations

    @property
    def budget_reason(self) -> str | None:
        """Return the reason the budget was exhausted, if any."""

        with self._budget_lock:
            return self._budget_reason

    def record_provider_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        """Record token and cost usage and raise when a budget is exceeded."""

        self.check()
        with self._budget_lock:
            self._total_tokens += input_tokens + output_tokens
            self._total_cost += cost
            if self._max_total_tokens is not None and self._total_tokens > self._max_total_tokens:
                self._budget_reason = "max_total_tokens"
                raise BudgetExhaustedError("provider token budget exhausted")
            if self._max_cost is not None and self._total_cost > self._max_cost:
                self._budget_reason = "max_cost"
                raise BudgetExhaustedError("provider cost budget exhausted")

    def record_tool_invocation(self) -> None:
        """Record one tool invocation and raise when the invocation budget is exceeded."""

        self.check()
        with self._budget_lock:
            self._tool_invocations += 1
            if (
                self._max_tool_invocations is not None
                and self._tool_invocations > self._max_tool_invocations
            ):
                self._budget_reason = "max_tool_invocations"
                raise BudgetExhaustedError("tool invocation budget exhausted")

    def is_cancelled(self) -> bool:
        """Return whether the caller requested cancellation."""

        signal = self._cancellation_signal
        return signal is not None and signal.is_set()

    def check(self) -> None:
        """Raise a stable error when the run cannot continue."""

        if self.is_cancelled():
            raise ExecutionCancelledError()
        if self._deadline is not None and self._monotonic() >= self._deadline:
            raise ExecutionTimeoutError()

    def remaining_seconds(self) -> float | None:
        """Return the time available to the next operation."""

        if self._deadline is None:
            return None
        return max(0.0, self._deadline - self._monotonic())

    def admit_retry(self) -> bool:
        """Consume one retry when the run still has retry capacity."""

        self.check()
        with self._retry_lock:
            if self._remaining_retries is not None and self._remaining_retries <= 0:
                self._retry_budget_exhausted = True
                return False
            if self._remaining_retries is not None:
                self._remaining_retries -= 1
            self._retry_count += 1
            return True


__all__ = ["CancellationSignal", "CancellationToken", "ExecutionControl"]
