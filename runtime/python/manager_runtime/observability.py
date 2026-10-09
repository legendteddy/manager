from __future__ import annotations

import hashlib
import json
import logging
import math
import queue
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import Any, Callable, Iterator, Mapping, Protocol, runtime_checkable

_REDACTED = "[REDACTED]"
_SECRET_KEY_FRAGMENTS = {
    "authorization", "token", "secret", "password", "credential", "api_key",
    "apikey", "cookie", "session", "headers", "prompt", "model_input",
    "arguments", "payload", "context", "private", "env",
}
_SAFE_LABEL_KEYS = {
    "operation", "provider", "status", "side_effect_class", "reason",
    "transport", "component", "outcome",
}
_SAFE_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
_SAFE_METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:.-]{0,95}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9:._-]{1,128}$")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*")
_SECRETISH = re.compile(r"(?i)\b(?:sk|pk|api)[-_][A-Za-z0-9_-]{12,}\b")
_DEFAULT_REDACTION_NODES = 256
_DEFAULT_SINK_QUEUE = 256
_LABEL_CARDINALITY_LIMIT = 64
_LABEL_OVERFLOW = "overflow"
_LABEL_SCAN_LIMIT = 64
_LABEL_HASH_PREFIX_CHARS = 256


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash_text(text: str) -> str:
    hasher = hashlib.sha256()
    for offset in range(0, len(text), 4096):
        hasher.update(text[offset : offset + 4096].encode("utf-8", errors="replace"))
    return hasher.hexdigest()


def _bounded_label_hash(text: str) -> str:
    """Fingerprint a label with work bounded independently of label length."""
    prefix = text[:_LABEL_HASH_PREFIX_CHARS].encode("utf-8", errors="replace")
    payload = str(len(text)).encode("ascii") + b":" + prefix
    return hashlib.sha256(payload).hexdigest()[:12]


def safe_identifier(value: Any) -> str | None:
    """Normalize correlation identifiers without invoking arbitrary object code."""
    if value is None:
        return None
    if type(value) is str:
        text = value
    elif type(value) is int:
        text = str(value)
    elif type(value) is bytes:
        return f"hash:{hashlib.sha256(value).hexdigest()[:20]}"
    else:
        typename = f"{type(value).__module__}.{type(value).__qualname__}"
        return f"hash:{hashlib.sha256(typename.encode()).hexdigest()[:20]}"
    if _SAFE_IDENTIFIER.fullmatch(text) and _SECRETISH.search(text) is None:
        return text
    return f"hash:{_hash_text(text)[:20]}"


def _safe_key(key: Any) -> str:
    if type(key) is str:
        return key[:96]
    return f"<{type(key).__name__}>"


def _sensitive_key(key: Any) -> bool:
    if type(key) is not str:
        return False
    normalized = key.strip().lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS)


def redact(
    value: Any,
    *,
    max_depth: int = 6,
    max_string_chars: int = 256,
    max_nodes: int = _DEFAULT_REDACTION_NODES,
) -> Any:
    """Return a bounded telemetry-safe representation.

    Traversal and retained output are bounded globally and per container. Only
    exact built-in containers are traversed so hostile collection subclasses or
    custom Mapping implementations cannot run hooks inside the telemetry path.
    """
    if type(max_depth) is not int or max_depth < 0:
        raise ValueError("max_depth must be a non-negative integer")
    if type(max_string_chars) is not int or max_string_chars < 1:
        raise ValueError("max_string_chars must be a positive integer")
    if type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_nodes must be a positive integer")

    remaining_nodes = max_nodes

    def visit(current: Any, depth: int) -> Any:
        nonlocal remaining_nodes
        if remaining_nodes <= 0:
            return "[TRUNCATED_BUDGET]"
        remaining_nodes -= 1
        if depth > max_depth:
            return "[TRUNCATED_DEPTH]"
        if current is None or type(current) in {bool, int}:
            return current
        if type(current) is float:
            return current if math.isfinite(current) else "[NON_FINITE_NUMBER]"
        if type(current) is str:
            # Only scan a bounded prefix because text after max_string_chars is
            # never retained in telemetry anyway.
            bounded = current[: max_string_chars + 128]
            text = _BEARER.sub("Bearer [REDACTED]", bounded)
            text = _SECRETISH.sub(_REDACTED, text)
            if len(current) > max_string_chars or len(text) > max_string_chars:
                return text[:max_string_chars] + "...[TRUNCATED]"
            return text
        if type(current) is dict:
            result: dict[str, Any] = {}
            truncated = False
            for index, (key, item) in enumerate(islice(current.items(), 65)):
                if index >= 64 or remaining_nodes <= 0:
                    truncated = True
                    break
                skey = _safe_key(key)
                result[skey] = _REDACTED if _sensitive_key(key) else visit(item, depth + 1)
            if truncated:
                result["_truncated_items"] = True
            return result
        if type(current) in {list, tuple, set, frozenset}:
            result = []
            truncated = False
            for index, item in enumerate(islice(current, 65)):
                if index >= 64 or remaining_nodes <= 0:
                    truncated = True
                    break
                result.append(visit(item, depth + 1))
            if truncated:
                result.append("[TRUNCATED_ITEMS]")
            return result
        return f"<{type(current).__name__}>"

    return visit(value, 0)


