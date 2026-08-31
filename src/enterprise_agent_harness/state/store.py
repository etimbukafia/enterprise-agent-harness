"""Principal-bound state storage for resumable workflows."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Protocol, Self, cast

from ..contracts import ExecutionState, PrincipalContext

StateRetentionHook = Callable[[ExecutionState], bool]


class StateOwnershipError(RuntimeError):
    """Raised when a principal or tenant uses another owner's state."""


class StateConflictError(RuntimeError):
    """Raised when a state write uses a stale version."""


class StateSerializationError(RuntimeError):
    """Raised when durable state data cannot be encoded as JSON."""


class StateStore(Protocol):
    """Storage boundary for versioned workflow state."""

    def get_or_create(
        self,
        principal: PrincipalContext,
        *,
        agent_id: str,
        agent_version: str,
        state_id: str | None = None,
    ) -> ExecutionState:
        """Return state owned by the supplied principal and tenant."""

    def save(self, state: ExecutionState, *, expected_version: int | None = None) -> None:
        """Save state and check its optimistic-concurrency version."""

    def find_execution(
        self,
        principal: PrincipalContext,
        execution_id: str,
    ) -> ExecutionState | None:
        """Find one execution state only within the supplied owner boundary."""

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete states outside the configured TTL or retention hook."""


class InMemoryStateStore:
    """Thread-safe state store for local runs and deterministic tests."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        ttl_seconds: float | None = None,
        retention_hook: StateRetentionHook | None = None,
    ) -> None:
        _validate_ttl(ttl_seconds)
        self._states: dict[tuple[str, str, str, str], ExecutionState] = {}
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl_seconds = ttl_seconds
        self._retention_hook = retention_hook
        self._lock = RLock()

    def get_or_create(
        self,
        principal: PrincipalContext,
        *,
        agent_id: str,
        agent_version: str,
        state_id: str | None = None,
    ) -> ExecutionState:
        resolved_state_id = state_id or principal.session_id
        key = (principal.tenant_id, principal.session_id, agent_id, resolved_state_id)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = ExecutionState(
                    state_id=resolved_state_id,
                    execution_id=f"state_{resolved_state_id}",
                    agent_id=agent_id,
                    agent_version=agent_version,
                    principal_id=principal.principal_id,
                    tenant_id=principal.tenant_id,
                    session_id=principal.session_id,
                )
                self._states[key] = state
            self._assert_owner(state, principal)
            if state.agent_version != agent_version:
                raise StateConflictError("state agent version does not match the requested version")
            return deepcopy(state)

    def save(self, state: ExecutionState, *, expected_version: int | None = None) -> None:
        key = (state.tenant_id, state.session_id, state.agent_id, state.state_id)
        with self._lock:
            existing = self._states.get(key)
            if existing is None:
                if state.version != 0:
                    raise StateConflictError("new state must start at version zero")
                if expected_version not in {None, 0}:
                    raise StateConflictError("new state expected version must be zero or unset")
            else:
                self._assert_same_owner(existing, state)
                if expected_version is not None and existing.version != expected_version:
                    raise StateConflictError("state version is stale")
                if expected_version is None and state.version < existing.version:
                    raise StateConflictError("state version cannot move backwards")
            self._states[key] = deepcopy(state)

    def find_execution(
        self,
        principal: PrincipalContext,
        execution_id: str,
    ) -> ExecutionState | None:
        with self._lock:
            matches = [
                state
                for state in self._states.values()
                if state.execution_id == execution_id
                and state.principal_id == principal.principal_id
                and state.tenant_id == principal.tenant_id
                and state.session_id == principal.session_id
            ]
            if not matches:
                return None
            return deepcopy(max(matches, key=lambda state: (state.version, state.updated_at)))

    def purge_expired(self, *, now: datetime | None = None) -> int:
        current = _aware_now(now, self._clock)
        with self._lock:
            expired_keys = [
                key
                for key, state in self._states.items()
                if _should_purge(
                    state,
                    now=current,
                    ttl_seconds=self._ttl_seconds,
                    retention_hook=self._retention_hook,
                )
            ]
            for key in expired_keys:
                del self._states[key]
            return len(expired_keys)

    @staticmethod
    def _assert_owner(state: ExecutionState, principal: PrincipalContext) -> None:
        if (
            state.principal_id != principal.principal_id
            or state.tenant_id != principal.tenant_id
            or state.session_id != principal.session_id
        ):
            raise StateOwnershipError("state belongs to a different principal or tenant")

    @staticmethod
    def _assert_same_owner(left: ExecutionState, right: ExecutionState) -> None:
        if (
            left.principal_id != right.principal_id
            or left.tenant_id != right.tenant_id
            or left.session_id != right.session_id
        ):
            raise StateOwnershipError("state owner cannot change")


