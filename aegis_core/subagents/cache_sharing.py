"""Cache-sharing fork subagents: spawn a child that reuses the parent's
exact rendered system prompt and full tool registry, so the child's first
request has a byte-identical prefix to the parent's last request and the
provider's prompt cache can be reused.

Source: hermes-agent — background summarization/compaction forks the live
session specifically for this reason, rather than rebuilding a prompt from
scratch and paying full input-token cost. Contrast with
subagents.delegate.delegate_task, which deliberately does the opposite
(fresh, isolated prompt + restricted tools) — this is for when reuse, not
isolation, is the point. See ARCHITECTURE.md / ROADMAP.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aegis_core.loop import Agent, AgentConfig
    from aegis_core.prompts.builder import SystemPromptBuilder


@dataclass
class FrozenPromptBuilder:
    """Stands in for a SystemPromptBuilder: build() always returns the same
    already-rendered string captured at freeze time. add_stable_section is a
    deliberate no-op (not an error) — the whole point of a frozen builder is
    that nothing further changes it; if the parent's prompt already includes
    e.g. model-family guidance, the frozen copy already has it too."""

    frozen_prompt: str

    def build(self) -> str:
        return self.frozen_prompt

    def add_stable_section(self, *_args, **_kwargs) -> None:
        return None

    def add_context_section(self, *_args, **_kwargs) -> None:
        return None

    def set_context_section(self, *_args, **_kwargs) -> None:
        return None

    def add_volatile_provider(self, *_args, **_kwargs) -> None:
        return None


def freeze_prompt(parent_prompt_builder: "SystemPromptBuilder") -> FrozenPromptBuilder:
    return FrozenPromptBuilder(frozen_prompt=parent_prompt_builder.build())


def fork_subagent(parent: "Agent", *, config: "AgentConfig | None" = None) -> "Agent":
    """Construct a child Agent sharing the parent's provider, exact rendered
    system prompt, full tool registry, and memory backend. Used for
    cache-economical forks (e.g. a compaction/summarization pass over the
    same session) where reuse, not isolation, is the goal."""
    from aegis_core.loop import Agent  # local import: avoids a loop<->subagents cycle

    return Agent(
        provider=parent.provider,
        tools=parent.registry.all_tools(),
        memory=parent.memory,
        context_engine=parent.context_engine,
        prompt_builder=freeze_prompt(parent.prompt_builder),
        config=config or parent.config,
    )
