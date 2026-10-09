from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.state import (
    SQLITE_STATE_SCHEMA_VERSION,
    RunStateError,
    SQLiteRunStore,
    migrate_agent_loop_checkpoint,
    resolve_recovery_required,
)
from manager_runtime.tools import ToolRegistry
from manager_runtime.tools.base import tool_definition_fingerprint, tool_request_fingerprint


class SyntheticTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        return {"value": arguments["value"]}

    def verify(self, arguments: dict, output) -> bool:
        return output == {"value": arguments["value"]}


def definition() -> dict:
    return {
        "name": "destroy",
        "version": "1",
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


def request(request_id: str = "request:recovery") -> dict:
    return {
        "request_id": request_id,
        "run_id": "run:recovery",
        "tool_name": "destroy",
        "arguments": {"value": "x"},
        "target": "synthetic-target",
        "proposed_by": "model",
        "proposal_ref": "proposal:recovery",
    }


def approval(req: dict) -> dict:
    return {
        "approval_id": f"approval:{req['request_id']}",
        "run_id": req["run_id"],
        "status": "approved",
        "action": "tool:destroy",
        "target": "synthetic-target",
        "material_parameters": req["arguments"],
        "materiality": "material",
        "risk_class": "critical",
        "reason": "Synthetic approved action.",
        "recommendation": "Synthetic only.",
        "recovery": "Reconcile external outcome before retry.",
        "issued_at": "2026-10-08T00:00:00Z",
        "resolved_at": "2026-10-08T00:01:00Z",
        "resolved_by": "synthetic-human",
        "approved_by": "synthetic-human",
        "action_fingerprint": tool_request_fingerprint(req),
    }


def recovery_state(registry: ToolRegistry, *, loop: bool = False) -> dict:
    req = request()
    registered = registry.get("destroy")
    assert registered is not None
    extensions = {}
    if loop:
        extensions["agent_loop"] = {
            "version": 1,
            "phase": "waiting_approval",
            "provider": "synthetic-provider",
            "model": "synthetic-model",
            "allowed_tools": ["destroy"],
            "tool_definition_fingerprints": {
                "destroy": tool_definition_fingerprint(registered.definition)
            },
            "max_model_steps": 4,
            "max_tool_calls": 8,
            "max_tool_result_chars": 1000,
            "max_output_tokens": None,
            "model_steps": 1,
            "tool_calls": 1,
            "seen_proposal_fingerprints": [tool_request_fingerprint(req)],
            "prior_response_ref": "response:1",
            "pending_proposal_id": "proposal:recovery",
            "pending_request_fingerprint": tool_request_fingerprint(req),
            "current_response": None,
            "continuation_tool_results": [],
        }
    return {
        "run_id": "run:recovery",
        "task_id": "recovery-task",
        "status": "recovery_required",
        "revision": 1,
        "created_at": "2026-10-08T00:00:00Z",
        "updated_at": "2026-10-08T00:02:00Z",
        "task": {
            "task_id": "recovery-task",
            "objective": "Recover one synthetic action.",
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
            "approval": approval(req),
            "tool_definition_fingerprint": tool_definition_fingerprint(registered.definition),
            "authorization_context": {"scope_authorized": True, "target_verified": True},
        },
        "last_tool_result": None,
        "trace_snapshot": {
            "run_id": "run:recovery",
            "status": "failed",
            "classification": {
                "materiality": "material",
                "consequence": "high",
                "uncertainty": "low",
            },
            "workflow": "direct",
            "capabilities": [],
            "events": [],
            "residual_uncertainty": [],
        },
        "result_snapshot": {
            "result_id": "result:recovery",
            "status": "partial",
            "finding": "Outcome uncertain.",
            "assumptions": [],
            "risks": [],
            "uncertainties": ["External outcome uncertain."],
            "confidence": "low",
            "owner_decision_required": True,
            "decision_request": "Reconcile the external outcome.",
            "next_handoff": None,
        },
        "recovery_reason": "Synthetic uncertain external outcome.",
        "recovery_resolution": None,
        "extensions": extensions,
    }


def resolution(decision: str) -> dict:
    return {
        "resolution_id": f"resolution:{decision}",
        "run_id": "run:recovery",
        "decision": decision,
        "decided_by": "synthetic-operator",
        "decided_at": "2026-10-08T00:03:00Z",
        "evidence": "Synthetic external reconciliation evidence.",
    }


def current_runtime_connection(path: Path) -> sqlite3.Connection:
    """Open raw SQLite for corruption tests while identifying as current code.

    Production callers should use SQLiteRunStore. These tests intentionally
    bypass state validation to inject malformed rows, so they must explicitly
    satisfy the schema-v3 mixed-runtime write fence instead of accidentally
    behaving like a pre-coordination runtime.
    """
    connection = sqlite3.connect(path)
    connection.create_function(
        "manager_runtime_schema_version",
        0,
        lambda: SQLITE_STATE_SCHEMA_VERSION,
        deterministic=True,
    )
    return connection


class ConformanceRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.registry = ToolRegistry()
        self.tool = SyntheticTool()
        self.registry.register(definition(), self.tool)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_corrupted_json_fails_closed_on_load(self) -> None:
        store = SQLiteRunStore(self.path)
        state = recovery_state(self.registry)
        store.create(state)
        with current_runtime_connection(self.path) as connection:
            connection.execute(
                "UPDATE manager_runs SET state_json = ? WHERE run_id = ?",
                ("{not-json", state["run_id"]),
            )
        with self.assertRaises(RunStateError):
            store.load(state["run_id"])

    def test_corrupted_waiting_state_without_pending_action_fails_closed(self) -> None:
        store = SQLiteRunStore(self.path)
        state = recovery_state(self.registry)
        store.create(state)
        corrupt = deepcopy(state)
        corrupt["status"] = "waiting_approval"
        corrupt["pending_action"] = None
        payload = json.dumps(corrupt, sort_keys=True, separators=(",", ":"))
        with current_runtime_connection(self.path) as connection:
            connection.execute(
                "UPDATE manager_runs SET state_json = ? WHERE run_id = ?",
                (payload, state["run_id"]),
            )
        with self.assertRaises(RunStateError):
            store.load(state["run_id"])

    def test_terminal_state_cannot_be_resurrected(self) -> None:
        store = SQLiteRunStore(self.path)
        state = recovery_state(self.registry)
        state["status"] = "cancelled"
        state["recovery_reason"] = None
        store.create(state)
        candidate = deepcopy(state)
        candidate["revision"] = 2
        candidate["status"] = "running"
        with self.assertRaises(RunStateError):
            store.compare_and_swap(state["run_id"], 1, candidate)

    def test_future_checkpoint_version_fails_closed(self) -> None:
        with self.assertRaises(RunStateError):
            migrate_agent_loop_checkpoint({"version": 2})
        self.assertEqual(migrate_agent_loop_checkpoint({"version": 1})["version"], 1)

    def test_confirmed_not_executed_creates_fresh_approval_without_execution(self) -> None:
        store = SQLiteRunStore(self.path)
        state = store.create(recovery_state(self.registry))
        recovered = resolve_recovery_required(
            store,
            state["run_id"],
            self.registry,
            resolution("confirmed_not_executed"),
            current_authorization={"scope_authorized": True, "target_verified": True},
        )
        self.assertEqual(recovered["status"], "waiting_approval")
        self.assertEqual(self.tool.calls, [])
        self.assertEqual(recovered["pending_action"]["approval"]["status"], "pending")
        self.assertNotEqual(
            recovered["pending_action"]["tool_request"]["request_id"],
            state["pending_action"]["tool_request"]["request_id"],
        )
        self.assertNotEqual(
            recovered["pending_action"]["approval"]["approval_id"],
            state["pending_action"]["approval"]["approval_id"],
        )

    def test_confirmed_success_never_reexecutes_and_terminalizes_single_action(self) -> None:
        store = SQLiteRunStore(self.path)
        state = store.create(recovery_state(self.registry))
        recovered = resolve_recovery_required(
            store,
            state["run_id"],
            self.registry,
            resolution("confirmed_succeeded"),
        )
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["last_tool_result"]["status"], "executed")
        self.assertEqual(recovered["last_tool_result"]["verification"]["status"], "pass")
        self.assertEqual(self.tool.calls, [])

    def test_confirmed_loop_success_returns_to_continuation_without_reexecution(self) -> None:
        store = SQLiteRunStore(self.path)
        state = store.create(recovery_state(self.registry, loop=True))
        recovered = resolve_recovery_required(
            store,
            state["run_id"],
            self.registry,
            resolution("confirmed_succeeded"),
        )
        checkpoint = recovered["extensions"]["agent_loop"]
        self.assertEqual(recovered["status"], "running")
        self.assertEqual(checkpoint["phase"], "continuation_ready")
        self.assertEqual(checkpoint["continuation_tool_results"][0]["proposal_id"], "proposal:recovery")
        self.assertEqual(self.tool.calls, [])

    def test_cancelled_recovery_never_executes(self) -> None:
        store = SQLiteRunStore(self.path)
        state = store.create(recovery_state(self.registry))
        recovered = resolve_recovery_required(
            store,
            state["run_id"],
            self.registry,
            resolution("cancelled"),
        )
        self.assertEqual(recovered["status"], "cancelled")
        self.assertEqual(self.tool.calls, [])


if __name__ == "__main__":
    unittest.main()
