from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.state import (
    OperationConflict,
    SQLiteRunStore,
    resolve_recovery_required,
    resume_tool_approval,
)
from manager_runtime.tools import ToolRegistry
from manager_runtime.tools.base import tool_definition_fingerprint, tool_request_fingerprint


def definition() -> dict:
    return {
        "name": "commit",
        "version": "1",
        "description": "Synthetic consequential operation.",
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


class NeverCalledTool:
    def execute(self, arguments: dict):  # pragma: no cover - must not run
        raise AssertionError("tool must not execute in identity hardening tests")

    def verify(self, arguments: dict, output) -> bool:  # pragma: no cover
        raise AssertionError("tool verifier must not run")


def registry() -> ToolRegistry:
    value = ToolRegistry()
    value.register(definition(), NeverCalledTool())
    return value


def request(run_id: str, operation_id: str = "request:shared") -> dict:
    return {
        "request_id": operation_id,
        "run_id": run_id,
        "tool_name": "commit",
        "arguments": {"value": "x"},
        "target": "synthetic-target",
        "proposed_by": "model",
        "proposal_ref": f"proposal:{run_id}",
    }


def state(
    run_id: str,
    *,
    status: str,
    revision: int = 1,
    operation_id: str = "request:shared",
) -> dict:
    req = request(run_id, operation_id)
    recovery_reason = (
        "Synthetic uncertain outcome." if status == "recovery_required" else None
    )
    return {
        "run_id": run_id,
        "task_id": f"task:{run_id}",
        "status": status,
        "revision": revision,
        "created_at": "2026-10-08T16:00:00Z",
        "updated_at": "2026-10-08T16:00:00Z",
        "task": {
            "task_id": f"task:{run_id}",
            "objective": "Exercise durable operation identity binding.",
            "classification": {
                "materiality": "material",
                "consequence": "high",
                "uncertainty": "low",
                "reversibility": "irreversible",
                "sensitivity": "public",
            },
        },
        "pending_action": {
            "tool_request": req,
            "approval": {
                "approval_id": f"approval:{operation_id}",
                "run_id": run_id,
                "status": "approved",
                "action": "tool:commit",
                "target": "synthetic-target",
                "material_parameters": {"value": "x"},
                "materiality": "material",
                "risk_class": "high",
                "reason": "Synthetic.",
                "recommendation": "Synthetic.",
                "recovery": "Reconcile evidence.",
                "issued_at": "2026-10-08T16:00:00Z",
                "resolved_at": "2026-10-08T16:01:00Z",
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
        "recovery_reason": recovery_reason,
        "recovery_resolution": None,
        "extensions": {},
    }


def recovery_resolution(decision: str) -> dict:
    return {
        "resolution_id": f"resolution:{decision}",
        "run_id": "run:recovery",
        "decision": decision,
        "decided_by": "synthetic-operator",
        "decided_at": "2026-10-08T16:10:00Z",
        "evidence": "Synthetic external evidence.",
    }


class StateIdentityHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "identity.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_recovery_cancellation_survives_removed_tool(self) -> None:
        store = SQLiteRunStore(self.path)
        store.create(state("run:recovery", status="recovery_required"))

        # Empty registry simulates a tool that was removed after the uncertain
        # action. Cancellation executes nothing, so tool availability must not
        # prevent the operator from terminating the run safely.
        cancelled = resolve_recovery_required(
            store,
            "run:recovery",
            ToolRegistry(),
            recovery_resolution("cancelled"),
        )
        self.assertEqual(cancelled["status"], "cancelled")

    def test_terminal_operation_conflicting_with_recovery_state_fails_closed(self) -> None:
        store = SQLiteRunStore(self.path)
        reg = registry()
        executing = store.create(state("run:recovery", status="executing"))
        lease = store.acquire_lease(
            executing["run_id"], "worker:seed", ttl_seconds=30
        )
        req = executing["pending_action"]["tool_request"]
        operation = store.begin_operation(
            lease=lease,
            operation_id=req["request_id"],
            request_fingerprint=tool_request_fingerprint(req),
        )
        store.finish_operation(
            operation,
            lease=lease,
            status="confirmed",
            result={
                "request_id": req["request_id"],
                "tool_name": req["tool_name"],
                "status": "executed",
            },
        )
        uncertain = deepcopy(executing)
        uncertain["status"] = "recovery_required"
        uncertain["revision"] = 2
        uncertain["recovery_reason"] = "Synthetic inconsistent state."
        store.fenced_compare_and_swap(
            executing["run_id"], 1, uncertain, lease=lease
        )
        store.release_lease(lease)

        with self.assertRaises(OperationConflict):
            resolve_recovery_required(
                store,
                executing["run_id"],
                reg,
                recovery_resolution("confirmed_succeeded"),
            )

    def test_cross_run_confirmed_operation_cannot_be_reused(self) -> None:
        store = SQLiteRunStore(self.path)
        reg = registry()
        operation_id = "request:collision"
        run_b = store.create(
            state("run:b", status="executing", operation_id=operation_id)
        )
        lease_b = store.acquire_lease(
            run_b["run_id"], "worker:b", ttl_seconds=30
        )
        req_b = run_b["pending_action"]["tool_request"]
        operation = store.begin_operation(
            lease=lease_b,
            operation_id=operation_id,
            request_fingerprint=tool_request_fingerprint(req_b),
        )
        store.finish_operation(
            operation,
            lease=lease_b,
            status="confirmed",
            result={
                "request_id": operation_id,
                "tool_name": "commit",
                "status": "executed",
            },
        )
        store.release_lease(lease_b)

        store.create(state("run:a", status="executing", operation_id=operation_id))
        with self.assertRaises(OperationConflict):
            resume_tool_approval(
                store,
                "run:a",
                reg,
                {},
                current_authorization={},
            )


if __name__ == "__main__":
    unittest.main()
