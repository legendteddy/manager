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
)

Verifier = Callable[[dict[str, Any], Any], bool]


class MCPToolAdapter:
    """Application-owned ToolAdapter that delegates one authorized call to MCP."""

    def __init__(
        self,
        client: MCPClient,
        remote_tool_name: str,
        *,
        verifier: Verifier | None = None,
    ) -> None:
        self.client = client
        self.remote_tool_name = remote_tool_name
        self._verifier = verifier

    def execute(self, arguments: dict[str, Any]) -> Any:
        return self.client.call_tool(self.remote_tool_name, dict(arguments))

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
) -> list[str]:
    """Register an allowlisted set of MCP tools into Manager's trusted registry.

    Discovery is treated as untrusted capability metadata. A remote tool is
    accepted only when a local binding already supplies Manager-owned policy
    metadata and an input schema exactly matching the discovered schema.

    Remote descriptions, annotations, titles, hints, and output declarations
    are never promoted into trusted Manager policy fields automatically.
    """
    verifiers = verifiers or {}
    if not isinstance(client.server_id, str) or not client.server_id:
        raise MCPBoundaryError("MCP client requires a non-empty server_id")

    discovered_list = [normalize_mcp_tool(item) for item in client.list_tools()]
    discovered: dict[str, dict[str, Any]] = {}
    for item in discovered_list:
        name = item["name"]
        if name in discovered:
            raise MCPBoundaryError(f"MCP discovery returned duplicate tool name: {name}")
        discovered[name] = item

    registered_names: list[str] = []
    seen_local_names: set[str] = set()
    for binding in bindings:
        _validate_binding(binding)
        if binding.get("enabled", True) is False:
            continue
        if binding["server_id"] != client.server_id:
            raise MCPBoundaryError(
                f"MCP binding server_id {binding['server_id']!r} does not match client {client.server_id!r}"
            )

        remote_name = binding["remote_tool_name"]
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
        if side_effect_class in CONSEQUENTIAL_CLASSES and verifier is None:
            raise MCPBoundaryError(
                f"consequential MCP tool {local_name!r} requires an application-owned verifier"
            )

        definition = _configured_definition(binding, remote)
        try:
            registry.register(
                definition,
                MCPToolAdapter(client, remote_name, verifier=verifier),
            )
        except (ToolRuntimeError, TypeError, ValueError) as exc:
            raise MCPBoundaryError(str(exc)) from exc
        registered_names.append(local_name)

    return registered_names
