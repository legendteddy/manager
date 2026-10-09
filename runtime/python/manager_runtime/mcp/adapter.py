from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from ..tools.base import (
    CONSEQUENTIAL_CLASSES,
    ToolRegistry,
    ToolRuntimeError,
    validate_tool_definition,
)
from .base import (
    MCPBoundaryError,
    MCPClient,
    normalize_mcp_tool,
    remote_schema_fingerprint,
    strict_json_snapshot,
)
from .security import MCPResourceLimits, bound_mcp_result, validate_discovered_tool

Verifier = Callable[[dict[str, Any], Any], bool]


def _resource_limits(client: MCPClient, configured: MCPResourceLimits | None) -> MCPResourceLimits:
    if configured is not None:
        return configured
    candidate = getattr(client, "resource_limits", None)
    if isinstance(candidate, MCPResourceLimits):
        return candidate
    return MCPResourceLimits()


def _bounded_discovery(client: MCPClient, limits: MCPResourceLimits) -> list[dict[str, Any]]:
    raw = client.list_tools()
    if not isinstance(raw, list):
        raise MCPBoundaryError("MCP discovery result must be a list")
    if len(raw) > limits.max_discovered_tools:
        raise MCPBoundaryError("MCP discovery exceeds Manager's tool-count limit")
    return [validate_discovered_tool(normalize_mcp_tool(item), limits) for item in raw]


class MCPToolAdapter:
    """Application-owned ToolAdapter delegating one authorized call to MCP."""

    def __init__(
        self,
        client: MCPClient,
        remote_tool_name: str,
        *,
        expected_server_id: str,
        expected_schema_fingerprint: str,
        verifier: Verifier | None = None,
        require_schema_bound_call: bool = False,
        resource_limits: MCPResourceLimits | None = None,
    ) -> None:
        self.client = client
        self.remote_tool_name = remote_tool_name
        self.expected_server_id = expected_server_id
        self.expected_schema_fingerprint = expected_schema_fingerprint
        self._verifier = verifier
        self.require_schema_bound_call = require_schema_bound_call
        self.resource_limits = _resource_limits(client, resource_limits)

    def _revalidate_remote_tool(self) -> None:
        if self.client.server_id != self.expected_server_id:
            raise MCPBoundaryError("MCP server identity changed before execution")
        matches = [
            item
            for item in _bounded_discovery(self.client, self.resource_limits)
            if item["name"] == self.remote_tool_name
        ]
        if not matches:
            raise MCPBoundaryError(
                f"MCP tool disappeared before execution: {self.remote_tool_name}"
            )
        if len(matches) != 1:
            raise MCPBoundaryError(
                f"MCP discovery returned duplicate tool name: {self.remote_tool_name}"
            )
        if matches[0]["schema_fingerprint"] != self.expected_schema_fingerprint:
            raise MCPBoundaryError(
                f"MCP input schema changed before execution for {self.remote_tool_name!r}; local review is required"
            )

    def _bound_result(self, output: Any) -> Any:
        bounded = bound_mcp_result(output, self.resource_limits)
        return strict_json_snapshot(bounded, label="MCP tool result")

    def execute(self, arguments: dict[str, Any]) -> Any:
        if self.client.server_id != self.expected_server_id:
            raise MCPBoundaryError("MCP server identity changed before execution")
        checked_call = getattr(self.client, "call_tool_checked", None)
        if callable(checked_call):
            output = checked_call(
                self.remote_tool_name,
                dict(arguments),
                expected_schema_fingerprint=self.expected_schema_fingerprint,
            )
            return self._bound_result(output)
        if self.require_schema_bound_call:
            raise MCPBoundaryError(
                "Consequential MCP execution requires a schema-bound client call"
            )
        self._revalidate_remote_tool()
        output = self.client.call_tool(self.remote_tool_name, dict(arguments))
        return self._bound_result(output)

    def verify(self, arguments: dict[str, Any], output: Any) -> bool:
        if self._verifier is None:
            return False
        return bool(self._verifier(dict(arguments), output))


def _validate_binding(binding: dict[str, Any]) -> None:
    required = ("server_id", "remote_tool_name", "local_definition")
    missing = [key for key in required if key not in binding]
    if missing:
        raise MCPBoundaryError(
            f"MCP binding missing required fields: {', '.join(missing)}"
        )
    if not isinstance(binding["server_id"], str) or not binding["server_id"]:
        raise MCPBoundaryError("MCP binding server_id must be non-empty text")
    if not isinstance(binding["remote_tool_name"], str) or not binding["remote_tool_name"]:
        raise MCPBoundaryError("MCP binding remote_tool_name must be non-empty text")
    definition = binding["local_definition"]
    if not isinstance(definition, dict):
        raise MCPBoundaryError("MCP binding local_definition must be an object")
    validate_tool_definition(definition)


