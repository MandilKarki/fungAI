"""Async/background subagent orchestration: spawn a child agent that runs in
the background, reporting completion via push (an optional callback) rather
than requiring the parent to poll in a loop. See ARCHITECTURE.md section 10.

In-process implementation (an asyncio.Task per background subagent) —
consistent with the rest of aegis_core being a from-scratch, non-LangGraph-
dependent framework; there is no remote deployment target here the way
openclaw's `sessions_spawn`/deepagents' AsyncSubAgentMiddleware target a
LangGraph Platform deployment. The push-based completion model and the
explicit "don't poll" discipline are carried over regardless, since that's
the part that actually matters for not burning the model's turn budget.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Union

from aegis_core.state import Message

if TYPE_CHECKING:
    from aegis_core.loop import Agent


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BackgroundTask:
    task_id: str
    description: str
    status: TaskStatus = TaskStatus.RUNNING
    result: str | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    _asyncio_task: asyncio.Task | None = field(default=None, repr=False, compare=False)

    async def poll(self) -> TaskStatus:
        return self.status

    async def cancel(self) -> None:
        if self._asyncio_task is not None and not self._asyncio_task.done():
            self._asyncio_task.cancel()
        self.status = TaskStatus.CANCELLED
        self.finished_at = time.time()


AgentFactory = Callable[[], Any]  # returns an aegis_core.loop.Agent
OnComplete = Callable[[BackgroundTask], Union[None, Awaitable[None]]]


class BackgroundOrchestrator:
    """Tracks in-flight background subagents. The defining property versus
    the synchronous delegate_task (delegate.py): the caller gets a task_id
    back immediately and is never blocked; completion is reported via an
    optional push callback rather than the caller polling — avoiding the
    "burn the whole turn checking status" anti-pattern that aegis_sentinel's
    planned async tooling explicitly guards against in its own prompts."""

    def __init__(self) -> None:
        self.tasks: dict[str, BackgroundTask] = {}

    def list_tasks(self) -> list[BackgroundTask]:
        return list(self.tasks.values())

    def get(self, task_id: str) -> BackgroundTask | None:
        return self.tasks.get(task_id)

    def spawn(
        self,
        *,
        agent_factory: AgentFactory,
        description: str,
        prompt: str,
        on_complete: OnComplete | None = None,
    ) -> BackgroundTask:
        task_id = uuid.uuid4().hex[:12]
        bg_task = BackgroundTask(task_id=task_id, description=description)
        self.tasks[task_id] = bg_task

        async def _run() -> None:
            try:
                child: "Agent" = agent_factory()
                child.state.append(Message(role="user", content=prompt))
                final_state = await child.run()
                bg_task.result = next(
                    (
                        m.content
                        for m in reversed(final_state.messages)
                        if m.role == "assistant" and m.content
                    ),
                    "",
                )
                bg_task.status = TaskStatus.COMPLETED
            except asyncio.CancelledError:
                bg_task.status = TaskStatus.CANCELLED
                raise
            except Exception as exc:  # noqa: BLE001 — a failed background task must not crash the parent
                bg_task.error = f"{type(exc).__name__}: {exc}"
                bg_task.status = TaskStatus.FAILED
            finally:
                bg_task.finished_at = time.time()
                if on_complete is not None:
                    result = on_complete(bg_task)
                    if asyncio.iscoroutine(result):
                        await result

        bg_task._asyncio_task = asyncio.create_task(_run())
        return bg_task
