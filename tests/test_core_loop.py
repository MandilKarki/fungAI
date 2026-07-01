from aegis_core.budget import IterationBudget
from aegis_core.loop import Agent, AgentConfig
from aegis_core.providers.mock_provider import MockProvider
from aegis_core.state import Message, StopReason, ToolCall
from aegis_core.tools.base import Tool


def test_budget_grace_call_then_stop():
    budget = IterationBudget(max_iterations=2, grace_calls=1)
    assert budget.should_continue() == (True, False)
    budget.record()
    assert budget.should_continue() == (True, False)
    budget.record()
    # hard cap hit -> one grace call granted
    assert budget.should_continue() == (True, True)
    budget.record()
    # grace exhausted -> stop
    assert budget.should_continue() == (False, False)


async def test_agent_runs_a_tool_call_to_completion():
    add_tool = Tool(
        name="add",
        description="add two numbers",
        input_schema={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
        handler=lambda a, b: a + b,
        concurrency_safe=True,
    )

    def scripted(messages, tools):
        if not any(m.role == "tool" for m in messages):
            return Message(role="assistant", tool_calls=[ToolCall(name="add", arguments={"a": 2, "b": 3})])
        return Message(role="assistant", content="5")

    agent = Agent(
        provider=MockProvider(response_fn=scripted),
        tools=[add_tool],
        config=AgentConfig(max_iterations=5),
    )
    agent.state.append(Message(role="user", content="what is 2+3?"))
    final_state = await agent.run()

    assert final_state.stop_reason == StopReason.COMPLETED
    tool_messages = [m for m in final_state.messages if m.role == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "5"


async def test_agent_stops_on_budget_exhaustion_without_tool_calls():
    def always_wants_a_tool(messages, tools):
        return Message(role="assistant", tool_calls=[ToolCall(name="noop", arguments={})])

    noop_tool = Tool(
        name="noop",
        description="does nothing",
        input_schema={"type": "object", "properties": {}},
        handler=lambda: "ok",
        concurrency_safe=True,
    )
    agent = Agent(
        provider=MockProvider(response_fn=always_wants_a_tool),
        tools=[noop_tool],
        config=AgentConfig(max_iterations=2, grace_calls=0),
    )
    agent.state.append(Message(role="user", content="loop forever"))
    final_state = await agent.run()
    assert final_state.stop_reason == StopReason.BUDGET_EXHAUSTED
