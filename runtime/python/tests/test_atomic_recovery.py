from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.state import (
    RunStateError,
    SQLiteRunStore,
    checkpoint_pending_tool_approval,
    resolve_recovery_required,
    resume_tool_approval,
)
from manager_runtime.tools import ToolRegistry, execute_tool_request
from manager_runtime.tools.base import tool_request_fingerprint


def task() -> dict:
    return {
        "task_id": "atomic-recovery",
        "objective": "Perform one synthetic external commitment.",
        "classification": {
            "materiality": "material",
            "consequence": "high",
            "uncertainty": "low",
            "reversibility": "irreversible",
            "sensitivity": "public",
        },
    }


def request() -> dict:
    return {
        "request_id": "request:atomic-recovery",
        "run_id": "run:atomic-recovery",
        "tool_name": "commit",
        "arguments": {"value": "x"},
        "target": "synthetic-target",
        "proposed_by": "model",
        "proposal_ref": "proposal:atomic-recovery",
    }


def definition() -> dict:
    return {
        "name": "commit",
        "version": "1",
        "description": "Synthetic irreversible commitment.",
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


def decision() -> dict:
    return {
        "approval_id": "approval:request:atomic-recovery",
        "decision": "approved",
        "decided_by": "synthetic-human",
        "decided_at": "2026-10-08T15:00:00Z",
    }


def resolution(value: str) -> dict:
    return {
        "resolution_id": f"resolution:{value}",
        "run_id": "run:atomic-recovery",
        "decision": value,
        "decided_by": "synthetic-operator",
        "decided_at": "2026-10-08T15:10:00Z",
        "evidence": "Synthetic external reconciliation evidence.",
    }


class EffectThenErrorTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        raise RuntimeError("synthetic response loss")

    def verify(self, arguments: dict, output) -> bool:
        return False


class CrashAfterEffectTool(EffectThenErrorTool):
    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        raise SystemExit("synthetic process loss")


class AtomicRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "recovery.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _checkpoint(self, tool):
        registry = ToolRegistry()
        registry.register(definition(), tool)
        req = request()
        pending = execute_tool_request(
            task(),
            req,
            registry,
            {"scope_authorized": True, "target_verified": True},
        )
        self.assertEqual(pending["status"], "approval_required")
        store = SQLiteRunStore(self.path)
        state = checkpoint_pending_tool_approval(
            task(),
            req,
            pending,
            registry,
            store,
            authorization_context={
                "scope_authorized": True,
                "target_verified": True,
            },
        )
        return store, registry, state

    def test_confirmed_success_atomically_resolves_unknown_operation(self) -> None:
        tool = EffectThenErrorTool()
        store, registry, state = self._checkpoint(tool)
        uncertain = resume_tool_approval(
            store,
            state["run_id"],
            registry,
            decision(),
            current_authorization={
                "scope_authorized": True,
                "target_verified": True,
                "human_intent_confirmed": True,
            },
            worker_id="worker:effect",
        )
        self.assertEqual(uncertain["status"], "recovery_required")
        self.assertEqual(store.load_operation(request()["request_id"]).status, "outcome_unknown")

        recovered = resolve_recovery_required(
            store,
            state["run_id"],
            registry,
            resolution("confirmed_succeeded"),
            worker_id="worker:recovery",
        )
        operation = store.load_operation(request()["request_id"])
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(operation.status, "confirmed")
        self.assertEqual(operation.result["status"], "executed")
        self.assertEqual(operation.fencing_token, 2)
        self.assertEqual(len(tool.calls), 1)

    def test_confirmed_not_executed_resolves_started_operation_and_mints_fresh_identity(self) -> None:
        tool = CrashAfterEffectTool()
        store, registry, state = self._checkpoint(tool)
        with self.assertRaises(SystemExit):
            resume_tool_approval(
                store,
                state["run_id"],
                registry,
                decision(),
                current_authorization={
                    "scope_authorized": True,
                    "target_verified": True,
                    "human_intent_confirmed": True,
                },
                worker_id="worker:crash",
            )
        recovery_state = resume_tool_approval(
            store,
            state["run_id"],
            registry,
            decision(),
            current_authorization={},
            worker_id="worker:detect",
        )
        self.assertEqual(recovery_state["status"], "recovery_required")
        self.assertEqual(store.load_operation(request()["request_id"]).status, "started")

        recovered = resolve_recovery_required(
            store,
            state["run_id"],
            registry,
            resolution("confirmed_not_executed"),
            current_authorization={
                "scope_authorized": True,
                "target_verified": True,
            },
            worker_id="worker:resolve",
        )
        operation = store.load_operation(request()["request_id"])
        self.assertEqual(operation.status, "not_executed")
        self.assertEqual(recovered["status"], "waiting_approval")
        self.assertNotEqual(
            recovered["pending_action"]["tool_request"]["request_id"],
            request()["request_id"],
        )
        self.assertEqual(len(tool.calls), 1)

    def test_atomic_recovery_rolls_back_operation_when_run_transition_is_invalid(self) -> None:
        tool = EffectThenErrorTool()
        store, registry, state = self._checkpoint(tool)
        uncertain = resume_tool_approval(
            store,
            state["run_id"],
            registry,
            decision(),
            current_authorization={
                "scope_authorized": True,
                "target_verified": True,
                "human_intent_confirmed": True,
            },
            worker_id="worker:effect",
        )
        self.assertEqual(uncertain["status"], "recovery_required")

        lease = store.acquire_lease(
            state["run_id"], "worker:invalid-recovery", ttl_seconds=30
        )
        invalid = deepcopy(uncertain)
        invalid["status"] = "completed"
        invalid["pending_action"] = None
        invalid["recovery_reason"] = None
        invalid["revision"] = uncertain["revision"] + 2
        with self.assertRaises(RunStateError):
            store.resolve_operation_and_compare_and_swap(
                state["run_id"],
                uncertain["revision"],
                invalid,
                lease=lease,
                operation_id=request()["request_id"],
                request_fingerprint=tool_request_fingerprint(request()),
                operation_status="confirmed",
                operation_result={"status": "executed"},
            )
        store.release_lease(lease)

        operation = store.load_operation(request()["request_id"])
        persisted = store.load(state["run_id"])
        self.assertEqual(operation.status, "outcome_unknown")
        self.assertEqual(persisted["status"], "recovery_required")
        self.assertEqual(persisted["revision"], uncertain["revision"])

    def test_cancelled_recovery_preserves_unknown_operation_evidence(self) -> None:
        tool = EffectThenErrorTool()
        store, registry, state = self._checkpoint(tool)
        uncertain = resume_tool_approval(
            store,
            state["run_id"],
            registry,
            decision(),
            current_authorization={
                "scope_authorized": True,
                "target_verified": True,
                "human_intent_confirmed": True,
            },
            worker_id="worker:effect",
        )
        self.assertEqual(uncertain["status"], "recovery_required")

        cancelled = resolve_recovery_required(
            store,
            state["run_id"],
            registry,
            resolution("cancelled"),
            worker_id="worker:cancel",
        )
        operation = store.load_operation(request()["request_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(operation.status, "outcome_unknown")
        self.assertEqual(len(tool.calls), 1)


if __name__ == "__main__":
    unittest.main()
