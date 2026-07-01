"""Agent state: messages, pending-message queues, stop reasons.

Modeled as an explicit, inspectable state object rather than implicit call-stack
state, so every transition in the loop (loop.py) is auditable. See
ARCHITECTURE.md section 4.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")


@dataclass(slots=True)
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # set when role == "tool"
    name: str | None = None  # tool name, set when role == "tool"
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class DrainMode(str, Enum):
    ALL = "all"
    ONE_AT_A_TIME = "one_at_a_time"


@dataclass
class PendingMessageQueue:
    """Queue for messages injected mid-session.

    Two distinct instances of this exist on every AgentState (steering and
    follow-up) because hermes-agent and openclaw independently converged on
    needing both, with different semantics:

    - steering: drained *before* the next model call, so a human can correct
      course mid-turn without waiting for the agent to finish.
    - follow-up: drained only once the agent would otherwise stop, so a queued
      message doesn't interrupt work already in flight.
    """

    drain_mode: DrainMode = DrainMode.ALL
    _items: list[Message] = field(default_factory=list)

    def push(self, message: Message) -> None:
        self._items.append(message)

    def drain(self) -> list[Message]:
        if not self._items:
            return []
        if self.drain_mode is DrainMode.ALL:
            items, self._items = self._items, []
            return items
        return [self._items.pop(0)]

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)


class StopReason(str, Enum):
    COMPLETED = "completed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ABORTED = "aborted"
    BLOCKED_BY_HOOK = "blocked_by_hook"
    ERROR = "error"


@dataclass
class AgentState:
    messages: list[Message] = field(default_factory=list)
    steering_queue: PendingMessageQueue = field(
        default_factory=lambda: PendingMessageQueue(DrainMode.ALL)
    )
    followup_queue: PendingMessageQueue = field(
        default_factory=lambda: PendingMessageQueue(DrainMode.ONE_AT_A_TIME)
    )
    iteration: int = 0
    aborted: bool = False
    stop_reason: StopReason | None = None
    # Free-form scratch space for middleware/tools to stash per-session data
    # (e.g. a chain-of-custody log, a running todo list) without the loop
    # needing to know about it.
    scratch: dict[str, Any] = field(default_factory=dict)

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def request_abort(self) -> None:
        self.aborted = True
