"""Tool registry: single registration point + single dispatch chokepoint.

Convergent pattern across all three legitimate sources studied. Distinctive
pieces taken from hermes-agent specifically: TTL-cached availability probes
with a flake-suppression grace window (a single transient probe failure
shouldn't strip a tool mid-session), and converting every handler exception
into a structured error result rather than letting it propagate and crash the
loop. Concurrency-safe batching (partition into a parallel-safe batch plus a
serial remainder) is convergent with openclaw/claude-code-style dispatchers.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from dataclasses import dataclass

from aegis_core.tools.base import Tool, ToolResult

DEFAULT_MAX_CONCURRENCY = 8
PROBE_TTL_SECONDS = 30.0
PROBE_FLAKE_GRACE_SECONDS = 60.0


@dataclass
class _ProbeState:
    last_result: bool = True
    last_checked: float = 0.0
    last_success: float = 0.0
    reason: str | None = None


class ToolNotFoundError(Exception):
    pass


class ToolRegistry:
    def __init__(self, max_concurrency: int = DEFAULT_MAX_CONCURRENCY):
        self._tools: dict[str, Tool] = {}
        self._probe_state: dict[str, _ProbeState] = {}
        self.max_concurrency = max_concurrency

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        self._probe_state.setdefault(tool.name, _ProbeState())

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self._probe_state.pop(name, None)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(name) from None

    def try_get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    async def _probe_available(self, tool: Tool) -> bool:
        """TTL-cached availability check with flake suppression: a tool that
        was available recently stays "available" through one probe failure,
        on the assumption a transient blip (e.g. a slow Docker daemon) isn't
        a real removal of the capability."""
        if tool.check_fn is None:
            return True
        state = self._probe_state.setdefault(tool.name, _ProbeState())
        now = time.monotonic()
        if now - state.last_checked < PROBE_TTL_SECONDS:
            return state.last_result

        ok, reason = await tool.is_available_with_reason()
        state.last_checked = now
        if ok:
            state.last_success = now
            state.last_result = True
            state.reason = None
        elif now - state.last_success < PROBE_FLAKE_GRACE_SECONDS:
            # Recent success within the grace window — suppress this failure.
            state.last_result = True
            state.reason = f"transient failure suppressed (grace window): {reason}"
        else:
            state.last_result = False
            state.reason = reason
        return state.last_result

    async def available_tools(self) -> list[Tool]:
        results = await asyncio.gather(
            *(self._probe_available(t) for t in self._tools.values())
        )
        return [t for t, ok in zip(self._tools.values(), results) if ok]

    async def availability_report(self) -> list[dict]:
        """Every tool's current visibility plus *why*, for tools that are
        hidden — openclaw-derived: a hidden tool should always be
        explainable (missing auth, disabled plugin, failed probe), never a
        silent omission. Useful both for debugging and for surfacing to the
        model itself if a domain wants to explain unavailable capabilities."""
        report = []
        for tool in self._tools.values():
            available = await self._probe_available(tool)
            state = self._probe_state.get(tool.name, _ProbeState())
            report.append(
                {"name": tool.name, "available": available, "reason": state.reason}
            )
        return report

    async def dispatch(self, name: str, arguments: dict) -> ToolResult:
        try:
            tool = self.get(name)
        except ToolNotFoundError:
            return ToolResult.failure(f"no such tool: {name!r}")

        if not await self._probe_available(tool):
            return ToolResult.failure(f"tool {name!r} is currently unavailable")

        try:
            raw = await tool.call(**arguments)
        except Exception as exc:  # noqa: BLE001 — deliberate: never let a tool crash the loop
            return ToolResult.failure(f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}")

        if isinstance(raw, ToolResult):
            return raw
        return ToolResult.success(raw)

    async def dispatch_batch(
        self, calls: list[tuple[str, dict]]
    ) -> list[ToolResult]:
        """Partition calls into a concurrency-safe parallel batch and a
        serial remainder, preserving overall call order in the result list."""
        if not calls:
            return []

        safe_indices: list[int] = []
        unsafe_indices: list[int] = []
        for i, (name, _args) in enumerate(calls):
            tool = self._tools.get(name)
            if tool is not None and tool.concurrency_safe:
                safe_indices.append(i)
            else:
                unsafe_indices.append(i)

        results: list[ToolResult | None] = [None] * len(calls)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def run_safe(i: int) -> None:
            name, args = calls[i]
            async with semaphore:
                results[i] = await self.dispatch(name, args)

        if safe_indices:
            await asyncio.gather(*(run_safe(i) for i in safe_indices))

        for i in unsafe_indices:
            name, args = calls[i]
            results[i] = await self.dispatch(name, args)

        return [r for r in results if r is not None]
