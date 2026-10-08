from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from adversarial_verification_suite import *  # noqa: F401,F403
from manager_runtime.state import SQLITE_STATE_SCHEMA_VERSION, RunStateError, SQLiteRunStore


def _current_runtime_sqlite_connection(path: Path) -> sqlite3.Connection:
    """Open a raw connection that deliberately identifies as the current v3 runtime.

    Chaos tests need to inject corrupt-at-rest bytes after the database-level
    mixed-runtime fence has already proven that unidentified/old writers are
    rejected. Registering the protocol function here keeps those two invariants
    independent instead of weakening the production trigger.
    """

    connection = sqlite3.connect(path)
    connection.create_function(
        "manager_runtime_schema_version",
        0,
        lambda: SQLITE_STATE_SCHEMA_VERSION,
        deterministic=True,
    )
    return connection


def _test_corrupted_json_fails_closed_on_load(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "corrupt.sqlite3"
        store = SQLiteRunStore(path)
        store.create(run_state())
        with _current_runtime_sqlite_connection(path) as connection:
            connection.execute(
                "UPDATE manager_runs SET state_json = ? WHERE run_id = ?",
                ('{"revision":', "run:state-machine"),
            )
        with self.assertRaisesRegex(RunStateError, "corrupted JSON"):
            store.load("run:state-machine")


def _test_revision_column_payload_split_brain_fails_closed(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "revision-corrupt.sqlite3"
        store = SQLiteRunStore(path)
        store.create(run_state())
        with _current_runtime_sqlite_connection(path) as connection:
            connection.execute(
                "UPDATE manager_runs SET revision = 9 WHERE run_id = ?",
                ("run:state-machine",),
            )
        with self.assertRaisesRegex(RunStateError, "revision metadata"):
            store.load("run:state-machine")


StateMachineChaosTests.test_corrupted_json_fails_closed_on_load = (
    _test_corrupted_json_fails_closed_on_load
)
StateMachineChaosTests.test_revision_column_payload_split_brain_fails_closed = (
    _test_revision_column_payload_split_brain_fails_closed
)
