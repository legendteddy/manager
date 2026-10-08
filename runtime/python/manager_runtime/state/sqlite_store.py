from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Iterator

from .base import (
    DurableOperation,
    OperationConflict,
    RunLease,
    RunLeaseConflict,
    RunLeaseExpired,
    RunState,
    RunStateConflict,
    RunStateError,
    RunStoreCapabilities,
)
from .transitions import validate_run_state_shape, validate_run_state_transition

SQLITE_STATE_SCHEMA_VERSION = 2
_OPERATION_STATUSES = {"started", "confirmed", "not_executed", "outcome_unknown"}
_TERMINAL_OPERATION_STATUSES = _OPERATION_STATUSES - {"started"}
_MAX_LEASE_TTL_SECONDS = 86_400


class SQLiteRunStore:
    """SQLite reference state backend with local multi-process coordination.

    SQLite proves the provider-neutral CAS/lease/fencing/idempotency contract for
    processes that coordinate through the same database file. It is not a claim
    of universal horizontal safety, a network database, a secret store, or an
    encryption boundary.
    """

    capabilities = RunStoreCapabilities(
        optimistic_concurrency=True,
        transactional_cas=True,
        leases=True,
        fencing=True,
        durable_idempotency=True,
        execution_guard=True,
        coordination_scope="local_multi_process",
    )

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or isinstance(
            timeout_seconds, bool
        ):
            raise TypeError("timeout_seconds must be numeric")
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        self.path = str(path)
        self.timeout_seconds = float(timeout_seconds)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.timeout_seconds)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _backend_error(exc: sqlite3.OperationalError) -> RunStateError:
        return RunStateError("SQLite durable-state backend is unavailable or busy")

    @staticmethod
    def _db_now(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT CAST(strftime('%s','now') AS INTEGER) AS now_epoch"
        ).fetchone()
        if row is None or not isinstance(row["now_epoch"], int):
            raise RunStateError("SQLite backend could not provide an authoritative clock")
        return int(row["now_epoch"])

    @staticmethod
    def _validate_ttl(ttl_seconds: int) -> int:
        if (
            not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or ttl_seconds < 1
            or ttl_seconds > _MAX_LEASE_TTL_SECONDS
        ):
            raise RunStateError(
                f"lease ttl_seconds must be an integer between 1 and {_MAX_LEASE_TTL_SECONDS}"
            )
        return ttl_seconds

    @staticmethod
    def _validate_owner(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise RunStateError("lease owner_id must be non-empty text")
        if len(owner_id) > 256:
            raise RunStateError("lease owner_id is too long")
        return owner_id

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

            if "manager_runs" not in tables:
                if "manager_state_meta" in tables:
                    raise RunStateError(
                        "SQLite migration metadata exists without manager_runs"
                    )
                connection.execute(
                    """
                    CREATE TABLE manager_runs (
                        run_id TEXT PRIMARY KEY,
                        revision INTEGER NOT NULL,
                        state_json TEXT NOT NULL
                    )
                    """
                )
                tables.add("manager_runs")

            if "manager_state_meta" not in tables:
                connection.execute(
                    """
                    CREATE TABLE manager_state_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO manager_state_meta(key, value) VALUES ('schema_version', '1')"
                )
                version = 1
            else:
                row = connection.execute(
                    "SELECT value FROM manager_state_meta WHERE key = 'schema_version'"
                ).fetchone()
                if row is None:
                    raise RunStateError("SQLite state schema version metadata is missing")
                try:
                    version = int(row["value"])
                except (TypeError, ValueError) as exc:
                    raise RunStateError(
                        "SQLite state schema version metadata is corrupted"
                    ) from exc

            if version < 1:
                raise RunStateError("SQLite state schema version is invalid")
            if version > SQLITE_STATE_SCHEMA_VERSION:
                raise RunStateError(
                    f"SQLite state schema version {version} is newer than this runtime"
                )

            while version < SQLITE_STATE_SCHEMA_VERSION:
                if version != 1:
                    raise RunStateError(
                        f"no reviewed SQLite migration exists from schema version {version}"
                    )
                self._migrate_v1_to_v2(connection)
                version = 2
                connection.execute(
                    "UPDATE manager_state_meta SET value = ? WHERE key = 'schema_version'",
                    (str(version),),
                )

            required = {"manager_runs", "manager_state_meta", "manager_run_leases", "manager_operations"}
            actual = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            missing = sorted(required - actual)
            if missing:
                raise RunStateError(
                    "SQLite state schema is incomplete: " + ", ".join(missing)
                )
            connection.commit()
        except sqlite3.OperationalError as exc:
            connection.rollback()
            raise self._backend_error(exc) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS manager_run_leases (
                run_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
                expires_at_epoch INTEGER NOT NULL,
                FOREIGN KEY(run_id) REFERENCES manager_runs(run_id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS manager_operations (
                operation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
                status TEXT NOT NULL CHECK (
                    status IN ('started', 'confirmed', 'not_executed', 'outcome_unknown')
                ),
                result_json TEXT,
                created_at_epoch INTEGER NOT NULL,
                updated_at_epoch INTEGER NOT NULL,
                FOREIGN KEY(run_id) REFERENCES manager_runs(run_id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS manager_operations_run_idx ON manager_operations(run_id)"
        )

    @staticmethod
    def _encoded(state: RunState) -> str:
        validate_run_state_shape(state)
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decoded(payload: str) -> RunState:
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RunStateError("persisted run state is corrupted JSON") from exc
        if not isinstance(value, dict):
            raise RunStateError("persisted run state must decode to an object")
        validate_run_state_shape(value)
        return value

    @staticmethod
    def _operation_result(payload: str | None) -> RunState | None:
        if payload is None:
            return None
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RunStateError("persisted operation result is corrupted JSON") from exc
        if not isinstance(value, dict):
            raise RunStateError("persisted operation result must decode to an object")
        return value

    @classmethod
    def _operation_from_row(
        cls, row: sqlite3.Row, *, claimed: bool = False
    ) -> DurableOperation:
        status = row["status"]
        if status not in _OPERATION_STATUSES:
            raise RunStateError("persisted operation has an unknown status")
        return DurableOperation(
            operation_id=row["operation_id"],
            run_id=row["run_id"],
            request_fingerprint=row["request_fingerprint"],
            fencing_token=int(row["fencing_token"]),
            status=status,
            result=cls._operation_result(row["result_json"]),
            claimed=claimed,
        )

    @staticmethod
    def _assert_lease_row(
        connection: sqlite3.Connection,
        lease: RunLease,
        *,
        require_unexpired: bool = True,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT run_id, owner_id, fencing_token, expires_at_epoch
            FROM manager_run_leases
            WHERE run_id = ?
            """,
            (lease.run_id,),
        ).fetchone()
        if row is None:
            raise RunLeaseExpired(f"run lease does not exist: {lease.run_id}")
        if row["owner_id"] != lease.owner_id or row["fencing_token"] != lease.fencing_token:
            raise RunLeaseExpired(
                f"run lease fencing token is stale: {lease.run_id}@{lease.fencing_token}"
            )
        if require_unexpired:
            now = SQLiteRunStore._db_now(connection)
            if row["expires_at_epoch"] <= now:
                raise RunLeaseExpired(
                    f"run lease expired: {lease.run_id}@{lease.fencing_token}"
                )
        return row

    def create(self, state: RunState) -> RunState:
        candidate = deepcopy(state)
        if candidate.get("revision") != 1:
            raise RunStateError("new run state must start at revision 1")
        validate_run_state_shape(candidate)
        run_id = candidate["run_id"]
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO manager_runs(run_id, revision, state_json) VALUES (?, ?, ?)",
                    (run_id, 1, self._encoded(candidate)),
                )
        except sqlite3.IntegrityError as exc:
            raise RunStateConflict(f"run already exists: {run_id}") from exc
        except sqlite3.OperationalError as exc:
            raise self._backend_error(exc) from exc
        return candidate

    def load(self, run_id: str) -> RunState | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT state_json FROM manager_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
        except sqlite3.OperationalError as exc:
            raise self._backend_error(exc) from exc
        if row is None:
            return None
        return self._decoded(row["state_json"])

    def _compare_and_swap_in_connection(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        expected_revision: int,
        candidate: RunState,
    ) -> RunState:
        row = connection.execute(
            "SELECT revision, state_json FROM manager_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RunStateConflict(f"run does not exist: {run_id}")
        if row["revision"] != expected_revision:
            raise RunStateConflict(
                f"run revision changed before update: {run_id}@{expected_revision}"
            )
        previous = self._decoded(row["state_json"])
        validate_run_state_transition(previous, candidate)
        cursor = connection.execute(
            """
            UPDATE manager_runs
            SET revision = ?, state_json = ?
            WHERE run_id = ? AND revision = ?
            """,
            (
                candidate["revision"],
                self._encoded(candidate),
                run_id,
                expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise RunStateConflict(
                f"run revision changed before update: {run_id}@{expected_revision}"
            )
        return candidate

    def compare_and_swap(
        self, run_id: str, expected_revision: int, state: RunState
    ) -> RunState:
        candidate = deepcopy(state)
        if candidate.get("run_id") != run_id:
            raise RunStateError("replacement run_id must match the stored run")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            value = self._compare_and_swap_in_connection(
                connection, run_id, expected_revision, candidate
            )
            connection.commit()
            return value
        except sqlite3.OperationalError as exc:
            connection.rollback()
            raise self._backend_error(exc) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def acquire_lease(
        self, run_id: str, owner_id: str, *, ttl_seconds: int
    ) -> RunLease:
        owner_id = self._validate_owner(owner_id)
        ttl_seconds = self._validate_ttl(ttl_seconds)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT 1 FROM manager_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise RunStateConflict(f"run does not exist: {run_id}")
            now = self._db_now(connection)
            row = connection.execute(
                """
                SELECT owner_id, fencing_token, expires_at_epoch
                FROM manager_run_leases WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is not None and row["expires_at_epoch"] > now:
                raise RunLeaseConflict(
                    f"run already has an active lease: {run_id}@{row['fencing_token']}"
                )

            fencing_token = 1 if row is None else int(row["fencing_token"]) + 1
            expires_at = now + ttl_seconds
            if row is None:
                connection.execute(
                    """
                    INSERT INTO manager_run_leases(
                        run_id, owner_id, fencing_token, expires_at_epoch
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (run_id, owner_id, fencing_token, expires_at),
                )
            else:
                connection.execute(
                    """
                    UPDATE manager_run_leases
                    SET owner_id = ?, fencing_token = ?, expires_at_epoch = ?
                    WHERE run_id = ?
                    """,
                    (owner_id, fencing_token, expires_at, run_id),
                )
            connection.commit()
            return RunLease(run_id, owner_id, fencing_token, expires_at)
        except sqlite3.OperationalError as exc:
            connection.rollback()
            raise self._backend_error(exc) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def renew_lease(self, lease: RunLease, *, ttl_seconds: int) -> RunLease:
        ttl_seconds = self._validate_ttl(ttl_seconds)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_lease_row(connection, lease)
            expires_at = self._db_now(connection) + ttl_seconds
            connection.execute(
                """
                UPDATE manager_run_leases SET expires_at_epoch = ?
                WHERE run_id = ? AND owner_id = ? AND fencing_token = ?
                """,
                (expires_at, lease.run_id, lease.owner_id, lease.fencing_token),
            )
            connection.commit()
            return RunLease(
                lease.run_id, lease.owner_id, lease.fencing_token, expires_at
            )
        except sqlite3.OperationalError as exc:
            connection.rollback()
            raise self._backend_error(exc) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def assert_lease(self, lease: RunLease) -> None:
        try:
            with self._connect() as connection:
                self._assert_lease_row(connection, lease)
        except sqlite3.OperationalError as exc:
            raise self._backend_error(exc) from exc

    def release_lease(self, lease: RunLease) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_lease_row(connection, lease, require_unexpired=False)
            cursor = connection.execute(
                """
                UPDATE manager_run_leases SET expires_at_epoch = 0
                WHERE run_id = ? AND owner_id = ? AND fencing_token = ?
                """,
                (lease.run_id, lease.owner_id, lease.fencing_token),
            )
            if cursor.rowcount != 1:
                raise RunLeaseExpired(
                    f"run lease changed before release: {lease.run_id}@{lease.fencing_token}"
                )
            connection.commit()
        except sqlite3.OperationalError as exc:
            connection.rollback()
            raise self._backend_error(exc) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fenced_compare_and_swap(
        self,
        run_id: str,
        expected_revision: int,
        state: RunState,
        *,
        lease: RunLease,
    ) -> RunState:
        if lease.run_id != run_id:
            raise RunStateError("lease run_id does not match fenced CAS run_id")
        candidate = deepcopy(state)
        if candidate.get("run_id") != run_id:
            raise RunStateError("replacement run_id must match the stored run")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_lease_row(connection, lease)
            value = self._compare_and_swap_in_connection(
                connection, run_id, expected_revision, candidate
            )
            connection.commit()
            return value
        except sqlite3.OperationalError as exc:
            connection.rollback()
            raise self._backend_error(exc) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def execution_guard(
        self, lease: RunLease, *, ttl_seconds: int
    ) -> Iterator[None]:
        """Hold SQLite writer ownership across one consequential execution.

        A committed ``executing`` intent must already exist. The guard prevents a
        second local process from transferring the lease or writing recovery
        state while the external call is in flight. If this process dies, SQLite
        releases the lock but the committed run remains ``executing`` and the
        durable operation remains ``started`` for explicit recovery.
        """

        ttl_seconds = self._validate_ttl(ttl_seconds)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_lease_row(connection, lease)
            row = connection.execute(
                "SELECT state_json FROM manager_runs WHERE run_id = ?",
                (lease.run_id,),
            ).fetchone()
            if row is None:
                raise RunStateConflict(f"run does not exist: {lease.run_id}")
            state = self._decoded(row["state_json"])
            if state["status"] != "executing":
                raise RunLeaseConflict(
                    "execution guard requires the durable run to be executing"
                )
            try:
                yield
            except BaseException:
                connection.execute(
                    """
                    UPDATE manager_run_leases SET expires_at_epoch = 0
                    WHERE run_id = ? AND owner_id = ? AND fencing_token = ?
                    """,
                    (lease.run_id, lease.owner_id, lease.fencing_token),
                )
                connection.commit()
                raise
            else:
                expires_at = self._db_now(connection) + ttl_seconds
                connection.execute(
                    """
                    UPDATE manager_run_leases SET expires_at_epoch = ?
                    WHERE run_id = ? AND owner_id = ? AND fencing_token = ?
                    """,
                    (expires_at, lease.run_id, lease.owner_id, lease.fencing_token),
                )
                connection.commit()
        except sqlite3.OperationalError as exc:
            connection.rollback()
            raise self._backend_error(exc) from exc
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def begin_operation(
        self,
        *,
        lease: RunLease,
        operation_id: str,
        request_fingerprint: str,
    ) -> DurableOperation:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise RunStateError("operation_id must be non-empty text")
        if not isinstance(request_fingerprint, str) or not request_fingerprint.strip():
            raise RunStateError("request_fingerprint must be non-empty text")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_lease_row(connection, lease)
            run_row = connection.execute(
                "SELECT state_json FROM manager_runs WHERE run_id = ?",
                (lease.run_id,),
            ).fetchone()
            if run_row is None:
                raise RunStateConflict(f"run does not exist: {lease.run_id}")
            run_state = self._decoded(run_row["state_json"])
            if run_state["status"] != "executing":
                raise RunStateError(
                    "durable operation can begin only after executing intent is committed"
                )

            row = connection.execute(
                """
                SELECT operation_id, run_id, request_fingerprint, fencing_token,
                       status, result_json
                FROM manager_operations WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is not None:
                if (
                    row["run_id"] != lease.run_id
                    or row["request_fingerprint"] != request_fingerprint
                ):
                    raise OperationConflict(
                        f"operation identity collision: {operation_id}"
                    )
                operation = self._operation_from_row(row, claimed=False)
                connection.commit()
                return operation

            now = self._db_now(connection)
            connection.execute(
                """
                INSERT INTO manager_operations(
                    operation_id, run_id, request_fingerprint, fencing_token,
                    status, result_json, created_at_epoch, updated_at_epoch
                ) VALUES (?, ?, ?, ?, 'started', NULL, ?, ?)
                """,
                (
                    operation_id,
                    lease.run_id,
                    request_fingerprint,
                    lease.fencing_token,
                    now,
                    now,
                ),
            )
            connection.commit()
            return DurableOperation(
                operation_id=operation_id,
                run_id=lease.run_id,
                request_fingerprint=request_fingerprint,
                fencing_token=lease.fencing_token,
                status="started",
                result=None,
                claimed=True,
            )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise OperationConflict(
                f"operation identity was concurrently claimed: {operation_id}"
            ) from exc
        except sqlite3.OperationalError as exc:
            connection.rollback()
            raise self._backend_error(exc) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_operation(
        self,
        operation: DurableOperation,
        *,
        lease: RunLease,
        status: str,
        result: RunState | None,
    ) -> DurableOperation:
        if status not in _TERMINAL_OPERATION_STATUSES:
            raise RunStateError("operation terminal status is not normalized")
        if operation.run_id != lease.run_id:
            raise RunStateError("operation run_id does not match lease run_id")
        encoded_result = (
            None
            if result is None
            else json.dumps(result, sort_keys=True, separators=(",", ":"), default=str)
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_lease_row(connection, lease)
            row = connection.execute(
                """
                SELECT operation_id, run_id, request_fingerprint, fencing_token,
                       status, result_json
                FROM manager_operations WHERE operation_id = ?
                """,
                (operation.operation_id,),
            ).fetchone()
            if row is None:
                raise OperationConflict(
                    f"durable operation does not exist: {operation.operation_id}"
                )
            if (
                row["run_id"] != operation.run_id
                or row["request_fingerprint"] != operation.request_fingerprint
            ):
                raise OperationConflict(
                    f"durable operation identity changed: {operation.operation_id}"
                )
            if row["fencing_token"] != lease.fencing_token:
                raise RunLeaseExpired(
                    "stale worker cannot finish an operation claimed by another fence"
                )
            if row["status"] != "started":
                if row["status"] == status and row["result_json"] == encoded_result:
                    value = self._operation_from_row(row, claimed=False)
                    connection.commit()
                    return value
                raise OperationConflict(
                    f"durable operation is already resolved: {operation.operation_id}"
                )
            now = self._db_now(connection)
            connection.execute(
                """
                UPDATE manager_operations
                SET status = ?, result_json = ?, updated_at_epoch = ?
                WHERE operation_id = ? AND status = 'started' AND fencing_token = ?
                """,
                (
                    status,
                    encoded_result,
                    now,
                    operation.operation_id,
                    lease.fencing_token,
                ),
            )
            connection.commit()
            return DurableOperation(
                operation_id=operation.operation_id,
                run_id=operation.run_id,
                request_fingerprint=operation.request_fingerprint,
                fencing_token=lease.fencing_token,
                status=status,
                result=deepcopy(result),
                claimed=False,
            )
        except sqlite3.OperationalError as exc:
            connection.rollback()
            raise self._backend_error(exc) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_operation(self, operation_id: str) -> DurableOperation | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT operation_id, run_id, request_fingerprint, fencing_token,
                           status, result_json
                    FROM manager_operations WHERE operation_id = ?
                    """,
                    (operation_id,),
                ).fetchone()
        except sqlite3.OperationalError as exc:
            raise self._backend_error(exc) from exc
        if row is None:
            return None
        return self._operation_from_row(row, claimed=False)
