"""Cooperative execution controls shared by the runtime boundaries."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event, Lock
from typing import Protocol

from ..errors import ExecutionCancelledError, ExecutionTimeoutError


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
    ) -> None:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_retries is not None and max_retries < 0:
            raise ValueError("max_retries must not be negative")
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
