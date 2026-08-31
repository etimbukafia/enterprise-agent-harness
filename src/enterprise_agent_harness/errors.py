"""Stable error taxonomy for runtime and provider boundaries."""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """Machine-readable error category."""

    CONTRACT_INVALID = "contract_invalid"
    POLICY_DENIED = "policy_denied"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_CHANGES_REQUESTED = "approval_changes_requested"
    APPROVAL_STALE = "approval_stale"
    APPROVAL_ACTION_MISMATCH = "approval_action_mismatch"
    APPROVAL_REQUEST_MISMATCH = "approval_request_mismatch"
    TOOL_VALIDATION_FAILED = "tool_validation_failed"
    TOOL_FAILED = "tool_failed"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_IDEMPOTENCY_REQUIRED = "idempotency_key_required"
    TOOL_IDEMPOTENCY_REUSED = "idempotency_key_reused"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_OUTPUT_INVALID = "provider_output_invalid"
    PROVIDER_TIMEOUT = "provider_timeout"
    EXECUTION_TIMEOUT = "execution_timeout"
    EXECUTION_CANCELLED = "execution_cancelled"
    RUNTIME_AUTHORIZATION_FAILED = "runtime_authorization_failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LEASE_CONFLICT = "lease_conflict"
    LEASE_EXPIRED = "lease_expired"
    DUPLICATE_EVENT = "duplicate_event"
    DEAD_LETTERED = "dead_lettered"


class HarnessError(RuntimeError):
    """Base error with a stable code and retry hint."""

    code: ErrorCode
    retryable: bool

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ContractValidationError(HarnessError):
    """Raised when data cannot cross a typed contract boundary."""

    def __init__(self, message: str = "contract validation failed") -> None:
        super().__init__(message, code=ErrorCode.CONTRACT_INVALID)


class PolicyDeniedError(HarnessError):
    """Raised when deterministic policy refuses a proposed operation."""

    def __init__(self, message: str = "policy denied the operation") -> None:
        super().__init__(message, code=ErrorCode.POLICY_DENIED)


class ApprovalRequiredError(HarnessError):
    """Raised when a sensitive operation has no valid approval."""

    def __init__(self, message: str = "approval is required") -> None:
        super().__init__(message, code=ErrorCode.APPROVAL_REQUIRED)


class ToolValidationError(HarnessError):
    """Raised when tool arguments or results fail their declared schema."""

    def __init__(self, message: str = "tool validation failed") -> None:
        super().__init__(message, code=ErrorCode.TOOL_VALIDATION_FAILED)


class ToolFailureError(HarnessError):
    """Raised when an application tool handler fails."""

    def __init__(self, message: str = "tool execution failed", *, retryable: bool = False) -> None:
        super().__init__(message, code=ErrorCode.TOOL_FAILED, retryable=retryable)


class ProviderError(HarnessError):
    """Raised when a provider call fails or returns unusable data."""

    def __init__(
        self,
        message: str = "provider call failed",
        *,
        code: ErrorCode = ErrorCode.PROVIDER_FAILED,
        retryable: bool = False,
    ) -> None:
        super().__init__(message, code=code, retryable=retryable)


class ProviderOutputError(ProviderError):
    """Raised when provider output cannot be normalized or validated."""

    def __init__(self, message: str = "provider output is invalid") -> None:
        super().__init__(message, code=ErrorCode.PROVIDER_OUTPUT_INVALID)


class ProviderTimeoutError(ProviderError):
    """Raised when a provider call exceeds its configured timeout."""

    def __init__(self, message: str = "provider call timed out") -> None:
        super().__init__(message, code=ErrorCode.PROVIDER_TIMEOUT, retryable=True)


class ExecutionTimeoutError(HarnessError):
    """Raised when a whole execution exceeds its configured time budget."""

    def __init__(self, message: str = "execution timed out") -> None:
        super().__init__(message, code=ErrorCode.EXECUTION_TIMEOUT)


class ExecutionCancelledError(HarnessError):
    """Raised when an execution is cancelled by its caller."""

    def __init__(self, message: str = "execution was cancelled") -> None:
        super().__init__(message, code=ErrorCode.EXECUTION_CANCELLED)


class RuntimeAuthorizationError(HarnessError):
    """Raised when a runtime is no longer authorized to start or resume work."""

    def __init__(self, message: str = "runtime execution is not authorized") -> None:
        super().__init__(message, code=ErrorCode.RUNTIME_AUTHORIZATION_FAILED)


class BudgetExhaustedError(HarnessError):
    """Raised when a trusted execution budget is exhausted mid-run."""

    def __init__(self, message: str = "execution budget exhausted") -> None:
        super().__init__(message, code=ErrorCode.BUDGET_EXHAUSTED)


__all__ = [
    "ApprovalRequiredError",
    "BudgetExhaustedError",
    "ContractValidationError",
    "ErrorCode",
    "ExecutionCancelledError",
    "ExecutionTimeoutError",
    "HarnessError",
    "PolicyDeniedError",
    "ProviderError",
    "ProviderOutputError",
    "ProviderTimeoutError",
    "RuntimeAuthorizationError",
    "ToolFailureError",
    "ToolValidationError",
]
