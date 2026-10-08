from __future__ import annotations

import json
import math
import queue
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


class CapacityError(RuntimeError):
    """Base class for deterministic resource-bound failures."""


class OverloadedError(CapacityError):
    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"capacity exhausted: {resource}")


class CheckpointTooLarge(CapacityError):
    pass


class _SizeLimitExceeded(Exception):
    pass


def _positive(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _checked(total: int, limit: int) -> int:
    if total > limit:
        raise _SizeLimitExceeded
    return total


def _json_string_size(value: str, limit: int) -> int:
    """Count ensure_ascii=False JSON bytes without building the encoded string."""
    total = 2  # quotes
    _checked(total, limit)
    for char in value:
        if char in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
            size = 2
        elif ord(char) < 0x20:
            size = 6
        else:
            try:
                size = len(char.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ValueError("checkpoint contains text that cannot be encoded as UTF-8") from exc
        total = _checked(total + size, limit)
    return total


def _integer_text(value: int, limit: int) -> str:
    bits = abs(value).bit_length()
    estimated_digits = max(1, int(bits * math.log10(2)) + 1)
    estimated = estimated_digits + (1 if value < 0 else 0)
    _checked(estimated, limit)
    try:
        return str(value)
    except ValueError as exc:
        raise ValueError("checkpoint integer exceeds the runtime conversion limit") from exc


def _json_key_text(value: Any, limit: int) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return _integer_text(value, limit)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("checkpoint dictionary key must be finite")
        return json.dumps(value, allow_nan=False)
    raise TypeError("checkpoint dictionary keys must be JSON-compatible scalars")


def _bounded_json_size(value: Any, limit: int) -> int:
    """Count JSON bytes and stop traversal as soon as the limit is exceeded."""
    seen: set[int] = set()

    def count(current: Any, budget: int, depth: int) -> int:
        if depth > 128:
            raise ValueError("checkpoint nesting exceeds the safe depth limit")
        if current is None:
            return _checked(4, budget)
        if current is True:
            return _checked(4, budget)
        if current is False:
            return _checked(5, budget)
        if isinstance(current, int) and not isinstance(current, bool):
            return _checked(len(_integer_text(current, budget)), budget)
        if isinstance(current, float):
            if not math.isfinite(current):
                raise ValueError("checkpoint numbers must be finite")
            return _checked(len(json.dumps(current, allow_nan=False)), budget)
        if isinstance(current, str):
            return _json_string_size(current, budget)

        if isinstance(current, dict):
            identity = id(current)
            if identity in seen:
                raise ValueError("checkpoint contains a reference cycle")
            seen.add(identity)
            try:
                total = _checked(2, budget)  # braces
                first = True
                for key, item in current.items():
                    if not first:
                        total = _checked(total + 1, budget)
                    first = False
                    key_text = _json_key_text(key, budget - total)
                    total = _checked(total + _json_string_size(key_text, budget - total), budget)
                    total = _checked(total + 1, budget)  # colon
                    total = _checked(total + count(item, budget - total, depth + 1), budget)
                return total
            finally:
                seen.remove(identity)

        if isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in seen:
                raise ValueError("checkpoint contains a reference cycle")
            seen.add(identity)
            try:
                total = _checked(2, budget)  # brackets
                first = True
                for item in current:
                    if not first:
                        total = _checked(total + 1, budget)
                    first = False
                    total = _checked(total + count(item, budget - total, depth + 1), budget)
                return total
            finally:
                seen.remove(identity)

        raise TypeError(f"checkpoint contains unsupported type: {type(current).__name__}")

    return count(value, limit, 0)


@dataclass(frozen=True)
class CapacityLimits:
    active_runs: int = 64
    queued_runs: int = 256
    model_concurrency: int = 16
    tool_concurrency: int = 32
    mcp_concurrency: int = 16
    state_concurrency: int = 32
    telemetry_buffer: int = 2048
    checkpoint_max_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            _positive(name, value)


class CapacityGate:
    def __init__(self, name: str, limit: int) -> None:
        self.name = name
        self.limit = _positive("limit", limit)
        self._semaphore = threading.BoundedSemaphore(limit)
        self._lock = threading.Lock()
        self._active = 0
        self._rejected = 0

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    @property
    def rejected(self) -> int:
        with self._lock:
            return self._rejected

    @contextmanager
    def acquire(self) -> Iterator[None]:
        if not self._semaphore.acquire(blocking=False):
            with self._lock:
                self._rejected += 1
            raise OverloadedError(self.name)
        with self._lock:
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
            self._semaphore.release()


class BoundedWorkQueue:
    """Small queue primitive for service workers; never grows without bound."""

    def __init__(self, maxsize: int) -> None:
        self.maxsize = _positive("maxsize", maxsize)
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
        self._rejected = 0
        self._lock = threading.Lock()

    def put_nowait(self, item: Any) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full as exc:
            with self._lock:
                self._rejected += 1
            raise OverloadedError("run_queue") from exc

    def get_nowait(self) -> Any:
        try:
            return self._queue.get_nowait()
        except queue.Empty as exc:
            raise LookupError("run queue is empty") from exc

    def task_done(self) -> None:
        self._queue.task_done()

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def rejected(self) -> int:
        with self._lock:
            return self._rejected


class CapacityManager:
    def __init__(self, limits: CapacityLimits | None = None) -> None:
        self.limits = limits or CapacityLimits()
        self.runs = CapacityGate("active_runs", self.limits.active_runs)
        self.models = CapacityGate("model_concurrency", self.limits.model_concurrency)
        self.tools = CapacityGate("tool_concurrency", self.limits.tool_concurrency)
        self.mcp = CapacityGate("mcp_concurrency", self.limits.mcp_concurrency)
        self.state = CapacityGate("state_concurrency", self.limits.state_concurrency)
        self.queue = BoundedWorkQueue(self.limits.queued_runs)

    def gate(self, kind: str) -> CapacityGate:
        mapping = {
            "run": self.runs,
            "model": self.models,
            "tool": self.tools,
            "mcp": self.mcp,
            "state": self.state,
        }
        try:
            return mapping[kind]
        except KeyError as exc:
            raise ValueError(f"unknown capacity kind: {kind}") from exc

    def assert_checkpoint_size(self, state: Any) -> int:
        try:
            return _bounded_json_size(state, self.limits.checkpoint_max_bytes)
        except _SizeLimitExceeded as exc:
            raise CheckpointTooLarge(
                f"checkpoint exceeds configured limit (> {self.limits.checkpoint_max_bytes} bytes)"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise CheckpointTooLarge("checkpoint cannot be safely serialized") from exc

    def snapshot(self) -> dict[str, Any]:
        return {
            "limits": dict(self.limits.__dict__),
            "active": {
                "runs": self.runs.active,
                "models": self.models.active,
                "tools": self.tools.active,
                "mcp": self.mcp.active,
                "state": self.state.active,
            },
            "queue_depth": self.queue.depth,
            "rejected": {
                "runs": self.runs.rejected,
                "models": self.models.rejected,
                "tools": self.tools.rejected,
                "mcp": self.mcp.rejected,
                "state": self.state.rejected,
                "queue": self.queue.rejected,
            },
        }