def _configured_definition(
    binding: dict[str, Any], remote_tool: dict[str, Any]
) -> dict[str, Any]:
    definition = deepcopy(binding["local_definition"])
    binding_fingerprint = remote_schema_fingerprint(
        {
            "server_id": binding["server_id"],
            "remote_tool_name": binding["remote_tool_name"],
            "input_schema": remote_tool["input_schema"],
        }
    )
    base_version = definition.get("version") or "unversioned"
    definition["version"] = f"{base_version}|mcp:{binding_fingerprint}"
    extensions = deepcopy(definition.get("extensions") or {})
    extensions["mcp"] = {
        "server_id": binding["server_id"],
        "remote_tool_name": binding["remote_tool_name"],
        "remote_schema_fingerprint": remote_tool["schema_fingerprint"],
        "binding_fingerprint": binding_fingerprint,
    }
    definition["extensions"] = extensions
    return definition


def register_mcp_bindings(
    registry: ToolRegistry,
    client: MCPClient,
    bindings: list[dict[str, Any]],
    *,
    verifiers: dict[str, Verifier] | None = None,
    resource_limits: MCPResourceLimits | None = None,
) -> list[str]:
    """Register allowlisted remote capabilities under local Manager authority."""
    verifiers = verifiers or {}
    limits = _resource_limits(client, resource_limits)
    if not isinstance(client.server_id, str) or not client.server_id:
        raise MCPBoundaryError("MCP client requires a non-empty server_id")
    if not isinstance(bindings, list):
        raise MCPBoundaryError("MCP bindings must be a list")
    if len(bindings) > limits.max_discovered_tools:
        raise MCPBoundaryError("MCP binding count exceeds Manager's tool-count limit")

    discovered_list = _bounded_discovery(client, limits)
    discovered: dict[str, dict[str, Any]] = {}
    for item in discovered_list:
        name = item["name"]
        if name in discovered:
            raise MCPBoundaryError(f"MCP discovery returned duplicate tool name: {name}")
        discovered[name] = item

    registered_names: list[str] = []
    seen_local_names: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise MCPBoundaryError("MCP binding entries must be objects")
        _validate_binding(binding)
        if binding.get("enabled", True) is False:
            continue
        if binding["server_id"] != client.server_id:
            raise MCPBoundaryError(
                f"MCP binding server_id {binding['server_id']!r} does not match client {client.server_id!r}"
            )
        remote_name = binding["remote_tool_name"]
        if len(remote_name.encode("utf-8")) > limits.max_tool_name_bytes:
            raise MCPBoundaryError("configured MCP remote tool name exceeds Manager's byte limit")
        remote = discovered.get(remote_name)
        if remote is None:
            raise MCPBoundaryError(f"configured MCP tool was not discovered: {remote_name}")
        local_definition = binding["local_definition"]
        expected_schema = local_definition["input_schema"]
        if remote["input_schema"] != expected_schema:
            raise MCPBoundaryError(
                f"MCP input schema changed for {remote_name!r}; local review is required"
            )
        local_name = local_definition["name"]
        if local_name in seen_local_names:
            raise MCPBoundaryError(f"duplicate local MCP tool binding: {local_name}")
        seen_local_names.add(local_name)
        side_effect_class = local_definition["side_effect_class"]
        verifier = verifiers.get(local_name)
        consequential = side_effect_class in CONSEQUENTIAL_CLASSES
        if consequential and verifier is None:
            raise MCPBoundaryError(
                f"consequential MCP tool {local_name!r} requires an application-owned verifier"
            )
        if consequential and not callable(getattr(client, "call_tool_checked", None)):
            raise MCPBoundaryError(
                f"consequential MCP tool {local_name!r} requires schema-bound execution support"
            )
        definition = _configured_definition(binding, remote)
        try:
            registry.register(
                definition,
                MCPToolAdapter(
                    client,
                    remote_name,
                    expected_server_id=binding["server_id"],
                    expected_schema_fingerprint=remote["schema_fingerprint"],
                    verifier=verifier,
                    require_schema_bound_call=consequential,
                    resource_limits=limits,
                ),
            )
        except (ToolRuntimeError, TypeError, ValueError) as exc:
            raise MCPBoundaryError(str(exc)) from exc
        registered_names.append(local_name)
    return registered_names
