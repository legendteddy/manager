from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from manager_runtime.operations import OperationalRuntime
from manager_runtime.state import SQLiteRunStore, require_coordinated_store


class SREStateIntegrationTests(unittest.TestCase):
    def test_observed_store_preserves_coordinated_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "runs.sqlite3")
            observed = OperationalRuntime().observe_run_store(store)

            coordinated = require_coordinated_store(observed)

            self.assertIs(coordinated, observed)
            self.assertEqual(coordinated.capabilities, store.capabilities)
            for method in (
                "acquire_lease",
                "renew_lease",
                "assert_lease",
                "release_lease",
                "fenced_compare_and_swap",
                "execution_guard",
                "begin_operation",
                "finish_operation",
                "resolve_operation_and_compare_and_swap",
                "load_operation",
            ):
                self.assertTrue(callable(getattr(coordinated, method)))


if __name__ == "__main__":
    unittest.main()
