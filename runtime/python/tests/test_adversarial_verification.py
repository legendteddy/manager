from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import adversarial_verification_suite as _suite
from adversarial_verification_suite import *  # noqa: F401,F403
from manager_runtime.state import SQLITE_STATE_SCHEMA_VERSION, RunStateError, SQLiteRunStore
from manager_runtime.tools.base import tool_request_fingerprint


def _valid_run_state(status: str = "running", revision: int = 1) -> dict:
    state = {
        "run_id": "run:state-machine",
        "task_id": "task:state-machine",
        "status": status,
        "revision": revision,
        "created_at": "2026-10-08T00:00:00Z",
        "updated_at": f"2026-10-08T00:00:{revision:02d}Z",
        "pending_action": None,
        "recovery_reason": None,
        "extensions": {},
    }
    if status in {"waiting_approval", "executing"}:
        request = {
            "request_id": "tool-request:state-machine",
            "run_id": "run:state-machine",
            "tool_name": "lookup",
            "arguments": {"value": "x"},
            "target": "synthetic-target",
            "proposed_by": "model",
            "proposal_ref": "proposal:state-machine",
        }
        state["pending_action"] = {
            "tool_request": request,
            "approval": {
                "approval_id": "approval:state-machine",
                "run_id": "run:state-machine",
                "action": "lookup",
                "target": "synthetic-target",
                "status": "approved" if status == "executing" else "pending",
                "materiality": "material",
                "reason": "Synthetic transition-matrix fixture.",
                "issued_at": "2026-10-08T00:00:00Z",
                "action_fingerprint": tool_request_fingerprint(request),
            },
            "tool_definition_fingerprint": "synthetic-tool-definition-v1",
            "authorization_context": {"scope_authorized": True},
        }
    if status == "recovery_required":
        state["recovery_reason"] = "Synthetic uncertain outcome."
    return state


# The Worker08 suite predates P2's complete live-approval invariant. Keep the
# adversarial transition matrix intact, but feed it states that are valid at
# the current persistence boundary instead of weakening production validation.
_suite.run_state = _valid_run_state
run_state = _valid_run_state


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
