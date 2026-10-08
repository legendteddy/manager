from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from ..capacity import OverloadedError
from ..operations import DEFAULT_OPERATIONS, OperationalRuntime
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
        operations: OperationalRuntime | None = None,
    ) -> None:
        if not isinstance(server_id, str) or not server_id:
            raise ValueError("server_id must be non-empty text")
        if operation_timeout_seconds is not None and operation_timeout_seconds <= 0:
            raise ValueError("operation_timeout_seconds must be positive when provided")
        self.server_id = server_id
        self.target = target
        self.operation_timeout_seconds = operation_timeout_seconds
        self.operations = operations or DEFAULT_OPERATIONS

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

    @staticmethod
    def _operation_label(operation: str) -> str:
        if operation == "tool discovery":
            return "tool_discovery"
        if operation.startswith("schema-bound tool call"):
            return "tool_call_checked"
        if operation.startswith("tool call"):
            return "tool_call"
        return "operation"

    def _run_operation(
        self, operation: str, function: Callable[[], Awaitable[T]]
    ) -> T:
        anyio, _ = self._imports()

        async def guarded() -> T:
            if self.operation_timeout_seconds is None:
                return await function()
            with anyio.fail_after(self.operation_timeout_seconds):
                return await function()

        label = self._operation_label(operation)
        try:
            with self.operations.operation(
                "mcp",
                labels={"operation": label},
                attributes={"operation": label},
            ):
                return anyio.run(guarded)
        except OverloadedError as exc:
            raise MCPBoundaryError("MCP operation rejected because local capacity is exhausted") from exc
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

    @staticmethod
    async def _discover_tool(client: Any, name: str) -> dict[str, Any]:
        matches: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            for tool in page.tools:
                normalized = normalize_mcp_tool(tool)
                if normalized["name"] == name:
                    matches.append(normalized)
            cursor = page.next_cursor
            if cursor is None:
                break
        if not matches:
            raise MCPBoundaryError(f"MCP tool disappeared before execution: {name}")
        if len(matches) != 1:
            raise MCPBoundaryError(f"MCP discovery returned duplicate tool name: {name}")
        return matches[0]

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
                        collected.append(normalize_mcp_tool(tool))
                    cursor = page.next_cursor
                    if cursor is None:
                        return collected

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
