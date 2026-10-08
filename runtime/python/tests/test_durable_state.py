from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from manager_runtime.state import (
    RunStateConflict,
    RunStateError,
    SQLiteRunStore,
    checkpoint_pending_tool_approval,
    resume_tool_approval,
)
from manager_runtime.tools import ToolRegistry, execute_tool_request


def task() -> dict:
    return {
        "task_id": "durable-test",
        "objective": "Perform one synthetic consequential action.",
        "classification": {
            "materiality": "routine",
            "consequence": "high",
            "uncertainty": "low",
            "reversibility": "irreversible",
            "sensitivity": "public",
        },
    }


def request(value: str = "x") -> dict:
    return {
        "request_id": "request:destroy",
        "run_id": "run:durable-test",
        "tool_name": "destroy",
        "arguments": {"value": value},
        "target": "synthetic-target",
        "proposed_by": "model",
        "proposal_ref": "proposal:destroy",
    }


def definition(version: str = "1") -> dict:
    return {
        "name": "destroy",
        "version": version,
        "description": "Synthetic destructive tool.",
        "side_effect_class": "sensitive_destructive",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "requires_verification": True,
        "sensitive_output": False,
    }


class FakeTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(arguments)
        return {"accepted": arguments["value"]}

    def verify(self, arguments: dict, output) -> bool:
        return output.get("accepted") == arguments["value"]


class CrashAfterEffectTool(FakeTool):
    def execute(self, arguments: dict):
        self.calls.append(arguments)
        raise SystemExit("synthetic interruption after side effect")


def decision(value: str = "approved") -> dict:
    return {
        "approval_id": "approval:request:destroy",
        "decision": value,
        "decided_by": "synthetic-human",
        "decided_at": "2026-10-08T01:00:00Z",
    }


class DurableApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "manager-state.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _checkpoint(self, tool: FakeTool | None = None, *, version: str = "1"):
        tool = tool or FakeTool()
        registry = ToolRegistry()
        registry.register(definition(version), tool)
        req = request()
        pending = execute_tool_request(
            task(),
            req,
            registry,
            {"scope_authorized": True, "target_verified": True},
        )
        store = SQLiteRunStore(self.path)
        state = checkpoint_pending_tool_approval(
            task(),
            req,
            pending,
            registry,
            store,
            authorization_context={"scope_authorized": True, "target_verified": True},
        )
        return store, registry, tool, state

    def test_sqlite_state_survives_new_store_instance(self) -> None:
        store, _registry, _tool, state = self._checkpoint()
        self.assertEqual(state["status"], "waiting_approval")
        reloaded = SQLiteRunStore(self.path).load(state["run_id"])
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["revision"], 1)
        self.assertEqual(reloaded["pending_action"]["approval"]["status"], "pending")

    def test_compare_and_swap_rejects_stale_revision(self) -> None:
        store, _registry, _tool, state = self._checkpoint()
        candidate = dict(state)
        candidate["revision"] = 2
        candidate["status"] = "cancelled"
        store.compare_and_swap(state["run_id"], 1, candidate)
        second = dict(candidate)
        second["revision"] = 2
        with self.assertRaises(RunStateConflict):
            store.compare_and_swap(state["run_id"], 1, second)

    def test_approved_resume_executes_once_and_completes(self) -> None:
        store, registry, tool, state = self._checkpoint()
        completed = resume_tool_approval(
            SQLiteRunStore(self.path),
            state["run_id"],
            registry,
            decision(),
            current_authorization={"scope_authorized": True, "target_verified": True},
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["last_tool_result"]["verification"]["status"], "pass")
        self.assertEqual(len(tool.calls), 1)
        self.assertIsNone(completed["pending_action"])

    def test_rejected_approval_cancels_without_execution(self) -> None:
        store, registry, tool, state = self._checkpoint()
        cancelled = resume_tool_approval(
            store,
            state["run_id"],
            registry,
            decision("rejected"),
            current_authorization={"scope_authorized": True, "target_verified": True},
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(tool.calls, [])
        self.assertEqual(cancelled["pending_action"]["approval"]["status"], "rejected")

    def test_changed_request_marks_approval_stale(self) -> None:
        store, registry, tool, state = self._checkpoint()
        stale = resume_tool_approval(
            store,
            state["run_id"],
            registry,
            decision(),
            current_authorization={"scope_authorized": True, "target_verified": True},
            current_request=request("changed"),
        )
        self.assertEqual(stale["status"], "waiting_approval")
        self.assertEqual(stale["pending_action"]["approval"]["status"], "stale")
        self.assertEqual(tool.calls, [])
        with self.assertRaises(RunStateError):
            resume_tool_approval(
                store,
                state["run_id"],
                registry,
                decision(),
                current_authorization={"scope_authorized": True, "target_verified": True},
            )

    def test_changed_tool_version_marks_approval_stale(self) -> None:
        store, _registry, tool, state = self._checkpoint()
        changed_registry = ToolRegistry()
        changed_registry.register(definition("2"), tool)
        stale = resume_tool_approval(
            store,
            state["run_id"],
            changed_registry,
            decision(),
            current_authorization={"scope_authorized": True, "target_verified": True},
        )
        self.assertEqual(stale["pending_action"]["approval"]["status"], "stale")
        self.assertEqual(tool.calls, [])

    def test_current_authorization_is_revalidated(self) -> None:
        store, registry, tool, state = self._checkpoint()
        with self.assertRaises(RunStateError):
            resume_tool_approval(
                store,
                state["run_id"],
                registry,
                decision(),
                current_authorization={"scope_authorized": False, "target_verified": True},
            )
        reloaded = store.load(state["run_id"])
        self.assertEqual(reloaded["status"], "waiting_approval")
        self.assertEqual(tool.calls, [])

    def test_interrupted_execution_requires_recovery_instead_of_retry(self) -> None:
        crash_tool = CrashAfterEffectTool()
        store, registry, _tool, state = self._checkpoint(crash_tool)
        with self.assertRaises(SystemExit):
            resume_tool_approval(
                store,
                state["run_id"],
                registry,
                decision(),
                current_authorization={"scope_authorized": True, "target_verified": True},
            )
        executing = store.load(state["run_id"])
        self.assertEqual(executing["status"], "executing")
        self.assertEqual(len(crash_tool.calls), 1)

        recovered = resume_tool_approval(
            store,
            state["run_id"],
            registry,
            decision(),
            current_authorization={"scope_authorized": True, "target_verified": True},
        )
        self.assertEqual(recovered["status"], "recovery_required")
        self.assertEqual(len(crash_tool.calls), 1)


if __name__ == "__main__":
    unittest.main()
