"""Tool definition. See ARCHITECTURE.md section 5."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union

Handler = Callable[..., Union[Any, Awaitable[Any]]]
CheckFn = Callable[[], Union[bool, Awaitable[bool]]]


@dataclass
class ToolResult:
    """Uniform wrapper so the loop never has to special-case a tool's return
    shape. `ok=False` results are still valid messages, not exceptions — the
    model sees the error and can react to it."""

    ok: bool
    content: Any
    error: str | None = None

    @classmethod
    def success(cls, content: Any) -> "ToolResult":
        return cls(ok=True, content=content)

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        return cls(ok=False, content=None, error=error)

    def to_model_text(self) -> str:
        if not self.ok:
            return f"Error: {self.error}"
        if isinstance(self.content, str):
            return self.content
        try:
            return json.dumps(self.content, default=str)
        except TypeError:
            return str(self.content)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    concurrency_safe: bool = False
    requires_approval: bool = False
    owner: str = "core"  # core | plugin | domain — mirrors openclaw's tool ownership tag
    deferred: bool = False  # if True, full schema withheld until tool_search resolves it
    check_fn: CheckFn | None = None

    async def call(self, **kwargs: Any) -> Any:
        result = self.handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def is_available(self) -> bool:
        ok, _reason = await self.is_available_with_reason()
        return ok

    async def is_available_with_reason(self) -> tuple[bool, str | None]:
        """check_fn may return a plain bool, or a (bool, reason) tuple when
        it wants to explain *why* it's unavailable — that reason flows
        through to ToolRegistry.availability_report() so a hidden tool is
        always explainable, never silently dropped."""
        if self.check_fn is None:
            return True, None
        result = self.check_fn()
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, tuple):
            ok, reason = result
            return bool(ok), reason
        ok = bool(result)
        return ok, (None if ok else f"check_fn for {self.name!r} returned False")
