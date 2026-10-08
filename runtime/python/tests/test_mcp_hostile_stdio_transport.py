from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from manager_runtime.mcp import MCPBoundaryError, OfficialMCPClient

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None
SERVER = Path(__file__).parent / "fixtures" / "hostile_mcp_stdio_server.py"
SECRET = "synthetic-hostile-secret-do-not-log"


@unittest.skipUnless(MCP_AVAILABLE, "optional MCP dependency is not installed")
class HostileMCPStdioTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        from mcp import StdioServerParameters

        self.StdioServerParameters = StdioServerParameters
        self.tempdir = tempfile.TemporaryDirectory()
        self.pid_path = Path(self.tempdir.name) / "child.pid"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def client(self, mode: str, *, timeout: float = 0.5) -> OfficialMCPClient:
        params = self.StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER)],
            env={
                "MANAGER_HOSTILE_MCP_MODE": mode,
                "MANAGER_HOSTILE_MCP_PID": str(self.pid_path),
            },
        )
        return OfficialMCPClient(
            f"hostile-{mode}",
            params,
            operation_timeout_seconds=timeout,
        )

    def read_pid(self) -> int:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if self.pid_path.exists():
                return int(self.pid_path.read_text(encoding="utf-8"))
            time.sleep(0.02)
        self.fail("hostile child did not publish a pid")

    def assert_process_reaped(self, pid: int) -> None:
        if os.name == "nt":
            self.skipTest("POSIX process liveness assertion")
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                self.fail("cannot verify hostile child process liveness")
            time.sleep(0.02)
        self.fail("hostile MCP child process remained alive after boundary failure")

    def test_hanging_child_times_out_and_is_reaped(self) -> None:
        client = self.client("hang")
        with self.assertRaisesRegex(MCPBoundaryError, "timed out"):
            client.list_tools()
        pid = self.read_pid()
        self.assert_process_reaped(pid)

    def test_stderr_flood_is_not_exposed_and_child_is_reaped(self) -> None:
        client = self.client("stderr_flood")
        with self.assertRaises(MCPBoundaryError) as raised:
            client.list_tools()
        message = str(raised.exception)
        self.assertNotIn(SECRET, message)
        self.assertNotIn("AUTHORIZATION", message)
        pid = self.read_pid()
        self.assert_process_reaped(pid)

    def test_crash_detail_is_redacted(self) -> None:
        client = self.client("crash", timeout=2.0)
        with self.assertRaises(MCPBoundaryError) as raised:
            client.list_tools()
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertNotIn("crash detail", str(raised.exception))
        pid = self.read_pid()
        self.assert_process_reaped(pid)

    def test_malformed_stdio_frame_fails_closed_without_payload_echo(self) -> None:
        client = self.client("malformed")
        with self.assertRaises(MCPBoundaryError) as raised:
            client.list_tools()
        message = str(raised.exception)
        self.assertNotIn('"jsonrpc"', message)
        self.assertNotIn('"result"', message)
        pid = self.read_pid()
        self.assert_process_reaped(pid)


if __name__ == "__main__":
    unittest.main()
