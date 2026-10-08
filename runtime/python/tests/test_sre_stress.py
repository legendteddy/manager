from __future__ import annotations

import threading
import unittest

from manager_runtime.capacity import CapacityLimits, OverloadedError
from manager_runtime.observability import InMemoryTelemetrySink
from manager_runtime.operations import ObservedRunStore, OperationalRuntime
from manager_runtime.tools import ToolRegistry, execute_tool_request


class BlockingTool:
    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        self.entered = entered
        self.release = release
        self.calls = 0

    def execute(self, arguments):
        self.calls += 1
        self.entered.set()
        self.release.wait(2)
        return {"ok": True}


class BlockingStore:
    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        self.entered = entered
        self.release = release

    def create(self, state):
        return dict(state)

    def load(self, run_id):
        self.entered.set()
        self.release.wait(2)
        return None

    def compare_and_swap(self, run_id, expected_revision, state):
        return dict(state)


def _tool_registry(adapter) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        {
            "name": "synthetic_slow_read",
            "description": "Synthetic read used only for capacity tests.",
            "side_effect_class": "analysis",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "requires_verification": False,
        },
        adapter,
    )
    return registry


def _tool_request(request_id: str, text: str) -> dict:
    return {
        "request_id": request_id,
        "run_id": "run:synthetic-stress",
        "tool_name": "synthetic_slow_read",
        "arguments": {"text": text},
        "proposed_by": "system",
    }


class SREStressTests(unittest.TestCase):
    def test_tool_saturation_blocks_before_second_adapter_call(self):
        entered = threading.Event()
        release = threading.Event()
        adapter = BlockingTool(entered, release)
        registry = _tool_registry(adapter)
        sink = InMemoryTelemetrySink(128)
        operations = OperationalRuntime(
            limits=CapacityLimits(tool_concurrency=1), telemetry_sink=sink
        )
        results = []

        def first():
            results.append(
                execute_tool_request(
                    {"classification": {"materiality": "routine"}},
                    _tool_request("tool-1", "Bearer synthetic-secret-value"),
                    registry,
                    operations=operations,
                )
            )

        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(entered.wait(1))
        second = execute_tool_request(
            {"classification": {"materiality": "routine"}},
            _tool_request("tool-2", "another private payload"),
            registry,
            operations=operations,
        )
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(second["decision_reason"], "overload_rejected")
        self.assertEqual(adapter.calls, 1)

        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["status"], "executed")
        self.assertEqual(operations.capacity.tools.active, 0)
        telemetry_text = repr(sink.snapshot()["records"])
        self.assertNotIn("synthetic-secret-value", telemetry_text)
        self.assertNotIn("another private payload", telemetry_text)

    def test_slow_state_backend_is_bounded(self):
        entered = threading.Event()
        release = threading.Event()
        operations = OperationalRuntime(limits=CapacityLimits(state_concurrency=1))
        store = ObservedRunStore(BlockingStore(entered, release), operations)
        errors = []

        def first():
            try:
                store.load("run:one")
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(entered.wait(1))
        with self.assertRaises(OverloadedError):
            store.load("run:two")
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(operations.capacity.state.active, 0)

    def test_retry_cancellation_and_completion_storms_remain_bounded(self):
        sink = InMemoryTelemetrySink(200)
        operations = OperationalRuntime(telemetry_sink=sink)
        for _ in range(2500):
            operations.record_outcome("retry")
            operations.record_outcome("cancellation")
            operations.record_outcome("run_completed")
            operations.record_outcome("duplicate_suppressed")
        snapshot = sink.snapshot()
        self.assertEqual(len(snapshot["records"]), 200)
        self.assertGreater(snapshot["dropped"], 10000)

    def test_queue_storm_is_bounded_and_reports_rejections(self):
        sink = InMemoryTelemetrySink(128)
        operations = OperationalRuntime(
            limits=CapacityLimits(queued_runs=8), telemetry_sink=sink
        )
        for index in range(8):
            operations.enqueue_run(index, request_id=f"req:{index}", run_id=f"run:{index}")
        rejected = 0
        for index in range(1000):
            try:
                operations.enqueue_run(f"overflow-{index}")
            except OverloadedError:
                rejected += 1
        self.assertEqual(rejected, 1000)
        self.assertEqual(operations.capacity.queue.depth, 8)
        self.assertEqual(operations.capacity.queue.rejected, 1000)
        self.assertEqual(len(sink.snapshot()["records"]), 128)

    def test_backlog_and_saturation_health_are_explicit(self):
        sink = InMemoryTelemetrySink(32)
        operations = OperationalRuntime(
            limits=CapacityLimits(queued_runs=1), telemetry_sink=sink
        )
        operations.record_backlog(approval_wait=17, recovery_required=4)
        operations.enqueue_run("queued")
        health = operations.health()
        self.assertEqual(health["status"], "saturated")
        self.assertEqual(health["saturation"]["queue"], 1.0)
        metrics = [record for kind, record in sink.snapshot()["records"] if kind == "metric"]
        names = {metric.name for metric in metrics}
        self.assertIn("manager_approval_wait_count", names)
        self.assertIn("manager_recovery_required_count", names)
        self.assertIn("manager_queue_depth", names)


if __name__ == "__main__":
    unittest.main()
