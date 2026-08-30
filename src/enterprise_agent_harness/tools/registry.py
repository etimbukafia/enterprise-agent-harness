"""Versioned tool registry and guarded handler execution."""

from __future__ import annotations

import builtins
import hashlib
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from copy import deepcopy
from dataclasses import dataclass, replace
from threading import RLock
from typing import Any

from ..contracts import (
    AgentLifecycleStatus,
    ExecutionContext,
    ToolDescriptor,
    ToolExecutionRecord,
    ToolKind,
    ToolResult,
    ToolResultStatus,
)
from ..errors import ExecutionCancelledError, ExecutionTimeoutError
from .definitions import ToolDefinition, ToolInvocationError

ToolTraceCallback = Callable[[str, dict[str, str]], None]
CancellationCheck = Callable[[], bool]
RetryAdmission = Callable[[], bool]


@dataclass(frozen=True)
class _IdempotencyEntry:
    """Cached result for one exact tool action."""

    argument_digest: str
    result: ToolResult


class ToolRegistry:
    """Resolve and execute only application-registered tools.

    Registry lookup is exact for versioned tools. Handler execution is kept in
    this boundary so timeout, retry, idempotency, and execution evidence use
    one consistent path.
    """

    def __init__(self, tools: Iterable[ToolDefinition] = ()) -> None:
        self._tools: dict[tuple[str, str], ToolDefinition] = {}
        self._idempotency: dict[tuple[str, str, str, str, str], _IdempotencyEntry] = {}
        self._execution_records: list[ToolExecutionRecord] = []
        self._lock = RLock()
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolDefinition, *, replace_existing: bool = False) -> ToolDefinition:
        """Register one versioned tool and reject accidental replacement."""

        key = (tool.tool_id, tool.version)
        with self._lock:
            if key in self._tools and not replace_existing:
                raise ValueError("tool identity and version pairs must be unique")
            self._tools[key] = tool
        return tool

    def resolve(self, tool_id: str, version: str | None = None) -> ToolDefinition:
        """Resolve an active tool by identity and optional exact version."""

        return self.get(tool_id, version)

    def get(self, tool_id: str, version: str | None = None) -> ToolDefinition:
        """Resolve an active tool or raise a stable invocation error."""

        with self._lock:
            candidates = [
                tool for (candidate_id, _), tool in self._tools.items() if candidate_id == tool_id
            ]
            if version is not None:
                try:
                    tool = self._tools[(tool_id, version)]
                except KeyError as exc:
                    raise ToolInvocationError(
                        f"unknown tool version: {tool_id}@{version}",
                        code="unknown_tool",
                    ) from exc
                self._assert_active(tool)
                return tool

            active = [tool for tool in candidates if tool.lifecycle == AgentLifecycleStatus.ACTIVE]
            if not active:
                if candidates:
                    self._assert_active(candidates[0])
                raise ToolInvocationError(f"unknown tool: {tool_id}", code="unknown_tool")
            if len(active) > 1:
                raise ToolInvocationError(
                    f"tool version is required: {tool_id}",
                    code="tool_version_required",
                )
            return active[0]

    def version(self, tool_id: str, version: str | None = None) -> ToolDefinition:
        """Resolve one exact or unambiguous active tool version."""

        return self.get(tool_id, version)

    def list(
        self,
        *,
        include_inactive: bool = False,
    ) -> list[ToolDefinition]:
        """List registered tools in stable identity order."""

        with self._lock:
            values = list(self._tools.values())
        if not include_inactive:
            values = [tool for tool in values if tool.lifecycle == AgentLifecycleStatus.ACTIVE]
        return sorted(values, key=lambda item: (item.tool_id, item.version))

    def deprecate(self, tool_id: str, version: str) -> ToolDefinition:
        """Mark an exact version deprecated so new calls cannot resolve it."""

        return self._set_lifecycle(tool_id, version, AgentLifecycleStatus.DEPRECATED)

    def disable(self, tool_id: str, version: str) -> ToolDefinition:
        """Suspend an exact version so new calls cannot resolve it."""

        return self._set_lifecycle(tool_id, version, AgentLifecycleStatus.SUSPENDED)

    def activate(self, tool_id: str, version: str) -> ToolDefinition:
        """Activate an exact registered version."""

        return self._set_lifecycle(tool_id, version, AgentLifecycleStatus.ACTIVE)

    def retire(self, tool_id: str, version: str) -> ToolDefinition:
        """Retire an exact version permanently for new calls."""

        return self._set_lifecycle(tool_id, version, AgentLifecycleStatus.RETIRED)

    def descriptors(
        self,
        allowed_tool_ids: Iterable[str] | None = None,
        *,
        include_inactive: bool = False,
    ) -> builtins.list[ToolDescriptor]:
        """Return stable provider-facing metadata without handlers."""

        allowed = set(allowed_tool_ids) if allowed_tool_ids is not None else None
        values = [
            tool.descriptor
            for tool in self.list(include_inactive=include_inactive)
            if allowed is None or tool.tool_id in allowed
        ]
        return sorted(values, key=lambda item: (item.tool_id, item.version))

    def names(self, *, include_inactive: bool = False) -> tuple[str, ...]:
        """Return registered tool identities in stable order."""

        return tuple(
            sorted({tool.tool_id for tool in self.list(include_inactive=include_inactive)})
        )

    def versions(self, tool_id: str, *, include_inactive: bool = True) -> tuple[str, ...]:
        """Return registered versions for one tool identity."""

        return tuple(
            tool.version
            for tool in self.list(include_inactive=include_inactive)
            if tool.tool_id == tool_id
        )

    def invoke(
        self,
        tool_id: str,
        context: ExecutionContext,
        arguments: dict[str, Any],
        *,
        version: str | None = None,
        idempotency_key: str | None = None,
        trace_callback: ToolTraceCallback | None = None,
        cancellation_check: CancellationCheck | None = None,
        deadline: float | None = None,
        retry_admission: RetryAdmission | None = None,
    ) -> ToolResult:
        """Validate and execute one tool with timeout, cancellation, retry, and idempotency.

        The callback receives event names and metadata only. It never receives
        raw arguments, idempotency keys, or tool output.
        """

        tool = self.get(tool_id, version)
        try:
            tool.validate_arguments(arguments)
        except ToolInvocationError as exc:
            self._record_execution(
                context=context,
                tool=tool,
                status=ToolResultStatus.INVALID_ARGUMENTS,
                attempts=1,
                latency_ms=0.0,
                idempotency_key_digest=None,
                error_code=exc.code,
                trace_callback=trace_callback,
            )
            raise
        if idempotency_key is not None and not idempotency_key.strip():
            self._record_execution(
                context=context,
                tool=tool,
                status=ToolResultStatus.FAILED,
                attempts=1,
                latency_ms=0.0,
                idempotency_key_digest=None,
                error_code="invalid_idempotency_key",
                trace_callback=trace_callback,
            )
            raise ToolInvocationError(
                "idempotency_key must not be empty",
                code="invalid_idempotency_key",
            )
        if tool.idempotency_required and idempotency_key is None:
            self._record_execution(
                context=context,
                tool=tool,
                status=ToolResultStatus.PERMISSION_DENIED,
                attempts=1,
                latency_ms=0.0,
                idempotency_key_digest=None,
                error_code="idempotency_key_required",
                trace_callback=trace_callback,
            )
            raise ToolInvocationError(
                f"tool {tool.tool_id} requires an idempotency key",
                code="idempotency_key_required",
            )

        argument_digest = tool.action_digest(arguments)
        cache_key = (
            (
                context.principal.tenant_id,
                context.principal.principal_id,
                tool.tool_id,
                tool.version,
                idempotency_key,
            )
            if idempotency_key is not None
            else None
        )
        with self._lock:
            if cache_key is not None:
                cached = self._idempotency.get(cache_key)
                if cached is not None:
                    if cached.argument_digest != argument_digest:
                        self._record_execution(
                            context=context,
                            tool=tool,
                            status=ToolResultStatus.FAILED,
                            attempts=1,
                            latency_ms=0.0,
                            idempotency_key_digest=_digest_key(cache_key),
                            error_code="idempotency_key_reused",
                            trace_callback=trace_callback,
                        )
                        raise ToolInvocationError(
                            "idempotency key was reused with different arguments",
                            code="idempotency_key_reused",
                        )
                    replayed = cached.result.model_copy(
                        update={
                            "execution_id": context.execution_id,
                            "metadata": {
                                **cached.result.metadata,
                                "attempts": "1",
                                "retry_count": "0",
                                "idempotent_replay": "true",
                            },
                        },
                        deep=True,
                    )
                    self._emit(
                        trace_callback,
                        "tool_idempotency_replayed",
                        {"tool_id": tool.tool_id, "tool_version": tool.version},
                    )
                    self._record_execution(
                        context=context,
                        tool=tool,
                        status=replayed.status,
                        attempts=1,
                        latency_ms=0.0,
                        idempotency_key_digest=_digest_key(cache_key),
                        error_code=replayed.error_code,
                        trace_callback=trace_callback,
                    )
                    return replayed

            return self._invoke_locked(
                tool=tool,
                context=context,
                arguments=arguments,
                cache_key=cache_key,
                argument_digest=argument_digest,
                trace_callback=trace_callback,
                cancellation_check=cancellation_check,
                deadline=deadline,
                retry_admission=retry_admission,
            )

    def execute(
        self,
        tool_id: str,
        context: ExecutionContext,
        arguments: dict[str, Any],
        *,
        version: str | None = None,
        idempotency_key: str | None = None,
        trace_callback: ToolTraceCallback | None = None,
        cancellation_check: CancellationCheck | None = None,
        deadline: float | None = None,
        retry_admission: RetryAdmission | None = None,
    ) -> ToolResult:
        """Alias for :meth:`invoke` for application-facing terminology."""

        return self.invoke(
            tool_id,
            context,
            arguments,
            version=version,
            idempotency_key=idempotency_key,
            trace_callback=trace_callback,
            cancellation_check=cancellation_check,
            deadline=deadline,
            retry_admission=retry_admission,
        )

    @property
    def execution_records(self) -> builtins.list[ToolExecutionRecord]:
        """Return a copy of structured registry execution records."""

        with self._lock:
            return deepcopy(self._execution_records)

    def clear_idempotency(self) -> None:
        """Clear local idempotency records for test or process lifecycle use."""

        with self._lock:
            self._idempotency.clear()

    def _invoke_locked(
        self,
        *,
        tool: ToolDefinition,
        context: ExecutionContext,
        arguments: dict[str, Any],
        cache_key: tuple[str, str, str, str, str] | None,
        argument_digest: str,
        trace_callback: ToolTraceCallback | None,
        cancellation_check: CancellationCheck | None,
        deadline: float | None,
        retry_admission: RetryAdmission | None,
    ) -> ToolResult:
        started = time.perf_counter()
        attempts = 0
        last_error: ToolInvocationError | None = None
        max_attempts = tool.retry_max_attempts if tool.retry_enabled else 1
        retry_allowed_for_action = tool.kind == ToolKind.READ or cache_key is not None

        while attempts < max_attempts:
            attempts += 1
            self._emit(
                trace_callback,
                "tool_execution_started" if attempts == 1 else "tool_retry_started",
                {
                    "tool_id": tool.tool_id,
                    "tool_version": tool.version,
                    "attempt": str(attempts),
                },
            )
            try:
                result = _run_with_timeout(
                    lambda: tool.invoke(context, arguments),
                    tool.timeout_seconds,
                    cancellation_check=cancellation_check,
                    deadline=deadline,
                )
            except (ExecutionCancelledError, ExecutionTimeoutError) as exc:
                self._record_execution(
                    context=context,
                    tool=tool,
                    status=ToolResultStatus.FAILED,
                    attempts=attempts,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    idempotency_key_digest=_digest_key(cache_key),
                    error_code=exc.code.value,
                    trace_callback=trace_callback,
                )
                raise
            except ToolInvocationError as exc:
                last_error = exc
                retry_requested = self._should_retry(
                    tool=tool,
                    error=exc,
                    attempt=attempts,
                    retry_allowed_for_action=retry_allowed_for_action,
                )
                if not retry_requested:
                    self._record_execution(
                        context=context,
                        tool=tool,
                        status=(
                            ToolResultStatus.INVALID_ARGUMENTS
                            if exc.code == "invalid_arguments"
                            else ToolResultStatus.FAILED
                        ),
                        attempts=attempts,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                        idempotency_key_digest=_digest_key(cache_key),
                        error_code=exc.code,
                        trace_callback=trace_callback,
                    )
                    raise
                if not _admit_retry(
                    retry_admission,
                    trace_callback=trace_callback,
                    tool=tool,
                    attempt=attempts,
                ):
                    self._record_execution(
                        context=context,
                        tool=tool,
                        status=ToolResultStatus.FAILED,
                        attempts=attempts,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                        idempotency_key_digest=_digest_key(cache_key),
                        error_code=exc.code,
                        trace_callback=trace_callback,
                    )
                    raise
                self._emit(
                    trace_callback,
                    "tool_retry_scheduled",
                    {
                        "tool_id": tool.tool_id,
                        "attempt": str(attempts),
                        "reason_code": exc.code,
                    },
                )
                _sleep(
                    tool.retry_backoff,
                    cancellation_check=cancellation_check,
                    deadline=deadline,
                )
                continue

            retry_requested = (
                result.status == ToolResultStatus.FAILED
                and self._should_retry_result(
                    tool=tool,
                    result=result,
                    attempt=attempts,
                    retry_allowed_for_action=retry_allowed_for_action,
                )
            )
            if retry_requested:
                last_error = ToolInvocationError(
                    f"tool {tool.tool_id} returned a retryable failure",
                    code=result.error_code or "tool_failed",
                    retryable=True,
                )
                if not _admit_retry(
                    retry_admission,
                    trace_callback=trace_callback,
                    tool=tool,
                    attempt=attempts,
                ):
                    result = _with_execution_metadata(
                        result,
                        attempts=attempts,
                        retry_count=attempts - 1,
                        timeout_seconds=tool.timeout_seconds,
                    )
                    self._record_execution(
                        context=context,
                        tool=tool,
                        status=result.status,
                        attempts=attempts,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                        idempotency_key_digest=_digest_key(cache_key),
                        error_code=result.error_code,
                        trace_callback=trace_callback,
                    )
                    return result
                self._emit(
                    trace_callback,
                    "tool_retry_scheduled",
                    {
                        "tool_id": tool.tool_id,
                        "attempt": str(attempts),
                        "reason_code": last_error.code,
                    },
                )
                _sleep(
                    tool.retry_backoff,
                    cancellation_check=cancellation_check,
                    deadline=deadline,
                )
                continue

            result = _with_execution_metadata(
                result,
                attempts=attempts,
                retry_count=attempts - 1,
                timeout_seconds=tool.timeout_seconds,
            )
            if cache_key is not None and result.status in {
                ToolResultStatus.SUCCEEDED,
                ToolResultStatus.EMPTY,
            }:
                self._idempotency[cache_key] = _IdempotencyEntry(
                    argument_digest=argument_digest,
                    result=result.model_copy(deep=True),
                )
            self._record_execution(
                context=context,
                tool=tool,
                status=result.status,
                attempts=attempts,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                idempotency_key_digest=_digest_key(cache_key),
                error_code=result.error_code,
                trace_callback=trace_callback,
            )
            return result

        assert last_error is not None
        raise last_error

    @staticmethod
    def _should_retry(
        *,
        tool: ToolDefinition,
        error: ToolInvocationError,
        attempt: int,
        retry_allowed_for_action: bool,
    ) -> bool:
        if not tool.retry_enabled or not retry_allowed_for_action:
            return False
        if attempt >= tool.retry_max_attempts:
            return False
        if error.code in {
            "invalid_arguments",
            "invalid_output",
            "invalid_result_identity",
            "invalid_idempotency_key",
            "idempotency_key_required",
            "idempotency_key_reused",
        }:
            return False
        cause = error.__cause__
        retryable_exception_types = (
            tool.retry_policy.retryable_exception_types
            if tool.retry_policy is not None
            else (ConnectionError, TimeoutError)
        )
        return (
            error.retryable
            or tool.retryable
            or bool(getattr(cause, "retryable", False))
            or isinstance(cause, retryable_exception_types)
        )

    @staticmethod
    def _should_retry_result(
        *,
        tool: ToolDefinition,
        result: ToolResult,
        attempt: int,
        retry_allowed_for_action: bool,
    ) -> bool:
        if not tool.retry_enabled or not retry_allowed_for_action:
            return False
        if attempt >= tool.retry_max_attempts:
            return False
        return tool.retryable or result.metadata.get("retryable") == "true"

    def _record_execution(
        self,
        *,
        context: ExecutionContext,
        tool: ToolDefinition,
        status: ToolResultStatus,
        attempts: int,
        latency_ms: float,
        idempotency_key_digest: str | None,
        error_code: str | None,
        trace_callback: ToolTraceCallback | None,
    ) -> None:
        record = ToolExecutionRecord(
            execution_id=context.execution_id,
            tool_id=tool.tool_id,
            tool_version=tool.version,
            status=status,
            attempts=attempts,
            retry_count=attempts - 1,
            latency_ms=latency_ms,
            timeout_seconds=tool.timeout_seconds,
            idempotency_key_digest=idempotency_key_digest,
            error_code=error_code,
        )
        with self._lock:
            self._execution_records.append(record)
        event = (
            "tool_execution_completed"
            if status in {ToolResultStatus.SUCCEEDED, ToolResultStatus.EMPTY}
            else "tool_execution_failed"
        )
        self._emit(
            trace_callback,
            event,
            {
                "tool_id": tool.tool_id,
                "tool_version": tool.version,
                "status": status.value,
                "attempts": str(attempts),
                "retry_count": str(attempts - 1),
            },
        )

    def _set_lifecycle(
        self,
        tool_id: str,
        version: str,
        lifecycle: AgentLifecycleStatus,
    ) -> ToolDefinition:
        with self._lock:
            try:
                current = self._tools[(tool_id, version)]
            except KeyError as exc:
                raise ToolInvocationError(
                    f"unknown tool version: {tool_id}@{version}",
                    code="unknown_tool",
                ) from exc
            if current.lifecycle == AgentLifecycleStatus.RETIRED and lifecycle != current.lifecycle:
                raise ToolInvocationError(
                    f"tool is retired: {tool_id}@{version}",
                    code="tool_retired",
                )
            updated = replace(current, lifecycle=lifecycle)
            self._tools[(tool_id, version)] = updated
            return updated

    @staticmethod
    def _assert_active(tool: ToolDefinition) -> None:
        if tool.lifecycle == AgentLifecycleStatus.DEPRECATED:
            raise ToolInvocationError(
                f"tool is deprecated: {tool.tool_id}@{tool.version}",
                code="tool_deprecated",
            )
        if tool.lifecycle == AgentLifecycleStatus.SUSPENDED:
            raise ToolInvocationError(
                f"tool is disabled: {tool.tool_id}@{tool.version}",
                code="tool_disabled",
            )
        if tool.lifecycle == AgentLifecycleStatus.RETIRED:
            raise ToolInvocationError(
                f"tool is retired: {tool.tool_id}@{tool.version}",
                code="tool_retired",
            )
        if tool.lifecycle != AgentLifecycleStatus.ACTIVE:
            raise ToolInvocationError(
                f"tool is not active: {tool.tool_id}@{tool.version}",
                code="tool_not_active",
            )

    @staticmethod
    def _emit(
        callback: ToolTraceCallback | None,
        event_type: str,
        metadata: dict[str, str],
    ) -> None:
        if callback is not None:
            callback(event_type, metadata)


