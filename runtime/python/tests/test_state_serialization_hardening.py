from __future__ import annotations

import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from manager_runtime.state import RunStateError, SQLiteRunStore


def state() -> dict:
    return {
        "run_id": "run:serialization-hardening",
        "task_id": "task:serialization-hardening",
        "status": "running",
        "revision": 1,
        "created_at": "2026-10-08T00:00:00Z",
        "updated_at": "2026-10-08T00:00:00Z",
        "pending_action": None,
        "recovery_reason": None,
        "extensions": {},
    }


class DurableSerializationHardeningTests(unittest.TestCase):
    def test_nonfinite_extension_cannot_be_persisted_as_nonstandard_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "state.sqlite3")
            candidate = state()
            candidate["extensions"]["hostile_number"] = math.nan
            with self.assertRaisesRegex(RunStateError, "safely serializable"):
                store.create(candidate)
            self.assertIsNone(store.load(candidate["run_id"]))

    def test_non_json_extension_type_fails_with_normalized_state_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "state.sqlite3")
            candidate = state()
            candidate["extensions"]["hostile_bytes"] = b"not-json"
            with self.assertRaisesRegex(RunStateError, "safely serializable"):
                store.create(candidate)
            self.assertIsNone(store.load(candidate["run_id"]))

    def test_nonfinite_constant_in_persisted_payload_is_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            store = SQLiteRunStore(path)
            store.create(state())
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE manager_runs SET state_json = replace(state_json, ?, ?) WHERE run_id = ?",
                    (
                        '"extensions":{}',
                        '"extensions":{"hostile_number":NaN}',
                        "run:serialization-hardening",
                    ),
                )
            with self.assertRaisesRegex(RunStateError, "corrupted JSON"):
                store.load("run:serialization-hardening")


if __name__ == "__main__":
    unittest.main()
