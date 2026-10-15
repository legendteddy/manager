from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from manager_runtime.service.idempotency import (
    IdempotencyError,
    SQLiteIdempotencyStore,
    canonical_fingerprint,
)


class ServiceIdempotencyReadinessTests(unittest.TestCase):
    def test_ready_proves_required_schema_and_write_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api.sqlite3"
            store = SQLiteIdempotencyStore(path)
            self.assertIs(store.ready(), True)

            blocker = sqlite3.connect(path, timeout=0, isolation_level=None)
            try:
                blocker.execute("BEGIN IMMEDIATE")
                started = time.monotonic()
                ready = store.ready()
                elapsed = time.monotonic() - started
                self.assertIsInstance(ready, tuple)
                self.assertFalse(ready[0])
                self.assertLess(elapsed, 0.5)
            finally:
                blocker.rollback()
                blocker.close()

    def test_malformed_existing_tables_are_not_reported_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE manager_api_idempotency (broken TEXT)")
                connection.execute("CREATE TABLE manager_api_run_owners (broken TEXT)")
            store = SQLiteIdempotencyStore(path)
            ready = store.ready()
            self.assertIsInstance(ready, tuple)
            self.assertFalse(ready[0])

    def test_corrupted_completed_replay_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api.sqlite3"
            store = SQLiteIdempotencyStore(path)
            fingerprint = canonical_fingerprint("POST", "/v1/runs", {"x": 1})
            claim = store.claim(
                subject="principal",
                key="corrupt",
                method="POST",
                path="/v1/runs",
                request_fingerprint=fingerprint,
                run_id="run:corrupt",
            )
            self.assertEqual("new", claim.disposition)
            self.assertTrue(
                store.complete(
                    subject="principal",
                    key="corrupt",
                    status_code=200,
                    response={"ok": True},
                )
            )
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    UPDATE manager_api_idempotency
                       SET response_json = ?
                     WHERE subject = ? AND idempotency_key = ?
                    """,
                    ('{"value":NaN}', "principal", "corrupt"),
                )
            with self.assertRaises(IdempotencyError):
                store.claim(
                    subject="principal",
                    key="corrupt",
                    method="POST",
                    path="/v1/runs",
                    request_fingerprint=fingerprint,
                    run_id="run:corrupt",
                )


if __name__ == "__main__":
    unittest.main()
