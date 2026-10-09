from __future__ import annotations

import math
import unittest

from manager_runtime.tools import ToolRegistry


class NoopTool:
    def execute(self, arguments: dict):
        return arguments


def definition() -> dict:
    return {
        "name": "lookup",
        "description": "Synthetic trusted tool definition.",
        "side_effect_class": "read",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "requires_verification": False,
        "sensitive_output": False,
        "extensions": {},
    }


class ToolDefinitionPayloadHardeningTests(unittest.TestCase):
    def test_non_json_schema_annotation_is_rejected_at_registration(self) -> None:
        candidate = definition()
        candidate["input_schema"]["properties"]["value"]["default"] = b"not-json"
        with self.assertRaisesRegex(TypeError, "JSON-compatible"):
            ToolRegistry().register(candidate, NoopTool())

    def test_nonfinite_schema_annotation_is_rejected_at_registration(self) -> None:
        candidate = definition()
        candidate["input_schema"]["properties"]["value"]["examples"] = [math.nan]
        with self.assertRaisesRegex(TypeError, "JSON-compatible"):
            ToolRegistry().register(candidate, NoopTool())

    def test_non_json_extension_is_rejected_at_registration(self) -> None:
        candidate = definition()
        candidate["extensions"]["opaque"] = object()
        with self.assertRaisesRegex(TypeError, "JSON-compatible"):
            ToolRegistry().register(candidate, NoopTool())


if __name__ == "__main__":
    unittest.main()
