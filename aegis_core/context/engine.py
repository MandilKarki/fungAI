"""Pluggable context-compaction interface. See ARCHITECTURE.md section 7."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from aegis_core.state import AgentState


class ContextEngine(ABC):
    @abstractmethod
    async def should_compress(self, state: AgentState) -> bool: ...

    @abstractmethod
    async def compress(self, state: AgentState) -> AgentState: ...

    async def update_from_response(self, state: AgentState, response: Any) -> None:
        """Optional hook called after each model response — e.g. to refine a
        token-usage estimate from real usage data the provider returned."""
        return None


class PassthroughContextEngine(ContextEngine):
    """No-op engine. Used as the default for short sessions, and as the safe
    target a misbehaving custom engine should be quarantined to (automatic
    quarantine supervisor is on the roadmap — see ROADMAP.md)."""

    async def should_compress(self, state: AgentState) -> bool:
        return False

    async def compress(self, state: AgentState) -> AgentState:
        return state
