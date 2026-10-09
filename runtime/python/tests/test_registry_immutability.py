from __future__ import annotations

import unittest
from copy import deepcopy

from manager_runtime.tools import ToolRegistry, execute_tool_request


SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


def definition() -> dict:
    return {
        "name": "lookup",
        "description": "Synthetic immutable-registry lookup.",
        "side_effect_class": "read",
        "input_schema": deepcopy(SCHEMA),
        "requires_verification": False,
        "sensitive_output": False,
    }


def task() -> dict:
    return {
        "task_id": "registry-immutability",
        "objective": "Exercise the trusted registry boundary.",
        "classification": {"materiality": "routine"},
    }


def request(value: object) -> dict:
    return {
        "request_id": "request:registry-immutability",
        "run_id": "run:registry-immutability",
        "tool_name": "lookup",
        "arguments": {"value": value},
        "target": None,
        "proposed_by": "model",
    }


class RecordingTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        return deepcopy(arguments)


class ToolRegistryImmutabilityTests(unittest.TestCase):
    def test_mutating_original_definition_after_registration_cannot_change_policy_schema(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        supplied = definition()
        registry.register(supplied, tool)

        supplied["input_schema"]["properties"]["value"]["type"] = "integer"
        supplied["input_schema"]["additionalProperties"] = True

        blocked = execute_tool_request(
            task(), request(123), registry, {"scope_authorized": True}
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["decision_reason"], "invalid_arguments")
        self.assertEqual(tool.calls, [])

    def test_mutating_get_result_cannot_change_subsequent_registry_behavior(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition(), tool)

        exposed = registry.get("lookup")
        self.assertIsNotNone(exposed)
        exposed.definition["input_schema"]["properties"]["value"]["type"] = "integer"
        exposed.definition["side_effect_class"] = "analysis"

        current = registry.get("lookup")
        self.assertIsNotNone(current)
        self.assertEqual(current.definition["side_effect_class"], "read")
        self.assertEqual(
            current.definition["input_schema"]["properties"]["value"]["type"],
            "string",
        )

    def test_mutating_model_facing_definition_cannot_mutate_execution_schema(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition(), tool)

        advertised = registry.model_definitions(["lookup"])
        advertised[0]["input_schema"]["properties"]["value"]["type"] = "integer"
        advertised[0]["input_schema"]["additionalProperties"] = True

        blocked = execute_tool_request(
            task(), request(123), registry, {"scope_authorized": True}
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(tool.calls, [])


if __name__ == "__main__":
    unittest.main()
