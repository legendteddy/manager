from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, runtime_checkable


class MCPBoundaryError(RuntimeError):
    """Raised when MCP discovery or execution cannot satisfy Manager's boundary."""


@runtime_checkable
class MCPClient(Protocol):
    """Small synchronous boundary used by Manager's governed tool runtime.

    Implementations may wrap asynchronous MCP SDKs internally. The rest of
    Manager depends only on this protocol, not on a particular MCP SDK.
    """

    server_id: str

    def list_tools(self) -> list[dict[str, Any]]:
        """Return normalized MCP tool discovery objects."""

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call one remote MCP tool after Manager has authorized execution."""


def _stable_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise MCPBoundaryError("MCP schema must be strict JSON") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def remote_schema_fingerprint(schema: dict[str, Any]) -> str:
    """Return a stable identity for a discovered MCP input schema."""
    return _stable_digest(schema)


def normalize_mcp_tool(value: Any) -> dict[str, Any]:
    """Normalize a discovered MCP tool without trusting its policy hints.

    The normalized object retains discovery metadata for matching and audit,
    but Manager never derives authority, side-effect class, verification,
    sensitivity, or local model-facing description from these remote fields.
    """
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, exclude_none=True)
    if not isinstance(value, dict):
        raise MCPBoundaryError("MCP tool discovery entry must be an object")

    name = value.get("name")
    if not isinstance(name, str) or not name:
        raise MCPBoundaryError("MCP tool discovery entry requires a non-empty name")

    schema = value.get("inputSchema")
    if schema is None:
        schema = value.get("input_schema")
    if not isinstance(schema, dict):
        raise MCPBoundaryError(f"MCP tool {name!r} is missing an input schema")

    description = value.get("description")
    if description is not None and not isinstance(description, str):
        description = str(description)

    return {
        "name": name,
        "description": description,
        "input_schema": schema,
        "schema_fingerprint": remote_schema_fingerprint(schema),
        "remote_metadata": {
            key: item
            for key, item in value.items()
            if key not in {"name", "description", "inputSchema", "input_schema"}
        },
    }