def _run_with_timeout(
    call: Callable[[], ToolResult],
    timeout_seconds: float | None,
    *,
    cancellation_check: CancellationCheck | None = None,
    deadline: float | None = None,
) -> ToolResult:
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
                raise ToolInvocationError(
                    "tool execution timed out",
                    code="tool_timeout",
                    retryable=True,
                )

            wait_for = _wait_seconds(
                now=now,
                local_deadline=local_deadline,
                deadline=deadline,
                poll=cancellation_check is not None,
            )
            try:
                result = future.result(timeout=wait_for)
                if cancellation_check is not None and cancellation_check():
                    raise ExecutionCancelledError()
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise ExecutionTimeoutError()
                if local_deadline is not None and now >= local_deadline:
                    raise ToolInvocationError(
                        "tool execution timed out",
                        code="tool_timeout",
                        retryable=True,
                    )
                return result
            except FutureTimeoutError as exc:
                if deadline is not None and time.monotonic() >= deadline:
                    future.cancel()
                    raise ExecutionTimeoutError() from exc
                if local_deadline is not None and time.monotonic() >= local_deadline:
                    future.cancel()
                    raise ToolInvocationError(
                        "tool execution timed out",
                        code="tool_timeout",
                        retryable=True,
                    ) from exc
    finally:
        # A running handler cannot be force-stopped. Do not block the runtime
        # after the timeout has been reported.
        executor.shutdown(wait=False, cancel_futures=True)


