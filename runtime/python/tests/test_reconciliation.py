from __future__ import annotations

import unittest

from manager_runtime.engine import run


def _task(task_id: str, objective: str = "Propagate an already-confirmed rule to repository consumers.") -> dict:
    return {
        "task_id": task_id,
        "objective": objective,
        "classification": {
            "materiality": "routine",
            "consequence": "low",
            "uncertainty": "low",
            "reversibility": "reversible",
            "sensitivity": "public",
        },
    }


def _context() -> dict:
    return {
        "change_scope": "routine_propagation",
        "candidate_owners": ["repo:canonical"],
        "authoritative_owner": "repo:canonical",
        "confirmation_evidence": "Synthetic maintainer-confirmed rule revision.",
        "authority_evidence": "Synthetic repository-local governance identifies repo:canonical as owner.",
        "dependency_evidence": "Synthetic repository search traced all known consumers from repo:canonical.",
        "confirmed_truth": "rule-v2",
        "owner_state": "rule-v2",
        "dependencies": ["repo:consumer-a", "repo:consumer-b"],
        "inspected_dependencies": ["repo:consumer-a", "repo:consumer-b"],
        "consumer_states": {
            "repo:consumer-a": "rule-v1",
            "repo:consumer-b": "rule-v1",
        },
        "required_surfaces": {
            "repo:consumer-a": ["docs", "tests"],
            "repo:consumer-b": ["docs", "tests", "adapters", "contracts"],
        },
        "reconciled_surfaces": {
            "repo:consumer-a": ["docs", "tests"],
            "repo:consumer-b": ["docs", "tests", "adapters", "contracts"],
        },
    }


class ReconciliationControlPlaneTests(unittest.TestCase):
    def test_authority_is_checked_before_consumer_propagation(self) -> None:
        context = _context()
        context["owner_state"] = "rule-v1"

        outputs = run(
            {
                "task": _task("reconcile-order"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        actions = outputs["reconciliation"]["actions"]
        self.assertEqual(actions[0]["target"], "repo:canonical")
        self.assertEqual(actions[0]["action_type"], "update_authority")
        propagated = [
            action["target"]
            for action in actions
            if action["action_type"] == "propagate"
        ]
        self.assertEqual(propagated, ["repo:consumer-a", "repo:consumer-b"])
        self.assertEqual(outputs["reconciliation"]["verification"]["status"], "pass")

    def test_missing_dependency_cannot_claim_verification_pass(self) -> None:
        context = _context()
        context["inspected_dependencies"] = ["repo:consumer-a"]

        outputs = run(
            {
                "task": _task("reconcile-missing-dependency"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        self.assertEqual(outputs["trace"]["status"], "blocked")
        self.assertEqual(outputs["reconciliation"]["verification"]["status"], "fail")
        self.assertIn(
            "Dependency repo:consumer-b was not inspected.",
            outputs["reconciliation"]["verification"]["residual_discrepancies"],
        )

    def test_docs_only_update_cannot_hide_required_test_adapter_contract_work(self) -> None:
        context = _context()
        context["reconciled_surfaces"]["repo:consumer-b"] = ["docs"]

        outputs = run(
            {
                "task": _task("reconcile-incomplete-surfaces"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        residual = outputs["reconciliation"]["verification"]["residual_discrepancies"]
        self.assertEqual(outputs["reconciliation"]["verification"]["status"], "fail")
        self.assertTrue(any("tests" in item for item in residual))
        self.assertTrue(any("adapters" in item for item in residual))
        self.assertTrue(any("contracts" in item for item in residual))

    def test_ambiguous_authority_requires_smallest_material_decision(self) -> None:
        context = _context()
        context["candidate_owners"] = ["repo:a", "repo:b"]
        context["authoritative_owner"] = None

        outputs = run(
            {
                "task": _task("reconcile-owner-conflict"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        self.assertEqual(outputs["trace"]["classification"]["materiality"], "material")
        self.assertEqual(outputs["approval"]["status"], "pending")
        self.assertTrue(outputs["result"]["owner_decision_required"])
        self.assertIn("authoritative", outputs["result"]["decision_request"].lower())
        self.assertNotIn("reconciliation", outputs)

    def test_routine_label_cannot_hide_material_rule_change(self) -> None:
        context = _context()
        context["change_scope"] = "material_rule_change"

        outputs = run(
            {
                "task": _task("reconcile-material-drift"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        self.assertEqual(outputs["trace"]["classification"]["materiality"], "material")
        self.assertEqual(outputs["approval"]["materiality"], "material")
        self.assertEqual(outputs["trace"]["status"], "blocked")

    def test_current_repository_truth_beats_stale_remembered_state(self) -> None:
        context = _context()
        context["remembered_state"] = "rule-v1"

        outputs = run(
            {
                "task": _task("reconcile-memory-conflict"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        self.assertEqual(outputs["reconciliation"]["verification"]["status"], "pass")
        self.assertIn(
            "Remembered context disagreed",
            outputs["reconciliation"]["verification"]["details"],
        )

    def test_keyword_only_reconciliation_no_longer_fabricates_pass(self) -> None:
        outputs = run({"task": _task("reconcile-no-evidence")})

        self.assertEqual(outputs["trace"]["workflow"], "reconciliation")
        self.assertEqual(outputs["trace"]["status"], "blocked")
        self.assertFalse(outputs["result"]["owner_decision_required"])
        self.assertNotIn("reconciliation", outputs)


if __name__ == "__main__":
    unittest.main()
