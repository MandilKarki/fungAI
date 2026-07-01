"""The core agent loop: an explicit, auditable state machine.

See ARCHITECTURE.md section 4. One iteration: drain steering messages ->
maybe compact context -> check budget (grace nudge if nearly exhausted) ->
call the model (streaming, dispatching tool calls as they arrive when safe
to do so, or non-streaming) -> dispatch any remaining tool calls -> drain
follow-up messages if the agent would otherwise stop -> loop, or terminate
with a typed stop reason.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from aegis_core.budget import IterationBudget
from aegis_core.context.engine import ContextEngine, PassthroughContextEngine
from aegis_core.memory.backend import BackendProtocol
from aegis_core.memory.state_backend import StateBackend
from aegis_core.middleware import MiddlewarePipeline, ToolCallBlocked
from aegis_core.permissions.approval import AskCallback, ApprovalPolicy
from aegis_core.persistence.session_store import SQLiteSessionStore
from aegis_core.prompts.builder import SystemPromptBuilder
from aegis_core.prompts.model_guidance import guidance_for_model
from aegis_core.prompts.skills import SkillCatalog
from aegis_core.providers.base import CompletionResponse, CompletionUsage, Provider, ToolSchema
from aegis_core.state import AgentState, Message, StopReason, ToolCall
from aegis_core.tools.base import Tool, ToolResult
from aegis_core.tools.registry import ToolRegistry
from aegis_core.tools.search import ToolSearchIndex

GRACE_NUDGE = (
    "Your iteration budget is nearly exhausted. Wrap up now: summarize what "
    "you've found/done so far and stop calling tools unless one more call is "
    "strictly necessary to finish."
)


@dataclass
class AgentConfig:
    max_iterations: int = 50
    max_tokens_budget: int | None = None
    grace_calls: int = 1
    max_tool_concurrency: int = 8
    # Per-turn streaming dispatch: tool calls execute as the model emits
    # them rather than only after the full response finishes. Default off
    # so existing non-streaming providers/tests keep their exact behavior;
    # AnthropicProvider.stream() implements genuine token-level streaming,
    # every other provider falls back to Provider.stream()'s compatible
    # (non-incremental but still tool-call-capable) default.
    enable_streaming: bool = False


class Agent:
    def __init__(
        self,
        *,
        provider: Provider,
        tools: list[Tool] | None = None,
        memory: BackendProtocol | None = None,
        context_engine: ContextEngine | None = None,
        permission_policy: ApprovalPolicy | None = None,
        ask_callback: AskCallback | None = None,
        prompt_builder: SystemPromptBuilder | None = None,
        system_prompt_extra: str | None = None,
        middleware: MiddlewarePipeline | None = None,
        config: AgentConfig | None = None,
        # Deferred/searchable tool schemas (see tools/search.py): pass True
        # to enable with default settings once the catalog crosses the
        # threshold, or a pre-built ToolSearchIndex for custom thresholds.
        tool_search: ToolSearchIndex | bool | None = None,
        # Durable session persistence (see persistence/session_store.py):
        # if both are set, state is saved after every iteration and can be
        # resumed across process restarts via Agent.resume().
        session_store: SQLiteSessionStore | None = None,
        session_id: str | None = None,
        # If set, the skill catalog is re-discovered and its lightweight
        # index re-rendered into the prompt every iteration — cheap since
        # discover() only reads frontmatter, never full skill bodies.
        skill_catalog: SkillCatalog | None = None,
        # UI-agnostic callback hooks so any surface (CLI, daemon, bot) can
        # drive this loop without the loop knowing about presentation.
        on_text_delta: Callable[[str], None] | None = None,
        on_tool_start: Callable[[str, dict], None] | None = None,
        on_tool_end: Callable[[str, ToolResult], None] | None = None,
    ):
        self.provider = provider
        self.config = config or AgentConfig()
        self.registry = ToolRegistry(max_concurrency=self.config.max_tool_concurrency)
        for t in tools or []:
            self.registry.register(t)

        self.memory = memory or StateBackend()
        self.context_engine = context_engine or PassthroughContextEngine()
        self.permission_policy = permission_policy or ApprovalPolicy()
        self.ask_callback = ask_callback

        self.budget = IterationBudget(
            max_iterations=self.config.max_iterations,
            max_tokens=self.config.max_tokens_budget,
            grace_calls=self.config.grace_calls,
        )
        self.middleware = middleware or MiddlewarePipeline()
        self.prompt_builder = prompt_builder or SystemPromptBuilder()
        if system_prompt_extra:
            self.prompt_builder.add_stable_section("Task", system_prompt_extra)
        model_name = getattr(provider, "model", None)
        if model_name:
            guidance = guidance_for_model(model_name)
            if guidance:
                self.prompt_builder.add_stable_section("Model-specific guidance", guidance)

        self.tool_search = self._setup_tool_search(tool_search)
        self.session_store = session_store
        self.session_id = session_id
        self.skill_catalog = skill_catalog

        self.on_text_delta = on_text_delta
        self.on_tool_start = on_tool_start
        self.on_tool_end = on_tool_end

        self.state = AgentState()

    @classmethod
    async def resume(
        cls,
        *,
        session_store: SQLiteSessionStore,
        session_id: str,
        **kwargs,
    ) -> "Agent":
        """Reconstruct an Agent and restore a previously-saved AgentState
        (messages, iteration count, scratch) from durable storage."""
        agent = cls(session_store=session_store, session_id=session_id, **kwargs)
        restored = await session_store.load(session_id)
        if restored is not None:
            agent.state = restored
        return agent

    def _setup_tool_search(
        self, tool_search: ToolSearchIndex | bool | None
    ) -> ToolSearchIndex | None:
        if tool_search is None or tool_search is False:
            return None
        index = tool_search if isinstance(tool_search, ToolSearchIndex) else ToolSearchIndex(
            self.registry
        )

        async def _tool_search_handler(query: str) -> list[dict]:
            return [r.__dict__ for r in index.search(query)]

        async def _tool_resolve_handler(names: list[str]) -> str:
            resolved = index.resolve(names)
            return (
                f"Resolved {len(resolved)} tool schema(s): "
                f"{[t.name for t in resolved]}. They are now callable."
            )

        self.registry.register(
            Tool(
                name="tool_search",
                description=(
                    "Search the full tool catalog by keyword, or "
                    "'select:name1,name2' to fetch specific tools by name. "
                    "Use this before calling a tool you don't see a schema "
                    "for — large catalogs defer most schemas to save tokens."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                handler=_tool_search_handler,
                concurrency_safe=True,
                owner="core",
            )
        )
        self.registry.register(
            Tool(
                name="tool_resolve",
                description="Fetch full schemas for specific deferred tools by name so you can call them.",
                input_schema={
                    "type": "object",
                    "properties": {"names": {"type": "array", "items": {"type": "string"}}},
                    "required": ["names"],
                },
                handler=_tool_resolve_handler,
                concurrency_safe=True,
                owner="core",
            )
        )
        return index

    async def _visible_tools(self) -> list[Tool]:
        available = await self.registry.available_tools()
        if self.tool_search is None:
            return available
        visible_names = {t.name for t in self.tool_search.visible_tools()}
        return [t for t in available if t.name in visible_names]

    def _tool_schemas(self, tools: list[Tool]) -> list[ToolSchema]:
        return [
            ToolSchema(name=t.name, description=t.description, input_schema=t.input_schema)
            for t in tools
        ]

    async def _dispatch_one_tool_call(self, tc: ToolCall) -> Message:
        """Run permission/middleware hooks and dispatch for a single tool
        call. Shared by the streaming path (calls land one at a time as the
        model emits them) — the non-streaming path uses the batch dispatcher
        in _dispatch_tool_calls instead, since it can exploit knowing the
        full set of calls up front for parallel-safe partitioning."""
        if self.on_tool_start:
            self.on_tool_start(tc.name, tc.arguments)

        try:
            args = await self.middleware.run_before_tool_call(
                tool_name=tc.name, arguments=tc.arguments, state=self.state
            )
        except ToolCallBlocked as exc:
            result = ToolResult.failure(f"blocked: {exc.reason}")
            args = tc.arguments
        else:
            tool = self.registry.try_get(tc.name)
            if tool is not None and tool.requires_approval:
                approved = await self.permission_policy.resolve(
                    tc.name, args, self.ask_callback
                )
                result = (
                    await self.registry.dispatch(tc.name, args)
                    if approved
                    else ToolResult.failure("denied by approval policy")
                )
            else:
                result = await self.registry.dispatch(tc.name, args)

        result = await self.middleware.run_after_tool_call(
            tool_name=tc.name, arguments=args, result=result, state=self.state
        )
        if self.on_tool_end:
            self.on_tool_end(tc.name, result)
        return Message(
            role="tool", content=result.to_model_text(), tool_call_id=tc.id, name=tc.name
        )

    async def _call_model_nonstreaming(self, messages: list[Message]) -> CompletionResponse:
        system_prompt = self.prompt_builder.build()
        tool_schemas = self._tool_schemas(await self._visible_tools())

        async def call_fn(msgs: list[Message]) -> CompletionResponse:
            return await self.provider.complete(
                system_prompt=system_prompt, messages=msgs, tools=tool_schemas
            )

        return await self.middleware.run_model_call(
            messages=messages, state=self.state, call_fn=call_fn
        )

    async def _call_model_streaming(
        self, messages: list[Message]
    ) -> tuple[CompletionResponse, list[Message]]:
        system_prompt = self.prompt_builder.build()
        tool_schemas = self._tool_schemas(await self._visible_tools())
        tool_messages: list[Message] = []

        async def call_fn(msgs: list[Message]) -> CompletionResponse:
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            pending_tasks: list[asyncio.Task] = []
            usage = CompletionUsage()

            async for chunk in self.provider.stream(
                system_prompt=system_prompt, messages=msgs, tools=tool_schemas
            ):
                if chunk.delta_text:
                    text_parts.append(chunk.delta_text)
                    if self.on_text_delta:
                        self.on_text_delta(chunk.delta_text)
                if chunk.delta_tool_call is not None:
                    tc = chunk.delta_tool_call
                    tool_calls.append(tc)
                    tool = self.registry.try_get(tc.name)
                    if tool is not None and tool.concurrency_safe:
                        # Kick off now — this is the whole point of wiring
                        # streaming dispatch in: it runs concurrently with
                        # whatever the model streams next, not after.
                        pending_tasks.append(asyncio.create_task(self._dispatch_one_tool_call(tc)))
                    else:
                        # Unsafe (or unrecognized) tools run to completion
                        # inline so they can't race with subsequent stream
                        # content that might depend on side effects.
                        tool_messages.append(await self._dispatch_one_tool_call(tc))
                if chunk.usage is not None:
                    usage = chunk.usage

            if pending_tasks:
                tool_messages.extend(await asyncio.gather(*pending_tasks))

            message = Message(
                role="assistant", content="".join(text_parts) or None, tool_calls=tool_calls
            )
            return CompletionResponse(message=message, usage=usage)

        response = await self.middleware.run_model_call(
            messages=messages, state=self.state, call_fn=call_fn
        )
        return response, tool_messages

    async def _dispatch_tool_calls(self, tool_calls: list[ToolCall]) -> list[Message]:
        n = len(tool_calls)
        resolved_args: list[dict] = [{} for _ in range(n)]
        blocked: dict[int, ToolResult] = {}

        for i, tc in enumerate(tool_calls):
            if self.on_tool_start:
                self.on_tool_start(tc.name, tc.arguments)

            try:
                args = await self.middleware.run_before_tool_call(
                    tool_name=tc.name, arguments=tc.arguments, state=self.state
                )
            except ToolCallBlocked as exc:
                blocked[i] = ToolResult.failure(f"blocked: {exc.reason}")
                resolved_args[i] = tc.arguments
                continue

            tool = self.registry.try_get(tc.name)
            if tool is not None and tool.requires_approval:
                approved = await self.permission_policy.resolve(
                    tc.name, args, self.ask_callback
                )
                if not approved:
                    blocked[i] = ToolResult.failure("denied by approval policy")
                    resolved_args[i] = args
                    continue

            resolved_args[i] = args

        dispatch_indices = [i for i in range(n) if i not in blocked]
        batch = [(tool_calls[i].name, resolved_args[i]) for i in dispatch_indices]
        dispatched = await self.registry.dispatch_batch(batch)

        results: list[ToolResult] = [ToolResult.failure("unreachable")] * n
        for idx, result in zip(dispatch_indices, dispatched):
            results[idx] = result
        for i, result in blocked.items():
            results[i] = result

        messages: list[Message] = []
        for i, tc in enumerate(tool_calls):
            result = await self.middleware.run_after_tool_call(
                tool_name=tc.name,
                arguments=resolved_args[i],
                result=results[i],
                state=self.state,
            )
            if self.on_tool_end:
                self.on_tool_end(tc.name, result)
            messages.append(
                Message(
                    role="tool",
                    content=result.to_model_text(),
                    tool_call_id=tc.id,
                    name=tc.name,
                )
            )
        return messages

    async def run(self) -> AgentState:
        """Run iterations until the agent stops on its own, the budget is
        exhausted, or it's aborted. Returns the final AgentState."""

        while True:
            if self.state.aborted:
                self.state.stop_reason = StopReason.ABORTED
                await self._persist()
                return self.state

            for msg in self.state.steering_queue.drain():
                self.state.append(msg)

            if await self.context_engine.should_compress(self.state):
                await self.context_engine.compress(self.state)

            if self.skill_catalog is not None:
                entries = await self.skill_catalog.discover()
                self.prompt_builder.set_context_section(
                    "Skills", self.skill_catalog.render_index(entries)
                )

            can_continue, is_grace = self.budget.should_continue()
            if not can_continue:
                self.state.stop_reason = StopReason.BUDGET_EXHAUSTED
                await self._persist()
                return self.state
            if is_grace:
                self.state.append(Message(role="system", content=GRACE_NUDGE))

            precomputed_tool_messages: list[Message] | None = None
            if self.config.enable_streaming:
                response, precomputed_tool_messages = await self._call_model_streaming(
                    self.state.messages
                )
            else:
                response = await self._call_model_nonstreaming(self.state.messages)

            self.budget.record(response.usage.input_tokens + response.usage.output_tokens)
            await self.context_engine.update_from_response(self.state, response)

            self.state.append(response.message)
            if not self.config.enable_streaming and self.on_text_delta and response.message.content:
                self.on_text_delta(response.message.content)

            if response.message.tool_calls:
                tool_messages = (
                    precomputed_tool_messages
                    if precomputed_tool_messages is not None
                    else await self._dispatch_tool_calls(response.message.tool_calls)
                )
                for tm in tool_messages:
                    self.state.append(tm)
                self.state.iteration += 1
                await self._persist()
                continue

            if self.state.followup_queue:
                for msg in self.state.followup_queue.drain():
                    self.state.append(msg)
                self.state.iteration += 1
                await self._persist()
                continue

            self.state.stop_reason = StopReason.COMPLETED
            await self._persist()
            return self.state

    async def _persist(self) -> None:
        if self.session_store is not None and self.session_id is not None:
            await self.session_store.save(self.session_id, self.state)
