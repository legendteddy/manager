from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
import uvicorn
from mcp.server import Server, ServerRequestContext
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Route

STATE_ENV = "MANAGER_MCP_HTTP_TEST_STATE"
LOG_ENV = "MANAGER_MCP_HTTP_TEST_LOG"
PORT_ENV = "MANAGER_MCP_HTTP_TEST_PORT"


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
                title="Synthetic HTTP echo",
                description="Synthetic Streamable HTTP conformance echo tool.",
                input_schema=_echo_schema(state),
            )
        )
    if _enabled("slow", state):
        tools.append(
            types.Tool(
                name="slow",
                title="Synthetic HTTP slow tool",
                description="Sleeps long enough to exercise HTTP cancellation.",
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
                title="Synthetic HTTP error tool",
                description="Returns an MCP error result over Streamable HTTP.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        )
    if _enabled("headers", state):
        tools.append(
            types.Tool(
                name="headers",
                title="Synthetic HTTP header echo",
                description="Returns one synthetic test header carried by HTTP.",
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

    if params.name == "headers":
        request = ctx.request
        header_value = None
        if isinstance(request, Request):
            header_value = request.headers.get("x-manager-test")
        payload = {"x_manager_test": header_value}
        _log("finish:headers")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, sort_keys=True))],
            structured_content=payload,
        )

    return types.CallToolResult(
        content=[types.TextContent(type="text", text="unknown synthetic tool")],
        is_error=True,
    )


async def same_origin_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/mcp", status_code=307)


server = Server(
    "manager-stage12-synthetic-http",
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool,
)

app = server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    host="127.0.0.1",
    custom_starlette_routes=[
        Route(
            "/redirect-mcp",
            endpoint=same_origin_redirect,
            methods=["GET", "POST", "DELETE"],
        )
    ],
)


if __name__ == "__main__":
    port = int(os.environ[PORT_ENV])
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    )
