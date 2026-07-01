"""Provider adapter interface. See ARCHITECTURE.md section 12.

The loop, tools, and middleware never talk to a vendor SDK directly — only to
this interface — which is what makes "pick any model" actually true.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator

from aegis_core.state import Message, ToolCall


@dataclass
class CompletionChunk:
    delta_text: str = ""
    delta_tool_call: ToolCall | None = None
    finished: bool = False
    # Set on (at latest) the finished chunk once final usage is known — lets
    # the streaming loop path account budget without a separate call.
    usage: CompletionUsage | None = None


@dataclass
class CompletionUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass
class CompletionResponse:
    message: Message
    usage: CompletionUsage = field(default_factory=CompletionUsage)
    stop_reason: str = "stop"


@dataclass
class ToolSchema:
    name: str
    description: str
    input_schema: dict


class Provider(ABC):
    name: str = "provider"

    @abstractmethod
    async def complete(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> CompletionResponse: ...

    async def stream(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AsyncIterator[CompletionChunk]:
        """Default: fakes streaming by yielding the full completion as one
        text chunk plus one delta_tool_call chunk per tool call, all at once
        rather than incrementally. This keeps every provider — even ones
        that only implement complete() — compatible with the loop's
        streaming dispatch path (see loop.py's _call_model_streaming); real
        adapters (e.g. AnthropicProvider) override this for genuine
        token-level / mid-response tool-call streaming."""
        response = await self.complete(
            system_prompt=system_prompt, messages=messages, tools=tools
        )
        if response.message.content:
            yield CompletionChunk(delta_text=response.message.content)
        for tc in response.message.tool_calls:
            yield CompletionChunk(delta_tool_call=tc)
        yield CompletionChunk(finished=True, usage=response.usage)

    def count_tokens(self, messages: list[Message]) -> int:
        """Cheap default approximation; override with the vendor's real
        tokenizer/count endpoint where available."""
        return sum(len(m.content or "") for m in messages) // 4
