from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.state import RunStateError, SQLiteRunStore


def state(*, revision: int = 1, status: str = "running") -> dict:
    return {
        "run_id": "run:mixed-runtime",
        "task_id": "mixed-runtime",
        "status": status,
        "revision": revision,
        "created_at": "2026-10-08T15:30:00Z",
        "updated_at": "2026-10-08T15:30:00Z",
        "task": {
            "task_id": "mixed-runtime",
            "objective": "Exercise mixed SQLite runtime protection.",
            "classification": {
                "materiality": "routine",
                "consequence": "low",
                "uncertainty": "low",
                "reversibility": "reversible",
                "sensitivity": "public",
            },
        },
        "pending_action": None,
        "last_tool_result": None,
        "trace_snapshot": None,
        "result_snapshot": None,
        "recovery_reason": None,
        "extensions": {},
    }


class SQLiteMixedRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "mixed-runtime.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_old_runtime_connection_cannot_update_manager_runs(self) -> None:
        store = SQLiteRunStore(self.path)
        original = store.create(state())

        # Simulate a pre-v3 Manager process: it opens SQLite directly and does
        # not register manager_runtime_schema_version(). The database trigger,
        # not application cooperation, must reject its authoritative write.
        old_connection = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                old_connection.execute(
                    "UPDATE manager_runs SET revision = 2, state_json = ? WHERE run_id = ?",
                    (
                        json.dumps(
                            {**original, "revision": 2, "status": "completed"},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        original["run_id"],
                    ),
                )
                old_connection.commit()
            old_connection.rollback()
        finally:
            old_connection.close()

        persisted = store.load(original["run_id"])
        self.assertEqual(persisted["revision"], 1)
        self.assertEqual(persisted["status"], "running")

    def test_old_runtime_connection_cannot_insert_new_run(self) -> None:
        SQLiteRunStore(self.path)
        old_connection = sqlite3.connect(self.path)
        try:
            candidate = state()
            with self.assertRaises(sqlite3.OperationalError):
                old_connection.execute(
                    "INSERT INTO manager_runs(run_id, revision, state_json) VALUES (?, ?, ?)",
                    (
                        candidate["run_id"],
                        1,
                        json.dumps(candidate, sort_keys=True, separators=(",", ":")),
                    ),
                )
                old_connection.commit()
            old_connection.rollback()
        finally:
            old_connection.close()

    def test_current_runtime_writes_still_pass_v3_guard(self) -> None:
        store = SQLiteRunStore(self.path)
        original = store.create(state())
        replacement = deepcopy(original)
        replacement["revision"] = 2
        replacement["status"] = "completed"
        replacement["updated_at"] = "2026-10-08T15:31:00Z"
        persisted = store.compare_and_swap(
            original["run_id"], original["revision"], replacement
        )
        self.assertEqual(persisted["status"], "completed")
        self.assertEqual(store.load(original["run_id"])["revision"], 2)

    def test_missing_runtime_guard_trigger_is_detected_on_open(self) -> None:
        SQLiteRunStore(self.path)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DROP TRIGGER manager_runs_runtime_guard_update")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(RunStateError):
            SQLiteRunStore(self.path)


if __name__ == "__main__":
    unittest.main()
