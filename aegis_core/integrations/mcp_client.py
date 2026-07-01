"""MCP (Model Context Protocol) client integration: connect to an MCP
server (a stdio-launched subprocess), discover its tools, and expose them as
plain aegis_core Tools through ToolRegistry. From that point on, the rest of
the framework — dispatch, middleware, approval, audit logging — treats an
MCP tool exactly like a built-in one. See ARCHITECTURE.md / ROADMAP.md.

Optional dependency: `pip install aegis-agent[mcp]` (the `mcp` package).
Verified end to end against a local FastMCP server in
examples/mcp_client_demo.py + examples/mcp_demo_server.py.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass

from aegis_core.tools.base import Tool, ToolResult
from aegis_core.tools.registry import ToolRegistry


@dataclass
class MCPServerSpec:
    name: str
    command: str
    args: list[str] | None = None
    env: dict[str, str] | None = None


class MCPClient:
    """Wraps one MCP server connection (stdio transport) and its
    ClientSession."""

    def __init__(self, spec: MCPServerSpec):
        self.spec = spec
        self._stack = AsyncExitStack()
        self._session = None

    async def connect(self) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise ImportError(
                "MCPClient requires the 'mcp' package: pip install 'aegis-agent[mcp]'"
            ) from exc

        params = StdioServerParameters(
            command=self.spec.command, args=self.spec.args or [], env=self.spec.env
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def close(self) -> None:
        await self._stack.aclose()

    async def __aenter__(self) -> "MCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    async def list_tools(self) -> list[Tool]:
        if self._session is None:
            raise RuntimeError("MCPClient not connected — call connect() first")
        response = await self._session.list_tools()
        return [self._wrap_tool(t) for t in response.tools]

    def _wrap_tool(self, mcp_tool) -> Tool:
        prefixed_name = f"mcp_{self.spec.name}_{mcp_tool.name}"
        original_name = mcp_tool.name

        async def _handler(**kwargs) -> ToolResult:
            result = await self._session.call_tool(original_name, arguments=kwargs)
            text_parts = [
                block.text for block in result.content if getattr(block, "type", None) == "text"
            ]
            text = "\n".join(text_parts) if text_parts else str(result.content)
            if getattr(result, "isError", False):
                return ToolResult.failure(text)
            return ToolResult.success(text)

        return Tool(
            name=prefixed_name,
            description=mcp_tool.description
            or f"MCP tool {original_name!r} from server {self.spec.name!r}",
            input_schema=mcp_tool.inputSchema or {"type": "object", "properties": {}},
            handler=_handler,
            concurrency_safe=False,  # an MCP server's side-effect safety is unknown by default
            owner="mcp",
            deferred=True,  # MCP tools default to deferred — see tools/search.py
        )

    async def register_into(self, registry: ToolRegistry) -> list[str]:
        tools = await self.list_tools()
        for tool in tools:
            registry.register(tool)
        return [t.name for t in tools]
