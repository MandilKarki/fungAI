"""OpenAI provider adapter. Optional dependency: `pip install
aegis-agent[openai]`. See ARCHITECTURE.md section 12.
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


def _to_openai_messages(system_prompt: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if m.role == "tool":
            out.append(
                {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""}
            )
        elif m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content or ""})
    return out


def _to_openai_tools(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
        }
        for t in tools
    ]


@dataclass
class OpenAIProvider(Provider):
    model: str = "gpt-5"
    api_key: str | None = None
    max_tokens: int = 4096
    name: str = "openai"

    def __post_init__(self) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "OpenAIProvider requires the 'openai' package: pip install 'aegis-agent[openai]'"
            ) from exc

    async def complete(
        self, *, system_prompt: str, messages: list[Message], tools: list[ToolSchema]
    ) -> CompletionResponse:
        """Retries transient failures (rate limits, connection errors, 5xx)
        with exponential backoff via tenacity — see AnthropicProvider.complete
        for why this isn't applied to stream() too."""
        import openai
        from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(
                (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)
            ),
            reraise=True,
        ):
            with attempt:
                client = openai.AsyncOpenAI(api_key=self.api_key)
                response = await client.chat.completions.create(
                    model=self.model,
                    max_completion_tokens=self.max_tokens,
                    messages=_to_openai_messages(system_prompt, messages),
                    tools=_to_openai_tools(tools) if tools else openai.NOT_GIVEN,
                )
        choice = response.choices[0]
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=json.loads(tc.function.arguments or "{}"))
            for tc in (choice.message.tool_calls or [])
        ]
        message = Message(role="assistant", content=choice.message.content, tool_calls=tool_calls)
        prompt_details = getattr(response.usage, "prompt_tokens_details", None)
        usage = CompletionUsage(
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            cached_input_tokens=getattr(prompt_details, "cached_tokens", 0) or 0,
        )
        return CompletionResponse(
            message=message, usage=usage, stop_reason=choice.finish_reason or "stop"
        )

    async def stream(
        self, *, system_prompt: str, messages: list[Message], tools: list[ToolSchema]
    ) -> AsyncIterator[CompletionChunk]:
        """Real incremental streaming via the Chat Completions streaming
        API. Note OpenAI's API (unlike Anthropic's) doesn't signal an
        individual tool call as "done" mid-stream — argument fragments for
        all tool calls accumulate together and are only finalized when
        finish_reason appears at the very end — so unlike
        AnthropicProvider.stream(), tool calls here surface together at
        stream end rather than one at a time mid-response. That's an
        accurate reflection of the API's actual semantics, not a
        simplification. Written against the documented streaming chunk
        shape; not exercised against a live API key in this environment.
        """
        import openai

        client = openai.AsyncOpenAI(api_key=self.api_key)
        stream = await client.chat.completions.create(
            model=self.model,
            max_completion_tokens=self.max_tokens,
            messages=_to_openai_messages(system_prompt, messages),
            tools=_to_openai_tools(tools) if tools else openai.NOT_GIVEN,
            stream=True,
            stream_options={"include_usage": True},
        )

        accumulating: dict[int, dict[str, Any]] = {}
        usage = CompletionUsage()

        async for chunk in stream:
            if chunk.usage is not None:
                usage = CompletionUsage(
                    input_tokens=chunk.usage.prompt_tokens,
                    output_tokens=chunk.usage.completion_tokens,
                )
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield CompletionChunk(delta_text=delta.content)
            for tc_delta in delta.tool_calls or []:
                slot = accumulating.setdefault(
                    tc_delta.index, {"id": None, "name": None, "args_fragments": []}
                )
                if tc_delta.id:
                    slot["id"] = tc_delta.id
                if tc_delta.function and tc_delta.function.name:
                    slot["name"] = tc_delta.function.name
                if tc_delta.function and tc_delta.function.arguments:
                    slot["args_fragments"].append(tc_delta.function.arguments)
            if chunk.choices[0].finish_reason is not None:
                for slot in accumulating.values():
                    raw_args = "".join(slot["args_fragments"]) or "{}"
                    try:
                        arguments = json.loads(raw_args)
                    except json.JSONDecodeError:
                        arguments = {}
                    yield CompletionChunk(
                        delta_tool_call=ToolCall(
                            id=slot["id"], name=slot["name"], arguments=arguments
                        )
                    )

        yield CompletionChunk(finished=True, usage=usage)
