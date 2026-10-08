from __future__ import annotations

import signal
import unittest

from manager_runtime.deployment.lifecycle import GracefulShutdown


class GracefulShutdownTests(unittest.TestCase):
    def test_callbacks_run_once_and_reason_is_stable(self) -> None:
        shutdown = GracefulShutdown()
        reasons: list[str] = []
        shutdown.add_drain_callback(reasons.append)
        self.assertTrue(shutdown.request("signal:SIGTERM"))
        self.assertFalse(shutdown.request("signal:SIGINT"))
        self.assertEqual(reasons, ["signal:SIGTERM"])
        self.assertEqual(shutdown.reason, "signal:SIGTERM")
        self.assertTrue(shutdown.wait(0))

    def test_callback_added_after_request_runs_immediately(self) -> None:
        shutdown = GracefulShutdown()
        shutdown.request("shutdown")
        reasons: list[str] = []
        shutdown.add_drain_callback(reasons.append)
        self.assertEqual(reasons, ["shutdown"])

    def test_sigterm_handler_requests_graceful_shutdown(self) -> None:
        shutdown = GracefulShutdown()
        old_term = signal.getsignal(signal.SIGTERM)
        old_int = signal.getsignal(signal.SIGINT)
        try:
            shutdown.install_signal_handlers()
            signal.raise_signal(signal.SIGTERM)
            self.assertTrue(shutdown.requested)
            self.assertEqual(shutdown.reason, "signal:SIGTERM")
        finally:
            signal.signal(signal.SIGTERM, old_term)
            signal.signal(signal.SIGINT, old_int)