def _safe_label_value(value: Any) -> str:
    if type(value) is str:
        return value
    if value is None:
        return "none"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float and math.isfinite(value):
        return str(value)
    return f"type:{type(value).__name__}"


def safe_labels(labels: Mapping[str, Any] | None) -> dict[str, str]:
    """Sanitize a bounded prefix of exact-dict metric labels."""
    if labels is None:
        return {}
    if type(labels) is not dict:
        return {}
    result: dict[str, str] = {}
    for key, value in islice(labels.items(), _LABEL_SCAN_LIMIT):
        if type(key) is not str or key not in _SAFE_LABEL_KEYS:
            continue
        text = _safe_label_value(value)
        if len(text) > 64:
            text = f"hash:{_bounded_label_hash(text)}"
        elif re.fullmatch(r"[A-Za-z0-9:._/-]+", text) is None:
            text = f"hash:{_hash_text(text)[:12]}"
        result[key] = text
    return result


@dataclass(frozen=True)
class Correlation:
    request_id: str | None = None
    run_id: str | None = None
    model_request_id: str | None = None
    tool_request_id: str | None = None
    state_revision: int | None = None

    @classmethod
    def from_values(cls, **values: Any) -> "Correlation":
        revision = values.get("state_revision")
        if type(revision) is not int:
            revision = None
        return cls(
            request_id=safe_identifier(values.get("request_id")),
            run_id=safe_identifier(values.get("run_id")),
            model_request_id=safe_identifier(values.get("model_request_id")),
            tool_request_id=safe_identifier(values.get("tool_request_id")),
            state_revision=revision,
        )


def _safe_correlation(value: Correlation | None) -> Correlation:
    if type(value) is not Correlation:
        return Correlation()
    return Correlation.from_values(
        request_id=value.request_id,
        run_id=value.run_id,
        model_request_id=value.model_request_id,
        tool_request_id=value.tool_request_id,
        state_revision=value.state_revision,
    )


@dataclass(frozen=True)
class StructuredEvent:
    timestamp: str
    name: str
    correlation: Correlation
    attributes: dict[str, Any]


@dataclass(frozen=True)
class MetricPoint:
    timestamp: str
    name: str
    kind: str
    value: float
    labels: dict[str, str]


@dataclass(frozen=True)
class SpanRecord:
    timestamp: str
    name: str
    duration_seconds: float
    status: str
    correlation: Correlation
    labels: dict[str, str]


@runtime_checkable
class TelemetrySink(Protocol):
    def emit_event(self, event: StructuredEvent) -> None: ...
    def emit_metric(self, metric: MetricPoint) -> None: ...
    def emit_span(self, span: SpanRecord) -> None: ...


class NullTelemetrySink:
    def emit_event(self, event: StructuredEvent) -> None:
        return None

    def emit_metric(self, metric: MetricPoint) -> None:
        return None

    def emit_span(self, span: SpanRecord) -> None:
        return None


