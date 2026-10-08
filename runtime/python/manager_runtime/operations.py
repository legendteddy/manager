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

    def health(self) -> dict[str, Any]:
        snapshot = self.capacity.snapshot()
        snapshot["telemetry_sink_failures"] = self.telemetry.sink_failures
        snapshot["status"] = "degraded" if self.telemetry.sink_failures else "ok"
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
