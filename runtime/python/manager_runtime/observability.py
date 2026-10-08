from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9:._-]{1,128}$")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*")
_SECRETISH = re.compile(r"(?i)\b(?:sk|pk|api)[-_][A-Za-z0-9_-]{12,}\b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if _SAFE_IDENTIFIER.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:20]
    return f"hash:{digest}"


def _sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS)


def redact(value: Any, *, max_depth: int = 6, max_string_chars: int = 256) -> Any:
    """Return a bounded telemetry-safe representation.

    This is intentionally lossy. Telemetry is not a debugging dump channel.
    """
    def visit(current: Any, depth: int) -> Any:
        if depth > max_depth:
            return "[TRUNCATED_DEPTH]"
        if current is None or isinstance(current, (bool, int, float)):
            return current
        if isinstance(current, str):
            text = _BEARER.sub("Bearer [REDACTED]", current)
            text = _SECRETISH.sub(_REDACTED, text)
            if len(text) > max_string_chars:
                return text[:max_string_chars] + "...[TRUNCATED]"
            return text
        if isinstance(current, Mapping):
            result: dict[str, Any] = {}
            for key, item in list(current.items())[:64]:
                skey = str(key)[:96]
                result[skey] = _REDACTED if _sensitive_key(key) else visit(item, depth + 1)
            if len(current) > 64:
                result["_truncated_items"] = len(current) - 64
            return result
        if isinstance(current, (list, tuple, set, frozenset)):
            items = list(current)
            result = [visit(item, depth + 1) for item in items[:64]]
            if len(items) > 64:
                result.append(f"[TRUNCATED_ITEMS:{len(items) - 64}]")
            return result
        return f"<{type(current).__name__}>"

    return visit(value, 0)


def safe_labels(labels: Mapping[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in (labels or {}).items():
        if key not in _SAFE_LABEL_KEYS:
            continue
        text = str(value)
        if len(text) > 64 or not re.fullmatch(r"[A-Za-z0-9:._/-]+", text):
            text = f"hash:{hashlib.sha256(text.encode()).hexdigest()[:12]}"
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
        if not isinstance(revision, int) or isinstance(revision, bool):
            revision = None
        return cls(
            request_id=safe_identifier(values.get("request_id")),
            run_id=safe_identifier(values.get("run_id")),
            model_request_id=safe_identifier(values.get("model_request_id")),
            tool_request_id=safe_identifier(values.get("tool_request_id")),
            state_revision=revision,
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
        self.logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))

    def emit_event(self, event: StructuredEvent) -> None:
        self._write("event", event)

    def emit_metric(self, metric: MetricPoint) -> None:
        self._write("metric", metric)

    def emit_span(self, span: SpanRecord) -> None:
        self._write("span", span)


class SafeTelemetry:
    """Provider-neutral, secret-minimizing telemetry facade.

    Sink failures are swallowed and counted locally. Observability is never an
    authority source and its failure cannot authorize, mutate, or deadlock work.
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
        if not _SAFE_EVENT_NAME.fullmatch(name):
            name = "telemetry.invalid_event_name"
        event = StructuredEvent(_now(), name, correlation or Correlation(), redact(dict(attributes or {})))
        try:
            self.sink.emit_event(event)
        except Exception:
            self._failed()

    def metric(self, name: str, kind: str, value: float, *, labels: Mapping[str, Any] | None = None) -> None:
        if kind not in {"counter", "gauge", "histogram"}:
            raise ValueError("metric kind must be counter, gauge, or histogram")
        point = MetricPoint(_now(), name[:96], kind, float(value), safe_labels(labels))
        try:
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
            span = SpanRecord(
                _now(), name[:96], max(0.0, time.monotonic() - started), status,
                correlation or Correlation(), safe_labels(labels),
            )
            try:
                self.sink.emit_span(span)
            except Exception:
                self._failed()
