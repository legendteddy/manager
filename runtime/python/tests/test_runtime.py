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


def reconciliation_evidence() -> dict:
    return {
        "governing_decision_confirmed": True,
        "authoritative_owner": "repo:canonical-doc",
        "detected_state": "Confirmed wording change must propagate to dependent documentation.",
        "dependencies": ["doc:dependent"],
        "authoritative_update": {
            "target": "repo:canonical-doc",
            "status": "completed",
            "evidence": "Canonical documentation already contains the confirmed wording.",
        },
        "propagation": [
            {
                "target": "doc:dependent",
                "status": "completed",
                "evidence": "Dependent documentation now matches the canonical wording.",
            }
        ],
        "verification": {
            "status": "pass",
            "details": "Canonical and dependent documentation were compared after propagation.",
            "residual_discrepancies": [],
        },
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

    def test_routine_reconciliation_is_authority_first_with_explicit_evidence(self) -> None:
        payload = task(
            "reconcile",
            "Propagate an already-confirmed non-material wording change to dependent documentation.",
        )
        payload["prior_state"] = {"reconciliation_evidence": reconciliation_evidence()}
        output = run(payload)
        reconciliation = output["reconciliation"]
        self.assertEqual(reconciliation["classification"], "routine")
        self.assertEqual(reconciliation["authoritative_owner"], "repo:canonical-doc")
        self.assertEqual(reconciliation["actions"][0]["action_type"], "update_authority")
        self.assertEqual(reconciliation["actions"][1]["action_type"], "propagate")
        self.assertEqual(reconciliation["verification"]["status"], "pass")

    def test_routine_reconciliation_without_evidence_does_not_manufacture_pass(self) -> None:
        output = run(
            task(
                "reconcile-missing-evidence",
                "Propagate an already-confirmed non-material wording change to dependent documentation.",
            )
        )
        self.assertEqual(output["trace"]["workflow"], "reconciliation")
        self.assertEqual(output["trace"]["status"], "blocked")
        self.assertEqual(output["result"]["status"], "blocked")
        self.assertNotIn("reconciliation", output)
        self.assertFalse(output["result"]["owner_decision_required"])

    def test_incomplete_reconciliation_evidence_does_not_manufacture_pass(self) -> None:
        payload = task(
            "reconcile-incomplete",
            "Propagate an already-confirmed non-material wording change to dependent documentation.",
        )
        evidence = reconciliation_evidence()
        evidence["verification"] = {
            "status": "unverified",
            "details": "Dependent verification could not be completed.",
            "residual_discrepancies": ["dependent state not checked"],
        }
        payload["prior_state"] = {"reconciliation_evidence": evidence}
        output = run(payload)
        self.assertEqual(output["trace"]["status"], "blocked")
        self.assertNotIn("reconciliation", output)

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
