"""Middleware pipeline: the composability backbone of aegis_core.

Every cross-cutting concern (permission checks, checkpointing before
destructive ops, audit logging, result eviction, prompt-section injection) is
a Middleware with optional hooks, run in a fixed registration order — rather
than hardcoded into the loop or tool dispatch. See ARCHITECTURE.md section 9.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from aegis_core.state import AgentState, Message


class Middleware:
    """Base class with no-op defaults; override only the hooks you need."""

    name: str = "middleware"

    async def before_tool_call(
        self, *, tool_name: str, arguments: dict[str, Any], state: "AgentState"
    ) -> dict[str, Any] | None:
        """Return replacement arguments, {"_block": reason} to block the
        call, or None to leave arguments unchanged."""
        return None

    async def after_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        state: "AgentState",
    ) -> Any | None:
        """Return a replacement result, or None to leave it unchanged."""
        return None

    async def wrap_model_call(
        self,
        *,
        messages: list["Message"],
        state: "AgentState",
        call_next: Callable[[list["Message"]], Awaitable[Any]],
    ) -> Any:
        """Override to inspect/rewrite messages before the call, or the
        response after. Must call and return call_next(messages) (or a
        rewritten message list) to continue the chain."""
        return await call_next(messages)


class ToolCallBlocked(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class MiddlewarePipeline:
    def __init__(self, middlewares: list[Middleware] | None = None):
        self.middlewares: list[Middleware] = list(middlewares or [])

    def add(self, mw: Middleware) -> "MiddlewarePipeline":
        self.middlewares.append(mw)
        return self

    async def run_before_tool_call(
        self, *, tool_name: str, arguments: dict[str, Any], state: "AgentState"
    ) -> dict[str, Any]:
        current = arguments
        for mw in self.middlewares:
            result = await mw.before_tool_call(
                tool_name=tool_name, arguments=current, state=state
            )
            if result is not None:
                if "_block" in result:
                    raise ToolCallBlocked(str(result["_block"]))
                current = result
        return current

    async def run_after_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        state: "AgentState",
    ) -> Any:
        current = result
        for mw in self.middlewares:
            updated = await mw.after_tool_call(
                tool_name=tool_name, arguments=arguments, result=current, state=state
            )
            if updated is not None:
                current = updated
        return current

    async def run_model_call(
        self,
        *,
        messages: list["Message"],
        state: "AgentState",
        call_fn: Callable[[list["Message"]], Awaitable[Any]],
    ) -> Any:
        async def make_handler(
            index: int, msgs: list["Message"]
        ) -> Any:
            if index >= len(self.middlewares):
                return await call_fn(msgs)
            mw = self.middlewares[index]

            async def call_next(next_msgs: list["Message"]) -> Any:
                return await make_handler(index + 1, next_msgs)

            return await mw.wrap_model_call(
                messages=msgs, state=state, call_next=call_next
            )

        return await make_handler(0, messages)
