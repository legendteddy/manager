from __future__ import annotations

from copy import deepcopy
import unittest

from manager_runtime.agent_loop import run_bounded_agent_loop
from manager_runtime.tools import ToolRegistry


class Tool:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, arguments: dict):
        self.calls += 1
        return {"value": "x" * 200}


class Adapter:
    provider = "bounds-test"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, request: dict) -> dict:
        self.calls.append(deepcopy(request))
        if len(self.calls) == 1:
            return {
                "response_id": "resp-1",
                "provider": self.provider,
                "model": request["model"],
                "status": "completed",
                "output_text": "",
                "tool_proposals": [
                    {
                        "proposal_id": "call-1",
                        "tool_name": "lookup",
                        "arguments": {"value": "large"},
                        "target": None,
                        "source_ref": "resp-1",
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
        return {
            "response_id": "resp-2",
            "provider": self.provider,
            "model": request["model"],
            "status": "completed",
            "output_text": "done",
            "tool_proposals": [],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


class AgentLoopOutputBoundTests(unittest.TestCase):
    def test_tool_result_never_exceeds_configured_character_bound(self) -> None:
        registry = ToolRegistry()
        tool = Tool()
        registry.register(
            {
                "name": "lookup",
                "description": "Synthetic bounded lookup.",
                "side_effect_class": "read",
                "input_schema": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                "requires_verification": False,
                "sensitive_output": False,
            },
            tool,
        )
        adapter = Adapter()
        task_input = {
            "task": {
                "task_id": "bounds-test",
                "objective": "Bound tool result size.",
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
            "model_input": "Use lookup.",
        }

        output = run_bounded_agent_loop(
            task_input,
            adapter,
            registry,
            model="synthetic-model",
            allowed_tools=["lookup"],
            max_tool_result_chars=8,
        )

        sent = adapter.calls[1]["continuation"]["tool_results"][0]["output"]
        self.assertLessEqual(len(sent), 8)
        self.assertEqual(output["agent_loop"]["status"], "completed")
        self.assertEqual(tool.calls, 1)


if __name__ == "__main__":
    unittest.main()
