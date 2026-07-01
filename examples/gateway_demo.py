"""End-to-end demo of the gateway daemon: starts a GatewayServer in-process,
connects a real WebSocket client, sends one message, and prints every event
streamed back — exercising the actual wire protocol over a real (localhost)
socket, not an in-memory call.

Run: `python examples/gateway_demo.py`
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aegis_core.loop import Agent, AgentConfig  # noqa: E402
from aegis_core.providers.mock_provider import MockProvider  # noqa: E402
from aegis_core.state import Message, ToolCall  # noqa: E402
from aegis_core.tools.base import Tool  # noqa: E402
from aegis_gateway.client import send_and_collect  # noqa: E402
from aegis_gateway.protocol import MSG_DONE, MSG_TEXT_DELTA, MSG_TOOL_END, MSG_TOOL_START  # noqa: E402
from aegis_gateway.server import GatewayServer  # noqa: E402

PORT = 8765


def make_agent() -> Agent:
    def ping(target: str) -> str:
        return f"{target} is reachable"

    ping_tool = Tool(
        name="ping",
        description="Check reachability of a target.",
        input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
        handler=ping,
        concurrency_safe=True,
    )

    def scripted(messages, tools):
        if not any(m.role == "tool" for m in messages):
            return Message(role="assistant", tool_calls=[ToolCall(name="ping", arguments={"target": "10.0.0.5"})])
        return Message(role="assistant", content="10.0.0.5 is up.")

    return Agent(provider=MockProvider(response_fn=scripted), tools=[ping_tool], config=AgentConfig(max_iterations=5))


async def main() -> None:
    server = GatewayServer(agent_factory=make_agent, port=PORT)
    server_task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0.2)  # let the listener come up

    try:
        events = await send_and_collect(f"ws://127.0.0.1:{PORT}", "demo-session", "Is 10.0.0.5 up?")
    finally:
        server_task.cancel()

    for e in events:
        if e.type == MSG_TEXT_DELTA:
            print("[text]", e.payload["text"])
        elif e.type == MSG_TOOL_START:
            print("[tool start]", e.payload["name"], e.payload["args"])
        elif e.type == MSG_TOOL_END:
            print("[tool end]", e.payload["name"], "->", e.payload["result"])
        elif e.type == MSG_DONE:
            print("[done]", e.payload["stop_reason"])

    assert events[-1].type == MSG_DONE
    assert events[-1].payload["stop_reason"] == "completed"


if __name__ == "__main__":
    asyncio.run(main())
