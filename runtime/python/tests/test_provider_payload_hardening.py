from __future__ import annotations

import math
import unittest

from manager_runtime.orchestrator import run_with_model, run_with_model_and_tools
from manager_runtime.tools import ToolRegistry


def task_input() -> dict:
    return {
        "task": {
            "task_id": "provider-payload-hardening",
            "objective": "Exercise synthetic provider payload validation.",
            "classification": {
                "materiality": "routine",
                "consequence": "low",
                "uncertainty": "low",
                "reversibility": "reversible",
                "sensitivity": "public",
            },
        },
        "trusted_policy_context": [],
        "untrusted_content": [],
        "model_input": "Use the synthetic lookup if needed.",
    }


def definition() -> dict:
    return {
        "name": "lookup",
        "description": "Synthetic read-only lookup.",
        "side_effect_class": "read",
        "input_schema": {
            "type": "object",
            "properties": {"value": {}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "requires_verification": False,
        "sensitive_output": False,
    }


class RecordingTool:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, arguments: dict):
        self.calls += 1
        return arguments


class ProposalAdapter:
    provider = "synthetic"

    def __init__(self, argument_value: object) -> None:
        self.argument_value = argument_value

    def generate(self, request: dict) -> dict:
        return {
            "response_id": "response:provider-payload-hardening",
            "provider": self.provider,
            "model": request["model"],
            "status": "completed",
            "output_text": "",
            "tool_proposals": [
                {
                    "proposal_id": "proposal:1",
                    "tool_name": "lookup",
                    "arguments": {"value": self.argument_value},
                    "source_ref": "response:provider-payload-hardening",
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


class RecordingAdapter:
    provider = "synthetic"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: dict) -> dict:
        self.calls += 1
        return {
            "response_id": "response:recording",
            "provider": self.provider,
            "model": request["model"],
            "status": "completed",
            "output_text": "ok",
            "tool_proposals": [],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


class ProviderPayloadHardeningTests(unittest.TestCase):
    def _run_hostile_proposal(self, value: object) -> tuple[dict, RecordingTool]:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition(), tool)
        output = run_with_model_and_tools(
            task_input(),
            ProposalAdapter(value),
            registry,
            model="synthetic-model",
            allowed_tools=["lookup"],
            authorization_contexts={"lookup": {"scope_authorized": True}},
        )
        return output, tool

    def test_nonfinite_proposal_argument_fails_at_provider_boundary(self) -> None:
        output, tool = self._run_hostile_proposal(math.nan)
        self.assertEqual(output["trace"]["status"], "failed")
        self.assertEqual(output["result"]["status"], "failed")
        self.assertEqual(output["model"]["reason"], "provider_boundary_failure")
        self.assertNotIn("model_response", output)
        self.assertEqual(tool.calls, 0)

    def test_non_json_proposal_argument_fails_at_provider_boundary(self) -> None:
        output, tool = self._run_hostile_proposal(b"not-json")
        self.assertEqual(output["trace"]["status"], "failed")
        self.assertEqual(output["result"]["status"], "failed")
        self.assertEqual(tool.calls, 0)

    def test_deep_proposal_argument_is_bounded_before_fingerprinting(self) -> None:
        value: object = "leaf"
        for _ in range(80):
            value = [value]
        output, tool = self._run_hostile_proposal(value)
        self.assertEqual(output["trace"]["status"], "failed")
        self.assertEqual(tool.calls, 0)

    def test_invalid_outbound_model_request_never_reaches_adapter(self) -> None:
        adapter = RecordingAdapter()
        output = run_with_model(
            task_input(),
            adapter,
            model="synthetic-model",
            tool_definitions=[
                {
                    "name": "lookup",
                    "description": "Synthetic lookup.",
                    "input_schema": {"type": "object"},
                    "side_effect_class": "sensitive_destructive",
                }
            ],
        )
        self.assertEqual(output["trace"]["status"], "failed")
        self.assertEqual(output["model"]["reason"], "provider_boundary_failure")
        self.assertEqual(adapter.calls, 0)


if __name__ == "__main__":
    unittest.main()
