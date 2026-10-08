from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .base import MCPBoundaryError, normalize_mcp_tool

T = TypeVar("T")


class OfficialMCPClient:
    """Optional synchronous bridge to the official MCP Python SDK v2.

    The bridge intentionally keeps connection targets and credentials outside
    Manager's canonical contracts. Each operation owns its SDK client context;
    applications needing long-lived pooling may provide another MCPClient
    implementation without changing Manager's policy boundary.
    """

    def __init__(
        self,
        server_id: str,
        target: Any,
        *,
        operation_timeout_seconds: float | None = None,
    ) -> None:
        if not isinstance(server_id, str) or not server_id:
            raise ValueError("server_id must be non-empty text")
        if operation_timeout_seconds is not None and operation_timeout_seconds <= 0:
            raise ValueError("operation_timeout_seconds must be positive when provided")
        self.server_id = server_id
        self.target = target
        self.operation_timeout_seconds = operation_timeout_seconds

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

    def _run_operation(
        self, operation: str, function: Callable[[], Awaitable[T]]
    ) -> T:
        anyio, _ = self._imports()

        async def guarded() -> T:
            if self.operation_timeout_seconds is None:
                return await function()
            with anyio.fail_after(self.operation_timeout_seconds):
                return await function()

        try:
            return anyio.run(guarded)
        except TimeoutError as exc:
            raise MCPBoundaryError(f"MCP {operation} timed out") from exc
        except MCPBoundaryError:
            raise
        except Exception as exc:
            raise MCPBoundaryError(
                f"MCP {operation} failed: {type(exc).__name__}: {exc}"
            ) from exc

    def list_tools(self) -> list[dict[str, Any]]:
        _, Client = self._imports()
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

        return self._run_operation("tool discovery", collect)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        _, Client = self._imports()
        target = self.target

        async def invoke() -> Any:
            async with Client(target) as client:
                # Discover immediately before execution. Manager-owned MCP tool
                # adapters perform the authoritative schema fingerprint check;
                # this bridge also refuses a tool that disappeared entirely.
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

        return self._run_operation(f"tool call {name!r}", invoke)
