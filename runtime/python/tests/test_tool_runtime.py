from __future__ import annotations

import unittest

from manager_runtime.orchestrator import run_with_model_and_tools
from manager_runtime.tools import ToolRegistry, execute_tool_request


def task(*, materiality: str = "routine", consequence: str = "low") -> dict:
    return {
        "task_id": "tool-test",
        "objective": "Use only explicitly allowed tools.",
        "classification": {
            "materiality": materiality,
            "consequence": consequence,
            "uncertainty": "low",
            "reversibility": "reversible",
            "sensitivity": "public",
        },
    }


def request(name: str, arguments: dict, *, target: str | None = None) -> dict:
    return {
        "request_id": f"request:{name}",
        "run_id": "run:tool-test",
        "tool_name": name,
        "arguments": arguments,
        "target": target,
        "proposed_by": "model",
        "proposal_ref": f"proposal:{name}",
    }


def definition(name: str, side_effect_class: str, *, verify: bool) -> dict:
    return {
        "name": name,
        "version": "1",
        "description": f"Synthetic {name} tool.",
        "side_effect_class": side_effect_class,
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}, "target": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "requires_verification": verify,
        "sensitive_output": False,
    }


class FakeTool:
    def __init__(self, *, verified: bool = True) -> None:
        self.calls: list[dict] = []
        self.verified = verified

    def execute(self, arguments: dict):
        self.calls.append(arguments)
        return {"accepted": arguments["value"]}

    def verify(self, arguments: dict, output) -> bool:
        return self.verified and output.get("accepted") == arguments["value"]


