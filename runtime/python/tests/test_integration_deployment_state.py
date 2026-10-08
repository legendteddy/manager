from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from manager_runtime.deployment import create_sqlite_backup, restore_sqlite_backup
from manager_runtime.state import SQLITE_STATE_SCHEMA_VERSION, SQLiteRunStore


def _state() -> dict:
    timestamp = "2026-10-08T12:00:00Z"
    return {
        "run_id": "run:backup-integration",
        "task_id": "backup-integration",
        "status": "executing",
        "revision": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
        "task": {
            "task_id": "backup-integration",
            "objective": "Prove coordinated state survives operator backup and restore.",
            "classification": {
                "materiality": "routine",
                "consequence": "low",
                "uncertainty": "low",
                "reversibility": "reversible",
                "sensitivity": "public",
            },
        },
        "pending_action": {"phase": "executing", "synthetic": True},
        "last_tool_result": None,
        "trace_snapshot": None,
        "result_snapshot": None,
        "recovery_reason": None,
        "extensions": {},
    }


class DeploymentStateIntegrationTests(unittest.TestCase):
    def test_backup_restore_preserves_v2_coordination_metadata_and_operation_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite3"
            backup = root / "backup.sqlite3"
            restored = root / "restored.sqlite3"

            store = SQLiteRunStore(source)
            store.create(_state())
            lease = store.acquire_lease(
                "run:backup-integration", "worker:backup-test", ttl_seconds=60
            )
            operation = store.begin_operation(
                lease=lease,
                operation_id="operation:backup-integration",
                request_fingerprint="sha256:synthetic-backup-fingerprint",
            )
            store.finish_operation(
                operation,
                lease=lease,
                status="confirmed",
                result={"ok": True},
            )
            store.release_lease(lease)

            metadata = create_sqlite_backup(source, backup)
            self.assertIn("manager_state_meta", metadata["schema"]["tables"])
            self.assertIn("manager_operations", metadata["schema"]["tables"])
            restore_sqlite_backup(backup, restored)

            with sqlite3.connect(restored) as connection:
                version = connection.execute(
                    "SELECT value FROM manager_state_meta WHERE key='schema_version'"
                ).fetchone()[0]
            self.assertEqual(str(SQLITE_STATE_SCHEMA_VERSION), version)

            reopened = SQLiteRunStore(restored)
            state = reopened.load("run:backup-integration")
            self.assertIsNotNone(state)
            self.assertEqual(1, state["revision"])
            recovered_operation = reopened.load_operation(
                "operation:backup-integration"
            )
            self.assertIsNotNone(recovered_operation)
            self.assertEqual("confirmed", recovered_operation.status)
            self.assertEqual({"ok": True}, recovered_operation.result)


if __name__ == "__main__":
    unittest.main()
