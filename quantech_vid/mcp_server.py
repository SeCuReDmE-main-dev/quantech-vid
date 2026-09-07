from __future__ import annotations

import inspect
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import anyio
import httpx
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (CallToolRequestParams, CallToolResult, ListToolsResult,
                       PaginatedRequestParams, TextContent, Tool, ToolAnnotations)

from .tool_catalog import COMMON_OUTPUT_SCHEMA, TOOL_BY_NAME, TOOL_DEFINITIONS


Dispatcher = Callable[[str, dict[str, Any]], dict | Awaitable[dict]]


def build_mcp_server(dispatcher: Dispatcher) -> Server:
    tools = [Tool(name=item.name, description=item.description, inputSchema=item.input_schema,
                  outputSchema=COMMON_OUTPUT_SCHEMA,
                  annotations=ToolAnnotations(readOnlyHint=item.read_only, destructiveHint=False,
                                              idempotentHint=item.idempotent, openWorldHint=False))
             for item in TOOL_DEFINITIONS]

    async def list_tools(_: ServerRequestContext, __: PaginatedRequestParams | None) -> ListToolsResult:
        return ListToolsResult(tools=tools)

    async def call_tool(_: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        value = dispatcher(params.name, params.arguments or {})
        result = await value if inspect.isawaitable(value) else value
        if not isinstance(result, dict) or set(result) != {"ok", "tool", "result", "error"}:
            result = {"ok": False, "tool": params.name, "result": None,
                      "error": {"code": "INVALID_DISPATCH_RESULT",
                                "message": "Tool request could not be completed", "retryable": False}}
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, separators=(",", ":")))],
                              structuredContent=result, isError=not bool(result["ok"]))

    return Server("quantech-vid", version="2.1.0", title="QuaNTecH-ViD Production Tools",
                  instructions="Eight bounded project tools. Human production approval is never available through MCP.",
                  on_list_tools=list_tools, on_call_tool=call_tool)


class HTTPToolDispatcher:
    def __init__(self, base_url: str, agent_token: str, timeout_seconds: float = 10.0) -> None:
        parsed = urlparse(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.path not in {"", "/"} or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment):
            raise ValueError("MCP API base URL must be an origin on loopback HTTP")
        if not agent_token or not (1 <= timeout_seconds <= 30):
            raise ValueError("A bounded agent credential and timeout are required")
        self.base_url = base_url.rstrip("/")
        self.agent_token = agent_token
        self.timeout_seconds = timeout_seconds

    async def __call__(self, name: str, arguments: dict[str, Any]) -> dict:
        if name not in TOOL_BY_NAME:
            return {"ok": False, "tool": name, "result": None,
                    "error": {"code": "TOOL_NOT_FOUND",
                              "message": "Tool request could not be completed", "retryable": False}}
        headers = {"Authorization": f"Bearer {self.agent_token}", "Origin": self.base_url}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds,
                                         trust_env=False, follow_redirects=False) as client:
                response = await client.post(f"/api/v2/tools/{name}", headers=headers, json=arguments)
            if response.status_code != 200:
                raise ValueError("rejected")
            payload = response.json()
            if not isinstance(payload, dict) or set(payload) != {"ok", "tool", "result", "error"}:
                raise ValueError("invalid")
            return payload
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            return {"ok": False, "tool": name, "result": None,
                    "error": {"code": "TOOL_API_UNAVAILABLE",
                              "message": "Tool request could not be completed", "retryable": True}}


def default_server() -> Server:
    token = os.getenv("QUANTECH_VID_AGENT_TOKEN", "")
    base_url = os.getenv("QUANTECH_VID_API_ORIGIN", "http://127.0.0.1:7476")
    try: timeout = float(os.getenv("QUANTECH_VID_TOOL_TIMEOUT_SECONDS", "10"))
    except ValueError as exc: raise RuntimeError("Invalid MCP tool timeout configuration") from exc
    return build_mcp_server(HTTPToolDispatcher(base_url, token, timeout))


async def _run_stdio() -> None:
    server = default_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    anyio.run(_run_stdio)


if __name__ == "__main__":
    main()
