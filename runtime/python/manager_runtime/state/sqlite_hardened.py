from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .base import RunState, RunStateConflict, RunStateError
from .sqlite_store import SQLITE_STATE_SCHEMA_VERSION, SQLiteRunStore as _CoordinatedSQLiteRunStore
from .transitions import validate_durable_run_state_transition, validate_run_state_shape


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not permitted: {value}")


class SQLiteRunStore(_CoordinatedSQLiteRunStore):
    """Coordinated SQLite store with canonical JSON and revision-integrity checks.

    This layer deliberately preserves schema-v3 leases, fencing, mixed-runtime
    guards, and durable operation semantics while refusing Python-only JSON
    extensions, non-finite numbers, malformed persisted payloads, and a split
    between the indexed revision and the serialized state revision.
    """

    @staticmethod
    def _encoded(state: RunState) -> str:
        validate_run_state_shape(state)
        try:
            return json.dumps(
                state,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise RunStateError("persisted run state is not safely serializable") from exc

    @staticmethod
    def _decoded(payload: str) -> RunState:
        try:
            value = json.loads(payload, parse_constant=_reject_nonfinite)
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
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
            value = json.loads(payload, parse_constant=_reject_nonfinite)
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
            raise RunStateError("persisted operation result is corrupted JSON") from exc
        if not isinstance(value, dict):
            raise RunStateError("persisted operation result must decode to an object")
        return value

    def load(self, run_id: str) -> RunState | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT revision, state_json FROM manager_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
        except sqlite3.OperationalError as exc:
            raise self._backend_error(exc) from exc
        if row is None:
            return None
        value = self._decoded(row["state_json"])
        if value.get("revision") != row["revision"]:
            raise RunStateError("persisted run revision metadata does not match serialized state")
        return value

    def _compare_and_swap_in_connection(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        expected_revision: int,
        candidate: RunState,
    ) -> RunState:
        row = connection.execute(
            "SELECT revision, state_json FROM manager_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunStateConflict(f"run does not exist: {run_id}")
        if row["revision"] != expected_revision:
            raise RunStateConflict(
                f"run revision changed before update: {run_id}@{expected_revision}"
            )
        previous = self._decoded(row["state_json"])
        if previous.get("revision") != row["revision"]:
            raise RunStateError("persisted run revision metadata does not match serialized state")
        validate_durable_run_state_transition(previous, candidate)
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


__all__ = ["SQLiteRunStore", "SQLITE_STATE_SCHEMA_VERSION"]
