from __future__ import annotations

import threading
import time
import unittest
from copy import deepcopy

from manager_runtime.mcp import MCPBoundaryError, OfficialMCPClient, register_mcp_bindings
from manager_runtime.mcp.base import normalize_mcp_tool
from manager_runtime.serialization import bounded_json_text
from manager_runtime.service.worker_pool import BoundedDaemonWorkerPool
from manager_runtime.tools import ToolRegistry, execute_tool_request


SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


def task() -> dict:
    return {
        "task_id": "integration-boundary-test",
        "objective": "Exercise a synthetic integration boundary.",
        "classification": {
            "materiality": "routine",
            "consequence": "low",
            "uncertainty": "low",
            "reversibility": "reversible",
            "sensitivity": "public",
        },
    }


def request(name: str = "lookup") -> dict:
    return {
        "request_id": f"request:{name}",
        "run_id": "run:integration-boundary-test",
        "tool_name": name,
        "arguments": {"value": "synthetic"},
        "target": None,
        "proposed_by": "model",
        "proposal_ref": f"proposal:{name}",
    }


def definition(name: str = "lookup") -> dict:
    return {
        "name": name,
        "version": "1",
        "description": "Synthetic integration-boundary lookup.",
        "side_effect_class": "read",
        "input_schema": deepcopy(SCHEMA),
        "requires_verification": False,
        "sensitive_output": False,
    }


class OutputTool:
    def __init__(self, output) -> None:
        self.output = output
        self.calls = 0

    def execute(self, arguments: dict):
        self.calls += 1
        return self.output


class MCPClient:
    server_id = "synthetic-mcp"

    def __init__(self, output) -> None:
        self.output = output
        self.calls = 0

    def list_tools(self):
        return [
            {
                "name": "remote_lookup",
                "description": "remote metadata is not policy",
                "inputSchema": deepcopy(SCHEMA),
            }
        ]

    def call_tool_checked(
        self,
        name: str,
        arguments: dict,
        *,
        expected_schema_fingerprint: str,
    ):
        self.calls += 1
        return self.output


def binding() -> dict:
    return {
        "server_id": "synthetic-mcp",
        "remote_tool_name": "remote_lookup",
        "local_definition": definition("catalog.lookup"),
    }


class ExplosiveDict(dict):
    def items(self):
        raise AssertionError("dict subclass hook executed")


class ExplosiveList(list):
    def __iter__(self):
        raise AssertionError("list subclass hook executed")


class RuntimeIntegrationBoundaryTests(unittest.TestCase):
    def test_mcp_schema_fingerprint_rejects_nonfinite_json(self) -> None:
        remote = {
            "name": "bad_schema",
            "inputSchema": {
                "type": "object",
                "properties": {"value": {"const": float("nan")}},
            },
        }
        with self.assertRaisesRegex(MCPBoundaryError, "non-finite|strict JSON"):
            normalize_mcp_tool(remote)

    def test_official_mcp_arguments_reject_nonfinite_json(self) -> None:
        client = OfficialMCPClient("synthetic", "http://127.0.0.1:1/mcp")
        with self.assertRaisesRegex(MCPBoundaryError, "non-finite|strict JSON"):
            client._validate_arguments({"value": float("inf")})

    def test_mcp_adapter_rejects_nonfinite_result(self) -> None:
        client = MCPClient({"value": float("nan")})
        registry = ToolRegistry()
        register_mcp_bindings(registry, client, [binding()])
        registered = registry.get("catalog.lookup")
        assert registered is not None
        with self.assertRaisesRegex(MCPBoundaryError, "non-finite|strict JSON"):
            registered.adapter.execute({"value": "synthetic"})
        self.assertEqual(1, client.calls)

    def test_native_tool_rejects_nonfinite_output(self) -> None:
        registry = ToolRegistry()
        tool = OutputTool({"value": float("nan")})
        registry.register(definition(), tool)
        result = execute_tool_request(
            task(),
            request(),
            registry,
            {"scope_authorized": True},
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual("invalid_tool_output", result["decision_reason"])
        self.assertEqual("unverified", result["verification"]["status"])
        self.assertNotIn("output", result)
        self.assertEqual(1, tool.calls)

    def test_native_tool_rejects_python_only_output(self) -> None:
        registry = ToolRegistry()
        tool = OutputTool({"value": object()})
        registry.register(definition(), tool)
        result = execute_tool_request(
            task(),
            request(),
            registry,
            {"scope_authorized": True},
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual("invalid_tool_output", result["decision_reason"])

    def test_native_tool_rejects_cyclic_output(self) -> None:
        cycle: dict = {}
        cycle["self"] = cycle
        registry = ToolRegistry()
        registry.register(definition(), OutputTool(cycle))
        result = execute_tool_request(
            task(),
            request(),
            registry,
            {"scope_authorized": True},
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual("invalid_tool_output", result["decision_reason"])

    def test_native_tool_rejects_output_beyond_resource_limit(self) -> None:
        registry = ToolRegistry()
        tool = OutputTool(list(range(10_001)))
        registry.register(definition(), tool)
        result = execute_tool_request(
            task(),
            request(),
            registry,
            {"scope_authorized": True},
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual("invalid_tool_output", result["decision_reason"])
        self.assertEqual("unverified", result["verification"]["status"])
        self.assertNotIn("output", result)
        self.assertEqual(1, tool.calls)

    def test_native_tool_result_is_detached_from_adapter_state(self) -> None:
        original = {"items": [{"value": "before"}]}
        registry = ToolRegistry()
        tool = OutputTool(original)
        registry.register(definition(), tool)
        result = execute_tool_request(
            task(),
            request(),
            registry,
            {"scope_authorized": True},
        )
        self.assertEqual("executed", result["status"])
        original["items"][0]["value"] = "after"
        self.assertEqual("before", result["output"]["items"][0]["value"])

    def test_bounded_serialization_does_not_execute_collection_subclass_hooks(self) -> None:
        rendered = bounded_json_text(
            {"dict": ExplosiveDict({"x": 1}), "list": ExplosiveList([1])},
            512,
        )
        self.assertIn("<ExplosiveDict>", rendered)
        self.assertIn("<ExplosiveList>", rendered)

    def test_service_worker_shutdown_is_bounded_by_configured_deadline(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        pool = BoundedDaemonWorkerPool(
            max_workers=1,
            max_pending=2,
            shutdown_timeout_seconds=0.05,
            thread_name_prefix="test-manager-http",
        )

        def block() -> None:
            entered.set()
            release.wait(2.0)

        pool.submit(block)
        self.assertTrue(entered.wait(1.0))
        started = time.monotonic()
        self.assertFalse(pool.shutdown(wait=True))
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.5)
        self.assertTrue(all(thread.daemon for thread in pool._threads))
        with self.assertRaises(RuntimeError):
            pool.submit(lambda: None)

        release.set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if pool.shutdown(wait=True):
                break
            time.sleep(0.01)
        self.assertTrue(pool.shutdown(wait=True))

    def test_service_worker_shutdown_drains_cooperative_work(self) -> None:
        completed = threading.Event()
        pool = BoundedDaemonWorkerPool(
            max_workers=1,
            max_pending=1,
            shutdown_timeout_seconds=0.5,
            thread_name_prefix="test-manager-http",
        )
        pool.submit(completed.set)
        self.assertTrue(pool.shutdown(wait=True))
        self.assertTrue(completed.is_set())


if __name__ == "__main__":
    unittest.main()
