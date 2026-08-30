"""Timeout and retry hooks for provider calls."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Protocol

from ..errors import (
    ExecutionCancelledError,
    ExecutionTimeoutError,
    ProviderError,
    ProviderTimeoutError,
)
from .contracts import ProviderOperation


class ProviderCallPolicy(Protocol):
    """Application-configurable timeout and retry policy for provider calls."""

    def timeout_seconds(self, operation: ProviderOperation) -> float | None:
        """Return the timeout for one operation, or `None` for no timeout."""

    def max_attempts(self, operation: ProviderOperation) -> int:
        """Return the total number of attempts allowed for one operation."""

    def should_retry(
        self,
        *,
        operation: ProviderOperation,
        error: BaseException,
        attempt: int,
    ) -> bool:
        """Return whether the failed attempt may be retried."""

    def backoff_seconds(
        self,
        *,
        operation: ProviderOperation,
        error: BaseException,
        attempt: int,
    ) -> float:
        """Return the delay before a retry."""


@dataclass(frozen=True)
class DefaultProviderCallPolicy:
    """Safe default provider policy with no retry and a finite timeout."""

    timeout_seconds_value: float = 30.0
    max_attempts_value: int = 1
    retry_backoff_seconds_value: float = 0.0
    retryable_exception_types: tuple[type[BaseException], ...] = (
        ConnectionError,
        TimeoutError,
        ProviderTimeoutError,
    )

    def __post_init__(self) -> None:
        if self.timeout_seconds_value <= 0:
            raise ValueError("timeout_seconds_value must be greater than zero")
        if self.max_attempts_value < 1:
            raise ValueError("max_attempts_value must be at least one")
        if self.retry_backoff_seconds_value < 0:
            raise ValueError("retry_backoff_seconds_value must not be negative")

    def timeout_seconds(self, operation: ProviderOperation) -> float | None:
        del operation
        return self.timeout_seconds_value

    def max_attempts(self, operation: ProviderOperation) -> int:
        del operation
        return self.max_attempts_value

    def should_retry(
        self,
        *,
        operation: ProviderOperation,
        error: BaseException,
        attempt: int,
    ) -> bool:
        del operation
        if attempt >= self.max_attempts_value:
            return False
        return isinstance(error, self.retryable_exception_types) or bool(
            getattr(error, "retryable", False)
        )

    def backoff_seconds(
        self,
        *,
        operation: ProviderOperation,
        error: BaseException,
        attempt: int,
    ) -> float:
        del operation, error, attempt
        return self.retry_backoff_seconds_value


@dataclass(frozen=True)
class ProviderInvocationResult:
    """Result and measured data from one provider operation."""

    value: object
    attempts: int
    latency_ms: float


def invoke_provider_call(
    *,
    operation: ProviderOperation,
    call: Callable[[], object],
    policy: ProviderCallPolicy,
    sleep: Callable[[float], None] = time.sleep,
    cancellation_check: Callable[[], bool] | None = None,
    deadline: float | None = None,
) -> ProviderInvocationResult:
    """Run one provider call with timeout, cancellation, and retry hooks."""

    max_attempts = policy.max_attempts(operation)
    if max_attempts < 1:
        raise ValueError("provider policy must allow at least one attempt")
    started = time.perf_counter()
    last_error: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            value = _run_with_timeout(
                call,
                policy.timeout_seconds(operation),
                cancellation_check=cancellation_check,
                deadline=deadline,
            )
            return ProviderInvocationResult(
                value=value,
                attempts=attempt,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        except (ExecutionCancelledError, ExecutionTimeoutError):
            raise
        except Exception as exc:  # noqa: BLE001 - provider code is an extension boundary.
            last_error = exc
            if not policy.should_retry(operation=operation, error=exc, attempt=attempt):
                break
            delay = policy.backoff_seconds(operation=operation, error=exc, attempt=attempt)
            if delay > 0:
                _sleep_with_control(
                    delay,
                    sleep=sleep,
                    cancellation_check=cancellation_check,
                    deadline=deadline,
                )

    assert last_error is not None
    if isinstance(last_error, ProviderError):
        raise last_error
    raise ProviderError(
        f"provider {operation.value} call failed",
        retryable=bool(getattr(last_error, "retryable", False)),
    ) from last_error


def _run_with_timeout(
    call: Callable[[], object],
    timeout_seconds: float | None,
    *,
    cancellation_check: Callable[[], bool] | None = None,
    deadline: float | None = None,
) -> object:
    if timeout_seconds is None and cancellation_check is None and deadline is None:
        return call()

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(call)
    local_deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    try:
        while True:
            if cancellation_check is not None and cancellation_check():
                future.cancel()
                raise ExecutionCancelledError()
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                future.cancel()
                raise ExecutionTimeoutError()
            if local_deadline is not None and now >= local_deadline:
                future.cancel()
                raise ProviderTimeoutError()

            wait_for = _wait_seconds(
                now=now,
                local_deadline=local_deadline,
                deadline=deadline,
                poll=cancellation_check is not None,
            )
            try:
                value = future.result(timeout=wait_for)
                if cancellation_check is not None and cancellation_check():
                    raise ExecutionCancelledError()
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise ExecutionTimeoutError()
                if local_deadline is not None and now >= local_deadline:
                    raise ProviderTimeoutError()
                return value
            except FutureTimeoutError as exc:
                now = time.monotonic()
                if (
                    deadline is not None
                    and (local_deadline is None or deadline <= local_deadline)
                    and now >= deadline
                ):
                    future.cancel()
                    raise ExecutionTimeoutError() from exc
                if local_deadline is not None and now >= local_deadline:
                    future.cancel()
                    raise ProviderTimeoutError() from exc
                if deadline is not None and now >= deadline:
                    future.cancel()
                    raise ExecutionTimeoutError() from exc
    finally:
        # A running provider call cannot be force-stopped. Do not block the
        # runtime after the timeout or cancellation has been reported.
        executor.shutdown(wait=False, cancel_futures=True)


def _wait_seconds(
    *,
    now: float,
    local_deadline: float | None,
    deadline: float | None,
    poll: bool,
) -> float | None:
    limits = [value - now for value in (local_deadline, deadline) if value is not None]
    if not limits:
        return 0.05 if poll else None
    remaining = max(0.0, min(limits))
    return min(remaining, 0.05) if poll else remaining


def _sleep_with_control(
    seconds: float,
    *,
    sleep: Callable[[float], None],
    cancellation_check: Callable[[], bool] | None,
    deadline: float | None,
) -> None:
    """Sleep between retries without ignoring a run control."""

    if cancellation_check is None and deadline is None:
        sleep(seconds)
        return

    end = time.monotonic() + seconds
    while True:
        if cancellation_check is not None and cancellation_check():
            raise ExecutionCancelledError()
        now = time.monotonic()
        if deadline is not None and now >= deadline:
            raise ExecutionTimeoutError()
        remaining = end - now
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.05))


__all__ = [
    "DefaultProviderCallPolicy",
    "ProviderCallPolicy",
    "ProviderInvocationResult",
    "invoke_provider_call",
]
