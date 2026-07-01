"""Deferred/searchable tools: a meta-tool that resolves full schemas on demand.

Source: generic technique for controlling prompt-token cost as a tool catalog
grows, also seen in hermes-agent's tool-search bridge (triggered once a
registry passes a tool-count threshold). Opt-in strategy, not forced — small
tool catalogs should just send full schemas, the indirection isn't free.
"""

from __future__ import annotations

from dataclasses import dataclass

from aegis_core.tools.base import Tool
from aegis_core.tools.registry import ToolRegistry

DEFAULT_DEFER_THRESHOLD = 40


@dataclass
class ToolSearchResult:
    name: str
    description: str
    owner: str


class ToolSearchIndex:
    """Wraps a ToolRegistry to decide which tools are sent in full vs.
    deferred behind a `tool_search` / `tool_describe` pair."""

    def __init__(self, registry: ToolRegistry, defer_threshold: int = DEFAULT_DEFER_THRESHOLD):
        self.registry = registry
        self.defer_threshold = defer_threshold
        # Names resolved via resolve() this session stay visible for the
        # rest of the session — resolving is sticky, not per-call.
        self._resolved_names: set[str] = set()

    def should_defer_catalog(self) -> bool:
        return len(self.registry.all_tools()) >= self.defer_threshold

    def visible_tools(self) -> list[Tool]:
        """Tools sent with full schema in the prompt: always-load tools,
        anything already resolved this session, plus everything if the
        catalog is small enough not to bother deferring."""
        all_tools = self.registry.all_tools()
        if not self.should_defer_catalog():
            return all_tools
        return [
            t for t in all_tools if not t.deferred or t.name in self._resolved_names
        ]

    def search(self, query: str) -> list[ToolSearchResult]:
        query_lower = query.lower()
        if query_lower.startswith("select:"):
            names = {n.strip() for n in query_lower.removeprefix("select:").split(",")}
            matches = [t for t in self.registry.all_tools() if t.name.lower() in names]
        else:
            matches = [
                t
                for t in self.registry.all_tools()
                if query_lower in t.name.lower() or query_lower in t.description.lower()
            ]
        return [ToolSearchResult(t.name, t.description, t.owner) for t in matches]

    def resolve(self, names: list[str]) -> list[Tool]:
        resolved = []
        for n in names:
            try:
                tool = self.registry.get(n)
            except Exception:  # noqa: BLE001
                continue
            self._resolved_names.add(n)
            resolved.append(tool)
        return resolved
