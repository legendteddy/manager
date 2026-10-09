from __future__ import annotations

import unittest

from manager_runtime.engine import run


def task(
    task_id: str,
    objective: str,
    *,
    materiality: str = "routine",
    consequence: str = "low",
    uncertainty: str = "low",
    reversibility: str = "reversible",
    sensitivity: str = "public",
) -> dict:
    return {
        "task": {
            "task_id": task_id,
            "objective": objective,
            "classification": {
                "materiality": materiality,
                "consequence": consequence,
                "uncertainty": uncertainty,
                "reversibility": reversibility,
                "sensitivity": sensitivity,
            },
        },
        "trusted_policy_context": [],
        "untrusted_content": [],
    }


class ReferenceRuntimeTests(unittest.TestCase):
    def test_simple_task_stays_direct(self) -> None:
        output = run(task("simple", "Summarize a short public paragraph."))
        self.assertEqual(output["trace"]["workflow"], "direct")
        self.assertNotIn("approval", output)
        self.assertFalse(output["result"]["owner_decision_required"])

    def test_material_action_blocks_for_approval(self) -> None:
        payload = task(
            "material",
            "Delete an authoritative production dataset.",
            materiality="material",
            consequence="critical",
            reversibility="irreversible",
            sensitivity="sensitive",
        )
        output = run(payload)
        self.assertEqual(output["trace"]["status"], "blocked")
        self.assertEqual(output["approval"]["status"], "pending")
        self.assertTrue(output["approval"]["action_fingerprint"].startswith("sha256:"))

    def test_stale_approval_is_rejected(self) -> None:
        payload = task(
            "stale",
            "Execute a previously approved action after its target parameters changed.",
            materiality="material",
            consequence="high",
            reversibility="partially_reversible",
            sensitivity="internal",
        )
        payload["prior_state"] = {
            "approval_status": "approved",
            "approved_action_fingerprint": "sha256:old",
            "current_action_fingerprint": "sha256:new",
        }
        output = run(payload)
        self.assertEqual(output["approval"]["status"], "stale")
        self.assertEqual(output["trace"]["status"], "blocked")

    def test_routine_reconciliation_is_authority_first(self) -> None:
        payload = task(
            "reconcile",
            "Propagate an already-confirmed non-material wording change to dependent documentation.",
        )
        payload["prior_state"] = {
            "reconciliation_context": {
                "change_scope": "routine_propagation",
                "candidate_owners": ["repo:canonical"],
                "authoritative_owner": "repo:canonical",
                "confirmation_evidence": "Synthetic maintainer-confirmed wording revision.",
                "authority_evidence": "Synthetic repository-local governance identifies the canonical owner.",
                "dependency_evidence": "Synthetic repository search traced the dependent documentation consumer.",
                "confirmed_truth": "wording-v2",
                "owner_state": "wording-v1",
                "dependencies": ["repo:docs-consumer"],
                "inspected_dependencies": ["repo:docs-consumer"],
                "consumer_states": {"repo:docs-consumer": "wording-v1"},
                "required_surfaces": {"repo:docs-consumer": ["docs"]},
                "reconciled_surfaces": {"repo:docs-consumer": ["docs"]},
                "verified_states": {
                    "repo:canonical": "wording-v2",
                    "repo:docs-consumer": "wording-v2",
                },
            }
        }
        output = run(payload)
        reconciliation = output["reconciliation"]
        self.assertEqual(reconciliation["classification"], "routine")
        self.assertEqual(reconciliation["actions"][0]["action_type"], "update_authority")
        self.assertEqual(reconciliation["verification"]["status"], "pass")

    def test_specialist_handoff_cannot_modify_state(self) -> None:
        output = run(
            task(
                "handoff",
                "Ask a specialist to analyze a deployment plan without modifying production.",
                consequence="medium",
                uncertainty="medium",
                sensitivity="internal",
            )
        )
        self.assertEqual(output["trace"]["workflow"], "specialist")
        authority = output["handoff"]["authority"]
        self.assertTrue(authority["analyze"])
        self.assertFalse(authority["modify_state"])
        self.assertFalse(authority["external_communication"])
        self.assertFalse(authority["destructive_action"])


if __name__ == "__main__":
    unittest.main()
