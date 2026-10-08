from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .base import MCPBoundaryError, normalize_mcp_tool

T = TypeVar("T")

_DEFAULT_OPERATION_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_DISCOVERY_PAGES = 100
_DEFAULT_MAX_DISCOVERED_TOOLS = 1000
_DEFAULT_MAX_CURSOR_CHARS = 4096


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
        operation_timeout_seconds: float | None = _DEFAULT_OPERATION_TIMEOUT_SECONDS,
        max_discovery_pages: int = _DEFAULT_MAX_DISCOVERY_PAGES,
        max_discovered_tools: int = _DEFAULT_MAX_DISCOVERED_TOOLS,
        max_cursor_chars: int = _DEFAULT_MAX_CURSOR_CHARS,
    ) -> None:
        if not isinstance(server_id, str) or not server_id:
            raise ValueError("server_id must be non-empty text")
        if operation_timeout_seconds is None:
            operation_timeout_seconds = _DEFAULT_OPERATION_TIMEOUT_SECONDS
        if (
            isinstance(operation_timeout_seconds, bool)
            or not isinstance(operation_timeout_seconds, (int, float))
            or operation_timeout_seconds <= 0
        ):
            raise ValueError("operation_timeout_seconds must be positive")
        for name, value in (
            ("max_discovery_pages", max_discovery_pages),
            ("max_discovered_tools", max_discovered_tools),
            ("max_cursor_chars", max_cursor_chars),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.server_id = server_id
        self.target = target
        self.operation_timeout_seconds = float(operation_timeout_seconds)
        self.max_discovery_pages = max_discovery_pages
        self.max_discovered_tools = max_discovered_tools
        self.max_cursor_chars = max_cursor_chars

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

    @staticmethod
    def _find_nested_exception(
        error: BaseException, expected: type[BaseException]
    ) -> BaseException | None:
        if isinstance(error, expected):
            return error
        if isinstance(error, BaseExceptionGroup):
            for nested in error.exceptions:
                match = OfficialMCPClient._find_nested_exception(nested, expected)
                if match is not None:
                    return match
        return None

    @staticmethod
    def _find_nested_sdk_error(error: BaseException) -> BaseException | None:
        try:
            from mcp.shared.exceptions import MCPError
        except ImportError:  # pragma: no cover - optional dependency
            return None
        return OfficialMCPClient._find_nested_exception(error, MCPError)

    def _run_operation(
        self, operation: str, function: Callable[[], Awaitable[T]]
    ) -> T:
        anyio, _ = self._imports()

        async def guarded() -> T:
            with anyio.fail_after(self.operation_timeout_seconds):
                return await function()

        try:
            return anyio.run(guarded)
        except Exception as exc:
            timeout = self._find_nested_exception(exc, TimeoutError)
            if timeout is not None:
                raise MCPBoundaryError(f"MCP {operation} timed out") from timeout

            boundary = self._find_nested_exception(exc, MCPBoundaryError)
            if boundary is not None:
                raise MCPBoundaryError(str(boundary)) from boundary

            sdk_error = self._find_nested_sdk_error(exc)
            if sdk_error is not None:
                raise MCPBoundaryError(
                    f"MCP {operation} failed ({type(sdk_error).__name__})"
                ) from sdk_error

            # Transport/SDK exception messages may contain remote response text,
            # URLs, headers, or credentials. Keep the chain locally but expose
            # only the exception class through Manager's public boundary.
            raise MCPBoundaryError(
                f"MCP {operation} failed ({type(exc).__name__})"
            ) from exc

    @staticmethod
    def _normalize_call_result(result: Any, name: str) -> Any:
        payload = OfficialMCPClient._dump(result)
        if not isinstance(payload, dict):
            return payload
        if bool(payload.get("isError") or payload.get("is_error")):
            raise MCPBoundaryError(f"MCP tool returned an error result: {name}")

        if "structuredContent" in payload:
            return payload["structuredContent"]
        if "structured_content" in payload:
            return payload["structured_content"]
        return payload.get("content", payload)

    def _checked_cursor(self, value: Any, seen: set[str]) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise MCPBoundaryError("MCP discovery returned an invalid pagination cursor")
        if len(value) > self.max_cursor_chars:
            raise MCPBoundaryError("MCP discovery cursor exceeded the configured size limit")
        if value in seen:
            raise MCPBoundaryError("MCP discovery repeated a pagination cursor")
        seen.add(value)
        return value

    async def _bounded_tools(self, client: Any) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages = 0

        while True:
            if pages >= self.max_discovery_pages:
                raise MCPBoundaryError("MCP discovery exceeded the configured page limit")
            page = await client.list_tools(cursor=cursor)
            pages += 1
            tools = getattr(page, "tools", None)
            if tools is None:
                raise MCPBoundaryError("MCP discovery returned a page without tools")
            for tool in tools:
                if len(collected) >= self.max_discovered_tools:
                    raise MCPBoundaryError("MCP discovery exceeded the configured tool limit")
                collected.append(normalize_mcp_tool(tool))

            cursor = self._checked_cursor(getattr(page, "next_cursor", None), seen_cursors)
            if cursor is None:
                return collected

    async def _discover_tool(self, client: Any, name: str) -> dict[str, Any]:
        matches = [
            tool for tool in await self._bounded_tools(client) if tool["name"] == name
        ]
        if not matches:
            raise MCPBoundaryError(f"MCP tool disappeared before execution: {name}")
        if len(matches) != 1:
            raise MCPBoundaryError(f"MCP discovery returned duplicate tool name: {name}")
        return matches[0]

    def list_tools(self) -> list[dict[str, Any]]:
        _, Client = self._imports()
        target = self.target

        async def collect() -> list[dict[str, Any]]:
            async with Client(target) as client:
                return await self._bounded_tools(client)

        return self._run_operation("tool discovery", collect)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool after same-session existence discovery."""
        _, Client = self._imports()
        target = self.target

        async def invoke() -> Any:
            async with Client(target) as client:
                await self._discover_tool(client, name)
                result = await client.call_tool(name, arguments=arguments)
                return self._normalize_call_result(result, name)

        return self._run_operation(f"tool call {name!r}", invoke)

    def call_tool_checked(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        expected_schema_fingerprint: str,
    ) -> Any:
        """Verify schema identity and invoke within one SDK client session.

        This closes Manager's previous reconnect-sized check/use window. It
        cannot make an arbitrary remote server internally immutable, but it
        guarantees Manager does not authorize against one client session and
        execute through a later independently discovered session.
        """
        if not isinstance(expected_schema_fingerprint, str) or not expected_schema_fingerprint:
            raise MCPBoundaryError("expected MCP schema fingerprint must be non-empty text")

        _, Client = self._imports()
        target = self.target

        async def invoke_checked() -> Any:
            async with Client(target) as client:
                remote = await self._discover_tool(client, name)
                if remote["schema_fingerprint"] != expected_schema_fingerprint:
                    raise MCPBoundaryError(
                        f"MCP input schema changed before execution for {name!r}; local review is required"
                    )
                result = await client.call_tool(name, arguments=arguments)
                return self._normalize_call_result(result, name)

        return self._run_operation(f"schema-bound tool call {name!r}", invoke_checked)
