from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Type

from manager_runtime.mcp import MCPBoundaryError, OfficialMCPClient, register_mcp_bindings
from manager_runtime.tools import ToolRegistry, execute_tool_request

MCP_AVAILABLE = (
    importlib.util.find_spec("mcp") is not None
    and importlib.util.find_spec("uvicorn") is not None
)
SERVER = Path(__file__).parent / "fixtures" / "synthetic_mcp_http_server.py"


def echo_schema() -> dict:
    return {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }


def echo_binding() -> dict:
    return {
        "server_id": "synthetic-http",
        "remote_tool_name": "echo",
        "local_definition": {
            "name": "mcp_http_echo",
            "description": "Synthetic locally governed MCP HTTP echo tool.",
            "side_effect_class": "read",
            "input_schema": echo_schema(),
            "requires_verification": False,
            "sensitive_output": False,
        },
    }


def task() -> dict:
    return {
        "classification": {
            "materiality": "routine",
            "consequence": "low",
            "uncertainty": "low",
            "reversibility": "reversible",
            "sensitivity": "public",
        }
    }


def request(value: str = "hello") -> dict:
    return {
        "request_id": "http-transport-request",
        "run_id": "http-transport-run",
        "tool_name": "mcp_http_echo",
        "arguments": {"value": value},
        "proposed_by": "system",
    }


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class QuietHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _drain(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        if length:
            self.rfile.read(length)


class CrossOriginRedirectHandler(QuietHandler):
    target_url = ""

    def do_POST(self) -> None:
        self._drain()
        self.send_response(307)
        self.send_header("Location", self.target_url)
        self.send_header("Content-Length", "0")
        self.end_headers()


class MalformedJSONHandler(QuietHandler):
    def do_POST(self) -> None:
        self._drain()
        body = b"{"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


class StubHTTPServer:
    def __init__(self, handler: Type[BaseHTTPRequestHandler]) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        port = int(self.server.server_address[1])
        return f"http://127.0.0.1:{port}/mcp"

    def __enter__(self) -> "StubHTTPServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@unittest.skipUnless(MCP_AVAILABLE, "optional MCP dependency is not installed")
class MCPStreamableHTTPConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        directory = Path(self.tempdir.name)
        self.state_path = directory / "state.json"
        self.log_path = directory / "calls.log"
        self.port = free_port()
        self.process: subprocess.Popen[str] | None = None
        self.write_state()
        self.start_server()

    def tearDown(self) -> None:
        self.stop_server()
        self.tempdir.cleanup()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def write_state(
        self,
        *,
        schema_version: int = 1,
        enabled_tools: list[str] | None = None,
    ) -> None:
        value = {
            "schema_version": schema_version,
            "enabled_tools": enabled_tools
            if enabled_tools is not None
            else ["echo", "slow", "fail", "headers"],
        }
        self.state_path.write_text(json.dumps(value), encoding="utf-8")

    def read_log(self) -> list[str]:
        if not self.log_path.exists():
            return []
        return self.log_path.read_text(encoding="utf-8").splitlines()

    def clear_log(self) -> None:
        self.log_path.write_text("", encoding="utf-8")

    def start_server(self) -> None:
        if self.process is not None:
            raise RuntimeError("synthetic HTTP server is already running")
        env = os.environ.copy()
        env.update(
            {
                "MANAGER_MCP_HTTP_TEST_STATE": str(self.state_path),
                "MANAGER_MCP_HTTP_TEST_LOG": str(self.log_path),
                "MANAGER_MCP_HTTP_TEST_PORT": str(self.port),
            }
        )
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate(timeout=1)
                self.process = None
                self.fail(
                    "synthetic MCP HTTP server exited during startup: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        self.stop_server()
        self.fail("synthetic MCP HTTP server did not become ready")

    def stop_server(self) -> None:
        if self.process is None:
            return
        process = self.process
        self.process = None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    def client(self, *, timeout: float | None = 3.0) -> OfficialMCPClient:
        return OfficialMCPClient(
            "synthetic-http",
            self.url,
            operation_timeout_seconds=timeout,
        )

    def test_official_sdk_streamable_http_discovery_and_result_normalization(self) -> None:
        client = self.client()
        discovered = {item["name"]: item for item in client.list_tools()}
        self.assertEqual({"echo", "slow", "fail", "headers"}, set(discovered))
        self.assertEqual(echo_schema(), discovered["echo"]["input_schema"])

        output = client.call_tool("echo", {"value": "http-ok"})
        self.assertEqual({"value": "http-ok"}, output)
        self.assertEqual(["start:echo", "finish:echo"], self.read_log())

    def test_governed_registry_executes_over_real_streamable_http(self) -> None:
        registry = ToolRegistry()
        register_mcp_bindings(registry, self.client(), [echo_binding()])

        result = execute_tool_request(
            task(),
            request("governed-http"),
            registry,
            {"scope_authorized": True},
        )

        self.assertEqual("executed", result["status"])
        self.assertEqual({"value": "governed-http"}, result["output"])
        self.assertEqual(["start:echo", "finish:echo"], self.read_log())

    def test_schema_drift_after_registration_fails_before_remote_http_call(self) -> None:
        registry = ToolRegistry()
        register_mcp_bindings(registry, self.client(), [echo_binding()])
        self.write_state(schema_version=2)
        self.clear_log()

        result = execute_tool_request(
            task(),
            request("blocked"),
            registry,
            {"scope_authorized": True},
        )

        self.assertEqual("failed", result["status"])
        self.assertEqual("tool_execution_failed", result["decision_reason"])
        self.assertEqual("MCPBoundaryError", result["error"])
        self.assertEqual([], self.read_log())

    def test_server_restart_recovers_without_reregistering_binding(self) -> None:
        registry = ToolRegistry()
        register_mcp_bindings(registry, self.client(timeout=1.0), [echo_binding()])
        self.stop_server()
        self.clear_log()

        unavailable = execute_tool_request(
            task(),
            request("offline"),
            registry,
            {"scope_authorized": True},
        )
        self.assertEqual("failed", unavailable["status"])
        self.assertEqual([], self.read_log())

        self.start_server()
        recovered = execute_tool_request(
            task(),
            request("online-again"),
            registry,
            {"scope_authorized": True},
        )
        self.assertEqual("executed", recovered["status"])
        self.assertEqual({"value": "online-again"}, recovered["output"])

    def test_timeout_cancels_slow_http_request_and_next_call_reconnects(self) -> None:
        client = self.client(timeout=1.5)

        with self.assertRaisesRegex(MCPBoundaryError, "timed out"):
            client.call_tool("slow", {"delay_ms": 5000})

        log = self.read_log()
        self.assertIn("start:slow", log)
        self.assertNotIn("finish:slow", log)

        output = client.call_tool("echo", {"value": "after-timeout"})
        self.assertEqual({"value": "after-timeout"}, output)
        self.assertIn("finish:echo", self.read_log())

    def test_mcp_error_result_is_normalized_over_http(self) -> None:
        with self.assertRaisesRegex(MCPBoundaryError, "returned an error result"):
            self.client().call_tool("fail", {})

    def test_same_origin_method_preserving_redirect_is_followed(self) -> None:
        redirected = OfficialMCPClient(
            "synthetic-http",
            f"http://127.0.0.1:{self.port}/redirect-mcp",
            operation_timeout_seconds=3.0,
        )
        names = {item["name"] for item in redirected.list_tools()}
        self.assertIn("echo", names)

    def test_cross_origin_redirect_is_rejected(self) -> None:
        handler = type(
            "ConfiguredCrossOriginRedirectHandler",
            (CrossOriginRedirectHandler,),
            {"target_url": self.url},
        )
        with StubHTTPServer(handler) as stub:
            client = OfficialMCPClient(
                "redirect-stub",
                stub.url,
                operation_timeout_seconds=3.0,
            )
            with self.assertRaisesRegex(MCPBoundaryError, "[Rr]edirect"):
                client.list_tools()

    def test_custom_http_header_configuration_stays_external_to_manager_contracts(self) -> None:
        import anyio
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        async def run() -> dict | None:
            http_client = httpx2.AsyncClient(
                headers={"X-Manager-Test": "stage12-synthetic-header"},
                timeout=httpx2.Timeout(3, read=3),
            )
            async with http_client:
                transport = streamable_http_client(self.url, http_client=http_client)
                async with Client(transport) as client:
                    await client.list_tools()
                    result = await client.call_tool("headers", {})
                    return result.structured_content

        output = anyio.run(run)
        self.assertEqual(
            {"x_manager_test": "stage12-synthetic-header"},
            output,
        )

    def test_malformed_http_response_fails_closed(self) -> None:
        with StubHTTPServer(MalformedJSONHandler) as stub:
            client = OfficialMCPClient(
                "malformed-stub",
                stub.url,
                operation_timeout_seconds=3.0,
            )
            with self.assertRaises(MCPBoundaryError):
                client.list_tools()


if __name__ == "__main__":
    unittest.main()