class InMemoryTelemetrySink:
    """Bounded synthetic/test sink. Oldest records are dropped at capacity."""

    def __init__(self, max_records: int = 2048) -> None:
        if type(max_records) is not int or max_records < 1:
            raise ValueError("max_records must be a positive integer")
        self.max_records = max_records
        self._records: deque[tuple[str, Any]] = deque(maxlen=max_records)
        self._lock = threading.Lock()
        self._dropped = 0

    def _append(self, kind: str, value: Any) -> None:
        with self._lock:
            if len(self._records) == self.max_records:
                self._dropped += 1
            self._records.append((kind, value))

    def emit_event(self, event: StructuredEvent) -> None:
        self._append("event", event)

    def emit_metric(self, metric: MetricPoint) -> None:
        self._append("metric", metric)

    def emit_span(self, span: SpanRecord) -> None:
        self._append("span", span)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            records = list(self._records)
            return {"records": records, "dropped": self._dropped, "capacity": self.max_records}


class JsonLoggingSink:
    """Emit already-sanitized telemetry as compact JSON through stdlib logging."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger if logger is not None else logging.getLogger("manager_runtime")

    def _write(self, kind: str, value: Any) -> None:
        payload = {"telemetry_type": kind, **asdict(value)}
        self.logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))

    def emit_event(self, event: StructuredEvent) -> None:
        self._write("event", event)

    def emit_metric(self, metric: MetricPoint) -> None:
        self._write("metric", metric)

    def emit_span(self, span: SpanRecord) -> None:
        self._write("span", span)


class _BoundedSinkDispatcher:
    """Isolate potentially blocking sink I/O behind one bounded daemon worker."""

    def __init__(
        self,
        sink: TelemetrySink,
        *,
        max_pending: int,
        on_failure: Callable[[], None],
        on_drop: Callable[[], None],
    ) -> None:
        self._sink = sink
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=max_pending)
        self._on_failure = on_failure
        self._on_drop = on_drop
        self._start_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._started = False
        self._inflight_started: float | None = None

    @property
    def capacity(self) -> int:
        return self._queue.maxsize

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def inflight_seconds(self) -> float:
        with self._state_lock:
            started = self._inflight_started
        if started is None:
            return 0.0
        return max(0.0, time.monotonic() - started)

    def _ensure_started(self) -> None:
        if self._started:
            return
        with self._start_lock:
            if self._started:
                return
            thread = threading.Thread(
                target=self._run,
                name="manager-telemetry-sink",
                daemon=True,
            )
            thread.start()
            self._started = True

    def emit(self, kind: str, value: Any) -> None:
        try:
            self._ensure_started()
            self._queue.put_nowait((kind, value))
        except queue.Full:
            self._on_drop()
        except Exception:
            self._on_failure()

    def _run(self) -> None:
        while True:
            kind, value = self._queue.get()
            with self._state_lock:
                self._inflight_started = time.monotonic()
            try:
                if kind == "event":
                    self._sink.emit_event(value)
                elif kind == "metric":
                    self._sink.emit_metric(value)
                else:
                    self._sink.emit_span(value)
            except BaseException:
                self._on_failure()
            finally:
                with self._state_lock:
                    self._inflight_started = None
                self._queue.task_done()


class SafeTelemetry:
    """Provider-neutral, secret-minimizing telemetry facade.

    Sanitization and sink failures are contained. Exact built-in in-memory/null
    sinks are called synchronously; all other sinks are isolated through a
    bounded daemon dispatcher so a slow or hung telemetry backend cannot stall
    the primary operation or create unbounded worker threads.
    """

    def __init__(
        self,
        sink: TelemetrySink | None = None,
        *,
        sink_queue_size: int = _DEFAULT_SINK_QUEUE,
    ) -> None:
        if type(sink_queue_size) is not int or sink_queue_size < 1:
            raise ValueError("sink_queue_size must be a positive integer")
        self.sink: TelemetrySink = sink if sink is not None else NullTelemetrySink()
        self._failure_lock = threading.Lock()
        self._sink_failures = 0
        self._sink_dropped = 0
        self._sanitization_failures = 0
        self._label_cardinality_overflows = 0
        self._label_values: dict[str, set[str]] = {}
        self._dispatcher: _BoundedSinkDispatcher | None = None
        if type(self.sink) not in {NullTelemetrySink, InMemoryTelemetrySink}:
            self._dispatcher = _BoundedSinkDispatcher(
                self.sink,
                max_pending=sink_queue_size,
                on_failure=self._sink_failed,
                on_drop=self._sink_dropped_one,
            )

    @property
    def sink_failures(self) -> int:
        with self._failure_lock:
            return self._sink_failures

    @property
    def sink_dropped(self) -> int:
        with self._failure_lock:
            return self._sink_dropped

    @property
    def sink_pending(self) -> int:
        return self._dispatcher.pending if self._dispatcher is not None else 0

    @property
    def sink_queue_capacity(self) -> int:
        return self._dispatcher.capacity if self._dispatcher is not None else 0

    @property
    def sink_inflight_seconds(self) -> float:
        return self._dispatcher.inflight_seconds if self._dispatcher is not None else 0.0

    @property
    def sanitization_failures(self) -> int:
        with self._failure_lock:
            return self._sanitization_failures

    @property
    def label_cardinality_overflows(self) -> int:
        with self._failure_lock:
            return self._label_cardinality_overflows

    def _sink_failed(self) -> None:
        with self._failure_lock:
            self._sink_failures += 1

    def _sink_dropped_one(self) -> None:
        with self._failure_lock:
            self._sink_dropped += 1

    def _sanitization_failed(self) -> None:
        with self._failure_lock:
            self._sanitization_failures += 1

    def _bound_label_cardinality(self, labels: dict[str, str]) -> dict[str, str]:
        if not labels:
            return labels
        bounded: dict[str, str] = {}
        with self._failure_lock:
            for key, value in labels.items():
                seen = self._label_values.setdefault(key, set())
                if value in seen:
                    bounded[key] = value
                elif len(seen) < _LABEL_CARDINALITY_LIMIT:
                    seen.add(value)
                    bounded[key] = value
                else:
                    bounded[key] = _LABEL_OVERFLOW
                    self._label_cardinality_overflows += 1
        return bounded

    def _safe_attributes(self, attributes: Mapping[str, Any] | None) -> dict[str, Any]:
        try:
            safe_attributes = redact(attributes if attributes is not None else {})
        except Exception:
            self._sanitization_failed()
            return {"telemetry_sanitization": "failed"}
        if not isinstance(safe_attributes, dict):
            return {"value": safe_attributes}
        return safe_attributes

    def _safe_labels(self, labels: Mapping[str, Any] | None) -> dict[str, str]:
        try:
            sanitized = safe_labels(labels)
        except Exception:
            self._sanitization_failed()
            return {}
        return self._bound_label_cardinality(sanitized)

    def _emit(self, kind: str, value: Any) -> None:
        if self._dispatcher is not None:
            self._dispatcher.emit(kind, value)
            return
        try:
            if kind == "event":
                self.sink.emit_event(value)
            elif kind == "metric":
                self.sink.emit_metric(value)
            else:
                self.sink.emit_span(value)
        except Exception:
            self._sink_failed()

    def event(
        self,
        name: str,
        *,
        correlation: Correlation | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        if type(name) is not str or not _SAFE_EVENT_NAME.fullmatch(name):
            name = "telemetry.invalid_event_name"
        event = StructuredEvent(
            _now(), name, _safe_correlation(correlation), self._safe_attributes(attributes)
        )
        self._emit("event", event)

    def metric(self, name: str, kind: str, value: float, *, labels: Mapping[str, Any] | None = None) -> None:
        if kind not in {"counter", "gauge", "histogram"}:
            raise ValueError("metric kind must be counter, gauge, or histogram")
        if type(value) not in {int, float} or not math.isfinite(float(value)):
            raise ValueError("metric value must be a finite number")
        safe_name = name if type(name) is str and _SAFE_METRIC_NAME.fullmatch(name) else "manager_invalid_metric"
        point = MetricPoint(_now(), safe_name, kind, float(value), self._safe_labels(labels))
        self._emit("metric", point)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        correlation: Correlation | None = None,
        labels: Mapping[str, Any] | None = None,
    ) -> Iterator[None]:
        started = time.monotonic()
        status = "completed"
        try:
            yield
        except BaseException:
            status = "failed"
            raise
        finally:
            safe_name = name if type(name) is str and _SAFE_EVENT_NAME.fullmatch(name) else "telemetry.invalid_span_name"
            span = SpanRecord(
                _now(), safe_name, max(0.0, time.monotonic() - started), status,
                _safe_correlation(correlation), self._safe_labels(labels),
            )
            self._emit("span", span)
