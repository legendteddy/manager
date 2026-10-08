from __future__ import annotations

from typing import Any

from .base import MCPBoundaryError, normalize_mcp_tool


class OfficialMCPClient:
    """Optional synchronous bridge to the official MCP Python SDK v2.

    The bridge intentionally keeps connection targets and credentials outside
    Manager's canonical contracts. Each operation owns its SDK client context;
    applications needing long-lived pooling may provide another MCPClient
    implementation without changing Manager's policy boundary.
    """

    def __init__(self, server_id: str, target: Any) -> None:
        if not isinstance(server_id, str) or not server_id:
            raise ValueError("server_id must be non-empty text")
        self.server_id = server_id
        self.target = target

    @staticmethod
    def _imports():
        try:
            import anyio
            from mcp import Client
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise MCPBoundaryError(
                "OfficialMCPClient requires the optional 'mcp' runtime extra"
            ) from exc
        return anyio, Client

    @staticmethod
    def _dump(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(by_alias=True, exclude_none=True)
        return value

    def list_tools(self) -> list[dict[str, Any]]:
        anyio, Client = self._imports()
        target = self.target

        async def collect() -> list[dict[str, Any]]:
            collected: list[dict[str, Any]] = []
            async with Client(target) as client:
                cursor: str | None = None
                while True:
                    page = await client.list_tools(cursor=cursor)
                    for tool in page.tools:
                        normalized = normalize_mcp_tool(tool)
                        collected.append(normalized)
                    cursor = page.next_cursor
                    if cursor is None:
                        return collected

        return anyio.run(collect)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        anyio, Client = self._imports()
        target = self.target

        async def invoke() -> Any:
            async with Client(target) as client:
                # List the selected tool first. The current SDK uses discovery
                # state for schema-aware calls, including newer HTTP parameter
                # header behavior, so execution never skips discovery.
                cursor: str | None = None
                found = False
                while True:
                    page = await client.list_tools(cursor=cursor)
                    if any(tool.name == name for tool in page.tools):
                        found = True
                        break
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                if not found:
                    raise MCPBoundaryError(
                        f"MCP tool disappeared before execution: {name}"
                    )

                result = await client.call_tool(name, arguments=arguments)
                payload = self._dump(result)
                if not isinstance(payload, dict):
                    return payload
                if bool(payload.get("isError") or payload.get("is_error")):
                    raise MCPBoundaryError(f"MCP tool returned an error result: {name}")

                if "structuredContent" in payload:
                    return payload["structuredContent"]
                if "structured_content" in payload:
                    return payload["structured_content"]
                return payload.get("content", payload)

        return anyio.run(invoke)
