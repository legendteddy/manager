from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from manager_runtime.mcp import MCPBoundaryError, OfficialMCPClient, register_mcp_bindings
from manager_runtime.tools import ToolRegistry, execute_tool_request

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None
SERVER = Path(__file__).parent / "fixtures" / "synthetic_mcp_server.py"


def echo_schema() -> dict:
    return {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }


def echo_binding() -> dict:
    return {
        "server_id": "synthetic-stdio",
        "remote_tool_name": "echo",
        "local_definition": {
            "name": "mcp_echo",
            "description": "Synthetic locally governed MCP echo tool.",
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
        "request_id": "transport-request",
        "run_id": "transport-run",
        "tool_name": "mcp_echo",
        "arguments": {"value": value},
        "proposed_by": "system",
    }


@unittest.skipUnless(MCP_AVAILABLE, "optional MCP dependency is not installed")
class MCPTransportConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        from mcp import StdioServerParameters

        self.tempdir = tempfile.TemporaryDirectory()
        directory = Path(self.tempdir.name)
        self.state_path = directory / "state.json"
        self.log_path = directory / "calls.log"
        self.write_state()
        self.params = StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER)],
            env={
                "MANAGER_MCP_TEST_STATE": str(self.state_path),
                "MANAGER_MCP_TEST_LOG": str(self.log_path),
            },
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

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
            else ["echo", "slow", "fail"],
        }
        self.state_path.write_text(json.dumps(value), encoding="utf-8")

    def read_log(self) -> list[str]:
        if not self.log_path.exists():
            return []
        return self.log_path.read_text(encoding="utf-8").splitlines()

    def clear_log(self) -> None:
        self.log_path.write_text("", encoding="utf-8")

    def client(self, *, timeout: float | None = None) -> OfficialMCPClient:
        return OfficialMCPClient(
            "synthetic-stdio",
            self.params,
            operation_timeout_seconds=timeout,
        )

    def test_official_sdk_stdio_discovery_and_result_normalization(self) -> None:
        client = self.client()
        discovered = {item["name"]: item for item in client.list_tools()}
        self.assertEqual({"echo", "slow", "fail"}, set(discovered))
        self.assertEqual(echo_schema(), discovered["echo"]["input_schema"])

        output = client.call_tool("echo", {"value": "transport-ok"})
        self.assertEqual({"value": "transport-ok"}, output)
        self.assertEqual(["start:echo", "finish:echo"], self.read_log())

    def test_governed_registry_executes_over_real_stdio_transport(self) -> None:
        registry = ToolRegistry()
        register_mcp_bindings(registry, self.client(), [echo_binding()])

        result = execute_tool_request(
            task(),
            request("governed"),
            registry,
            {"scope_authorized": True},
        )

        self.assertEqual("executed", result["status"])
        self.assertEqual({"value": "governed"}, result["output"])
        self.assertEqual(["start:echo", "finish:echo"], self.read_log())

    def test_schema_drift_after_registration_fails_before_remote_call(self) -> None:
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

    def test_disappearance_blocks_then_reconnect_succeeds_without_reregister(self) -> None:
        registry = ToolRegistry()
        register_mcp_bindings(registry, self.client(), [echo_binding()])
        self.write_state(enabled_tools=["slow", "fail"])
        self.clear_log()

        blocked = execute_tool_request(
            task(),
            request("missing"),
            registry,
            {"scope_authorized": True},
        )
        self.assertEqual("failed", blocked["status"])
        self.assertEqual("tool_execution_failed", blocked["decision_reason"])
        self.assertEqual("MCPBoundaryError", blocked["error"])
        self.assertEqual([], self.read_log())

        self.write_state()
        recovered = execute_tool_request(
            task(),
            request("back"),
            registry,
            {"scope_authorized": True},
        )
        self.assertEqual("executed", recovered["status"])
        self.assertEqual({"value": "back"}, recovered["output"])

    def test_timeout_cancels_slow_stdio_process_and_next_call_reconnects(self) -> None:
        client = self.client(timeout=2.0)

        with self.assertRaisesRegex(MCPBoundaryError, "timed out"):
            client.call_tool("slow", {"delay_ms": 5000})

        log = self.read_log()
        self.assertIn("start:slow", log)
        self.assertNotIn("finish:slow", log)

        output = client.call_tool("echo", {"value": "after-timeout"})
        self.assertEqual({"value": "after-timeout"}, output)
        self.assertIn("finish:echo", self.read_log())

    def test_mcp_error_result_is_normalized_to_boundary_error(self) -> None:
        with self.assertRaisesRegex(MCPBoundaryError, "returned an error result"):
            self.client().call_tool("fail", {})

    def test_timeout_configuration_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be positive"):
            OfficialMCPClient(
                "synthetic-stdio",
                self.params,
                operation_timeout_seconds=0,
            )


if __name__ == "__main__":
    unittest.main()