def _with_execution_metadata(
    result: ToolResult,
    *,
    attempts: int,
    retry_count: int,
    timeout_seconds: float | None,
) -> ToolResult:
    metadata = {
        **result.metadata,
        "attempts": str(attempts),
        "retry_count": str(retry_count),
    }
    if timeout_seconds is not None:
        metadata["timeout_seconds"] = str(timeout_seconds)
    return result.model_copy(update={"metadata": metadata}, deep=True)


def _digest_key(cache_key: tuple[str, str, str, str, str] | None) -> str | None:
    if cache_key is None:
        return None
    return hashlib.sha256(cache_key[4].encode("utf-8")).hexdigest()


def _sleep(
    seconds: float,
    *,
    cancellation_check: CancellationCheck | None = None,
    deadline: float | None = None,
) -> None:
    if seconds <= 0 and cancellation_check is None and deadline is None:
        return
    end = time.monotonic() + max(0.0, seconds)
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


def _admit_retry(
    retry_admission: RetryAdmission | None,
    *,
    trace_callback: ToolTraceCallback | None,
    tool: ToolDefinition,
    attempt: int,
) -> bool:
    if retry_admission is None or retry_admission():
        return True
    ToolRegistry._emit(
        trace_callback,
        "retry_budget_exhausted",
        {"tool_id": tool.tool_id, "attempt": str(attempt)},
    )
    return False


def _metadata_int(metadata: dict[str, str], key: str, *, default: int = 0) -> int:
    try:
        value = int(metadata.get(key, str(default)))
    except (TypeError, ValueError):
        return default
    return max(0, value)


__all__ = ["CancellationCheck", "RetryAdmission", "ToolRegistry", "ToolTraceCallback"]
