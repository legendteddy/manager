from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from .capacity import CapacityLimits, CapacityManager, OverloadedError
from .observability import Correlation, NullTelemetrySink, SafeTelemetry, TelemetrySink


_EVENT_BY_KIND = {
    "run": "run",
    "model": "provider_request",
    "tool": "tool_execution",
    "mcp": "mcp_invocation",
    "state": "state_operation",
}
_LATENCY_METRIC = {
    "model": "manager_provider_latency_seconds",
    "tool": "manager_tool_latency_seconds",
    "mcp": "manager_mcp_latency_seconds",
    "state": "manager_state_latency_seconds",
    "run": "manager_run_latency_seconds",
}
_ACTIVE_METRIC = {
    "model": "manager_active_model_calls",
    "tool": "manager_active_tool_calls",
    "mcp": "manager_active_mcp_calls",
    "state": "manager_active_state_calls",
    "run": "manager_active_runs",
}
_OUTCOME_METRIC = {
    "run_completed": "manager_completed_runs_total",
    "run_failed": "manager_failed_runs_total",
    "retry": "manager_retries_total",
    "duplicate_suppressed": "manager_duplicate_suppression_total",
    "policy_denial": "manager_policy_denials_total",
    "timeout": "manager_timeouts_total",
    "cancellation": "manager_cancellations_total",
    "budget_exhausted": "manager_budget_exhaustion_total",
    "state_error": "manager_state_errors_total",
}
_BACKLOG_METRIC = {
    "approval_wait": "manager_approval_wait_count",
    "recovery_required": "manager_recovery_required_count",
}


class OperationalRuntime:
    """SRE control plane that never participates in authorization decisions."""

    def __init__(
        self,
        *,
        limits: CapacityLimits | None = None,
        telemetry_sink: TelemetrySink | None = None,
    ) -> None:
        self.capacity = CapacityManager(limits)
        self.telemetry = SafeTelemetry(telemetry_sink or NullTelemetrySink())

    @contextmanager
    def operation(
        self,
        kind: str,
        *,
        correlation: Correlation | None = None,
        labels: Mapping[str, Any] | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[None]:
        if kind not in _EVENT_BY_KIND:
            raise ValueError(f"unknown operation kind: {kind}")
        correlation = correlation or Correlation()
        labels = dict(labels or {})
        event_prefix = _EVENT_BY_KIND[kind]
        started = time.monotonic()
        try:
            with self.capacity.gate(kind).acquire():
                self.telemetry.metric(_ACTIVE_METRIC[kind], "gauge", self.capacity.gate(kind).active, labels=labels)
                self.telemetry.event(f"{event_prefix}.started", correlation=correlation, attributes=attributes)
                try:
                    yield
                except BaseException as exc:
                    self.telemetry.event(
                        f"{event_prefix}.failed",
                        correlation=correlation,
                        attributes={"error_type": type(exc).__name__, **dict(attributes or {})},
                    )
                    raise
                else:
                    self.telemetry.event(f"{event_prefix}.completed", correlation=correlation, attributes=attributes)
                finally:
                    self.telemetry.metric(
                        _LATENCY_METRIC[kind], "histogram", max(0.0, time.monotonic() - started), labels=labels
                    )
        except OverloadedError:
            self.telemetry.metric(
                "manager_overload_rejections_total", "counter", 1,
                labels={"component": kind, "reason": "capacity_exhausted"},
            )
            self.telemetry.event(
                "overload.rejected", correlation=correlation,
                attributes={"component": kind, "configured_limit": self.capacity.gate(kind).limit},
            )
            raise
        finally:
            self.telemetry.metric(_ACTIVE_METRIC[kind], "gauge", self.capacity.gate(kind).active, labels=labels)

    @contextmanager
    def run_scope(
        self, *, request_id: Any = None, run_id: Any = None
    ) -> Iterator[Correlation]:
        correlation = Correlation.from_values(request_id=request_id, run_id=run_id)
        self.telemetry.event("run.submitted", correlation=correlation)
        with self.operation("run", correlation=correlation):
            yield correlation

    def enqueue_run(self, item: Any, *, request_id: Any = None, run_id: Any = None) -> None:
        correlation = Correlation.from_values(request_id=request_id, run_id=run_id)
        try:
            self.capacity.queue.put_nowait(item)
        except OverloadedError:
            self.telemetry.metric(
                "manager_overload_rejections_total", "counter", 1,
                labels={"component": "run_queue", "reason": "capacity_exhausted"},
            )
            self.telemetry.event("overload.rejected", correlation=correlation, attributes={"component": "run_queue"})
            raise
        self.telemetry.metric("manager_queue_depth", "gauge", self.capacity.queue.depth)
        self.telemetry.event("run.queued", correlation=correlation)

    def dequeue_run(self) -> Any:
        item = self.capacity.queue.get_nowait()
        self.telemetry.metric("manager_queue_depth", "gauge", self.capacity.queue.depth)
        return item

    def record_outcome(
        self,
        outcome: str,
        *,
        correlation: Correlation | None = None,
        labels: Mapping[str, Any] | None = None,
    ) -> None:
        metric = _OUTCOME_METRIC.get(outcome)
        if metric is None:
            raise ValueError(f"unknown operational outcome: {outcome}")
        self.telemetry.metric(metric, "counter", 1, labels=labels)
        self.telemetry.event(
            f"outcome.{outcome}", correlation=correlation or Correlation(),
            attributes={"outcome": outcome},
        )

    def record_backlog(self, *, approval_wait: int, recovery_required: int) -> None:
        values = {
            "approval_wait": approval_wait,
            "recovery_required": recovery_required,
        }
        for name, value in values.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} backlog must be a non-negative integer")
            self.telemetry.metric(_BACKLOG_METRIC[name], "gauge", value)

    def health(self) -> dict[str, Any]:
        snapshot = self.capacity.snapshot()
        limits = snapshot["limits"]
        active = snapshot["active"]
        saturation = {
            "runs": active["runs"] / limits["active_runs"],
            "models": active["models"] / limits["model_concurrency"],
            "tools": active["tools"] / limits["tool_concurrency"],
            "mcp": active["mcp"] / limits["mcp_concurrency"],
            "state": active["state"] / limits["state_concurrency"],
            "queue": snapshot["queue_depth"] / limits["queued_runs"],
        }
        snapshot["saturation"] = saturation
        snapshot["telemetry_sink_failures"] = self.telemetry.sink_failures
        if any(value >= 1.0 for value in saturation.values()):
            snapshot["status"] = "saturated"
        elif self.telemetry.sink_failures:
            snapshot["status"] = "degraded"
        else:
            snapshot["status"] = "ok"
        return snapshot

    def observe_model_adapter(self, adapter: Any) -> "ObservedModelAdapter":
        if isinstance(adapter, ObservedModelAdapter) and adapter.operations is self:
            return adapter
        return ObservedModelAdapter(adapter, self)

    def observe_run_store(self, store: Any) -> "ObservedRunStore":
        if isinstance(store, ObservedRunStore) and store.operations is self:
            return store
        return ObservedRunStore(store, self)


