from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.state.approvals import resume_tool_approval
from manager_runtime.state.sqlite_store import SQLiteRunStore
from manager_runtime.tools import ToolRegistry
from manager_runtime.tools.base import tool_definition_fingerprint, tool_request_fingerprint


class RecordingTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        return {"value": arguments["value"]}

    def verify(self, arguments: dict, output) -> bool:
        return True


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


def request() -> dict:
    return {
        "request_id": "request:post-effect",
        "run_id": "run:post-effect",
        "tool_name": "destroy",
        "arguments": {"value": "x"},
        "target": "synthetic-target",
        "proposed_by": "model",
        "proposal_ref": "proposal:post-effect",
    }


class DurablePostEffectCheckpointTests(unittest.TestCase):
    def test_recorded_verified_effect_recovers_without_reexecution(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        tool_definition = definition()
        registry.register(tool_definition, tool)
        req = request()
        approval = {
            "approval_id": "approval:post-effect",
            "run_id": req["run_id"],
            "status": "approved",
            "action": "tool:destroy",
            "target": req["target"],
            "material_parameters": req["arguments"],
            "materiality": "material",
            "risk_class": "critical",
            "reason": "Synthetic approval.",
            "recommendation": "Synthetic only.",
            "recovery": "Reconcile before retry.",
            "issued_at": "2026-10-08T00:00:00Z",
            "resolved_at": "2026-10-08T00:01:00Z",
            "resolved_by": "synthetic-human",
            "approved_by": "synthetic-human",
            "action_fingerprint": tool_request_fingerprint(req),
        }
        state = {
            "run_id": req["run_id"],
            "task_id": "post-effect",
            "status": "executing",
            "revision": 1,
            "created_at": "2026-10-08T00:00:00Z",
            "updated_at": "2026-10-08T00:02:00Z",
            "task": {
                "task_id": "post-effect",
                "objective": "Synthetic durable action.",
                "classification": {
                    "materiality": "routine",
                    "consequence": "low",
                    "uncertainty": "low",
                    "reversibility": "reversible",
                    "sensitivity": "public",
                },
            },
            "pending_action": {
                "tool_request": req,
                "approval": approval,
                "tool_definition_fingerprint": tool_definition_fingerprint(tool_definition),
                "authorization_context": {
                    "scope_authorized": True,
                    "target_verified": True,
                },
            },
            "last_tool_result": {
                "request_id": req["request_id"],
                "tool_name": req["tool_name"],
                "status": "executed",
                "side_effect_class": "sensitive_destructive",
                "decision_reason": "policy_allowed_execution",
                "verification": {
                    "status": "pass",
                    "details": "Synthetic verification passed.",
                },
                "approval_ref": approval["approval_id"],
                "error": None,
                "redacted": False,
                "output": {"value": "x"},
            },
            "trace_snapshot": {
                "run_id": req["run_id"],
                "status": "blocked",
                "classification": {
                    "materiality": "routine",
                    "consequence": "low",
                    "uncertainty": "low",
                },
                "workflow": "direct",
                "capabilities": [],
                "events": [],
                "residual_uncertainty": [],
            },
            "result_snapshot": {
                "result_id": "result:post-effect",
                "status": "blocked",
                "finding": "Waiting for durable continuation.",
                "assumptions": [],
                "risks": [],
                "uncertainties": [],
                "confidence": "high",
                "owner_decision_required": True,
                "decision_request": "Approve.",
                "next_handoff": None,
            },
            "recovery_reason": None,
            "extensions": {
                "agent_loop": {
                    "version": 1,
                    "phase": "waiting_approval",
                    "provider": "synthetic-provider",
                    "model": "synthetic-model",
                    "allowed_tools": ["destroy"],
                    "tool_definition_fingerprints": {
                        "destroy": tool_definition_fingerprint(tool_definition)
                    },
                    "max_model_steps": 4,
                    "max_tool_calls": 8,
                    "max_tool_result_chars": 1000,
                    "max_output_tokens": None,
                    "model_steps": 1,
                    "tool_calls": 1,
                    "seen_proposal_fingerprints": [tool_request_fingerprint(req)],
                    "prior_response_ref": "response:1",
                    "pending_proposal_id": req["proposal_ref"],
                    "pending_request_fingerprint": tool_request_fingerprint(req),
                    "current_response": None,
                    "continuation_tool_results": [],
                }
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "state.sqlite3")
            store.create(state)
            recovered = resume_tool_approval(
                store,
                req["run_id"],
                registry,
                {},
                current_authorization={},
                success_status="running",
            )

        self.assertEqual(recovered["status"], "running")
        checkpoint = recovered["extensions"]["agent_loop"]
        self.assertEqual(checkpoint["phase"], "continuation_ready")
        self.assertEqual(
            checkpoint["continuation_tool_results"][0]["proposal_id"],
            req["proposal_ref"],
        )
        self.assertEqual(recovered["pending_action"], None)
        self.assertEqual(tool.calls, [])


if __name__ == "__main__":
    unittest.main()
