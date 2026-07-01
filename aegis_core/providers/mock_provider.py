"""A deterministic, no-network provider for tests and examples.

Never used in real aegis_sentinel deployments — exists so the framework (and
anything built on it) can be exercised and smoke-tested without API keys.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from aegis_core.providers.base import (
    CompletionResponse,
    CompletionUsage,
    Provider,
    ToolSchema,
)
from aegis_core.state import Message

ResponseFn = Callable[[list[Message], list[ToolSchema]], Message]


@dataclass
class MockProvider(Provider):
    """Replays a fixed script of responses, or calls `response_fn` if given."""

    script: deque = field(default_factory=deque)
    response_fn: ResponseFn | None = None
    name: str = "mock"

    async def complete(
        self, *, system_prompt: str, messages: list[Message], tools: list[ToolSchema]
    ) -> CompletionResponse:
        if self.response_fn is not None:
            message = self.response_fn(messages, tools)
        elif self.script:
            message = self.script.popleft()
        else:
            message = Message(role="assistant", content="(mock provider: no script left)")
        return CompletionResponse(
            message=message, usage=CompletionUsage(input_tokens=10, output_tokens=10)
        )
