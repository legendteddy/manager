from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import Any, Iterator, Mapping, Protocol, runtime_checkable

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash_text(text: str) -> str:
    hasher = hashlib.sha256()
    for offset in range(0, len(text), 4096):
        hasher.update(text[offset : offset + 4096].encode("utf-8", errors="replace"))
    return hasher.hexdigest()


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


def redact(value: Any, *, max_depth: int = 6, max_string_chars: int = 256) -> Any:
    """Return a bounded telemetry-safe representation.

    Traversal and retained output are both bounded. Only concrete built-in
    containers are traversed so observability never executes application-owned
    iterator or mapping hooks. Telemetry is not a debugging dump channel.
    """
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 0:
        raise ValueError("max_depth must be a non-negative integer")
    if not isinstance(max_string_chars, int) or isinstance(max_string_chars, bool) or max_string_chars < 1:
        raise ValueError("max_string_chars must be a positive integer")

    def visit(current: Any, depth: int) -> Any:
        if depth > max_depth:
            return "[TRUNCATED_DEPTH]"
        if current is None or type(current) in (bool, int):
            return current
        if type(current) is float:
            return current if math.isfinite(current) else "[NON_FINITE_NUMBER]"
        if type(current) is str:
            bounded = current[: max_string_chars + 128]
            text = _BEARER.sub("Bearer [REDACTED]", bounded)
            text = _SECRETISH.sub(_REDACTED, text)
            if len(current) > max_string_chars or len(text) > max_string_chars:
                return text[:max_string_chars] + "...[TRUNCATED]"
            return text
        if type(current) is dict:
            result: dict[str, Any] = {}
            entries = list(islice(current.items(), 65))
            for key, item in entries[:64]:
                skey = _safe_key(key)
                result[skey] = _REDACTED if _sensitive_key(key) else visit(item, depth + 1)
            if len(entries) > 64:
                result["_truncated_items"] = True
            return result
        if type(current) in (list, tuple):
            items = current[:65]
            result = [visit(item, depth + 1) for item in items[:64]]
            if len(items) > 64:
                result.append("[TRUNCATED_ITEMS]")
            return result
        if type(current) in (set, frozenset):
            items = list(islice(current, 65))
            result = [visit(item, depth + 1) for item in items[:64]]
            if len(items) > 64:
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
    if type(labels) is not dict:
        return {}
    result: dict[str, str] = {}
    for key, value in labels.items():
        if type(key) is not str or key not in _SAFE_LABEL_KEYS:
            continue
        text = _safe_label_value(value)
        if len(text) > 64 or not re.fullmatch(r"[A-Za-z0-9:._/-]+", text):
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
    if not isinstance(value, Correlation):
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
        if not isinstance(max_records, int) or isinstance(max_records, bool) or max_records < 1:
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
        self.logger = logger or logging.getLogger("manager_runtime")

    def _write(self, kind: str, value: Any) -> None:
        payload = {"telemetry_type": kind, **asdict(value)}
        self.logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))

    def emit_event(self, event: StructuredEvent) -> None:
        self._write("event", event)

    def emit_metric(self, metric: MetricPoint) -> None:
        self._write("metric", metric)

    def emit_span(self, span: SpanRecord) -> None:
        self._write("span", span)


class SafeTelemetry:
    """Provider-neutral, secret-minimizing telemetry facade.

    Sink and sanitization failures are swallowed and counted locally.
    Observability is never an authority source and its failure cannot
    authorize, mutate, or deadlock work.
    """

    def __init__(self, sink: TelemetrySink | None = None) -> None:
        self.sink: TelemetrySink = sink or NullTelemetrySink()
        self._failure_lock = threading.Lock()
        self._sink_failures = 0

    @property
    def sink_failures(self) -> int:
        with self._failure_lock:
            return self._sink_failures

    def _failed(self) -> None:
        with self._failure_lock:
            self._sink_failures += 1

    def event(
        self,
        name: str,
        *,
        correlation: Correlation | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            safe_name = name if type(name) is str and _SAFE_EVENT_NAME.fullmatch(name) else "telemetry.invalid_event_name"
            source_attributes = {} if attributes is None else attributes
            safe_attributes = redact(source_attributes)
            if not isinstance(safe_attributes, dict):
                safe_attributes = {"value": safe_attributes}
            event = StructuredEvent(
                _now(), safe_name, _safe_correlation(correlation), safe_attributes
            )
            self.sink.emit_event(event)
        except Exception:
            self._failed()

    def metric(self, name: str, kind: str, value: float, *, labels: Mapping[str, Any] | None = None) -> None:
        if kind not in {"counter", "gauge", "histogram"}:
            raise ValueError("metric kind must be counter, gauge, or histogram")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError("metric value must be a finite number")
        try:
            safe_name = name if type(name) is str and _SAFE_METRIC_NAME.fullmatch(name) else "manager_invalid_metric"
            point = MetricPoint(_now(), safe_name, kind, float(value), safe_labels(labels))
            self.sink.emit_metric(point)
        except Exception:
            self._failed()

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
            try:
                safe_name = name if type(name) is str and _SAFE_EVENT_NAME.fullmatch(name) else "telemetry.invalid_span_name"
                span = SpanRecord(
                    _now(), safe_name, max(0.0, time.monotonic() - started), status,
                    _safe_correlation(correlation), safe_labels(labels),
                )
                self.sink.emit_span(span)
            except Exception:
                self._failed()
