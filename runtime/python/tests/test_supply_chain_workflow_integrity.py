from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load_repository_integrity():
    path = ROOT / "scripts" / "repository_integrity.py"
    spec = importlib.util.spec_from_file_location("manager_repository_integrity", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load repository_integrity.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


repository_integrity = load_repository_integrity()


class SupplyChainWorkflowIntegrityTests(unittest.TestCase):
    def test_every_repository_workflow_is_in_supply_chain_scan(self):
        workflows_dir = ROOT / ".github" / "workflows"
        expected = {
            *workflows_dir.glob("*.yml"),
            *workflows_dir.glob("*.yaml"),
        }
        self.assertEqual(set(repository_integrity.SUPPLY_CHAIN_WORKFLOWS), expected)
        self.assertIn(workflows_dir / "deployment-reference.yml", expected)

    def test_unpinned_action_in_compact_workflow_step_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            workflow = Path(tmp) / "new-release-lane.yml"
            workflow.write_text(
                "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v4\n",
                encoding="utf-8",
            )
            failures: list[str] = []
            repository_integrity.validate_supply_chain_workflow(workflow, failures)
            self.assertTrue(any("pinned to a 40-hex commit" in failure for failure in failures))

    def test_dispatch_input_in_compact_run_step_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            workflow = Path(tmp) / "unsafe-dispatch.yml"
            workflow.write_text(
                "jobs:\n  test:\n    steps:\n      - run: |\n          echo '${{ inputs.target }}'\n",
                encoding="utf-8",
            )
            failures: list[str] = []
            repository_integrity.validate_supply_chain_workflow(workflow, failures)
            self.assertTrue(
                any("must enter shell through env" in failure for failure in failures)
            )

    def test_release_publish_requires_both_exact_revision_ci_workflows(self):
        publish = (ROOT / ".github" / "workflows" / "release-publish.yml").read_text(
            encoding="utf-8"
        )
        for workflow in ("repository-integrity.yml", "deployment-reference.yml"):
            self.assertGreaterEqual(
                publish.count(workflow),
                2,
                f"{workflow} must be checked before approval and immediately before publish",
            )
        self.assertGreaterEqual(publish.count('--commit "${RELEASE_SHA}"'), 2)


if __name__ == "__main__":
    unittest.main()
