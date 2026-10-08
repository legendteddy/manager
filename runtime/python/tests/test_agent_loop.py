from __future__ import annotations

from copy import deepcopy
import unittest

from manager_runtime.agent_loop import run_bounded_agent_loop
from manager_runtime.tools import ToolRegistry


def task_input() -> dict:
    return {
        "task": {
            "task_id": "agent-loop-test",
            "objective": "Use bounded synthetic tools and return a final answer.",
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
        "model_input": "Use synthetic tools only when needed.",
    }


def definition(
    name: str,
    side_effect_class: str = "read",
    *,
    sensitive_output: bool = False,
) -> dict:
    value = {
        "name": name,
        "description": f"Synthetic {name} tool.",
        "side_effect_class": side_effect_class,
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "target": {"type": "string"},
            },
            "required": ["value"],
            "additionalProperties": False,
        },
        "requires_verification": side_effect_class
        in {"reversible_write", "external_commitment", "sensitive_destructive"},
        "sensitive_output": sensitive_output,
    }
    if value["requires_verification"]:
        value["version"] = "1"
    return value


class RecordingTool:
    def __init__(self, output_prefix: str = "result") -> None:
        self.calls: list[dict] = []
        self.output_prefix = output_prefix

    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        return {"value": f"{self.output_prefix}:{arguments['value']}"}

    def verify(self, arguments: dict, output) -> bool:
        return output.get("value") == f"{self.output_prefix}:{arguments['value']}"


class SequenceAdapter:
    provider = "sequence-test"

    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def generate(self, request: dict) -> dict:
        self.calls.append(deepcopy(request))
        index = len(self.calls) - 1
        if index >= len(self.responses):
            raise AssertionError("unexpected model continuation")
        return deepcopy(self.responses[index])


