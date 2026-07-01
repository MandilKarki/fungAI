"""End-to-end demo of aegis_core's MCP client integration: launches the demo
MCP server (mcp_demo_server.py) as a subprocess, discovers its tools,
registers them into a ToolRegistry, and has an Agent call one through the
normal dispatch path — exercising the full chain: MCP wire protocol ->
ToolRegistry -> Agent loop.

Run: `python examples/mcp_client_demo.py`
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aegis_core.integrations.mcp_client import MCPClient, MCPServerSpec  # noqa: E402
from aegis_core.loop import Agent, AgentConfig  # noqa: E402
from aegis_core.providers.mock_provider import MockProvider  # noqa: E402
from aegis_core.state import Message, ToolCall  # noqa: E402

SERVER_SCRIPT = str(Path(__file__).resolve().parent / "mcp_demo_server.py")


def scripted_response(messages, tools):
    has_result = any(m.role == "tool" for m in messages)
    if not has_result:
        tool_name = next(t.name for t in tools if "lookup_cve_severity" in t.name)
        return Message(
            role="assistant",
            tool_calls=[ToolCall(name=tool_name, arguments={"cve_id": "CVE-2021-44228"})],
        )
    return Message(role="assistant", content="Severity looked up via MCP server.")


async def main() -> None:
    spec = MCPServerSpec(name="demo", command=sys.executable, args=[SERVER_SCRIPT])
    async with MCPClient(spec) as client:
        mcp_tools = await client.list_tools()
        print(f"discovered {len(mcp_tools)} MCP tool(s): {[t.name for t in mcp_tools]}")

        agent = Agent(
            provider=MockProvider(response_fn=scripted_response),
            tools=mcp_tools,
            config=AgentConfig(max_iterations=5),
        )
        agent.state.append(Message(role="user", content="What's the severity of CVE-2021-44228?"))
        final_state = await agent.run()

        for m in final_state.messages:
            if m.role == "tool":
                print(f"[tool:{m.name}] {m.content}")
            elif m.content:
                print(f"[{m.role}] {m.content}")
        print(f"[stopped: {final_state.stop_reason}]")
        assert final_state.stop_reason.value == "completed"


if __name__ == "__main__":
    asyncio.run(main())
