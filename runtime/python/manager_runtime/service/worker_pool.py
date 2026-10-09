from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

_Task = tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]
_STOP = object()


class BoundedDaemonWorkerPool:
    """Small daemon worker pool with a truthful bounded shutdown wait.

    The service process must be able to leave an uncooperative request behind
    after its configured drain budget expires. Standard ThreadPoolExecutor
    workers are joined during interpreter shutdown, so wait=False alone cannot
    provide that process-exit guarantee.
    """

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
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending < 1:
            raise ValueError("max_pending must be a positive integer")
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
        self._threads = tuple(
            threading.Thread(
                target=self._worker,
                name=f"{thread_name_prefix}-{index}",
                daemon=True,
            )
            for index in range(max_workers)
        )
        for thread in self._threads:
            thread.start()

    def submit(self, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
        if not callable(function):
            raise TypeError("submitted work must be callable")
        with self._condition:
            if self._closing:
                raise RuntimeError("worker pool is shutting down")
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
                # HTTP request workers own their error reporting. Keep this
                # final containment boundary from killing capacity permanently.
                pass
            finally:
                with self._condition:
                    self._unfinished -= 1
                    self._condition.notify_all()

    def shutdown(self, *, wait: bool = True) -> bool:
        """Stop accepting work and wait no longer than the configured budget.

        Returns True when all submitted work drained inside the budget. On a
        timeout, daemon workers may still be inside already-accepted work, but
        they cannot keep interpreter shutdown alive.
        """
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

        if drained:
            for _ in self._threads:
                try:
                    self._tasks.put_nowait(_STOP)
                except queue.Full:
                    break
            if wait:
                for thread in self._threads:
                    thread.join(timeout=0.1)
        return drained
