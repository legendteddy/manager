from __future__ import annotations

import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.state import RunLeaseConflict, RunStateConflict, SQLiteRunStore
from manager_runtime.state.agent_loop import (
    resume_durable_agent_loop,
    run_durable_agent_loop,
)
from manager_runtime.tools import ToolRegistry


def task_input(*, model_input: str = "Use the synthetic model.") -> dict:
    return {
        "task": {
            "task_id": "distributed-loop",
            "objective": "Complete one synthetic durable loop.",
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
        "model_input": model_input,
    }


def response(
    response_id: str,
    *,
    proposals: list[dict] | None = None,
    text: str = "done",
) -> dict:
    return {
        "response_id": response_id,
        "provider": "distributed-loop-test",
        "model": "synthetic-model",
        "status": "completed",
        "output_text": text,
        "tool_proposals": proposals or [],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


def read_definition() -> dict:
    return {
        "name": "lookup",
        "description": "Synthetic read tool.",
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


def read_proposal() -> dict:
    return {
        "proposal_id": "proposal:lookup",
        "tool_name": "lookup",
        "arguments": {"value": "x"},
        "target": None,
        "source_ref": None,
    }


class RecordingReadTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        return {"value": arguments["value"]}


class ImmediateAdapter:
    provider = "distributed-loop-test"

    def __init__(self, result: dict | None = None) -> None:
        self.result = result or response("response:final")
        self.calls: list[dict] = []

    def generate(self, request: dict) -> dict:
        self.calls.append(deepcopy(request))
        return deepcopy(self.result)


class BlockingInitialAdapter(ImmediateAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(self, request: dict) -> dict:
        self.calls.append(deepcopy(request))
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("synthetic provider timeout")
        return response("response:initial")


class CrashInitialAdapter(ImmediateAdapter):
    def generate(self, request: dict) -> dict:
        self.calls.append(deepcopy(request))
        raise SystemExit("synthetic process loss during provider request")


class BlockingContinuationAdapter:
    provider = "distributed-loop-test"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.continuation_started = threading.Event()
        self.release = threading.Event()

    def generate(self, request: dict) -> dict:
        self.calls.append(deepcopy(request))
        if len(self.calls) == 1:
            return response(
                "response:one",
                proposals=[read_proposal()],
                text="read first",
            )
        if len(self.calls) == 2:
            self.continuation_started.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError("synthetic continuation timeout")
            return response("response:two", text="finished")
        raise AssertionError("duplicate model continuation was issued")


class DistributedAgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "loop-state.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_duplicate_initial_submission_does_not_call_provider_twice(self) -> None:
        adapter = BlockingInitialAdapter()
        registry = ToolRegistry()
        results: list[dict] = []
        errors: list[BaseException] = []

        def first_worker() -> None:
            try:
                results.append(
                    run_durable_agent_loop(
                        task_input(),
                        adapter,
                        registry,
                        SQLiteRunStore(self.path),
                        model="synthetic-model",
                        allowed_tools=[],
                        worker_id="worker:first",
                    )
                )
            except BaseException as exc:  # pragma: no cover - diagnostic capture
                errors.append(exc)

        thread = threading.Thread(target=first_worker)
        thread.start()
        self.assertTrue(adapter.started.wait(timeout=2))

        with self.assertRaises(RunLeaseConflict):
            run_durable_agent_loop(
                task_input(),
                adapter,
                registry,
                SQLiteRunStore(self.path, timeout_seconds=0.05),
                model="synthetic-model",
                allowed_tools=[],
                worker_id="worker:duplicate",
            )
        self.assertEqual(len(adapter.calls), 1)

        adapter.release.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["durable_state"]["status"], "completed")
        self.assertEqual(len(adapter.calls), 1)

    def test_same_task_id_with_changed_submission_fails_before_provider(self) -> None:
        registry = ToolRegistry()
        first = ImmediateAdapter()
        completed = run_durable_agent_loop(
            task_input(model_input="original"),
            first,
            registry,
            SQLiteRunStore(self.path),
            model="synthetic-model",
            allowed_tools=[],
        )
        self.assertEqual(completed["durable_state"]["status"], "completed")
        self.assertEqual(len(first.calls), 1)

        changed = ImmediateAdapter()
        with self.assertRaises(RunStateConflict):
            run_durable_agent_loop(
                task_input(model_input="changed"),
                changed,
                registry,
                SQLiteRunStore(self.path),
                model="synthetic-model",
                allowed_tools=[],
            )
        self.assertEqual(changed.calls, [])

    def test_same_task_id_cannot_reset_budget_by_resubmission(self) -> None:
        registry = ToolRegistry()
        first = ImmediateAdapter()
        run_durable_agent_loop(
            task_input(),
            first,
            registry,
            SQLiteRunStore(self.path),
            model="synthetic-model",
            allowed_tools=[],
            max_model_steps=2,
        )

        duplicate = ImmediateAdapter()
        with self.assertRaises(RunStateConflict):
            run_durable_agent_loop(
                task_input(),
                duplicate,
                registry,
                SQLiteRunStore(self.path),
                model="synthetic-model",
                allowed_tools=[],
                max_model_steps=20,
            )
        self.assertEqual(duplicate.calls, [])

    def test_crash_during_initial_provider_preserves_claim_for_restart(self) -> None:
        registry = ToolRegistry()
        crashing = CrashInitialAdapter()
        with self.assertRaises(SystemExit):
            run_durable_agent_loop(
                task_input(),
                crashing,
                registry,
                SQLiteRunStore(self.path),
                model="synthetic-model",
                allowed_tools=[],
                worker_id="worker:crash",
            )
        self.assertEqual(len(crashing.calls), 1)

        store = SQLiteRunStore(self.path)
        state = store.load("run:distributed-loop")
        self.assertEqual(state["status"], "running")
        self.assertIn("initial_provider", state["extensions"])
        self.assertNotIn("agent_loop", state["extensions"])

        restarted = ImmediateAdapter(response("response:restart"))
        finished = run_durable_agent_loop(
            task_input(),
            restarted,
            registry,
            store,
            model="synthetic-model",
            allowed_tools=[],
            worker_id="worker:restart",
        )
        self.assertEqual(finished["durable_state"]["status"], "completed")
        self.assertEqual(len(restarted.calls), 1)
        self.assertEqual(
            restarted.calls[0]["request_id"],
            crashing.calls[0]["request_id"],
        )

    def test_active_continuation_lease_blocks_second_worker(self) -> None:
        registry = ToolRegistry()
        read_tool = RecordingReadTool()
        registry.register(read_definition(), read_tool)
        adapter = BlockingContinuationAdapter()
        results: list[dict] = []
        errors: list[BaseException] = []

        def owner() -> None:
            try:
                results.append(
                    run_durable_agent_loop(
                        task_input(),
                        adapter,
                        registry,
                        SQLiteRunStore(self.path),
                        model="synthetic-model",
                        allowed_tools=["lookup"],
                        worker_id="worker:owner",
                    )
                )
            except BaseException as exc:  # pragma: no cover - diagnostic capture
                errors.append(exc)

        thread = threading.Thread(target=owner)
        thread.start()
        self.assertTrue(adapter.continuation_started.wait(timeout=2))
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(len(read_tool.calls), 1)

        with self.assertRaises(RunLeaseConflict):
            resume_durable_agent_loop(
                SQLiteRunStore(self.path, timeout_seconds=0.05),
                "run:distributed-loop",
                adapter,
                registry,
                worker_id="worker:second",
            )
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(len(read_tool.calls), 1)

        adapter.release.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["durable_state"]["status"], "completed")
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(len(read_tool.calls), 1)


if __name__ == "__main__":
    unittest.main()