def response(response_id: str, *, proposals: list[dict] | None = None, text: str = "") -> dict:
    return {
        "response_id": response_id,
        "provider": "sequence-test",
        "model": "synthetic-model",
        "status": "completed",
        "output_text": text,
        "tool_proposals": proposals or [],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


def proposal(proposal_id: str, value: str, *, tool_name: str = "lookup", target=None) -> dict:
    arguments = {"value": value}
    if target is not None:
        arguments["target"] = target
    return {
        "proposal_id": proposal_id,
        "tool_name": tool_name,
        "arguments": arguments,
        "target": target,
        "source_ref": None,
    }


class AgentLoopTests(unittest.TestCase):
    def test_two_tool_rounds_continue_to_final_model_response(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition("lookup"), tool)
        adapter = SequenceAdapter(
            [
                response("resp-1", proposals=[proposal("call-1", "first")]),
                response("resp-2", proposals=[proposal("call-2", "second")]),
                response("resp-3", text="final synthetic answer"),
            ]
        )

        output = run_bounded_agent_loop(
            task_input(),
            adapter,
            registry,
            model="synthetic-model",
            allowed_tools=["lookup"],
        )

        self.assertEqual(output["agent_loop"]["status"], "completed")
        self.assertEqual(output["agent_loop"]["model_steps"], 3)
        self.assertEqual(output["agent_loop"]["tool_calls"], 2)
        self.assertEqual(output["result"]["finding"], "final synthetic answer")
        self.assertEqual(len(tool.calls), 2)
        self.assertEqual(
            adapter.calls[1]["continuation"]["prior_response_ref"], "resp-1"
        )
        self.assertEqual(
            adapter.calls[1]["continuation"]["tool_results"][0]["proposal_id"],
            "call-1",
        )

    def test_repeated_exact_proposal_stops_before_second_execution(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition("lookup"), tool)
        adapter = SequenceAdapter(
            [
                response("resp-1", proposals=[proposal("call-1", "same")]),
                response("resp-2", proposals=[proposal("call-2", "same")]),
            ]
        )

        output = run_bounded_agent_loop(
            task_input(), adapter, registry, model="synthetic-model", allowed_tools=["lookup"]
        )

        self.assertEqual(output["agent_loop"]["stop_reason"], "repeated_tool_proposal")
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(output["agent_loop"]["tool_calls"], 1)

    def test_model_step_budget_blocks_before_tool_execution(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition("lookup"), tool)
        adapter = SequenceAdapter(
            [response("resp-1", proposals=[proposal("call-1", "unused")])]
        )

        output = run_bounded_agent_loop(
            task_input(),
            adapter,
            registry,
            model="synthetic-model",
            allowed_tools=["lookup"],
            max_model_steps=1,
        )

        self.assertEqual(output["agent_loop"]["stop_reason"], "model_step_budget_exhausted")
        self.assertEqual(tool.calls, [])

    def test_tool_budget_does_not_partially_execute_over_budget_batch(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition("lookup"), tool)
        adapter = SequenceAdapter(
            [
                response(
                    "resp-1",
                    proposals=[proposal("call-1", "a"), proposal("call-2", "b")],
                )
            ]
        )

        output = run_bounded_agent_loop(
            task_input(),
            adapter,
            registry,
            model="synthetic-model",
            allowed_tools=["lookup"],
            max_tool_calls=1,
        )

        self.assertEqual(output["agent_loop"]["stop_reason"], "tool_call_budget_exhausted")
        self.assertEqual(tool.calls, [])
        self.assertEqual(output["agent_loop"]["tool_calls"], 0)

    def test_consequential_multi_tool_batch_is_serialized_before_any_execution(self) -> None:
        registry = ToolRegistry()
        read_tool = RecordingTool()
        write_tool = RecordingTool()
        registry.register(definition("lookup"), read_tool)
        registry.register(definition("update", "reversible_write"), write_tool)
        adapter = SequenceAdapter(
            [
                response(
                    "resp-1",
                    proposals=[
                        proposal("call-1", "read-first", tool_name="lookup"),
                        proposal("call-2", "write-second", tool_name="update"),
                    ],
                )
            ]
        )

        output = run_bounded_agent_loop(
            task_input(),
            adapter,
            registry,
            model="synthetic-model",
            allowed_tools=["lookup", "update"],
            authorization_contexts={"update": {"scope_authorized": True}},
        )

        self.assertEqual(
            output["agent_loop"]["stop_reason"],
            "consequential_multi_tool_batch_requires_serialization",
        )
        self.assertEqual(read_tool.calls, [])
        self.assertEqual(write_tool.calls, [])
        self.assertEqual(output["agent_loop"]["tool_calls"], 0)

    def test_prior_approval_is_not_carried_into_new_destructive_action(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition("destroy", "sensitive_destructive"), tool)
        adapter = SequenceAdapter(
            [
                response(
                    "resp-1",
                    proposals=[
                        proposal(
                            "call-1",
                            "x",
                            tool_name="destroy",
                            target="synthetic-target",
                        )
                    ],
                )
            ]
        )

        output = run_bounded_agent_loop(
            task_input(),
            adapter,
            registry,
            model="synthetic-model",
            allowed_tools=["destroy"],
            authorization_contexts={
                "destroy": {
                    "scope_authorized": True,
                    "target_verified": True,
                    "approval": {
                        "status": "approved",
                        "action_fingerprint": "old-action",
                    },
                }
            },
        )

        self.assertEqual(output["agent_loop"]["stop_reason"], "tool_approval_required")
        self.assertEqual(tool.calls, [])
        self.assertTrue(output["result"]["owner_decision_required"])

    def test_sensitive_tool_output_is_withheld_from_continuation(self) -> None:
        registry = ToolRegistry()
        tool = RecordingTool(output_prefix="secret-material")
        registry.register(definition("lookup", sensitive_output=True), tool)
        adapter = SequenceAdapter(
            [
                response("resp-1", proposals=[proposal("call-1", "hidden")]),
                response("resp-2", text="done"),
            ]
        )

        output = run_bounded_agent_loop(
            task_input(), adapter, registry, model="synthetic-model", allowed_tools=["lookup"]
        )

        continuation = adapter.calls[1]["continuation"]["tool_results"][0]
        self.assertTrue(continuation["redacted"])
        self.assertNotIn("secret-material", continuation["output"])
        self.assertEqual(output["agent_loop"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
