"""Minimal runnable example: an agent with one tool, driven by MockProvider
so it runs with no API key and no network access.

Run: `python examples/minimal_agent.py`
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aegis_core.loop import Agent, AgentConfig  # noqa: E402
from aegis_core.providers.base import ToolSchema  # noqa: E402
from aegis_core.providers.mock_provider import MockProvider  # noqa: E402
from aegis_core.state import Message, ToolCall  # noqa: E402
from aegis_core.tools.base import Tool  # noqa: E402


def add_numbers(a: float, b: float) -> float:
    return a + b


add_tool = Tool(
    name="add_numbers",
    description="Add two numbers.",
    input_schema={
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    },
    handler=add_numbers,
    concurrency_safe=True,
)


def scripted_response(messages: list[Message], tools: list[ToolSchema]) -> Message:
    has_tool_result = any(m.role == "tool" for m in messages)
    if not has_tool_result:
        return Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(name="add_numbers", arguments={"a": 2, "b": 3})],
        )
    return Message(role="assistant", content="2 + 3 = 5. Done.")


async def main() -> None:
    provider = MockProvider(response_fn=scripted_response)
    agent = Agent(
        provider=provider,
        tools=[add_tool],
        config=AgentConfig(max_iterations=5),
        on_text_delta=lambda t: print(f"[assistant] {t}"),
        on_tool_start=lambda name, args: print(f"[tool start] {name}({args})"),
        on_tool_end=lambda name, result: print(f"[tool end] {name} -> {result.to_model_text()}"),
    )
    agent.state.append(Message(role="user", content="What is 2 + 3?"))
    final_state = await agent.run()
    print(f"[stopped: {final_state.stop_reason}]")
    assert final_state.stop_reason.value == "completed"


if __name__ == "__main__":
    asyncio.run(main())
