"""Background skill-review self-improvement loop: after a turn, fork the
agent (reusing its prompt-cache prefix via subagents.cache_sharing)
restricted to memory/skill-writing tools only, and ask it whether this
turn's experience is worth turning into a new skill or memory update.

Source: hermes-agent's background_review.py. See ARCHITECTURE.md / ROADMAP.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aegis_core.memory.backend import BackendProtocol
from aegis_core.state import AgentState, Message
from aegis_core.subagents.cache_sharing import freeze_prompt
from aegis_core.tools.base import Tool

if TYPE_CHECKING:
    from aegis_core.loop import Agent, AgentConfig

REVIEW_PROMPT_TEMPLATE = """\
Review the most recent turn of this session for anything durably useful:
- A reusable procedure that should become a new skill (write_skill).
- A fact about the user, environment, or codebase worth remembering
  (write_memory).
- Otherwise, do nothing — most turns are not worth recording.

Be conservative: only write something if it would clearly help a future
session. Do not record one-off details specific only to this exact request.

Most recent turn:
{transcript}
"""


def _render_recent_turn(state: AgentState, max_messages: int = 12) -> str:
    recent = state.messages[-max_messages:]
    lines = []
    for m in recent:
        if m.role == "tool":
            lines.append(f"[tool:{m.name}] {(m.content or '')[:500]}")
        elif m.content:
            lines.append(f"[{m.role}] {m.content[:1000]}")
    return "\n".join(lines)


@dataclass
class SkillReviewResult:
    ran: bool
    summary: str | None = None


async def run_background_skill_review(
    parent: "Agent",
    *,
    memory_tools: list[Tool],
    config: "AgentConfig | None" = None,
) -> SkillReviewResult:
    """Spawn a cache-sharing-prompt reviewer restricted to `memory_tools`
    (typically write_skill/write_memory only — never the parent's full
    toolset) and let it decide whether to record anything from the most
    recent turn. A failure here must never affect the parent's own turn, so
    any exception is swallowed and reported in the result instead of
    propagating."""
    from aegis_core.loop import Agent  # local import: avoids a loop<->self_improvement cycle

    try:
        reviewer = Agent(
            provider=parent.provider,
            tools=memory_tools,
            memory=parent.memory,
            context_engine=parent.context_engine,
            prompt_builder=freeze_prompt(parent.prompt_builder),
            config=config or parent.config,
        )
        transcript = _render_recent_turn(parent.state)
        reviewer.state.append(
            Message(role="user", content=REVIEW_PROMPT_TEMPLATE.format(transcript=transcript))
        )
        final_state = await reviewer.run()
        summary = next(
            (
                m.content
                for m in reversed(final_state.messages)
                if m.role == "assistant" and m.content
            ),
            None,
        )
        return SkillReviewResult(ran=True, summary=summary)
    except Exception as exc:  # noqa: BLE001 — a failed self-review must never break the parent's turn
        return SkillReviewResult(ran=False, summary=f"review failed: {exc}")


def build_memory_writing_tools(memory: BackendProtocol) -> list[Tool]:
    """Default write_skill/write_memory tools, sufficient to use
    run_background_skill_review out of the box. Callers wanting different
    storage conventions can supply their own memory_tools instead."""

    async def _write_skill(name: str, description: str, body: str) -> str:
        content = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
        path = f"/skills/{name}/SKILL.md"
        await memory.write(path, content)
        return f"wrote skill to {path}"

    async def _write_memory(note: str) -> str:
        path = "/memory/MEMORY.md"
        try:
            existing = await memory.read(path)
        except Exception:  # noqa: BLE001 — no memory file yet
            existing = ""
        await memory.write(path, existing + f"\n- {note}")
        return f"appended note to {path}"

    return [
        Tool(
            name="write_skill",
            description="Record a new reusable skill learned from this session.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["name", "description", "body"],
            },
            handler=_write_skill,
            concurrency_safe=False,
            owner="core",
        ),
        Tool(
            name="write_memory",
            description="Append a durable fact or note worth remembering across future sessions.",
            input_schema={
                "type": "object",
                "properties": {"note": {"type": "string"}},
                "required": ["note"],
            },
            handler=_write_memory,
            concurrency_safe=False,
            owner="core",
        ),
    ]
