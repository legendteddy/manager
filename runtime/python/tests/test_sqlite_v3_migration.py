from __future__ import annotations

import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.state import SQLITE_STATE_SCHEMA_VERSION, RunStateError, SQLiteRunStore
from manager_runtime.state.sqlite_store_v2 import SQLiteRunStore as SQLiteRunStoreV2


def state() -> dict:
    return {
        "run_id": "run:v2-upgrade",
        "task_id": "v2-upgrade",
        "status": "running",
        "revision": 1,
        "created_at": "2026-10-08T16:40:00Z",
        "updated_at": "2026-10-08T16:40:00Z",
        "task": {
            "task_id": "v2-upgrade",
            "objective": "Verify the mixed-runtime schema upgrade.",
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


class SQLiteV3MigrationTests(unittest.TestCase):
    def test_v2_state_upgrades_to_v3_and_old_writer_is_fenced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.sqlite3"
            old_store = SQLiteRunStoreV2(path)
            original = old_store.create(state())

            with sqlite3.connect(path) as connection:
                before = int(
                    connection.execute(
                        "SELECT value FROM manager_state_meta WHERE key = 'schema_version'"
                    ).fetchone()[0]
                )
            self.assertEqual(before, 2)

            upgraded = SQLiteRunStore(path)
            self.assertEqual(
                upgraded.load(original["run_id"])["revision"], original["revision"]
            )
            with sqlite3.connect(path) as connection:
                after = int(
                    connection.execute(
                        "SELECT value FROM manager_state_meta WHERE key = 'schema_version'"
                    ).fetchone()[0]
                )
            self.assertEqual(after, SQLITE_STATE_SCHEMA_VERSION)
            self.assertEqual(after, 3)

            stale_candidate = deepcopy(original)
            stale_candidate["revision"] = 2
            stale_candidate["status"] = "completed"
            stale_candidate["updated_at"] = "2026-10-08T16:41:00Z"
            # The old implementation does not register the v3 protocol
            # function. Its write is rejected by the database trigger before it
            # can bypass leases/fencing on the upgraded shared file.
            with self.assertRaises(RunStateError):
                old_store.compare_and_swap(
                    original["run_id"], original["revision"], stale_candidate
                )

            current_candidate = deepcopy(original)
            current_candidate["revision"] = 2
            current_candidate["status"] = "completed"
            current_candidate["updated_at"] = "2026-10-08T16:42:00Z"
            persisted = upgraded.compare_and_swap(
                original["run_id"], original["revision"], current_candidate
            )
            self.assertEqual(persisted["status"], "completed")


if __name__ == "__main__":
    unittest.main()
