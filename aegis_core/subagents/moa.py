"""Mixture-of-Agents advisory fan-out: non-acting reference opinions folded
into the next turn. See ARCHITECTURE.md section 10.

Distinct from delegate_task: advisors cannot call tools or act, only respond
with text. Cheap way to get ensemble reasoning (e.g. a second opinion on a
severity classification) without giving up single-agent execution control.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from aegis_core.state import Message

AdvisorCall = Callable[[list[Message]], Awaitable[str]]


@dataclass
class Advisor:
    name: str
    call: AdvisorCall


async def gather_advisory_opinions(
    messages: list[Message], advisors: list[Advisor]
) -> list[Message]:
    """Fan the current context out to N advisors concurrently. Returns
    system messages ready to fold into the acting model's next turn — the
    caller decides whether/how to surface them, they are not appended to the
    transcript automatically."""

    async def run_one(advisor: Advisor) -> Message:
        try:
            text = await advisor.call(messages)
        except Exception as exc:  # noqa: BLE001 — one failed advisor shouldn't sink the round
            text = f"[advisor {advisor.name} failed: {exc}]"
        return Message(
            role="system",
            content=f"[advisory opinion from {advisor.name}]\n{text}",
            metadata={"moa_advisor": advisor.name},
        )

    return list(await asyncio.gather(*(run_one(a) for a in advisors)))
