from __future__ import annotations

import threading
import time
import unittest
from collections.abc import Mapping
from unittest.mock import patch

from manager_runtime.capacity import CapacityLimits, CheckpointTooLarge, OverloadedError
from manager_runtime.observability import Correlation, InMemoryTelemetrySink, SafeTelemetry, redact, safe_identifier
from manager_runtime.operations import ObservedModelAdapter, OperationalRuntime


class FailingSink:
    def __init__(self) -> None:
        self.called = threading.Event()

    def _fail(self) -> None:
        self.called.set()
        raise RuntimeError("sink unavailable token=should-not-escape")

    def emit_event(self, event):
        self._fail()

    def emit_metric(self, metric):
        self._fail()

    def emit_span(self, span):
        self._fail()


class BlockingSink:
    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        self.entered = entered
        self.release = release

    def _block(self) -> None:
        self.entered.set()
        self.release.wait(5)

    def emit_event(self, event):
        self._block()

    def emit_metric(self, metric):
        self._block()

    def emit_span(self, span):
        self._block()


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


class ExplosiveString:
    def __str__(self):  # pragma: no cover - must never execute
        raise AssertionError("telemetry must not invoke arbitrary __str__")


class ExplosiveMapping(Mapping):
    def __len__(self):  # pragma: no cover - must never execute
        raise AssertionError("telemetry must not invoke custom mapping hooks")

    def __iter__(self):  # pragma: no cover - must never execute
        raise AssertionError("telemetry must not invoke custom mapping hooks")

    def __getitem__(self, key):  # pragma: no cover - must never execute
        raise AssertionError("telemetry must not invoke custom mapping hooks")


class ExplosiveList(list):
    def __iter__(self):  # pragma: no cover - must never execute
        raise AssertionError("telemetry must not invoke collection subclass hooks")


