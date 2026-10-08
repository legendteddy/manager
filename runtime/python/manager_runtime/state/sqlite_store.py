from __future__ import annotations

import sqlite3

from .base import RunStateError
from .sqlite_store_v2 import SQLiteRunStore as _SQLiteRunStoreV2

SQLITE_STATE_SCHEMA_VERSION = 3
_SQLITE_RUNTIME_PROTOCOL_FUNCTION = "manager_runtime_schema_version"
_SQLITE_RUNTIME_GUARD_TRIGGERS = {
    "manager_runs_runtime_guard_insert",
    "manager_runs_runtime_guard_update",
    "manager_runs_runtime_guard_delete",
}


class SQLiteRunStore(_SQLiteRunStoreV2):
    """SQLite reference backend with a mixed-runtime write fence.

    Schema v3 preserves the v2 lease/fencing/idempotency implementation and adds
    database triggers that require every writer of ``manager_runs`` to expose a
    Manager runtime protocol function. Older Manager runtimes do not register
    that function, so their writes fail at SQLite rather than silently bypassing
    leases and fencing on a database already upgraded for coordinated execution.

    This remains a local/shared-file reference backend, not a horizontally
    scaled production datastore.
    """

    def _connect(self) -> sqlite3.Connection:
        connection = super()._connect()
        connection.create_function(
            _SQLITE_RUNTIME_PROTOCOL_FUNCTION,
            0,
            lambda: SQLITE_STATE_SCHEMA_VERSION,
            deterministic=True,
        )
        return connection

    @staticmethod
    def _create_runtime_guard_triggers(connection: sqlite3.Connection) -> None:
        message = "Manager runtime is too old for this coordinated SQLite schema"
        for operation in ("INSERT", "UPDATE", "DELETE"):
            name = f"manager_runs_runtime_guard_{operation.lower()}"
            connection.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS {name}
                BEFORE {operation} ON manager_runs
                BEGIN
                    SELECT CASE
                        WHEN {_SQLITE_RUNTIME_PROTOCOL_FUNCTION}() < 3
                        THEN RAISE(ABORT, '{message}')
                    END;
                END
                """
            )

    @staticmethod
    def _schema_version(connection: sqlite3.Connection) -> int:
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
        return version

    @staticmethod
    def _validate_v3_schema(connection: sqlite3.Connection) -> None:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        required_tables = {
            "manager_runs",
            "manager_state_meta",
            "manager_run_leases",
            "manager_operations",
        }
        missing_tables = sorted(required_tables - tables)
        if missing_tables:
            raise RunStateError(
                "SQLite state schema is incomplete: " + ", ".join(missing_tables)
            )

        triggers = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
        missing_triggers = sorted(_SQLITE_RUNTIME_GUARD_TRIGGERS - triggers)
        if missing_triggers:
            raise RunStateError(
                "SQLite coordinated runtime guard is incomplete: "
                + ", ".join(missing_triggers)
            )

    def _upgrade_v2_to_v3(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = self._schema_version(connection)
            if version == SQLITE_STATE_SCHEMA_VERSION:
                self._validate_v3_schema(connection)
                connection.commit()
                return
            if version != 2:
                raise RunStateError(
                    f"no reviewed SQLite migration exists from schema version {version}"
                )
            self._create_runtime_guard_triggers(connection)
            connection.execute(
                "UPDATE manager_state_meta SET value = ? WHERE key = 'schema_version'",
                (str(SQLITE_STATE_SCHEMA_VERSION),),
            )
            self._validate_v3_schema(connection)
            connection.commit()
        except sqlite3.OperationalError as exc:
            connection.rollback()
            raise self._backend_error(exc) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        """Upgrade historical stores while refusing incomplete/future v3 state."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

            if "manager_runs" not in tables or "manager_state_meta" not in tables:
                connection.rollback()
                connection.close()
                super()._initialize()
                self._upgrade_v2_to_v3()
                return

            version = self._schema_version(connection)
            if version == SQLITE_STATE_SCHEMA_VERSION:
                self._validate_v3_schema(connection)
                connection.commit()
                return

            connection.rollback()
            connection.close()
            # The reviewed v1 -> v2 migration remains implemented by the v2
            # reference class. Once it reaches v2, this wrapper adds the v3
            # database-level mixed-runtime write fence.
            super()._initialize()
            self._upgrade_v2_to_v3()
        except sqlite3.OperationalError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise self._backend_error(exc) from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            try:
                connection.close()
            except Exception:
                pass
