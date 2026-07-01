"""Tiered, cache-aware system prompt assembly. See ARCHITECTURE.md section 8.

Convergent across all three legitimate sources studied: stable content first
(cached for the session), volatile content last, an explicit boundary marker
between them so prefix-caching backends get maximum reuse turn over turn.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable

CACHE_BOUNDARY_MARKER = "<!-- aegis:cache-boundary -->"


@dataclass
class PromptSection:
    name: str
    content: str
    tier: str  # "stable" | "context" | "volatile"


@dataclass
class SystemPromptBuilder:
    identity: str = "You are a capable, careful AI agent."
    operating_principles: list[str] = field(default_factory=list)
    stable_extra: list[PromptSection] = field(default_factory=list)
    context_sections: list[PromptSection] = field(default_factory=list)
    volatile_providers: list[Callable[[], str]] = field(default_factory=list)
    insert_cache_boundary: bool = True
    clock: Callable[[], dt.datetime] = field(
        default=lambda: dt.datetime.now(dt.timezone.utc)
    )

    def add_stable_section(self, name: str, content: str) -> None:
        self.stable_extra.append(PromptSection(name, content, "stable"))

    def add_context_section(self, name: str, content: str) -> None:
        self.context_sections.append(PromptSection(name, content, "context"))

    def set_context_section(self, name: str, content: str) -> None:
        """Replace a context section by name if it exists, else append it —
        for content refreshed every turn (e.g. a skills index) rather than
        added once, so it doesn't accumulate duplicate sections."""
        for i, section in enumerate(self.context_sections):
            if section.name == name:
                self.context_sections[i] = PromptSection(name, content, "context")
                return
        self.add_context_section(name, content)

    def add_volatile_provider(self, provider: Callable[[], str]) -> None:
        """`provider` is called fresh on every build() — for content that
        changes turn to turn (memory snapshot, session metadata)."""
        self.volatile_providers.append(provider)

    def _date_precision_timestamp(self) -> str:
        # Deliberately date-, not minute-, precision: a finer-grained
        # timestamp invalidates the prompt cache every turn for no benefit.
        return self.clock().strftime("%Y-%m-%d")

    def build(self) -> str:
        stable_parts = [self.identity]
        if self.operating_principles:
            stable_parts.append(
                "Operating principles:\n"
                + "\n".join(f"- {p}" for p in self.operating_principles)
            )
        for section in self.stable_extra:
            stable_parts.append(f"## {section.name}\n{section.content}")

        context_parts = [f"## {s.name}\n{s.content}" for s in self.context_sections]

        volatile_parts = [f"Date: {self._date_precision_timestamp()}"]
        for provider in self.volatile_providers:
            text = provider()
            if text:
                volatile_parts.append(text)

        cached_block = "\n\n".join(stable_parts + context_parts)
        volatile_block = "\n\n".join(volatile_parts)

        if self.insert_cache_boundary:
            return f"{cached_block}\n\n{CACHE_BOUNDARY_MARKER}\n\n{volatile_block}"
        return f"{cached_block}\n\n{volatile_block}"