class ExplosiveCorrelation(Correlation):
    def __getattribute__(self, name):  # pragma: no cover - fields must never be read
        if name in {"request_id", "run_id", "model_request_id", "tool_request_id", "state_revision"}:
            raise AssertionError("telemetry must not invoke correlation subclass hooks")
        return super().__getattribute__(name)


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

    def test_custom_mapping_hooks_are_not_traversed(self):
        mapping = ExplosiveMapping()
        self.assertEqual(redact(mapping), "<ExplosiveMapping>")
        sink = InMemoryTelemetrySink(8)
        telemetry = SafeTelemetry(sink)
        telemetry.event("run.started", attributes=mapping)
        telemetry.metric("manager_active_runs", "gauge", 1, labels=mapping)
        records = sink.snapshot()["records"]
        self.assertEqual(records[0][1].attributes, {"value": "<ExplosiveMapping>"})
        self.assertEqual(records[1][1].labels, {})

    def test_collection_subclass_hooks_are_not_traversed(self):
        sink = InMemoryTelemetrySink(8)
        telemetry = SafeTelemetry(sink)
        telemetry.event("run.started", attributes={"items": ExplosiveList([1, 2, 3])})
        event = sink.snapshot()["records"][0][1]
        self.assertEqual(event.attributes["items"], "<ExplosiveList>")

    def test_correlation_subclass_hooks_are_not_invoked(self):
        sink = InMemoryTelemetrySink(8)
        telemetry = SafeTelemetry(sink)
        telemetry.event("run.started", correlation=ExplosiveCorrelation())
        event = sink.snapshot()["records"][0][1]
        self.assertEqual(event.correlation, Correlation())

    def test_redaction_has_global_node_budget_for_wide_nested_input(self):
        shared: dict[str, object] = {"leaf": "value"}
        for _ in range(6):
            shared = {f"key-{index}": shared for index in range(64)}
        result = redact(shared, max_nodes=64)
        text = repr(result)
        self.assertIn("_truncated_items", text)
        self.assertLess(len(text), 20_000)

    def test_hostile_objects_never_run_string_methods(self):
        sink = InMemoryTelemetrySink(8)
        telemetry = SafeTelemetry(sink)
        hostile = ExplosiveString()
        telemetry.event(
            "run.started",
            correlation=Correlation(request_id=hostile),
            attributes={"object": hostile},
        )
        telemetry.metric(
            "manager_active_runs", "gauge", 1, labels={"provider": hostile}
        )
        records = sink.snapshot()["records"]
        self.assertEqual(len(records), 2)
        self.assertIn("<ExplosiveString>", repr(records[0][1].attributes))
        self.assertEqual(records[1][1].labels["provider"], "type:ExplosiveString")

    def test_long_strings_are_scanned_and_retained_with_fixed_bounds(self):
        text = "Bearer secret-prefix " + ("x" * 1_000_000)
        value = redact({"message": text})["message"]
        self.assertNotIn("secret-prefix", value)
        self.assertLessEqual(len(value), 256 + len("...[TRUNCATED]"))

    def test_long_metric_label_is_compacted(self):
        sink = InMemoryTelemetrySink(8)
        telemetry = SafeTelemetry(sink)
        telemetry.metric(
            "manager_provider_latency_seconds",
            "histogram",
            0.1,
            labels={"provider": "provider-" + ("x" * 1_000_000)},
        )
        metric = sink.snapshot()["records"][0][1]
        self.assertRegex(metric.labels["provider"], r"^hash:[0-9a-f]{12}$")

    def test_long_correlation_identifier_is_compacted_deterministically(self):
        identifier = "request-" + ("x" * 1_000_000)
        first = safe_identifier(identifier)
        second = safe_identifier(identifier)
        self.assertEqual(first, second)
        self.assertRegex(first or "", r"^hash:[0-9a-f]{20}$")

    def test_sink_failure_is_non_authoritative(self):
        failing = FailingSink()
        operations = OperationalRuntime(telemetry_sink=failing)
        adapter = ObservedModelAdapter(FakeAdapter(), operations)
        result = adapter.generate(
            {"request_id": "m1", "model": "synthetic", "metadata": {"task_id": "t1"}}
        )
        self.assertTrue(result["ok"])
        self.assertTrue(failing.called.wait(1))
        deadline = time.monotonic() + 1
        while operations.telemetry.sink_failures == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreater(operations.telemetry.sink_failures, 0)

    def test_blocked_sink_cannot_block_work_or_create_unbounded_backlog(self):
        entered = threading.Event()
        release = threading.Event()
        sink = BlockingSink(entered, release)
        operations = OperationalRuntime(telemetry_sink=sink)
        started = time.monotonic()
        operations.telemetry.event("run.started")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(entered.wait(1))
        for index in range(300):
            operations.telemetry.event("run.completed", attributes={"index": index})
        health = operations.health()
        self.assertEqual(health["telemetry_sink_queue_capacity"], 256)
        self.assertLessEqual(health["telemetry_sink_pending"], 256)
        self.assertGreater(health["telemetry_sink_dropped"], 0)
        self.assertGreater(health["telemetry_sink_inflight_seconds"], 0)
        self.assertEqual(health["status"], "degraded")
        release.set()

    def test_event_sanitization_failure_is_contained_and_visible(self):
        sink = InMemoryTelemetrySink(8)
        telemetry = SafeTelemetry(sink)
        with patch("manager_runtime.observability.redact", side_effect=RuntimeError("synthetic sanitizer failure")):
            telemetry.event("run.started", attributes={"safe": "value"})
        event = sink.snapshot()["records"][0][1]
        self.assertEqual(event.attributes, {"telemetry_sanitization": "failed"})
        self.assertEqual(telemetry.sanitization_failures, 1)

    def test_metric_label_sanitization_failure_is_contained(self):
        sink = InMemoryTelemetrySink(8)
        telemetry = SafeTelemetry(sink)
        with patch("manager_runtime.observability.safe_labels", side_effect=RuntimeError("synthetic sanitizer failure")):
            telemetry.metric("manager_active_runs", "gauge", 1, labels={"status": "ok"})
        metric = sink.snapshot()["records"][0][1]
        self.assertEqual(metric.labels, {})
        self.assertEqual(telemetry.sanitization_failures, 1)

    def test_span_sanitization_cannot_replace_primary_failure(self):
        sink = InMemoryTelemetrySink(8)
        telemetry = SafeTelemetry(sink)
        with patch("manager_runtime.observability.safe_labels", side_effect=RuntimeError("synthetic sanitizer failure")):
            with self.assertRaisesRegex(RuntimeError, "primary operation failed"):
                with telemetry.span("run.started", labels={"status": "failed"}):
                    raise RuntimeError("primary operation failed")
        span = sink.snapshot()["records"][0][1]
        self.assertEqual(span.status, "failed")
        self.assertEqual(span.labels, {})
        self.assertEqual(telemetry.sanitization_failures, 1)

    def test_operation_telemetry_mapping_cannot_replace_primary_failure(self):
        operations = OperationalRuntime(telemetry_sink=InMemoryTelemetrySink(32))
        hostile = ExplosiveMapping()
        with self.assertRaisesRegex(RuntimeError, "primary operation failed"):
            with operations.operation("run", labels=hostile, attributes=hostile):
                raise RuntimeError("primary operation failed")
        self.assertEqual(operations.capacity.runs.active, 0)

    def test_sanitization_failure_degrades_operational_health(self):
        operations = OperationalRuntime(telemetry_sink=InMemoryTelemetrySink(8))
        with patch("manager_runtime.observability.redact", side_effect=RuntimeError("synthetic sanitizer failure")):
            operations.telemetry.event("run.started", attributes={"safe": "value"})
        health = operations.health()
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["telemetry_sink_failures"], 0)
        self.assertEqual(health["telemetry_sanitization_failures"], 1)

    def test_metric_label_cardinality_is_bounded_and_health_visible(self):
        sink = InMemoryTelemetrySink(256)
        operations = OperationalRuntime(telemetry_sink=sink)
        for index in range(100):
            operations.telemetry.metric(
                "manager_provider_latency_seconds",
                "histogram",
                0.1,
                labels={"provider": f"provider-{index}"},
            )
        metrics = [record for kind, record in sink.snapshot()["records"] if kind == "metric"]
        provider_values = {metric.labels["provider"] for metric in metrics}
        self.assertEqual(len(provider_values), 65)
        self.assertIn("overflow", provider_values)
        self.assertEqual(operations.telemetry.label_cardinality_overflows, 36)
        health = operations.health()
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["telemetry_label_cardinality_overflows"], 36)

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

    def test_metric_rejects_non_finite_values(self):
        telemetry = SafeTelemetry(InMemoryTelemetrySink(8))
        with self.assertRaises(ValueError):
            telemetry.metric("manager_bad", "gauge", float("nan"))


if __name__ == "__main__":
    unittest.main()
