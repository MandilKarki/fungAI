"""Tiered context compaction: dedupe -> protected tail -> LLM summarize ->
static fallback, with an anti-thrashing guard.

Source: hermes-agent's staged compressor. See ARCHITECTURE.md section 7.
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from aegis_core.context.engine import ContextEngine
from aegis_core.state import AgentState, Message

TokenCounter = Callable[[list[Message]], int]
Summarizer = Callable[[list[Message]], Awaitable[str]]


def default_token_counter(messages: list[Message]) -> int:
    """Cheap ~4-chars/token approximation. Swap in a real tokenizer via the
    provider adapter for production accuracy."""
    return sum(len(m.content or "") for m in messages) // 4


def _hash_content(content: str | None) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16]


@dataclass
class TieredCompressionEngine(ContextEngine):
    token_budget: int = 100_000
    protected_tail_tokens: int = 20_000
    protected_tail_floor: int = 6  # never compress the last N messages regardless of token count
    min_savings_ratio: float = 0.10
    summarizer: Summarizer | None = None
    token_counter: TokenCounter = field(default=default_token_counter)

    _recent_savings: deque = field(default_factory=lambda: deque(maxlen=2))

    async def should_compress(self, state: AgentState) -> bool:
        return self.token_counter(state.messages) >= self.token_budget

    def _split_protected_tail(
        self, messages: list[Message]
    ) -> tuple[list[Message], list[Message]]:
        """Walk backward accumulating tokens until the tail budget is spent,
        then snap the boundary so a tool_call/tool_result pair is never split."""
        if len(messages) <= self.protected_tail_floor:
            return [], list(messages)

        tail: list[Message] = []
        tokens = 0
        i = len(messages) - 1
        while i >= 0 and (
            tokens < self.protected_tail_tokens or len(tail) < self.protected_tail_floor
        ):
            msg = messages[i]
            tail.insert(0, msg)
            tokens += self.token_counter([msg])
            i -= 1

        boundary = len(messages) - len(tail)
        # A leading tool-result in the tail means its tool_call lives in the
        # head — pull the boundary back so the pair stays together.
        while boundary < len(messages) and boundary > 0 and messages[boundary].role == "tool":
            boundary -= 1
        tail = messages[boundary:]

        return messages[:boundary], tail

    def _dedupe_tool_results(self, head: list[Message]) -> list[Message]:
        seen: set[str] = set()
        result: list[Message] = []
        for msg in head:
            if msg.role != "tool":
                result.append(msg)
                continue
            key = _hash_content(msg.content)
            if key in seen:
                result.append(
                    Message(
                        role="tool",
                        content=f"[duplicate of an earlier identical result, hash={key}]",
                        tool_call_id=msg.tool_call_id,
                        name=msg.name,
                    )
                )
            else:
                seen.add(key)
                result.append(msg)
        return result

    def _static_fallback_summary(self, head: list[Message]) -> str:
        kinds: dict[str, int] = {}
        for m in head:
            kinds[m.role] = kinds.get(m.role, 0) + 1
        parts = ", ".join(f"{count} {role}" for role, count in kinds.items())
        return (
            f"[{len(head)} earlier messages omitted ({parts}) — "
            "summarization unavailable, static fallback used]"
        )

    async def compress(self, state: AgentState) -> AgentState:
        before_tokens = self.token_counter(state.messages)
        head, tail = self._split_protected_tail(state.messages)
        if not head:
            return state

        head = self._dedupe_tool_results(head)

        if self.summarizer is not None:
            try:
                summary_text = await self.summarizer(head)
            except Exception:  # noqa: BLE001 — summarization must never hard-fail a turn
                summary_text = self._static_fallback_summary(head)
        else:
            summary_text = self._static_fallback_summary(head)

        summary_message = Message(
            role="system",
            content=f"[conversation summary of {len(head)} earlier messages]\n{summary_text}",
            metadata={"compaction_summary": True},
        )
        state.messages = [summary_message, *tail]

        after_tokens = self.token_counter(state.messages)
        savings = 1 - (after_tokens / before_tokens) if before_tokens else 0.0
        self._recent_savings.append(savings)
        return state

    def is_thrashing(self) -> bool:
        """True once two consecutive compression passes each saved less than
        min_savings_ratio — signals the caller should stop recompacting every
        turn and instead hard-truncate or surface a budget error."""
        return len(self._recent_savings) == self._recent_savings.maxlen and all(
            s < self.min_savings_ratio for s in self._recent_savings
        )

    async def update_from_response(self, state: AgentState, response) -> None:
        return None
