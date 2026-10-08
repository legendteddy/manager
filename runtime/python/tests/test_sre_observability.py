from __future__ import annotations

import threading
import unittest

from manager_runtime.capacity import CapacityLimits, CheckpointTooLarge, OverloadedError
from manager_runtime.observability import Correlation, InMemoryTelemetrySink, SafeTelemetry
from manager_runtime.operations import ObservedModelAdapter, OperationalRuntime


class FailingSink:
    def emit_event(self, event):
        raise RuntimeError("sink unavailable token=should-not-escape")

    def emit_metric(self, metric):
        raise RuntimeError("sink unavailable")

    def emit_span(self, span):
        raise RuntimeError("sink unavailable")


class FakeAdapter:
    provider = "synthetic"

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return {"ok": True, "request_id": request["request_id"]}


class BlockingAdapter:
    provider = "slow"

    def __init__(self, entered: threading.Event, release: threading.Event):
        self.entered = entered
        self.release = release

    def generate(self, request):
        self.entered.set()
        self.release.wait(2)
        return {"ok": True}


class SREObservabilityTests(unittest.TestCase):
    def test_redaction_hides_sensitive_fields_and_tokens(self):
        sink = InMemoryTelemetrySink(32)
        telemetry = SafeTelemetry(sink)
        telemetry.event(
            "provider_request.started",
            correlation=Correlation.from_values(request_id="req-1", run_id="run-1"),
            attributes={
                "authorization": "Bearer top-secret-token",
                "nested": {"api_key": "sk-abcdefghijklmnopqrstuvwxyz", "safe": "ok"},
                "message": "Bearer abcdefghijklmnop",
            },
        )
        event = sink.snapshot()["records"][0][1]
        text = repr(event)
        self.assertNotIn("top-secret-token", text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", text)
        self.assertIn("[REDACTED]", text)
        self.assertEqual(event.attributes["nested"]["safe"], "ok")

    def test_sink_failure_is_non_authoritative(self):
        operations = OperationalRuntime(telemetry_sink=FailingSink())
        adapter = ObservedModelAdapter(FakeAdapter(), operations)
        result = adapter.generate(
            {"request_id": "m1", "model": "synthetic", "metadata": {"task_id": "t1"}}
        )
        self.assertTrue(result["ok"])
        self.assertGreater(operations.telemetry.sink_failures, 0)

    def test_telemetry_buffer_is_bounded_under_stress(self):
        sink = InMemoryTelemetrySink(25)
        telemetry = SafeTelemetry(sink)
        for index in range(5000):
            telemetry.event("run.completed", attributes={"index": index})
        snapshot = sink.snapshot()
        self.assertEqual(len(snapshot["records"]), 25)
        self.assertEqual(snapshot["dropped"], 4975)

    def test_model_concurrency_rejects_without_unbounded_wait(self):
        entered = threading.Event()
        release = threading.Event()
        operations = OperationalRuntime(limits=CapacityLimits(model_concurrency=1))
        adapter = ObservedModelAdapter(BlockingAdapter(entered, release), operations)
        errors = []

        def first():
            try:
                adapter.generate(
                    {"request_id": "first", "model": "x", "metadata": {"task_id": "t"}}
                )
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(entered.wait(1))
        with self.assertRaises(OverloadedError):
            adapter.generate(
                {"request_id": "second", "model": "x", "metadata": {"task_id": "t"}}
            )
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(operations.capacity.models.active, 0)
        self.assertEqual(operations.capacity.models.rejected, 1)

    def test_queue_full_is_deterministic(self):
        operations = OperationalRuntime(limits=CapacityLimits(queued_runs=2))
        operations.capacity.queue.put_nowait("a")
        operations.capacity.queue.put_nowait("b")
        with self.assertRaises(OverloadedError):
            operations.capacity.queue.put_nowait("c")
        self.assertEqual(operations.capacity.queue.depth, 2)

    def test_checkpoint_size_limit_is_enforced_before_state_write(self):
        operations = OperationalRuntime(limits=CapacityLimits(checkpoint_max_bytes=100))
        with self.assertRaises(CheckpointTooLarge):
            operations.capacity.assert_checkpoint_size(
                {"run_id": "r", "payload": "x" * 500}
            )

    def test_metric_labels_drop_unbounded_keys(self):
        sink = InMemoryTelemetrySink(8)
        telemetry = SafeTelemetry(sink)
        telemetry.metric(
            "manager_tool_latency_seconds",
            "histogram",
            0.2,
            labels={
                "tool_name": "user-controlled-123",
                "status": "completed",
                "operation": "execute",
            },
        )
        metric = sink.snapshot()["records"][0][1]
        self.assertNotIn("tool_name", metric.labels)
        self.assertEqual(metric.labels["status"], "completed")


if __name__ == "__main__":
    unittest.main()