class ObservedModelAdapter:
    def __init__(self, adapter: Any, operations: OperationalRuntime) -> None:
        if not isinstance(getattr(adapter, "provider", None), str) or not adapter.provider:
            raise TypeError("model adapter must expose a non-empty provider")
        if not callable(getattr(adapter, "generate", None)):
            raise TypeError("model adapter must expose generate(request)")
        self._adapter = adapter
        self.operations = operations
        self.provider = adapter.provider

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        metadata = request.get("metadata") if isinstance(request, dict) else None
        task_id = metadata.get("task_id") if isinstance(metadata, dict) else None
        correlation = Correlation.from_values(
            run_id=f"run:{task_id}" if task_id else None,
            model_request_id=request.get("request_id") if isinstance(request, dict) else None,
        )
        with self.operations.operation(
            "model", correlation=correlation,
            labels={"provider": self.provider, "operation": "generate"},
            attributes={"provider": self.provider, "model": request.get("model")},
        ):
            return self._adapter.generate(request)


class ObservedRunStore:
    """Add state latency/error telemetry and checkpoint-size protection to a RunStore."""

    def __init__(self, store: Any, operations: OperationalRuntime) -> None:
        for method in ("create", "load", "compare_and_swap"):
            if not callable(getattr(store, method, None)):
                raise TypeError(f"run store must expose {method}()")
        self._store = store
        self.operations = operations

    def create(self, state: dict[str, Any]) -> dict[str, Any]:
        self.operations.capacity.assert_checkpoint_size(state)
        correlation = Correlation.from_values(run_id=state.get("run_id"), state_revision=state.get("revision"))
        with self.operations.operation("state", correlation=correlation, labels={"operation": "create"}):
            return self._store.create(state)

    def load(self, run_id: str) -> dict[str, Any] | None:
        correlation = Correlation.from_values(run_id=run_id)
        with self.operations.operation("state", correlation=correlation, labels={"operation": "load"}):
            state = self._store.load(run_id)
        if state is not None:
            self.operations.capacity.assert_checkpoint_size(state)
        return state

    def compare_and_swap(self, run_id: str, expected_revision: int, state: dict[str, Any]) -> dict[str, Any]:
        self.operations.capacity.assert_checkpoint_size(state)
        correlation = Correlation.from_values(run_id=run_id, state_revision=expected_revision)
        with self.operations.operation("state", correlation=correlation, labels={"operation": "compare_and_swap"}):
            return self._store.compare_and_swap(run_id, expected_revision, state)


DEFAULT_OPERATIONS = OperationalRuntime()
