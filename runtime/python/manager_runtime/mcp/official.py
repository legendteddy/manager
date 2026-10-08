from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .base import MCPBoundaryError, normalize_mcp_tool
from .security import (
    BoundedTextSink,
    MCPNetworkPolicy,
    MCPResourceLimits,
    MCPStdioPolicy,
    bound_json_value,
    bound_mcp_result,
    prepare_stdio_target,
    validate_discovered_tool,
    validate_http_target,
)

T = TypeVar("T")

_DEFAULT_OPERATION_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_DISCOVERY_PAGES = 100
_DEFAULT_MAX_DISCOVERED_TOOLS = 1000
_DEFAULT_MAX_CURSOR_CHARS = 4096


class OfficialMCPClient:
    """Policy-bounded synchronous bridge to the official MCP Python SDK v2.

    Manager keeps application-owned policy authoritative while treating remote
    discovery, transport metadata, process stderr, arguments and results as
    hostile input. Every operation gets a fresh SDK client context and is
    bounded by time, concurrency, pagination, item count and byte budgets.
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
        resource_limits: MCPResourceLimits | None = None,
        network_policy: MCPNetworkPolicy | None = None,
        stdio_policy: MCPStdioPolicy | None = None,
        max_concurrent_operations: int = 8,
        allow_custom_transport: bool = False,
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
            ("max_concurrent_operations", max_concurrent_operations),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

        self.server_id = server_id
        self.target = target
        self.operation_timeout_seconds = float(operation_timeout_seconds)
        self.max_discovery_pages = max_discovery_pages
        self.max_discovered_tools = max_discovered_tools
        self.max_cursor_chars = max_cursor_chars
        self.resource_limits = resource_limits or MCPResourceLimits(
            max_discovered_tools=min(max_discovered_tools, MCPResourceLimits().max_discovered_tools),
            max_discovery_pages=min(max_discovery_pages, MCPResourceLimits().max_discovery_pages),
        )
        # Preserve explicitly tighter legacy P2 constructor limits even when a
        # caller supplies a broader resource policy.
        self.max_discovery_pages = min(self.max_discovery_pages, self.resource_limits.max_discovery_pages)
        self.max_discovered_tools = min(self.max_discovered_tools, self.resource_limits.max_discovered_tools)
        self.network_policy = network_policy or MCPNetworkPolicy()
        self.stdio_policy = stdio_policy or MCPStdioPolicy()
        self.allow_custom_transport = bool(allow_custom_transport)
        self._operation_slots = threading.BoundedSemaphore(max_concurrent_operations)

    @staticmethod
    def _imports():
        try:
            import anyio
            from mcp import Client
            from mcp.client.stdio import stdio_client
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise MCPBoundaryError(
                "OfficialMCPClient requires the optional 'mcp' runtime extra"
            ) from exc
        return anyio, Client, stdio_client

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

    def _validated_target(self) -> tuple[Any, BoundedTextSink | None]:
        target = self.target
        if isinstance(target, str):
            validate_http_target(
                target,
                policy=self.network_policy,
                limits=self.resource_limits,
            )
            return target, None
        if hasattr(target, "command") and hasattr(target, "args"):
            prepared = prepare_stdio_target(
                target,
                policy=self.stdio_policy,
                limits=self.resource_limits,
            )
            return prepared, BoundedTextSink(self.resource_limits.max_stderr_bytes)
        if not self.allow_custom_transport:
            raise MCPBoundaryError(
                "custom MCP transports require explicit allow_custom_transport policy"
            )
        return target, None

    def _client_transport(self) -> tuple[Any, BoundedTextSink | None]:
        _, _, stdio_client = self._imports()
        target, stderr_sink = self._validated_target()
        if stderr_sink is not None:
            return stdio_client(target, errlog=stderr_sink), stderr_sink
        return target, None

    def _run_operation(
        self, operation: str, function: Callable[[], Awaitable[T]]
    ) -> T:
        anyio, _, _ = self._imports()
        if not self._operation_slots.acquire(blocking=False):
            raise MCPBoundaryError("MCP concurrent operation limit reached")

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
            raise MCPBoundaryError(
                f"MCP {operation} failed ({type(exc).__name__})"
            ) from exc
        finally:
            self._operation_slots.release()

    def _validate_tool_name(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            raise MCPBoundaryError("MCP tool name must be non-empty text")
        if len(name.encode("utf-8")) > self.resource_limits.max_tool_name_bytes:
            raise MCPBoundaryError("MCP tool name exceeds Manager's byte limit")

    def _validate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise MCPBoundaryError("MCP tool arguments must be an object")
        return bound_json_value(
            arguments,
            label="MCP tool arguments",
            max_bytes=self.resource_limits.max_request_bytes,
            max_depth=self.resource_limits.max_request_depth,
            max_items=self.resource_limits.max_request_items,
        )

    def _normalize_call_result(self, result: Any, name: str) -> Any:
        payload = self._dump(result)
        if not isinstance(payload, dict):
            return bound_mcp_result(payload, self.resource_limits)
        if bool(payload.get("isError") or payload.get("is_error")):
            raise MCPBoundaryError(f"MCP tool returned an error result: {name}")
        if "structuredContent" in payload:
            normalized = payload["structuredContent"]
        elif "structured_content" in payload:
            normalized = payload["structured_content"]
        else:
            normalized = payload.get("content", payload)
        return bound_mcp_result(normalized, self.resource_limits)

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
                normalized = validate_discovered_tool(
                    normalize_mcp_tool(tool), self.resource_limits
                )
                collected.append(normalized)
            cursor = self._checked_cursor(
                getattr(page, "next_cursor", None), seen_cursors
            )
            if cursor is None:
                return collected

    async def _discover_tool(self, client: Any, name: str) -> dict[str, Any]:
        self._validate_tool_name(name)
        matches = [
            tool for tool in await self._bounded_tools(client) if tool["name"] == name
        ]
        if not matches:
            raise MCPBoundaryError(f"MCP tool disappeared before execution: {name}")
        if len(matches) != 1:
            raise MCPBoundaryError(f"MCP discovery returned duplicate tool name: {name}")
        return matches[0]

    def list_tools(self) -> list[dict[str, Any]]:
        _, Client, _ = self._imports()

        async def collect() -> list[dict[str, Any]]:
            transport, stderr_sink = self._client_transport()
            try:
                async with Client(transport) as client:
                    return await self._bounded_tools(client)
            finally:
                if stderr_sink is not None:
                    stderr_sink.close()

        return self._run_operation("tool discovery", collect)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        _, Client, _ = self._imports()
        self._validate_tool_name(name)
        bounded_arguments = self._validate_arguments(arguments)

        async def invoke() -> Any:
            transport, stderr_sink = self._client_transport()
            try:
                async with Client(transport) as client:
                    await self._discover_tool(client, name)
                    result = await client.call_tool(name, arguments=bounded_arguments)
                    return self._normalize_call_result(result, name)
            finally:
                if stderr_sink is not None:
                    stderr_sink.close()

        return self._run_operation(f"tool call {name!r}", invoke)

    def call_tool_checked(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        expected_schema_fingerprint: str,
    ) -> Any:
        if not isinstance(expected_schema_fingerprint, str) or not expected_schema_fingerprint:
            raise MCPBoundaryError("expected MCP schema fingerprint must be non-empty text")
        _, Client, _ = self._imports()
        self._validate_tool_name(name)
        bounded_arguments = self._validate_arguments(arguments)

        async def invoke_checked() -> Any:
            transport, stderr_sink = self._client_transport()
            try:
                async with Client(transport) as client:
                    remote = await self._discover_tool(client, name)
                    if remote["schema_fingerprint"] != expected_schema_fingerprint:
                        raise MCPBoundaryError(
                            f"MCP input schema changed before execution for {name!r}; local review is required"
                        )
                    result = await client.call_tool(name, arguments=bounded_arguments)
                    return self._normalize_call_result(result, name)
            finally:
                if stderr_sink is not None:
                    stderr_sink.close()

        return self._run_operation(f"schema-bound tool call {name!r}", invoke_checked)
