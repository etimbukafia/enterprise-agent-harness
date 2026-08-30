"""Optional, bounded memory that cannot grant authority."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Protocol

from ..contracts import MemoryItem, PrincipalContext
from ..governance.safety import direct_injection_matches, indirect_injection_matches


class MemoryStrategy(Protocol):
    """Boundary for optional memory selection and storage."""

    def select(self, principal: PrincipalContext) -> list[MemoryItem]:
        """Return bounded memory for one principal and session."""

    def remember(self, item: MemoryItem) -> None:
        """Store one principal-bound memory item."""


class BoundedMemory:
    """Keep a small principal-bound set of non-authoritative values."""

    def __init__(self, max_items: int = 8) -> None:
        if max_items < 1:
            raise ValueError("max_items must be positive")
        self.max_items = max_items
        self._items: dict[tuple[str, str, str], list[MemoryItem]] = {}
        self._lock = RLock()

    def select(self, principal: PrincipalContext) -> list[MemoryItem]:
        keys = (
            (principal.tenant_id, principal.principal_id, principal.session_id),
            (principal.tenant_id, principal.principal_id, principal.principal_id),
            (principal.tenant_id, principal.principal_id, principal.tenant_id),
        )
        with self._lock:
            values = [item for key in keys for item in self._items.get(key, [])]
            return deepcopy(values[-self.max_items :])

    def remember(self, item: MemoryItem) -> None:
        if direct_injection_matches(item.value) or indirect_injection_matches(item.value):
            raise ValueError("instruction-like memory is not allowed")
        key = (item.tenant_id, item.principal_id, item.source_scope_id)
        with self._lock:
            values = self._items.setdefault(key, [])
            values.append(deepcopy(item))
            del values[: -self.max_items]
