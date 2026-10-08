from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.state import RunLeaseConflict, SQLiteRunStore


def state() -> dict:
    return {
        "run_id": "run:lease-cas",
        "task_id": "lease-cas",
        "status": "running",
        "revision": 1,
        "created_at": "2026-10-08T16:20:00Z",
        "updated_at": "2026-10-08T16:20:00Z",
        "task": {
            "task_id": "lease-cas",
            "objective": "Exercise fenced state ownership.",
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


class LeaseCASGuardTests(unittest.TestCase):
    def test_plain_cas_cannot_bypass_active_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteRunStore(Path(temp) / "state.sqlite3")
            original = store.create(state())
            lease = store.acquire_lease(
                original["run_id"], "worker:owner", ttl_seconds=30
            )

            revision_two = deepcopy(original)
            revision_two["revision"] = 2
            revision_two["updated_at"] = "2026-10-08T16:21:00Z"
            with self.assertRaises(RunLeaseConflict):
                store.compare_and_swap(
                    original["run_id"], original["revision"], revision_two
                )

            fenced = store.fenced_compare_and_swap(
                original["run_id"],
                original["revision"],
                revision_two,
                lease=lease,
            )
            self.assertEqual(fenced["revision"], 2)
            store.release_lease(lease)

            completed = deepcopy(fenced)
            completed["revision"] = 3
            completed["status"] = "completed"
            completed["updated_at"] = "2026-10-08T16:22:00Z"
            persisted = store.compare_and_swap(
                original["run_id"], fenced["revision"], completed
            )
            self.assertEqual(persisted["status"], "completed")


if __name__ == "__main__":
    unittest.main()
