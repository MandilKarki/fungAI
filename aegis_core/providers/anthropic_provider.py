"""Anthropic provider adapter. Optional dependency: `pip install
aegis-agent[anthropic]`. See ARCHITECTURE.md section 12.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from aegis_core.providers.base import (
    CompletionChunk,
    CompletionResponse,
    CompletionUsage,
    Provider,
    ToolSchema,
)
from aegis_core.state import Message, ToolCall


def _to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id,
                            "content": m.content or "",
                        }
                    ],
                }
            )
        elif m.role == "assistant" and m.tool_calls:
            content: list[dict[str, Any]] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                content.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            out.append({"role": "assistant", "content": content})
        elif m.role == "system":
            # System messages mid-transcript (e.g. compaction summaries) are
            # not a distinct role in the Anthropic API; fold them in as user
            # turns tagged so the model can tell them apart from real user input.
            out.append({"role": "user", "content": f"[system note] {m.content or ''}"})
        else:
            out.append({"role": m.role, "content": m.content or ""})
    return out


def _to_anthropic_tools(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]


@dataclass
class AnthropicProvider(Provider):
    model: str = "claude-sonnet-4-5"
    api_key: str | None = None
    max_tokens: int = 4096
    name: str = "anthropic"

    def __post_init__(self) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "AnthropicProvider requires the 'anthropic' package: "
                "pip install 'aegis-agent[anthropic]'"
            ) from exc

    async def complete(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> CompletionResponse:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        response = await client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=_to_anthropic_messages(messages),
            tools=_to_anthropic_tools(tools) if tools else anthropic.NOT_GIVEN,
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input)
                )

        message = Message(
            role="assistant",
            content="".join(text_parts) or None,
            tool_calls=tool_calls,
        )
        usage = CompletionUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cached_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        return CompletionResponse(
            message=message, usage=usage, stop_reason=response.stop_reason or "stop"
        )

    async def stream(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AsyncIterator[CompletionChunk]:
        """Genuine token-level streaming, with each tool_use content block
        dispatched as a delta_tool_call chunk the moment it finishes — not
        only once the whole response completes. This is what lets
        aegis_core.loop.Agent's streaming path (AgentConfig.enable_streaming)
        start executing a tool call while the model is still emitting
        subsequent content. Written against the documented Anthropic SDK
        streaming event shape (content_block_start/delta/stop,
        message_delta); not exercised against a live API key in this
        environment, so treat as structurally verified, not live-tested.
        """
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        anthropic_tools = _to_anthropic_tools(tools) if tools else anthropic.NOT_GIVEN

        current_tool: dict[str, Any] | None = None
        usage = CompletionUsage()

        async with client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=_to_anthropic_messages(messages),
            tools=anthropic_tools,
        ) as stream:
            async for event in stream:
                if event.type == "content_block_start" and event.content_block.type == "tool_use":
                    current_tool = {
                        "id": event.content_block.id,
                        "name": event.content_block.name,
                        "fragments": [],
                    }
                elif event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield CompletionChunk(delta_text=event.delta.text)
                    elif event.delta.type == "input_json_delta" and current_tool is not None:
                        current_tool["fragments"].append(event.delta.partial_json)
                elif event.type == "content_block_stop" and current_tool is not None:
                    raw_json = "".join(current_tool["fragments"]) or "{}"
                    try:
                        arguments = json.loads(raw_json)
                    except json.JSONDecodeError:
                        arguments = {}
                    yield CompletionChunk(
                        delta_tool_call=ToolCall(
                            id=current_tool["id"], name=current_tool["name"], arguments=arguments
                        )
                    )
                    current_tool = None

            final_message = await stream.get_final_message()
            usage = CompletionUsage(
                input_tokens=final_message.usage.input_tokens,
                output_tokens=final_message.usage.output_tokens,
                cached_input_tokens=getattr(final_message.usage, "cache_read_input_tokens", 0)
                or 0,
            )

        yield CompletionChunk(finished=True, usage=usage)
