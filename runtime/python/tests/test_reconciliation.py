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
        "authoritative_update": {
            "target": "repo:canonical",
            "status": "completed",
            "evidence": "Synthetic authoritative update receipt.",
        },
        "propagation": [
            {
                "target": "repo:consumer-a",
                "status": "completed",
                "evidence": "Synthetic consumer-a propagation receipt.",
            },
            {
                "target": "repo:consumer-b",
                "status": "completed",
                "evidence": "Synthetic consumer-b propagation receipt.",
            },
        ],
        "verification": {
            "status": "pass",
            "details": "Synthetic post-action verification inspected all declared targets.",
            "residual_discrepancies": [],
        },
        "verified_states": {
            "repo:canonical": "rule-v2",
            "repo:consumer-a": "rule-v2",
            "repo:consumer-b": "rule-v2",
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
        self.assertEqual(actions[0]["evidence"], "Synthetic authoritative update receipt.")
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

    def test_dependency_inventory_requires_trace_evidence(self) -> None:
        context = _context()
        context.pop("dependency_evidence")
        context["dependencies"] = []
        context["inspected_dependencies"] = []
        context["consumer_states"] = {}
        context["required_surfaces"] = {}
        context["reconciled_surfaces"] = {}

        outputs = run(
            {
                "task": _task("reconcile-missing-dependency-evidence"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        self.assertEqual(outputs["trace"]["status"], "blocked")
        self.assertIn("dependencies were traced", outputs["result"]["finding"])
        self.assertNotIn("reconciliation", outputs)

    def test_inspected_consumer_must_be_declared_in_dependency_map(self) -> None:
        context = _context()
        context["dependencies"] = ["repo:consumer-a"]
        context["inspected_dependencies"] = ["repo:consumer-a", "repo:consumer-c"]
        context["consumer_states"] = {"repo:consumer-a": "rule-v1"}
        context["propagation"] = [context["propagation"][0]]
        context["verified_states"] = {
            "repo:canonical": "rule-v2",
            "repo:consumer-a": "rule-v2",
        }
        context["required_surfaces"] = {"repo:consumer-a": ["docs", "tests"]}
        context["reconciled_surfaces"] = {"repo:consumer-a": ["docs", "tests"]}

        outputs = run(
            {
                "task": _task("reconcile-inspected-untracked-consumer"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        residual = outputs["reconciliation"]["verification"]["residual_discrepancies"]
        self.assertEqual(outputs["reconciliation"]["verification"]["status"], "fail")
        self.assertIn(
            "Consumer repo:consumer-c is absent from the dependency map.",
            residual,
        )

    def test_failed_consumer_write_cannot_claim_verification_pass(self) -> None:
        context = _context()
        context["verified_states"]["repo:consumer-b"] = "rule-v1"

        outputs = run(
            {
                "task": _task("reconcile-failed-consumer-write"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        residual = outputs["reconciliation"]["verification"]["residual_discrepancies"]
        self.assertEqual(outputs["reconciliation"]["verification"]["status"], "fail")
        self.assertIn(
            "Post-action verification for target repo:consumer-b did not match confirmed truth.",
            residual,
        )
        failed_actions = [
            action
            for action in outputs["reconciliation"]["actions"]
            if action["target"] == "repo:consumer-b" and action["status"] == "failed"
        ]
        self.assertTrue(failed_actions)

    def test_authoritative_owner_cannot_be_its_own_dependent_consumer(self) -> None:
        context = _context()
        context["dependencies"].append("repo:canonical")
        context["inspected_dependencies"].append("repo:canonical")
        context["consumer_states"]["repo:canonical"] = "rule-v1"
        context["required_surfaces"]["repo:canonical"] = ["docs"]
        context["reconciled_surfaces"]["repo:canonical"] = ["docs"]

        outputs = run(
            {
                "task": _task("reconcile-owner-self-dependency"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        residual = outputs["reconciliation"]["verification"]["residual_discrepancies"]
        self.assertEqual(outputs["reconciliation"]["verification"]["status"], "fail")
        self.assertIn(
            "Authoritative owner repo:canonical cannot also be a dependent consumer.",
            residual,
        )
        propagated_owner = [
            action
            for action in outputs["reconciliation"]["actions"]
            if action["target"] == "repo:canonical" and action["action_type"] == "propagate"
        ]
        self.assertEqual(propagated_owner, [])

    def test_converged_state_without_propagation_receipts_cannot_pass(self) -> None:
        context = _context()
        context.pop("propagation")

        outputs = run(
            {
                "task": _task("reconcile-no-propagation-receipts"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        residual = outputs["reconciliation"]["verification"]["residual_discrepancies"]
        self.assertEqual(outputs["trace"]["status"], "blocked")
        self.assertEqual(outputs["reconciliation"]["verification"]["status"], "fail")
        self.assertIn(
            "Propagation for repo:consumer-a lacks explicit completed action evidence.",
            residual,
        )
        self.assertIn(
            "Propagation for repo:consumer-b lacks explicit completed action evidence.",
            residual,
        )

    def test_converged_owner_state_without_authoritative_update_receipt_cannot_pass(self) -> None:
        context = _context()
        context["owner_state"] = "rule-v1"
        context.pop("authoritative_update")

        outputs = run(
            {
                "task": _task("reconcile-no-owner-update-receipt"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        residual = outputs["reconciliation"]["verification"]["residual_discrepancies"]
        self.assertEqual(outputs["reconciliation"]["verification"]["status"], "fail")
        self.assertIn(
            "Authoritative update for repo:canonical lacks explicit completed action evidence.",
            residual,
        )

    def test_final_state_without_explicit_consistency_verification_cannot_pass(self) -> None:
        context = _context()
        context.pop("verification")

        outputs = run(
            {
                "task": _task("reconcile-no-final-verification"),
                "prior_state": {"reconciliation_context": context},
            }
        )

        self.assertEqual(outputs["reconciliation"]["verification"]["status"], "fail")
        self.assertIn(
            "Final consistency verification lacks explicit passing evidence.",
            outputs["reconciliation"]["verification"]["residual_discrepancies"],
        )

    def test_keyword_only_reconciliation_no_longer_fabricates_pass(self) -> None:
        outputs = run({"task": _task("reconcile-no-evidence")})

        self.assertEqual(outputs["trace"]["workflow"], "reconciliation")
        self.assertEqual(outputs["trace"]["status"], "blocked")
        self.assertFalse(outputs["result"]["owner_decision_required"])
        self.assertNotIn("reconciliation", outputs)


if __name__ == "__main__":
    unittest.main()
