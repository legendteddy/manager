from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from manager_runtime.service.server import _sqlite_dependency
from manager_runtime.state import SQLiteRunStore


class ServiceHealthProbeBoundsTests(unittest.TestCase):
    def test_sqlite_readiness_does_not_inherit_store_lock_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "manager-state.sqlite3")
            SQLiteRunStore(path)

            locker = sqlite3.connect(path, timeout=1.0, isolation_level=None)
            try:
                locker.execute("BEGIN EXCLUSIVE")
                result: list[object] = []

                def probe() -> None:
                    result.append(_sqlite_dependency(path))

                worker = threading.Thread(target=probe, daemon=True)
                worker.start()
                worker.join(timeout=0.75)
                self.assertFalse(
                    worker.is_alive(),
                    "readiness probe exceeded its bounded lock wait",
                )
                self.assertEqual(1, len(result))
            finally:
                try:
                    locker.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                locker.close()

            self.assertIs(_sqlite_dependency(path), True)


if __name__ == "__main__":
    unittest.main()
