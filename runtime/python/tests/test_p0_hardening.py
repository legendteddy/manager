from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.orchestrator import run_with_model_and_tools
from manager_runtime.state import RunStateError, SQLiteRunStore, resolve_recovery_required
from manager_runtime.tools import ToolRegistry, execute_tool_request
from manager_runtime.tools.base import tool_definition_fingerprint, tool_request_fingerprint


class RecordingTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        return {"value": arguments.get("value"), "amount": arguments.get("amount")}

    def verify(self, arguments: dict, output) -> bool:
        return True


def definition(
    name: str,
    side_effect_class: str = "read",
    *,
    input_schema: dict | None = None,
    sensitive_output: bool = False,
) -> dict:
    result = {
        "name": name,
        "description": f"Synthetic {name} tool.",
        "side_effect_class": side_effect_class,
        "input_schema": input_schema
        or {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "requires_verification": side_effect_class
        in {"reversible_write", "external_commitment", "sensitive_destructive"},
        "sensitive_output": sensitive_output,
    }
    if result["requires_verification"]:
        result["version"] = "1"
    return result


def task() -> dict:
    return {
        "task_id": "p0-hardening",
        "objective": "Use only the supplied synthetic tool.",
        "classification": {
            "materiality": "routine",
            "consequence": "low",
            "uncertainty": "low",
            "reversibility": "reversible",
            "sensitivity": "public",
        },
    }


def request(name: str, arguments: dict, *, target: str | None = None) -> dict:
    return {
        "request_id": f"request:{name}",
        "run_id": "run:p0-hardening",
        "tool_name": name,
        "arguments": arguments,
        "target": target,
        "proposed_by": "model",
        "proposal_ref": f"proposal:{name}",
    }


class SequenceAdapter:
    provider = "p0-test"

    def __init__(self, proposals: list[dict]) -> None:
        self.proposals = proposals

    def generate(self, model_request: dict) -> dict:
        return {
            "response_id": "response:p0-test",
            "provider": self.provider,
            "model": model_request["model"],
            "status": "completed",
            "output_text": "",
            "tool_proposals": deepcopy(self.proposals),
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


def proposal(
    proposal_id: str,
    *,
    tool_name: str,
    value: str,
    target: str | None = None,
) -> dict:
    return {
        "proposal_id": proposal_id,
        "tool_name": tool_name,
        "arguments": {"value": value},
        "target": target,
        "source_ref": "response:p0-test",
    }


class P0HardeningTests(unittest.TestCase):
    def test_truthy_string_cannot_authorize_read(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition("lookup"), tool)

        result = execute_tool_request(
            task(),
            request("lookup", {"value": "x"}),
            registry,
            {"scope_authorized": "false"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["decision_reason"], "invalid_authorization_context")
        self.assertEqual(tool.calls, [])

    def test_unsupported_json_schema_keyword_fails_registration(self) -> None:
        registry = ToolRegistry()
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "integer"},
                    ]
                }
            },
            "required": ["value"],
            "additionalProperties": False,
        }

        with self.assertRaisesRegex(ValueError, "unsupported keywords"):
            registry.register(definition("unsupported", input_schema=schema), RecordingTool())

    def test_supported_numeric_constraints_are_enforced_before_execution(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        schema = {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1000,
                }
            },
            "required": ["amount"],
            "additionalProperties": False,
        }
        registry.register(definition("charge", input_schema=schema), tool)

        result = execute_tool_request(
            task(),
            request("charge", {"amount": -1}),
            registry,
            {"scope_authorized": True},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["decision_reason"], "invalid_arguments")
        self.assertEqual(tool.calls, [])

    def test_one_shot_duplicate_proposals_block_before_any_execution(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition("lookup"), tool)
        repeated = [
            proposal("call-1", tool_name="lookup", value="same"),
            proposal("call-2", tool_name="lookup", value="same"),
        ]
        output = run_with_model_and_tools(
            {
                "task": task(),
                "trusted_policy_context": [],
                "untrusted_content": [],
                "model_input": "Use lookup.",
            },
            SequenceAdapter(repeated),
            registry,
            model="synthetic-model",
            allowed_tools=["lookup"],
        )

        self.assertEqual(output["trace"]["status"], "blocked")
        self.assertEqual(output["tool_results"][-1]["decision_reason"], "repeated_tool_proposal")
        self.assertEqual(tool.calls, [])

    def test_one_shot_consequential_batch_blocks_before_any_execution(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition("send", "external_commitment"), tool)
        proposals = [
            proposal("call-1", tool_name="send", value="one", target="target-a"),
            proposal("call-2", tool_name="send", value="two", target="target-b"),
        ]
        output = run_with_model_and_tools(
            {
                "task": task(),
                "trusted_policy_context": [],
                "untrusted_content": [],
                "model_input": "Send synthetic messages.",
            },
            SequenceAdapter(proposals),
            registry,
            model="synthetic-model",
            allowed_tools=["send"],
            authorization_contexts={
                "send": {
                    "scope_authorized": True,
                    "target_verified": True,
                    "human_intent_confirmed": True,
                }
            },
        )

        self.assertEqual(output["trace"]["status"], "blocked")
        self.assertTrue(
            all(
                item["decision_reason"]
                == "consequential_multi_tool_batch_requires_serialization"
                for item in output["tool_results"]
            )
        )
        self.assertEqual(tool.calls, [])

    def test_one_shot_does_not_reuse_supplied_approval(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition("destroy", "sensitive_destructive"), tool)
        req = request("destroy", {"value": "x"}, target="synthetic-target")
        pending = execute_tool_request(
            task(),
            req,
            registry,
            {"scope_authorized": True, "target_verified": True},
        )
        approval = deepcopy(pending["approval"])
        approval["status"] = "approved"

        output = run_with_model_and_tools(
            {
                "task": task(),
                "trusted_policy_context": [],
                "untrusted_content": [],
                "model_input": "Propose destroy.",
            },
            SequenceAdapter(
                [
                    proposal(
                        "call-1",
                        tool_name="destroy",
                        value="x",
                        target="synthetic-target",
                    )
                ]
            ),
            registry,
            model="synthetic-model",
            allowed_tools=["destroy"],
            authorization_contexts={
                "destroy": {
                    "scope_authorized": True,
                    "target_verified": True,
                    "approval": approval,
                }
            },
        )

        self.assertEqual(output["tool_results"][0]["status"], "approval_required")
        self.assertEqual(tool.calls, [])

    def test_recovery_cannot_unredact_registry_sensitive_output(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        tool_definition = definition(
            "destroy", "sensitive_destructive", sensitive_output=True
        )
        registry.register(tool_definition, tool)
        req = request("destroy", {"value": "x"}, target="synthetic-target")
        approval = {
            "approval_id": "approval:recovery",
            "run_id": req["run_id"],
            "status": "approved",
            "action": "tool:destroy",
            "target": "synthetic-target",
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
            "task_id": "p0-hardening",
            "status": "recovery_required",
            "revision": 1,
            "created_at": "2026-10-08T00:00:00Z",
            "updated_at": "2026-10-08T00:02:00Z",
            "pending_action": {
                "tool_request": req,
                "approval": approval,
                "tool_definition_fingerprint": tool_definition_fingerprint(tool_definition),
            },
            "last_tool_result": None,
            "recovery_reason": "Synthetic uncertain outcome.",
            "extensions": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "state.sqlite3")
            store.create(state)
            recovered = resolve_recovery_required(
                store,
                req["run_id"],
                registry,
                {
                    "resolution_id": "resolution:sensitive",
                    "run_id": req["run_id"],
                    "decision": "confirmed_succeeded",
                    "decided_by": "synthetic-operator",
                    "decided_at": "2026-10-08T00:03:00Z",
                    "evidence": "Synthetic evidence.",
                    "redacted": False,
                    "output": "SECRET-MATERIAL",
                },
            )

        self.assertTrue(recovered["last_tool_result"]["redacted"])
        self.assertNotIn("output", recovered["last_tool_result"])

    def test_recovery_rejects_truthy_string_authorization(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        tool_definition = definition("destroy", "sensitive_destructive")
        registry.register(tool_definition, tool)
        req = request("destroy", {"value": "x"}, target="synthetic-target")
        state = {
            "run_id": req["run_id"],
            "task_id": "p0-hardening",
            "status": "recovery_required",
            "revision": 1,
            "created_at": "2026-10-08T00:00:00Z",
            "updated_at": "2026-10-08T00:02:00Z",
            "task": task(),
            "pending_action": {
                "tool_request": req,
                "approval": {
                    "approval_id": "approval:recovery",
                    "status": "approved",
                },
                "tool_definition_fingerprint": tool_definition_fingerprint(tool_definition),
            },
            "last_tool_result": None,
            "recovery_reason": "Synthetic uncertain outcome.",
            "extensions": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "state.sqlite3")
            store.create(state)
            with self.assertRaisesRegex(RunStateError, "must be boolean"):
                resolve_recovery_required(
                    store,
                    req["run_id"],
                    registry,
                    {
                        "resolution_id": "resolution:no-effect",
                        "run_id": req["run_id"],
                        "decision": "confirmed_not_executed",
                        "decided_by": "synthetic-operator",
                        "decided_at": "2026-10-08T00:03:00Z",
                        "evidence": "Synthetic evidence.",
                    },
                    current_authorization={
                        "scope_authorized": "false",
                        "target_verified": True,
                    },
                )


if __name__ == "__main__":
    unittest.main()
