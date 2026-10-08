from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from manager_runtime.state import (
    RunStateError,
    SQLiteRunStore,
    resolve_recovery_required,
)
from manager_runtime.tools import ToolRegistry
from manager_runtime.tools.base import tool_definition_fingerprint, tool_request_fingerprint


def definition() -> dict:
    return {
        "name": "commit",
        "version": "1",
        "description": "Synthetic recovery-race tool.",
        "side_effect_class": "external_commitment",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "requires_verification": True,
        "sensitive_output": False,
    }


class NeverExecute:
    def execute(self, arguments: dict):  # pragma: no cover - recovery must not execute
        raise AssertionError("recovery race must not execute the tool")

    def verify(self, arguments: dict, output) -> bool:  # pragma: no cover
        raise AssertionError("recovery race must not verify a new execution")


def registry() -> ToolRegistry:
    value = ToolRegistry()
    value.register(definition(), NeverExecute())
    return value


def state() -> dict:
    req = {
        "request_id": "request:recovery-race",
        "run_id": "run:recovery-race",
        "tool_name": "commit",
        "arguments": {"value": "x"},
        "target": "synthetic-target",
        "proposed_by": "model",
        "proposal_ref": "proposal:recovery-race",
    }
    return {
        "run_id": "run:recovery-race",
        "task_id": "recovery-race",
        "status": "recovery_required",
        "revision": 1,
        "created_at": "2026-10-08T17:00:00Z",
        "updated_at": "2026-10-08T17:00:00Z",
        "task": {
            "task_id": "recovery-race",
            "objective": "Resolve one synthetic uncertain action.",
            "classification": {
                "materiality": "material",
                "consequence": "high",
                "uncertainty": "high",
                "reversibility": "irreversible",
                "sensitivity": "public",
            },
        },
        "pending_action": {
            "tool_request": req,
            "approval": {
                "approval_id": "approval:recovery-race",
                "run_id": "run:recovery-race",
                "status": "approved",
                "action": "tool:commit",
                "target": "synthetic-target",
                "material_parameters": {"value": "x"},
                "materiality": "material",
                "risk_class": "high",
                "reason": "Synthetic.",
                "recommendation": "Synthetic.",
                "recovery": "Reconcile evidence.",
                "issued_at": "2026-10-08T16:59:00Z",
                "resolved_at": "2026-10-08T16:59:30Z",
                "resolved_by": "synthetic-human",
                "approved_by": "synthetic-human",
                "action_fingerprint": tool_request_fingerprint(req),
            },
            "tool_definition_fingerprint": tool_definition_fingerprint(definition()),
            "authorization_context": {
                "scope_authorized": True,
                "target_verified": True,
            },
        },
        "last_tool_result": None,
        "trace_snapshot": None,
        "result_snapshot": None,
        "recovery_reason": "Synthetic uncertain outcome.",
        "recovery_resolution": None,
        "extensions": {},
    }


def resolution(decision: str, suffix: str) -> dict:
    return {
        "resolution_id": f"resolution:{suffix}",
        "run_id": "run:recovery-race",
        "decision": decision,
        "decided_by": f"operator:{suffix}",
        "decided_at": "2026-10-08T17:01:00Z",
        "evidence": f"Synthetic evidence from {suffix}.",
    }


class ConcurrentRecoveryResolutionTests(unittest.TestCase):
    def test_conflicting_recovery_decisions_cannot_both_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.sqlite3"
            SQLiteRunStore(path).create(state())
            reg = registry()
            barrier = threading.Barrier(2)
            successes: list[str] = []
            failures: list[BaseException] = []

            def resolve(decision: str, suffix: str) -> None:
                try:
                    barrier.wait(timeout=2)
                    result = resolve_recovery_required(
                        SQLiteRunStore(path, timeout_seconds=0.1),
                        "run:recovery-race",
                        reg,
                        resolution(decision, suffix),
                        current_authorization={
                            "scope_authorized": True,
                            "target_verified": True,
                        },
                        worker_id=f"worker:{suffix}",
                    )
                    successes.append(result["status"])
                except BaseException as exc:  # diagnostic capture
                    failures.append(exc)

            first = threading.Thread(
                target=resolve, args=("confirmed_not_executed", "first")
            )
            second = threading.Thread(target=resolve, args=("cancelled", "second"))
            first.start()
            second.start()
            first.join(timeout=3)
            second.join(timeout=3)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(len(successes), 1)
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], RunStateError)

            persisted = SQLiteRunStore(path).load("run:recovery-race")
            self.assertIn(persisted["status"], {"waiting_approval", "cancelled"})
            self.assertEqual(persisted["revision"], 2)
            self.assertIsNotNone(persisted.get("recovery_resolution"))


if __name__ == "__main__":
    unittest.main()
