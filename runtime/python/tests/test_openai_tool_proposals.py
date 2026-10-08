from __future__ import annotations

import unittest
from types import SimpleNamespace

from manager_runtime.providers.openai_adapter import OpenAIResponsesAdapter


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="resp_tool_test",
            model=kwargs["model"],
            status="completed",
            output_text="",
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call_123",
                    name="lookup",
                    arguments='{"value":"synthetic"}',
                )
            ],
            usage=SimpleNamespace(input_tokens=7, output_tokens=3, total_tokens=10),
        )


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


class OpenAIToolProposalTests(unittest.TestCase):
    def test_custom_function_is_proposed_but_not_executed_by_adapter(self) -> None:
        client = FakeClient()
        adapter = OpenAIResponsesAdapter(client=client)
        response = adapter.generate(
            {
                "request_id": "request:tool-test",
                "model": "synthetic-model",
                "input": "Use lookup if needed.",
                "tools": [
                    {
                        "name": "lookup",
                        "description": "Synthetic lookup.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                    }
                ],
            }
        )
        sent_tool = client.responses.calls[0]["tools"][0]
        self.assertEqual(sent_tool["type"], "function")
        self.assertEqual(sent_tool["name"], "lookup")
        self.assertTrue(sent_tool["strict"])
        self.assertEqual(response["tool_proposals"][0]["proposal_id"], "call_123")
        self.assertEqual(response["tool_proposals"][0]["tool_name"], "lookup")
        self.assertEqual(response["tool_proposals"][0]["arguments"], {"value": "synthetic"})


if __name__ == "__main__":
    unittest.main()
