from __future__ import annotations

import signal
import threading
from collections.abc import Callable


class GracefulShutdown:
    """Coordinates signal-driven service drain without owning service semantics."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None
        self._callbacks: list[Callable[[str], None]] = []
        self._installed = False

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def add_drain_callback(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            if self._event.is_set():
                reason = self._reason or "shutdown"
            else:
                self._callbacks.append(callback)
                return
        callback(reason)

    def request(self, reason: str = "shutdown") -> bool:
        normalized = reason.strip() or "shutdown"
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = normalized
            callbacks = tuple(self._callbacks)
            self._event.set()
        for callback in callbacks:
            callback(normalized)
        return True

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("signal handlers must be installed from the main thread")
        with self._lock:
            if self._installed:
                return
            self._installed = True

        def handler(signum: int, _frame: object) -> None:
            try:
                name = signal.Signals(signum).name
            except ValueError:
                name = str(signum)
            self.request(f"signal:{name}")

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)
