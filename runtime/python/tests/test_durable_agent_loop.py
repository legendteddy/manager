from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest
from pathlib import Path

from manager_runtime.state.agent_loop import (
    resume_durable_agent_loop,
    run_durable_agent_loop,
)
from manager_runtime.state.sqlite_store import SQLiteRunStore
from manager_runtime.tools import ToolRegistry


def task_input(task_id: str = "durable-loop-test") -> dict:
    return {
        "task": {
            "task_id": task_id,
            "objective": "Use bounded synthetic tools and finish the task.",
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
        "model_input": "Use only the synthetic tools supplied by Manager.",
    }


def definition(name: str, side_effect_class: str = "read", *, version: str = "1") -> dict:
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
        "sensitive_output": False,
    }
    if value["requires_verification"]:
        value["version"] = version
    return value


class RecordingTool:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        return {"value": f"{self.prefix}:{arguments['value']}"}

    def verify(self, arguments: dict, output) -> bool:
        return output.get("value") == f"{self.prefix}:{arguments['value']}"


class CrashAfterEffectTool(RecordingTool):
    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        raise SystemExit("synthetic interruption after external effect")


class SequenceAdapter:
    provider = "sequence-test"

    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def generate(self, request: dict) -> dict:
        self.calls.append(deepcopy(request))
        index = len(self.calls) - 1
        if index >= len(self.responses):
            raise AssertionError("unexpected model call")
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


def proposal(
    proposal_id: str,
    value: str,
    *,
    tool_name: str,
    target: str | None = None,
) -> dict:
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


def approval_decision(store: SQLiteRunStore, run_id: str, value: str = "approved") -> dict:
    state = store.load(run_id)
    assert state is not None
    approval = state["pending_action"]["approval"]
    return {
        "approval_id": approval["approval_id"],
        "decision": value,
        "decided_by": "synthetic-human",
        "decided_at": "2026-10-08T02:00:00Z",
    }


class DurableAgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "manager-loop.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _registry(self, destroy_tool=None, *, destroy_version: str = "1"):
        lookup = RecordingTool("lookup")
        destroy = destroy_tool or RecordingTool("destroy")
        registry = ToolRegistry()
        registry.register(definition("lookup"), lookup)
        registry.register(
            definition("destroy", "sensitive_destructive", version=destroy_version),
            destroy,
        )
        return registry, lookup, destroy

    def test_process_restart_resumes_approved_action_then_finishes_loop(self) -> None:
        registry, lookup, destroy = self._registry()
        store = SQLiteRunStore(self.path)
        initial_adapter = SequenceAdapter(
            [
                response(
                    "resp-1",
                    proposals=[proposal("call-1", "first", tool_name="lookup")],
                ),
                response(
                    "resp-2",
                    proposals=[
                        proposal(
                            "call-2",
                            "approved",
                            tool_name="destroy",
                            target="synthetic-target",
                        )
                    ],
                ),
            ]
        )
        started = run_durable_agent_loop(
            task_input(),
            initial_adapter,
            registry,
            store,
            model="synthetic-model",
            allowed_tools=["lookup", "destroy"],
            authorization_contexts={
                "destroy": {"scope_authorized": True, "target_verified": True}
            },
        )
        self.assertEqual(started["durable_state"]["status"], "waiting_approval")
        self.assertEqual(len(initial_adapter.calls), 2)
        self.assertEqual(len(lookup.calls), 1)
        self.assertEqual(destroy.calls, [])

        restarted_store = SQLiteRunStore(self.path)
        restarted_adapter = SequenceAdapter(
            [response("resp-3", text="final after restart")]
        )
        finished = resume_durable_agent_loop(
            restarted_store,
            "run:durable-loop-test",
            restarted_adapter,
            registry,
            decision=approval_decision(restarted_store, "run:durable-loop-test"),
            authorization_contexts={
                "destroy": {"scope_authorized": True, "target_verified": True}
            },
        )
        self.assertEqual(finished["durable_state"]["status"], "completed")
        self.assertEqual(finished["agent_loop"]["stop_reason"], "final_model_response")
        self.assertEqual(len(lookup.calls), 1)
        self.assertEqual(len(destroy.calls), 1)
        self.assertEqual(len(restarted_adapter.calls), 1)
        self.assertEqual(
            restarted_adapter.calls[0]["continuation"]["prior_response_ref"],
            "resp-2",
        )

    def test_changed_allowed_tool_definition_marks_waiting_approval_stale(self) -> None:
        registry, _lookup, destroy = self._registry()
        store = SQLiteRunStore(self.path)
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
        run_durable_agent_loop(
            task_input(),
            adapter,
            registry,
            store,
            model="synthetic-model",
            allowed_tools=["lookup", "destroy"],
            authorization_contexts={
                "destroy": {"scope_authorized": True, "target_verified": True}
            },
        )

        changed_registry = ToolRegistry()
        changed_registry.register(definition("lookup"), RecordingTool("lookup"))
        changed_registry.register(
            definition("destroy", "sensitive_destructive", version="2"), destroy
        )
        resumed = resume_durable_agent_loop(
            store,
            "run:durable-loop-test",
            SequenceAdapter([]),
            changed_registry,
            decision=approval_decision(store, "run:durable-loop-test"),
            authorization_contexts={
                "destroy": {"scope_authorized": True, "target_verified": True}
            },
        )
        state = store.load("run:durable-loop-test")
        self.assertIsNotNone(state)
        self.assertEqual(resumed["durable_state"]["status"], "waiting_approval")
        self.assertEqual(state["pending_action"]["approval"]["status"], "stale")
        self.assertEqual(destroy.calls, [])

    def test_crash_after_approved_side_effect_moves_to_recovery_required_without_retry(self) -> None:
        crash_tool = CrashAfterEffectTool("destroy")
        registry, _lookup, _destroy = self._registry(crash_tool)
        store = SQLiteRunStore(self.path)
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
        run_durable_agent_loop(
            task_input(),
            adapter,
            registry,
            store,
            model="synthetic-model",
            allowed_tools=["lookup", "destroy"],
            authorization_contexts={
                "destroy": {"scope_authorized": True, "target_verified": True}
            },
        )
        decision = approval_decision(store, "run:durable-loop-test")
        with self.assertRaises(SystemExit):
            resume_durable_agent_loop(
                store,
                "run:durable-loop-test",
                SequenceAdapter([]),
                registry,
                decision=decision,
                authorization_contexts={
                    "destroy": {"scope_authorized": True, "target_verified": True}
                },
            )
        executing = store.load("run:durable-loop-test")
        self.assertEqual(executing["status"], "executing")
        self.assertEqual(len(crash_tool.calls), 1)

        recovered = resume_durable_agent_loop(
            store,
            "run:durable-loop-test",
            SequenceAdapter([]),
            registry,
        )
        self.assertEqual(recovered["durable_state"]["status"], "recovery_required")
        self.assertEqual(len(crash_tool.calls), 1)

    def test_routine_reversible_write_requires_durable_approval_before_execution(self) -> None:
        update_tool = RecordingTool("update")
        registry = ToolRegistry()
        registry.register(definition("update", "reversible_write"), update_tool)
        store = SQLiteRunStore(self.path)
        adapter = SequenceAdapter(
            [
                response(
                    "resp-1",
                    proposals=[proposal("call-1", "x", tool_name="update")],
                )
            ]
        )
        result = run_durable_agent_loop(
            task_input("durable-write-test"),
            adapter,
            registry,
            store,
            model="synthetic-model",
            allowed_tools=["update"],
            authorization_contexts={"update": {"scope_authorized": True}},
        )
        self.assertEqual(result["durable_state"]["status"], "waiting_approval")
        self.assertEqual(update_tool.calls, [])

    def test_seen_action_fingerprint_survives_approval_restart(self) -> None:
        registry, _lookup, destroy = self._registry()
        store = SQLiteRunStore(self.path)
        initial_adapter = SequenceAdapter(
            [
                response(
                    "resp-1",
                    proposals=[
                        proposal(
                            "call-1",
                            "same",
                            tool_name="destroy",
                            target="synthetic-target",
                        )
                    ],
                )
            ]
        )
        run_durable_agent_loop(
            task_input(),
            initial_adapter,
            registry,
            store,
            model="synthetic-model",
            allowed_tools=["lookup", "destroy"],
            authorization_contexts={
                "destroy": {"scope_authorized": True, "target_verified": True}
            },
        )
        repeated_adapter = SequenceAdapter(
            [
                response(
                    "resp-2",
                    proposals=[
                        proposal(
                            "call-2",
                            "same",
                            tool_name="destroy",
                            target="synthetic-target",
                        )
                    ],
                )
            ]
        )
        stopped = resume_durable_agent_loop(
            store,
            "run:durable-loop-test",
            repeated_adapter,
            registry,
            decision=approval_decision(store, "run:durable-loop-test"),
            authorization_contexts={
                "destroy": {"scope_authorized": True, "target_verified": True}
            },
        )
        self.assertEqual(stopped["durable_state"]["status"], "blocked")
        self.assertEqual(stopped["agent_loop"]["stop_reason"], "repeated_tool_proposal")
        self.assertEqual(len(destroy.calls), 1)


if __name__ == "__main__":
    unittest.main()
