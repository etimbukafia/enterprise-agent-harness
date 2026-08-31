"""Configurable redaction for exported observability representations."""

from __future__ import annotations

from typing import Protocol

DEFAULT_BLOCKED_KEY_PARTS = (
    "message",
    "prompt",
    "text",
    "content",
    "output",
    "secret",
    "token",
    "credential",
    "password",
)


class Redactor(Protocol):
    """Boundary that decides which metadata keys and values may be exported."""

    def redact_key(self, key: str) -> bool:
        """Return whether a metadata key must be omitted from export."""

    def redact_value(self, key: str, value: str) -> str:
        """Return a safe representation for one metadata value."""

    def truncate(self, value: str, limit: int = 200) -> str:
        """Bound a single value before export."""


class DefaultRedactor:
    """Block sensitive keys and bound every exported value.

    Redaction applies to exported trace and audit metadata only. It never
    modifies the data the runtime needs to execute an approved action.
    """

    def __init__(
        self,
        *,
        blocked_key_parts: tuple[str, ...] = DEFAULT_BLOCKED_KEY_PARTS,
        sensitive_field_names: tuple[str, ...] = (),
        value_limit: int = 200,
    ) -> None:
        if value_limit < 1:
            raise ValueError("value_limit must be positive")
        self._blocked = tuple(part.lower() for part in blocked_key_parts)
        self._sensitive = set(sensitive_field_names)
        self.value_limit = value_limit

    def redact_key(self, key: str) -> bool:
        lowered = key.lower()
        if key in self._sensitive:
            return True
        return any(part in lowered for part in self._blocked)

    def redact_value(self, key: str, value: str) -> str:
        if self.redact_key(key):
            return "[REDACTED]"
        return self.truncate(value)

    def truncate(self, value: str, limit: int | None = None) -> str:
        bound = self.value_limit if limit is None else limit
        return value[:bound]
