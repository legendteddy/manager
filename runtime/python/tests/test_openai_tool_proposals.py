from __future__ import annotations

import unittest
from types import SimpleNamespace

from manager_runtime.providers.base import ProviderMalformedResponseError
from manager_runtime.providers.openai_adapter import OpenAIResponsesAdapter


class FakeResponses:
    def __init__(self, *, arguments: str = '{"value":"synthetic"}') -> None:
        self.calls: list[dict] = []
        self.arguments = arguments

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id=f"resp_tool_test_{len(self.calls)}",
            model=kwargs["model"],
            status="completed",
            output_text="",
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call_123",
                    name="lookup",
                    arguments=self.arguments,
                )
            ] if len(self.calls) == 1 else [],
            usage=SimpleNamespace(input_tokens=7, output_tokens=3, total_tokens=10),
        )


class FakeClient:
    def __init__(self, *, arguments: str = '{"value":"synthetic"}') -> None:
        self.responses = FakeResponses(arguments=arguments)


class OpenAIToolProposalTests(unittest.TestCase):
    @staticmethod
    def tool_definition() -> dict:
        return {
            "name": "lookup",
            "description": "Synthetic lookup.",
            "input_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        }

    def test_custom_function_is_proposed_but_not_executed_by_adapter(self) -> None:
        client = FakeClient()
        adapter = OpenAIResponsesAdapter(client=client)
        response = adapter.generate(
            {
                "request_id": "request:tool-test",
                "model": "synthetic-model",
                "input": "Use lookup if needed.",
                "tools": [self.tool_definition()],
            }
        )
        sent_tool = client.responses.calls[0]["tools"][0]
        self.assertEqual(sent_tool["type"], "function")
        self.assertEqual(sent_tool["name"], "lookup")
        self.assertTrue(sent_tool["strict"])
        self.assertEqual(response["tool_proposals"][0]["proposal_id"], "call_123")
        self.assertEqual(response["tool_proposals"][0]["tool_name"], "lookup")
        self.assertEqual(response["tool_proposals"][0]["arguments"], {"value": "synthetic"})

    def test_recursion_hostile_function_arguments_are_malformed_response(self) -> None:
        deep_arguments = '{"x":' * 1200 + "0" + "}" * 1200
        adapter = OpenAIResponsesAdapter(client=FakeClient(arguments=deep_arguments))
        with self.assertRaises(ProviderMalformedResponseError):
            adapter.generate(
                {
                    "request_id": "request:recursive-tool-arguments",
                    "model": "synthetic-model",
                    "input": "Use lookup if needed.",
                    "tools": [self.tool_definition()],
                }
            )

    def test_continuation_uses_previous_response_and_function_call_output(self) -> None:
        client = FakeClient()
        adapter = OpenAIResponsesAdapter(client=client)
        adapter.generate(
            {
                "request_id": "request:continuation",
                "model": "synthetic-model",
                "input": "Continue with verified tool results.",
                "continuation": {
                    "prior_response_ref": "resp_prior",
                    "tool_results": [
                        {
                            "proposal_id": "call_123",
                            "status": "executed",
                            "output": "{\"value\":\"synthetic-result\"}",
                            "redacted": False,
                        }
                    ],
                },
            }
        )
        sent = client.responses.calls[0]
        self.assertEqual(sent["previous_response_id"], "resp_prior")
        self.assertEqual(
            sent["input"],
            [
                {
                    "type": "function_call_output",
                    "call_id": "call_123",
                    "output": "{\"value\":\"synthetic-result\"}",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
