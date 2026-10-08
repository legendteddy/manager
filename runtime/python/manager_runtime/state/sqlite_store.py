from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

from .base import RunState, RunStateConflict, RunStateError


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
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decoded(payload: str) -> RunState:
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise RunStateError("persisted run state must decode to an object")
        return value

    def create(self, state: RunState) -> RunState:
        candidate = deepcopy(state)
        if candidate.get("revision") != 1:
            raise RunStateError("new run state must start at revision 1")
        run_id = candidate.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise RunStateError("run state requires a non-empty run_id")
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
                "SELECT state_json FROM manager_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return self._decoded(row["state_json"])

    def compare_and_swap(
        self, run_id: str, expected_revision: int, state: RunState
    ) -> RunState:
        candidate = deepcopy(state)
        new_revision = candidate.get("revision")
        if new_revision != expected_revision + 1:
            raise RunStateError(
                "replacement run state revision must be expected_revision + 1"
            )
        if candidate.get("run_id") != run_id:
            raise RunStateError("replacement run_id must match the stored run")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE manager_runs
                SET revision = ?, state_json = ?
                WHERE run_id = ? AND revision = ?
                """,
                (
                    new_revision,
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
