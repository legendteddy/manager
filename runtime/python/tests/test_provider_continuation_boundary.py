from __future__ import annotations

import unittest
from copy import deepcopy

from manager_runtime.agent_loop import run_bounded_agent_loop
from manager_runtime.providers.base import ProviderTimeoutError
from manager_runtime.tools import ToolRegistry


def task_input() -> dict:
    return {
        "task": {
            "task_id": "provider-continuation-boundary",
            "objective": "Use the synthetic lookup once.",
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
        "model_input": "Use lookup once.",
    }


def definition() -> dict:
    return {
        "name": "lookup",
        "description": "Synthetic lookup.",
        "side_effect_class": "read",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "requires_verification": False,
        "sensitive_output": False,
    }


def first_response() -> dict:
    return {
        "response_id": "response:first",
        "provider": "continuation-test",
        "model": "synthetic-model",
        "status": "completed",
        "output_text": "",
        "tool_proposals": [
            {
                "proposal_id": "proposal:1",
                "tool_name": "lookup",
                "arguments": {"value": "one"},
                "source_ref": "response:first",
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


class Lookup:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        return {"value": arguments["value"]}


class TimeoutOnContinuationAdapter:
    provider = "continuation-test"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: dict) -> dict:
        self.calls += 1
        if self.calls == 1:
            return first_response()
        raise ProviderTimeoutError(self.provider, detail="must-not-leak-secret")


class WrongProviderOnContinuationAdapter:
    provider = "continuation-test"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: dict) -> dict:
        self.calls += 1
        if self.calls == 1:
            return first_response()
        return {
            "response_id": "response:wrong",
            "provider": "other-provider",
            "model": "synthetic-model",
            "status": "completed",
            "output_text": "should not be accepted",
            "tool_proposals": [],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


class ProviderContinuationBoundaryTests(unittest.TestCase):
    def run_loop(self, adapter):
        registry = ToolRegistry()
        lookup = Lookup()
        registry.register(definition(), lookup)
        output = run_bounded_agent_loop(
            task_input(),
            adapter,
            registry,
            model="synthetic-model",
            allowed_tools=["lookup"],
        )
        return output, lookup

    def test_direct_adapter_timeout_during_continuation_is_bounded(self):
        output, lookup = self.run_loop(TimeoutOnContinuationAdapter())
        self.assertEqual(output["agent_loop"]["status"], "failed")
        self.assertEqual(
            output["agent_loop"]["stop_reason"], "model_provider_boundary_failed"
        )
        self.assertEqual(output["agent_loop"]["model_steps"], 2)
        self.assertEqual(len(lookup.calls), 1)
        self.assertNotIn("must-not-leak-secret", repr(output))

    def test_continuation_provider_identity_mismatch_fails_closed(self):
        output, lookup = self.run_loop(WrongProviderOnContinuationAdapter())
        self.assertEqual(output["agent_loop"]["status"], "failed")
        self.assertEqual(
            output["agent_loop"]["stop_reason"], "model_provider_boundary_failed"
        )
        self.assertEqual(len(lookup.calls), 1)
        self.assertNotEqual(output.get("model_response", {}).get("provider"), "other-provider")


if __name__ == "__main__":
    unittest.main()
