"""Isolated child-agent delegation. See ARCHITECTURE.md section 10.

Isolation model is deepagents-derived (judged the cleanest of those studied):
the child gets fresh message history — only its task description, never the
parent's transcript — but shares the parent's memory backend, so its work
becomes visible to the parent through files rather than through a flood of
intermediate tool-call messages. Depth and concurrency bounds are always
enforced, never optional (convergent across all three legitimate sources).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from aegis_core.memory.backend import BackendProtocol
from aegis_core.state import Message
from aegis_core.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from aegis_core.loop import Agent


class DepthExceededError(Exception):
    pass


class ConcurrencyExceededError(Exception):
    pass


@dataclass
class DelegationPolicy:
    max_spawn_depth: int = 1
    max_concurrent_children: int = 3
    # An orchestrator-role child may itself call delegate_task (recursive
    # orchestration, up to max_spawn_depth). Off by default — convergent
    # finding that delegation should be deliberately constrained, not assumed.
    allow_orchestrator_role: bool = False


@dataclass
class DelegationLimiter:
    policy: DelegationPolicy = field(default_factory=DelegationPolicy)
    _active_children: int = field(default=0, init=False)

    def check_can_spawn(self, current_depth: int) -> None:
        if current_depth >= self.policy.max_spawn_depth:
            raise DepthExceededError(
                f"max_spawn_depth={self.policy.max_spawn_depth} reached at depth {current_depth}"
            )
        if self._active_children >= self.policy.max_concurrent_children:
            raise ConcurrencyExceededError(
                f"max_concurrent_children={self.policy.max_concurrent_children} reached"
            )

    def enter(self) -> None:
        self._active_children += 1

    def exit(self) -> None:
        self._active_children = max(0, self._active_children - 1)


# Kept loose (not typed as Agent directly) to avoid a loop.py <-> subagents
# import cycle; agent_factory is expected to return an aegis_core.loop.Agent.
AgentFactory = Callable[..., Any]


async def delegate_task(
    *,
    agent_factory: AgentFactory,
    description: str,
    prompt: str,
    tools: list[Tool],
    shared_memory: BackendProtocol,
    limiter: DelegationLimiter,
    current_depth: int = 0,
    role: str = "worker",
    extra_system_prompt: str | None = None,
) -> ToolResult:
    """Spawn an isolated child agent for one bounded subtask and return its
    final text response as a ToolResult (so this function is usable directly
    as a Tool handler)."""

    limiter.check_can_spawn(current_depth)
    if role == "orchestrator" and not limiter.policy.allow_orchestrator_role:
        return ToolResult.failure("orchestrator role not permitted by delegation policy")

    limiter.enter()
    try:
        child: "Agent" = agent_factory(
            tools=tools,
            memory=shared_memory,
            system_prompt_extra=extra_system_prompt
            or f"You are a delegated subagent. Task: {description}",
        )
        child.state.append(Message(role="user", content=prompt))
        final_state = await child.run()
        final_text = next(
            (m.content for m in reversed(final_state.messages) if m.role == "assistant" and m.content),
            "",
        )
        return ToolResult.success(final_text)
    except Exception as exc:  # noqa: BLE001 — a failed child must not crash the parent's turn
        return ToolResult.failure(f"delegated task failed: {exc}")
    finally:
        limiter.exit()
