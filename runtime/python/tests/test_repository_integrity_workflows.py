from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.repository_integrity import validate_supply_chain_workflow


class SupplyChainWorkflowIntegrityTests(unittest.TestCase):
    def _failures_for(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "workflow.yml"
            path.write_text(text, encoding="utf-8")
            failures: list[str] = []
            validate_supply_chain_workflow(path, failures)
            return failures

    def test_inline_run_rejects_direct_dispatch_input_interpolation(self) -> None:
        failures = self._failures_for(
            "jobs:\n"
            "  publish:\n"
            "    steps:\n"
            "      - run: echo \"${{ inputs.destination }}\"\n"
        )
        self.assertTrue(
            any("workflow_dispatch inputs must enter shell through env" in item for item in failures)
        )

    def test_compact_block_run_rejects_direct_dispatch_input_interpolation(self) -> None:
        failures = self._failures_for(
            "jobs:\n"
            "  publish:\n"
            "    steps:\n"
            "      - run: |\n"
            "          echo \"${{ inputs.destination }}\"\n"
        )
        self.assertTrue(
            any("workflow_dispatch inputs must enter shell through env" in item for item in failures)
        )

    def test_inline_run_allows_env_indirection(self) -> None:
        failures = self._failures_for(
            "jobs:\n"
            "  publish:\n"
            "    steps:\n"
            "      - run: echo \"${DESTINATION}\"\n"
            "        env:\n"
            "          DESTINATION: ${{ inputs.destination }}\n"
        )
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
