from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server

STATE_ENV = "MANAGER_MCP_TEST_STATE"
LOG_ENV = "MANAGER_MCP_TEST_LOG"


def _state() -> dict[str, Any]:
    path = os.environ.get(STATE_ENV)
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _log(event: str) -> None:
    path = os.environ.get(LOG_ENV)
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(event + "\n")


def _echo_schema(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("schema_version") == 2:
        return {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "extra": {"type": "string"},
            },
            "required": ["value", "extra"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }


def _enabled(name: str, state: dict[str, Any]) -> bool:
    configured = state.get("enabled_tools")
    if configured is None:
        return True
    return name in configured


async def handle_list_tools(
    ctx: ServerRequestContext, params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    state = _state()
    tools: list[types.Tool] = []
    if _enabled("echo", state):
        tools.append(
            types.Tool(
                name="echo",
                title="Synthetic echo",
                description="Synthetic transport-conformance echo tool.",
                input_schema=_echo_schema(state),
            )
        )
    if _enabled("slow", state):
        tools.append(
            types.Tool(
                name="slow",
                title="Synthetic slow tool",
                description="Sleeps long enough to exercise cancellation and timeout cleanup.",
                input_schema={
                    "type": "object",
                    "properties": {"delay_ms": {"type": "integer"}},
                    "required": ["delay_ms"],
                    "additionalProperties": False,
                },
            )
        )
    if _enabled("fail", state):
        tools.append(
            types.Tool(
                name="fail",
                title="Synthetic error tool",
                description="Returns an MCP error result for normalization tests.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        )
    return types.ListToolsResult(tools=tools)


async def handle_call_tool(
    ctx: ServerRequestContext, params: types.CallToolRequestParams
) -> types.CallToolResult:
    state = _state()
    if not _enabled(params.name, state):
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="tool unavailable")],
            is_error=True,
        )

    arguments = params.arguments or {}
    _log(f"start:{params.name}")

    if params.name == "echo":
        payload = {"value": arguments.get("value", "")}
        _log("finish:echo")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, sort_keys=True))],
            structured_content=payload,
        )

    if params.name == "slow":
        delay_ms = int(arguments.get("delay_ms", 1000))
        await anyio.sleep(delay_ms / 1000)
        payload = {"slept_ms": delay_ms}
        _log("finish:slow")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, sort_keys=True))],
            structured_content=payload,
        )

    if params.name == "fail":
        _log("finish:fail")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="synthetic MCP error")],
            is_error=True,
        )

    return types.CallToolResult(
        content=[types.TextContent(type="text", text="unknown synthetic tool")],
        is_error=True,
    )


server = Server(
    "manager-stage11-synthetic",
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool,
)


async def run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    anyio.run(run)