class SQLiteStateStore:
    """SQLite-backed state store with migrations and optimistic writes.

    The store uses one serialized connection per instance. A caller can open
    another instance against the same database after a process restart. State
    data is JSON, and the application must protect the database with its own
    encryption, access control, backup, and retention settings.
    """

    _SCHEMA_VERSION = 1

    def __init__(
        self,
        database: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        ttl_seconds: float | None = None,
        retention_hook: StateRetentionHook | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        _validate_ttl(ttl_seconds)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self.database = str(database)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl_seconds = ttl_seconds
        self._retention_hook = retention_hook
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.database,
            timeout=timeout_seconds,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._migrate()

    def get_or_create(
        self,
        principal: PrincipalContext,
        *,
        agent_id: str,
        agent_version: str,
        state_id: str | None = None,
    ) -> ExecutionState:
        resolved_state_id = state_id or principal.session_id
        key = (principal.tenant_id, principal.session_id, agent_id, resolved_state_id)
        with self._lock:
            self._begin_write()
            try:
                row = self._select_key(key)
                if row is None:
                    state = ExecutionState(
                        state_id=resolved_state_id,
                        execution_id=f"state_{resolved_state_id}",
                        agent_id=agent_id,
                        agent_version=agent_version,
                        principal_id=principal.principal_id,
                        tenant_id=principal.tenant_id,
                        session_id=principal.session_id,
                    )
                    self._insert(state)
                else:
                    state = self._row_to_state(row)
                    InMemoryStateStore._assert_owner(state, principal)
                    if state.agent_version != agent_version:
                        raise StateConflictError(
                            "state agent version does not match the requested version"
                        )
                self._commit()
            except BaseException:
                self._rollback()
                raise
            return deepcopy(state)

    def save(self, state: ExecutionState, *, expected_version: int | None = None) -> None:
        key = (state.tenant_id, state.session_id, state.agent_id, state.state_id)
        with self._lock:
            self._begin_write()
            try:
                row = self._select_key(key)
                if row is None:
                    if state.version != 0:
                        raise StateConflictError("new state must start at version zero")
                    if expected_version not in {None, 0}:
                        raise StateConflictError("new state expected version must be zero or unset")
                    self._insert(state)
                else:
                    existing = self._row_to_state(row)
                    InMemoryStateStore._assert_same_owner(existing, state)
                    if expected_version is not None and existing.version != expected_version:
                        raise StateConflictError("state version is stale")
                    if expected_version is None and state.version < existing.version:
                        raise StateConflictError("state version cannot move backwards")
                    self._update(state)
                self._commit()
            except BaseException:
                self._rollback()
                raise

    def find_execution(
        self,
        principal: PrincipalContext,
        execution_id: str,
    ) -> ExecutionState | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM execution_states
                WHERE execution_id = ? AND principal_id = ?
                  AND tenant_id = ? AND session_id = ?
                ORDER BY version DESC, updated_at DESC
                LIMIT 1
                """,
                (
                    execution_id,
                    principal.principal_id,
                    principal.tenant_id,
                    principal.session_id,
                ),
            ).fetchone()
            return deepcopy(self._row_to_state(row)) if row is not None else None

    def purge_expired(self, *, now: datetime | None = None) -> int:
        current = _aware_now(now, self._clock)
        if self._ttl_seconds is None and self._retention_hook is None:
            return 0
        with self._lock:
            self._begin_write()
            try:
                rows = self._connection.execute("SELECT * FROM execution_states").fetchall()
                states = [self._row_to_state(row) for row in rows]
                expired = [
                    state
                    for state in states
                    if _should_purge(
                        state,
                        now=current,
                        ttl_seconds=self._ttl_seconds,
                        retention_hook=self._retention_hook,
                    )
                ]
                for state in expired:
                    self._connection.execute(
                        """
                        DELETE FROM execution_states
                        WHERE tenant_id = ? AND session_id = ?
                          AND agent_id = ? AND state_id = ?
                        """,
                        (state.tenant_id, state.session_id, state.agent_id, state.state_id),
                    )
                self._commit()
            except BaseException:
                self._rollback()
                raise
            return len(expired)

    def close(self) -> None:
        """Close the database connection."""

        with self._lock:
            self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS state_schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            int(row[0])
            for row in self._connection.execute(
                "SELECT version FROM state_schema_migrations"
            ).fetchall()
        }
        if 1 not in applied:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_states (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    state_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, session_id, agent_id, state_id)
                )
                """
            )
            self._connection.execute(
                "INSERT INTO state_schema_migrations(version, applied_at) VALUES (?, ?)",
                (self._SCHEMA_VERSION, datetime.now(UTC).isoformat()),
            )
        self._connection.commit()

    def _begin_write(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._connection.execute("COMMIT")

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")

    def _select_key(
        self,
        key: tuple[str, str, str, str],
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._connection.execute(
                """
                SELECT * FROM execution_states
                WHERE tenant_id = ? AND session_id = ? AND agent_id = ? AND state_id = ?
                """,
                key,
            ).fetchone(),
        )

    def _insert(self, state: ExecutionState) -> None:
        self._connection.execute(
            """
            INSERT INTO execution_states(
                tenant_id, session_id, agent_id, state_id, execution_id,
                agent_version, principal_id, status, version, data,
                updated_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _state_parameters(state),
        )

    def _update(self, state: ExecutionState) -> None:
        self._connection.execute(
            """
            UPDATE execution_states
            SET execution_id = ?, agent_version = ?, principal_id = ?,
                status = ?, version = ?, data = ?, updated_at = ?, schema_version = ?
            WHERE tenant_id = ? AND session_id = ? AND agent_id = ? AND state_id = ?
            """,
            (
                state.execution_id,
                state.agent_version,
                state.principal_id,
                state.status.value,
                state.version,
                _encode_data(state.data),
                state.updated_at.isoformat(),
                state.schema_version,
                state.tenant_id,
                state.session_id,
                state.agent_id,
                state.state_id,
            ),
        )

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> ExecutionState:
        try:
            data = json.loads(row["data"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StateSerializationError("stored state data is not valid JSON") from exc
        if not isinstance(data, dict):
            raise StateSerializationError("stored state data must be a JSON object")
        return ExecutionState(
            schema_version=row["schema_version"],
            state_id=row["state_id"],
            execution_id=row["execution_id"],
            agent_id=row["agent_id"],
            agent_version=row["agent_version"],
            principal_id=row["principal_id"],
            tenant_id=row["tenant_id"],
            session_id=row["session_id"],
            status=row["status"],
            version=row["version"],
            data=data,
            updated_at=row["updated_at"],
        )


def _state_parameters(state: ExecutionState) -> tuple[object, ...]:
    return (
        state.tenant_id,
        state.session_id,
        state.agent_id,
        state.state_id,
        state.execution_id,
        state.agent_version,
        state.principal_id,
        state.status.value,
        state.version,
        _encode_data(state.data),
        state.updated_at.isoformat(),
        state.schema_version,
    )


def _encode_data(data: dict[str, object]) -> str:
    try:
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise StateSerializationError("state data must be JSON serializable") from exc


def _validate_ttl(ttl_seconds: float | None) -> None:
    if ttl_seconds is not None and ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be greater than zero")


def _aware_now(now: datetime | None, clock: Callable[[], datetime]) -> datetime:
    value = now or clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("state clock must return an aware datetime")
    return value


def _should_purge(
    state: ExecutionState,
    *,
    now: datetime,
    ttl_seconds: float | None,
    retention_hook: StateRetentionHook | None,
) -> bool:
    ttl_expired = ttl_seconds is not None and now >= state.updated_at + timedelta(
        seconds=ttl_seconds
    )
    hook_expired = retention_hook is not None and not retention_hook(state)
    return ttl_expired or hook_expired


__all__ = [
    "InMemoryStateStore",
    "SQLiteStateStore",
    "StateConflictError",
    "StateOwnershipError",
    "StateRetentionHook",
    "StateSerializationError",
    "StateStore",
]
