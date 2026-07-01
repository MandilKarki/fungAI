import asyncio

from aegis_core.loop import Agent, AgentConfig
from aegis_core.memory.state_backend import StateBackend
from aegis_core.providers.mock_provider import MockProvider
from aegis_core.state import Message
from aegis_core.subagents.cache_sharing import fork_subagent
from aegis_core.subagents.delegate import DelegationLimiter, DelegationPolicy, DepthExceededError, delegate_task
from aegis_core.subagents.moa import Advisor, gather_advisory_opinions
from aegis_core.subagents.orchestrator import BackgroundOrchestrator, TaskStatus
from aegis_core.subagents.swarm import AgentSwarm, build_swarm_tools


async def test_delegate_task_returns_child_final_text():
    provider = MockProvider(response_fn=lambda m, t: Message(role="assistant", content="child done"))

    def factory(*, tools, memory, system_prompt_extra):
        return Agent(provider=provider, tools=tools, memory=memory, system_prompt_extra=system_prompt_extra)

    limiter = DelegationLimiter(policy=DelegationPolicy(max_spawn_depth=1))
    result = await delegate_task(
        agent_factory=factory,
        description="test task",
        prompt="do the thing",
        tools=[],
        shared_memory=StateBackend(),
        limiter=limiter,
    )
    assert result.ok is True
    assert result.content == "child done"


async def test_delegate_task_respects_depth_limit():
    limiter = DelegationLimiter(policy=DelegationPolicy(max_spawn_depth=1))
    limiter.check_can_spawn(current_depth=0)  # fine
    try:
        limiter.check_can_spawn(current_depth=1)
        assert False, "expected DepthExceededError"
    except DepthExceededError:
        pass


async def test_moa_gathers_all_advisors_even_if_one_fails():
    async def good(messages):
        return "looks fine to me"

    async def bad(messages):
        raise RuntimeError("advisor down")

    opinions = await gather_advisory_opinions([], [Advisor("good", good), Advisor("bad", bad)])
    assert len(opinions) == 2
    contents = [o.content for o in opinions]
    assert any("looks fine" in c for c in contents)
    assert any("failed" in c for c in contents)


async def test_background_orchestrator_reports_completion():
    provider = MockProvider(response_fn=lambda m, t: Message(role="assistant", content="bg result"))
    orch = BackgroundOrchestrator()
    completed = {}

    def on_done(task):
        completed[task.task_id] = task.status

    task = orch.spawn(
        agent_factory=lambda: Agent(provider=provider, config=AgentConfig(max_iterations=2)),
        description="bg test",
        prompt="go",
        on_complete=on_done,
    )
    assert task.status == TaskStatus.RUNNING
    await asyncio.sleep(0.05)
    assert task.status == TaskStatus.COMPLETED
    assert task.result == "bg result"
    assert completed[task.task_id] == TaskStatus.COMPLETED


async def test_swarm_message_delivery():
    swarm = AgentSwarm()
    tools_a = build_swarm_tools(swarm, "agent_a")
    tools_b = build_swarm_tools(swarm, "agent_b")

    send = next(t for t in tools_a if t.name == "send_message")
    check_b = next(t for t in tools_b if t.name == "check_messages")

    await send.call(to="agent_b", content="hello")
    messages = await check_b.call()
    assert messages == [{"from": "agent_a", "content": "hello"}]


def test_fork_subagent_reuses_exact_prompt():
    provider = MockProvider()
    parent = Agent(provider=provider)
    parent.prompt_builder.add_stable_section("Extra", "parent-specific content")

    child = fork_subagent(parent)
    assert child.prompt_builder.build() == parent.prompt_builder.build()
