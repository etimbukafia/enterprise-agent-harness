"""Lease and lock semantics for duplicate background handling.

The in-memory implementation is deterministic and single-process. It does not
provide distributed-lock guarantees; a production deployment must supply a
durable lease store across workers.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Protocol

from ..contracts import ContractModel


class LeaseConflictError(RuntimeError):
    """Raised when a lease is held by another owner and has not expired."""


class LeaseExpiredError(RuntimeError):
    """Raised when a lease is used or released after its expiry."""


class Lease(ContractModel):
    """A time-bounded ownership record for one event key."""

    schema_version: str = "agent-lease.v1"
    key: str
    owner: str
    expires_at: datetime
    version: int = 0


class LeaseStore(Protocol):
    """Storage boundary for event-handling leases."""

    def acquire(self, key: str, owner: str, ttl_seconds: float) -> Lease:
        """Acquire a lease for a key, or fail on an active foreign lease."""

    def renew(self, key: str, owner: str, ttl_seconds: float) -> Lease:
        """Extend an owned lease and increment its version."""

    def release(self, key: str, owner: str) -> None:
        """Release an owned lease."""

    def get(self, key: str) -> Lease | None:
        """Return the current lease for a key, if any."""


class InMemoryLeaseStore:
    """Thread-safe, deterministic lease store for tests and local use."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._leases: dict[str, Lease] = {}
        self._lock = RLock()

    def acquire(self, key: str, owner: str, ttl_seconds: float) -> Lease:
        if not key.strip() or not owner.strip():
            raise ValueError("lease key and owner must not be empty")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        now = self._clock()
        with self._lock:
            existing = self._leases.get(key)
            if existing is not None and existing.expires_at > now and existing.owner != owner:
                raise LeaseConflictError(f"lease is held by another owner: {key}")
            version = 0 if existing is None else existing.version + 1
            lease = Lease(
                key=key,
                owner=owner,
                expires_at=now + timedelta(seconds=ttl_seconds),
                version=version,
            )
            self._leases[key] = lease
            return deepcopy(lease)

    def renew(self, key: str, owner: str, ttl_seconds: float) -> Lease:
        now = self._clock()
        with self._lock:
            existing = self._leases.get(key)
            if existing is None:
                raise LeaseExpiredError(f"no lease for key: {key}")
            if existing.owner != owner:
                raise LeaseConflictError(f"lease belongs to another owner: {key}")
            if existing.expires_at <= now:
                raise LeaseExpiredError(f"lease expired for key: {key}")
            lease = Lease(
                key=key,
                owner=owner,
                expires_at=now + timedelta(seconds=ttl_seconds),
                version=existing.version + 1,
            )
            self._leases[key] = lease
            return deepcopy(lease)

    def release(self, key: str, owner: str) -> None:
        with self._lock:
            existing = self._leases.get(key)
            if existing is None:
                return
            if existing.owner != owner:
                raise LeaseConflictError(f"lease belongs to another owner: {key}")
            del self._leases[key]

    def get(self, key: str) -> Lease | None:
        with self._lock:
            lease = self._leases.get(key)
            return deepcopy(lease) if lease is not None else None


__all__ = [
    "InMemoryLeaseStore",
    "Lease",
    "LeaseConflictError",
    "LeaseExpiredError",
    "LeaseStore",
]
