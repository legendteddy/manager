from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.state import RunLeaseExpired, SQLiteRunStore


def state() -> dict:
    return {
        "run_id": "run:stale-release",
        "task_id": "stale-release",
        "status": "running",
        "revision": 1,
        "created_at": "2026-10-08T17:10:00Z",
        "updated_at": "2026-10-08T17:10:00Z",
        "task": {
            "task_id": "stale-release",
            "objective": "Verify stale lease release cannot revoke a successor.",
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


class StaleLeaseReleaseTests(unittest.TestCase):
    def test_stale_release_cannot_revoke_successor_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteRunStore(Path(temp) / "state.sqlite3")
            original = store.create(state())
            first = store.acquire_lease(
                original["run_id"], "worker:first", ttl_seconds=30
            )
            store.release_lease(first)
            successor = store.acquire_lease(
                original["run_id"], "worker:successor", ttl_seconds=30
            )
            self.assertGreater(successor.fencing_token, first.fencing_token)

            with self.assertRaises(RunLeaseExpired):
                store.release_lease(first)
            store.assert_lease(successor)

            replacement = deepcopy(original)
            replacement["revision"] = 2
            replacement["updated_at"] = "2026-10-08T17:11:00Z"
            persisted = store.fenced_compare_and_swap(
                original["run_id"], 1, replacement, lease=successor
            )
            self.assertEqual(persisted["revision"], 2)


if __name__ == "__main__":
    unittest.main()
