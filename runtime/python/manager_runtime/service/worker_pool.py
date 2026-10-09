from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

_Task = tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]
_STOP = object()


class BoundedDaemonWorkerPool:
    """Small daemon worker pool with a bounded shutdown wait."""

    def __init__(
        self,
        *,
        max_workers: int,
        max_pending: int,
        shutdown_timeout_seconds: float,
        thread_name_prefix: str,
    ) -> None:
        if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending < max_workers:
            raise ValueError("max_pending must be an integer at least as large as max_workers")
        if (
            isinstance(shutdown_timeout_seconds, bool)
            or not isinstance(shutdown_timeout_seconds, (int, float))
            or shutdown_timeout_seconds <= 0
        ):
            raise ValueError("shutdown_timeout_seconds must be positive")
        if not isinstance(thread_name_prefix, str) or not thread_name_prefix:
            raise ValueError("thread_name_prefix must be non-empty text")

        self._shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self._tasks: queue.Queue[_Task | object] = queue.Queue(maxsize=max_pending)
        self._condition = threading.Condition()
        self._unfinished = 0
        self._closing = False
        self._started = False
        self._stops_sent = False
        self._threads = tuple(
            threading.Thread(
                target=self._worker,
                name=f"{thread_name_prefix}-{index}",
                daemon=True,
            )
            for index in range(max_workers)
        )

    def _start_workers(self) -> None:
        if self._started:
            return
        self._started = True
        for thread in self._threads:
            thread.start()

    def submit(self, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
        if not callable(function):
            raise TypeError("submitted work must be callable")
        with self._condition:
            if self._closing:
                raise RuntimeError("worker pool is shutting down")
            self._start_workers()
            self._unfinished += 1
        try:
            self._tasks.put_nowait((function, args, kwargs))
        except BaseException:
            with self._condition:
                self._unfinished -= 1
                self._condition.notify_all()
            raise

    def _worker(self) -> None:
        while True:
            task = self._tasks.get()
            if task is _STOP:
                return
            function, args, kwargs = task
            try:
                function(*args, **kwargs)
            except BaseException:
                pass
            finally:
                with self._condition:
                    self._unfinished -= 1
                    self._condition.notify_all()

    def shutdown(self, *, wait: bool = True) -> bool:
        with self._condition:
            self._closing = True
            if wait and self._unfinished:
                deadline = time.monotonic() + self._shutdown_timeout_seconds
                while self._unfinished:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
            drained = self._unfinished == 0
            send_stops = drained and self._started and not self._stops_sent
            if send_stops:
                self._stops_sent = True

        if send_stops:
            for _ in self._threads:
                self._tasks.put_nowait(_STOP)
        return drained
