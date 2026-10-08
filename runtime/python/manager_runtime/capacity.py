from __future__ import annotations

import json
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


def _positive(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


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
            encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise CheckpointTooLarge("checkpoint cannot be safely serialized") from exc
        size = len(encoded)
        if size > self.limits.checkpoint_max_bytes:
            raise CheckpointTooLarge(
                f"checkpoint exceeds configured limit ({size} > {self.limits.checkpoint_max_bytes} bytes)"
            )
        return size

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
