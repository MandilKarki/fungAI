"""Peer-messaging "swarm": agents addressable by name, able to message each
other through an in-process bus rather than only through a parent/child
delegation hierarchy. Source: hermes-agent's agent-swarm/teammates concept.
See ARCHITECTURE.md / ROADMAP.md.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from aegis_core.state import Message
from aegis_core.tools.base import Tool


@dataclass
class SwarmMailbox:
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)

    async def send(self, message: Message) -> None:
        await self.queue.put(message)

    async def receive(self, timeout: float | None = None) -> Message | None:
        try:
            if timeout is None:
                return await self.queue.get()
            return await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None


class AgentSwarm:
    """A registry of named mailboxes. Any agent holding a reference to the
    swarm can send a message to any other named participant; each
    participant drains its own mailbox on its own schedule (typically via
    the `check_messages` tool built by build_swarm_tools), which keeps the
    swarm decoupled from any one agent's run() loop — no agent's mailbox
    push can interrupt another agent mid-turn."""

    def __init__(self) -> None:
        self._mailboxes: dict[str, SwarmMailbox] = {}

    def register(self, name: str) -> SwarmMailbox:
        return self._mailboxes.setdefault(name, SwarmMailbox())

    def participants(self) -> list[str]:
        return list(self._mailboxes.keys())

    async def send(self, *, to: str, sender: str, content: str) -> bool:
        mailbox = self._mailboxes.get(to)
        if mailbox is None:
            return False
        await mailbox.send(Message(role="user", content=content, metadata={"from": sender}))
        return True


def build_swarm_tools(swarm: AgentSwarm, self_name: str) -> list[Tool]:
    """Registers `self_name` in the swarm and returns the send_message /
    check_messages tool pair bound to that identity, ready to hand to
    Agent(tools=[...])."""
    mailbox = swarm.register(self_name)

    async def _send_message(to: str, content: str) -> str:
        ok = await swarm.send(to=to, sender=self_name, content=content)
        return "sent" if ok else f"no such participant: {to!r}"

    async def _check_messages() -> list[dict]:
        messages = []
        while True:
            msg = await mailbox.receive(timeout=0.01)
            if msg is None:
                break
            messages.append({"from": msg.metadata.get("from"), "content": msg.content})
        return messages

    return [
        Tool(
            name="send_message",
            description=(
                f"Send a message to another named agent in the swarm. "
                f"Current participants: {swarm.participants()}"
            ),
            input_schema={
                "type": "object",
                "properties": {"to": {"type": "string"}, "content": {"type": "string"}},
                "required": ["to", "content"],
            },
            handler=_send_message,
            concurrency_safe=True,
            owner="core",
        ),
        Tool(
            name="check_messages",
            description="Drain any pending messages other agents in the swarm have sent you.",
            input_schema={"type": "object", "properties": {}},
            handler=_check_messages,
            concurrency_safe=True,
            owner="core",
        ),
    ]
