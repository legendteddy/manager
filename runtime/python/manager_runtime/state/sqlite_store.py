from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

from .base import RunState, RunStateConflict, RunStateError
from .transitions import validate_run_state_shape, validate_run_state_transition


class SQLiteRunStore:
    """Zero-dependency durable run store using Python's sqlite3 module.

    The database path is supplied by the embedding application and must remain
    outside the public repository. This store is a reference durability layer,
    not a secret store or encryption boundary.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS manager_runs (
                    run_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    state_json TEXT NOT NULL
                )
                """
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
    def _validate_revision_consistency(row_revision: int, state: RunState) -> None:
        if state["revision"] != row_revision:
            raise RunStateError(
                "persisted run revision metadata does not match serialized state"
            )

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
        return candidate

    def load(self, run_id: str) -> RunState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision, state_json FROM manager_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        state = self._decoded(row["state_json"])
        self._validate_revision_consistency(row["revision"], state)
        return state

    def compare_and_swap(
        self, run_id: str, expected_revision: int, state: RunState
    ) -> RunState:
        candidate = deepcopy(state)
        if candidate.get("run_id") != run_id:
            raise RunStateError("replacement run_id must match the stored run")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision, state_json FROM manager_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise RunStateConflict(f"run does not exist: {run_id}")
            if row["revision"] != expected_revision:
                connection.rollback()
                raise RunStateConflict(
                    f"run revision changed before update: {run_id}@{expected_revision}"
                )
            previous = self._decoded(row["state_json"])
            try:
                self._validate_revision_consistency(row["revision"], previous)
            except RunStateError:
                connection.rollback()
                raise
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
                connection.rollback()
                raise RunStateConflict(
                    f"run revision changed before update: {run_id}@{expected_revision}"
                )
            connection.commit()
        finally:
            connection.close()
        return candidate
