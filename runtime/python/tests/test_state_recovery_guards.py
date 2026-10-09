from __future__ import annotations

import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.state import RunStateError, SQLiteRunStore
from manager_runtime.tools.base import tool_request_fingerprint


def _now() -> str:
    return "2026-10-09T00:00:00Z"


def _request() -> dict:
    return {
        "request_id": "request:recovery-guard",
        "run_id": "run:recovery-guard",
        "tool_name": "commit",
        "arguments": {"value": "synthetic"},
        "target": "synthetic-target",
        "proposed_by": "model",
        "proposal_ref": "proposal:recovery-guard",
    }


def _pending_action() -> dict:
    request = _request()
    return {
        "tool_request": request,
        "approval": {
            "approval_id": "approval:request:recovery-guard",
            "run_id": request["run_id"],
            "status": "approved",
            "action": "tool:commit",
            "target": "synthetic-target",
            "material_parameters": request["arguments"],
            "materiality": "material",
            "risk_class": "high",
            "reason": "Synthetic recovery fixture.",
            "recommendation": "Reconcile external evidence before retry.",
            "recovery": "Do not replay an uncertain action automatically.",
            "issued_at": _now(),
            "resolved_at": _now(),
            "resolved_by": "synthetic-human",
            "approved_by": "synthetic-human",
            "action_fingerprint": tool_request_fingerprint(request),
        },
        "tool_definition_fingerprint": "sha256:synthetic",
        "authorization_context": {
            "scope_authorized": True,
            "target_verified": True,
        },
    }


def _state(*, status: str = "recovery_required", revision: int = 1) -> dict:
    return {
        "run_id": "run:recovery-guard",
        "task_id": "recovery-guard",
        "status": status,
        "revision": revision,
        "created_at": _now(),
        "updated_at": _now(),
        "task": {
            "task_id": "recovery-guard",
            "objective": "Exercise durable recovery transition guards.",
            "classification": {
                "materiality": "material",
                "consequence": "high",
                "uncertainty": "high",
                "reversibility": "unknown",
                "sensitivity": "public",
            },
        },
        "pending_action": _pending_action(),
        "last_tool_result": None,
        "trace_snapshot": None,
        "result_snapshot": None,
        "recovery_reason": (
            "Synthetic uncertain external outcome."
            if status == "recovery_required"
            else None
        ),
        "recovery_resolution": None,
        "extensions": {},
    }


def _resolution(decision: str) -> dict:
    return {
        "resolution_id": f"resolution:{decision}",
        "run_id": "run:recovery-guard",
        "decision": decision,
        "decided_by": "synthetic-operator",
        "decided_at": "2026-10-09T00:05:00Z",
        "evidence": "Synthetic external reconciliation evidence.",
    }


class RecoveryTransitionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "recovery-guards.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_recovery_required_cannot_exit_without_resolution_evidence(self) -> None:
        store = SQLiteRunStore(self.path)
        uncertain = store.create(_state())
        lease = store.acquire_lease(
            uncertain["run_id"], "worker:recovery-bypass", ttl_seconds=30
        )
        candidate = deepcopy(uncertain)
        candidate["status"] = "completed"
        candidate["revision"] = uncertain["revision"] + 1
        candidate["updated_at"] = "2026-10-09T00:01:00Z"
        candidate["pending_action"] = None
        candidate["recovery_reason"] = None

        with self.assertRaises(RunStateError):
            store.fenced_compare_and_swap(
                uncertain["run_id"], uncertain["revision"], candidate, lease=lease
            )
        store.release_lease(lease)
        self.assertEqual(store.load(uncertain["run_id"])["status"], "recovery_required")

    def test_recovery_resolution_decision_must_match_exit_state(self) -> None:
        store = SQLiteRunStore(self.path)
        uncertain = store.create(_state())
        lease = store.acquire_lease(
            uncertain["run_id"], "worker:recovery-mismatch", ttl_seconds=30
        )
        candidate = deepcopy(uncertain)
        candidate["status"] = "completed"
        candidate["revision"] = uncertain["revision"] + 1
        candidate["updated_at"] = "2026-10-09T00:02:00Z"
        candidate["pending_action"] = None
        candidate["recovery_reason"] = None
        candidate["recovery_resolution"] = _resolution("confirmed_not_executed")

        with self.assertRaises(RunStateError):
            store.fenced_compare_and_swap(
                uncertain["run_id"], uncertain["revision"], candidate, lease=lease
            )
        store.release_lease(lease)
        self.assertEqual(store.load(uncertain["run_id"])["status"], "recovery_required")


class SQLiteRuntimeGuardIntegrityTests(unittest.TestCase):
    def test_disabled_runtime_guard_trigger_fails_closed_on_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "runtime-guard.sqlite3"
            store = SQLiteRunStore(path)
            running = _state(status="running")
            running["pending_action"] = None
            store.create(running)

            with sqlite3.connect(path) as connection:
                connection.execute("DROP TRIGGER manager_runs_runtime_guard_update")
                connection.execute(
                    """
                    CREATE TRIGGER manager_runs_runtime_guard_update
                    BEFORE UPDATE ON manager_runs
                    WHEN 0
                    BEGIN
                        SELECT CASE
                            WHEN manager_runtime_schema_version() < 3
                            THEN RAISE(ABORT, 'Manager runtime is too old for this coordinated SQLite schema')
                        END;
                    END
                    """
                )
                connection.commit()

            with self.assertRaises(RunStateError):
                SQLiteRunStore(path)


if __name__ == "__main__":
    unittest.main()