class ProposalAdapter:
    provider = "proposal-test"

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.calls: list[dict] = []

    def generate(self, model_request: dict) -> dict:
        self.calls.append(model_request)
        return {
            "response_id": "response:proposal-test",
            "provider": self.provider,
            "model": model_request["model"],
            "status": "completed",
            "output_text": "",
            "tool_proposals": [
                {
                    "proposal_id": "call:1",
                    "tool_name": self.tool_name,
                    "arguments": {"value": "synthetic"},
                    "target": None,
                    "source_ref": "response:proposal-test",
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


class ToolRuntimeTests(unittest.TestCase):
    def test_read_tool_executes_when_exposed_and_authorized(self) -> None:
        registry = ToolRegistry()
        tool = FakeTool()
        registry.register(definition("lookup", "read", verify=False), tool)
        result = execute_tool_request(
            task(), request("lookup", {"value": "x"}), registry, {"scope_authorized": True}
        )
        self.assertEqual(result["status"], "executed")
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(result["verification"]["status"], "not_required")

    def test_reversible_write_requires_scope_and_verification(self) -> None:
        registry = ToolRegistry()
        tool = FakeTool()
        registry.register(definition("update", "reversible_write", verify=True), tool)
        blocked = execute_tool_request(task(), request("update", {"value": "x"}), registry)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["decision_reason"], "scope_not_authorized")
        allowed = execute_tool_request(
            task(), request("update", {"value": "x"}), registry, {"scope_authorized": True}
        )
        self.assertEqual(allowed["status"], "executed")
        self.assertEqual(allowed["verification"]["status"], "pass")

    def test_destructive_tool_requires_exact_approval(self) -> None:
        registry = ToolRegistry()
        tool = FakeTool()
        registry.register(definition("destroy", "sensitive_destructive", verify=True), tool)
        req = request("destroy", {"value": "x"}, target="synthetic-target")
        pending = execute_tool_request(
            task(), req, registry, {"scope_authorized": True, "target_verified": True}
        )
        self.assertEqual(pending["status"], "approval_required")
        self.assertEqual(pending["approval"]["materiality"], "material")
        self.assertEqual(pending["approval"]["risk_class"], "critical")
        self.assertEqual(tool.calls, [])

        approval = dict(pending["approval"])
        approval["status"] = "approved"
        approval["approved_by"] = "synthetic-human"
        executed = execute_tool_request(
            task(),
            req,
            registry,
            {"scope_authorized": True, "target_verified": True, "approval": approval},
        )
        self.assertEqual(executed["status"], "executed")
        self.assertEqual(len(tool.calls), 1)

    def test_changed_arguments_make_approval_stale(self) -> None:
        registry = ToolRegistry()
        tool = FakeTool()
        registry.register(definition("destroy", "sensitive_destructive", verify=True), tool)
        original = request("destroy", {"value": "old"}, target="synthetic-target")
        pending = execute_tool_request(
            task(), original, registry, {"scope_authorized": True, "target_verified": True}
        )
        approval = dict(pending["approval"])
        approval["status"] = "approved"
        changed = request("destroy", {"value": "new"}, target="synthetic-target")
        stale = execute_tool_request(
            task(),
            changed,
            registry,
            {"scope_authorized": True, "target_verified": True, "approval": approval},
        )
        self.assertEqual(stale["status"], "approval_required")
        self.assertEqual(stale["approval"]["status"], "stale")
        self.assertEqual(tool.calls, [])

    def test_external_commitment_requires_intent_and_verified_target(self) -> None:
        registry = ToolRegistry()
        tool = FakeTool()
        registry.register(definition("send", "external_commitment", verify=True), tool)
        req = request("send", {"value": "hello", "target": "synthetic-destination"}, target="synthetic-destination")
        blocked = execute_tool_request(
            task(), req, registry, {"scope_authorized": True, "target_verified": False}
        )
        self.assertEqual(blocked["decision_reason"], "external_target_not_verified")
        pending = execute_tool_request(
            task(), req, registry, {"scope_authorized": True, "target_verified": True}
        )
        self.assertEqual(pending["status"], "approval_required")
        allowed = execute_tool_request(
            task(),
            req,
            registry,
            {
                "scope_authorized": True,
                "target_verified": True,
                "human_intent_confirmed": True,
            },
        )
        self.assertEqual(allowed["status"], "executed")

    def test_unknown_tool_is_blocked(self) -> None:
        result = execute_tool_request(task(), request("missing", {"value": "x"}), ToolRegistry())
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["decision_reason"], "unknown_tool")

    def test_model_proposal_for_read_tool_executes_through_policy(self) -> None:
        registry = ToolRegistry()
        tool = FakeTool()
        registry.register(definition("lookup", "read", verify=False), tool)
        adapter = ProposalAdapter("lookup")
        task_input = {
            "task": task(),
            "trusted_policy_context": [],
            "untrusted_content": [],
            "model_input": "Look up synthetic data if needed.",
        }
        output = run_with_model_and_tools(
            task_input,
            adapter,
            registry,
            model="synthetic-model",
            allowed_tools=["lookup"],
        )
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0]["tools"][0]["name"], "lookup")
        self.assertEqual(output["tool_results"][0]["status"], "executed")
        self.assertEqual(len(tool.calls), 1)

    def test_model_proposal_cannot_auto_execute_destructive_tool(self) -> None:
        registry = ToolRegistry()
        tool = FakeTool()
        registry.register(definition("destroy", "sensitive_destructive", verify=True), tool)
        adapter = ProposalAdapter("destroy")
        task_input = {
            "task": task(),
            "trusted_policy_context": [],
            "untrusted_content": [],
            "model_input": "Propose the appropriate tool if needed.",
        }
        output = run_with_model_and_tools(
            task_input,
            adapter,
            registry,
            model="synthetic-model",
            allowed_tools=["destroy"],
            authorization_contexts={"destroy": {"scope_authorized": True}},
        )
        self.assertEqual(output["tool_results"][0]["status"], "approval_required")
        self.assertEqual(output["trace"]["status"], "blocked")
        self.assertEqual(tool.calls, [])


if __name__ == "__main__":
    unittest.main()
