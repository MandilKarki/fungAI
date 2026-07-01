"""Gateway daemon: a single long-lived process that owns Agent sessions and
exposes them to thin WebSocket clients (CLI, channel adapters, admin UI).

Source: openclaw's architecture — one daemon owning the agent runtime, every
surface a thin client speaking a shared protocol, rather than embedding the
loop in each surface. See ARCHITECTURE.md sections 13/15, ROADMAP.md.

Verified end to end in examples/gateway_demo.py (in-process server + a real
WebSocket client round trip, no external services required).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from aegis_core.loop import Agent
from aegis_core.state import Message
from aegis_gateway.protocol import (
    MSG_DONE,
    MSG_ERROR,
    MSG_TEXT_DELTA,
    MSG_TOOL_END,
    MSG_TOOL_START,
    MSG_USER_MESSAGE,
    GatewayMessage,
)

logger = logging.getLogger("aegis_gateway")

AgentFactory = Callable[[], Agent]


class GatewayServer:
    def __init__(self, agent_factory: AgentFactory, host: str = "127.0.0.1", port: int = 8765):
        self.agent_factory = agent_factory
        self.host = host
        self.port = port
        self._sessions: dict[str, Agent] = {}

    def _get_or_create_agent(self, session_id: str) -> Agent:
        if session_id not in self._sessions:
            self._sessions[session_id] = self.agent_factory()
        return self._sessions[session_id]

    async def _handle_connection(self, ws) -> None:
        # All outgoing frames for this connection funnel through one queue
        # drained by a single writer task — callbacks fire from sync
        # contexts (Agent's on_text_delta/on_tool_start/on_tool_end), and
        # without a single writer, concurrent ws.send() calls from those
        # callbacks could interleave or race against connection teardown.
        send_queue: asyncio.Queue = asyncio.Queue()

        async def writer() -> None:
            while True:
                item = await send_queue.get()
                if item is None:
                    return
                await ws.send(item)

        writer_task = asyncio.create_task(writer())
        try:
            async for raw in ws:
                try:
                    msg = GatewayMessage.from_json(raw)
                except Exception as exc:  # noqa: BLE001
                    send_queue.put_nowait(
                        GatewayMessage(MSG_ERROR, "", {"error": str(exc)}).to_json()
                    )
                    continue

                if msg.type != MSG_USER_MESSAGE:
                    continue

                sid = msg.session_id
                agent = self._get_or_create_agent(sid)

                def on_text_delta(text: str, sid: str = sid) -> None:
                    send_queue.put_nowait(GatewayMessage(MSG_TEXT_DELTA, sid, {"text": text}).to_json())

                def on_tool_start(name: str, args: dict, sid: str = sid) -> None:
                    send_queue.put_nowait(
                        GatewayMessage(MSG_TOOL_START, sid, {"name": name, "args": args}).to_json()
                    )

                def on_tool_end(name: str, result, sid: str = sid) -> None:
                    send_queue.put_nowait(
                        GatewayMessage(
                            MSG_TOOL_END, sid, {"name": name, "result": result.to_model_text()}
                        ).to_json()
                    )

                agent.on_text_delta = on_text_delta
                agent.on_tool_start = on_tool_start
                agent.on_tool_end = on_tool_end

                agent.state.append(Message(role="user", content=msg.payload.get("text", "")))
                final_state = await agent.run()
                stop_reason = final_state.stop_reason.value if final_state.stop_reason else None
                await send_queue.put(
                    GatewayMessage(MSG_DONE, sid, {"stop_reason": stop_reason}).to_json()
                )
        finally:
            await send_queue.put(None)
            await writer_task

    async def serve_forever(self) -> None:
        from websockets.asyncio.server import serve

        async with serve(self._handle_connection, self.host, self.port):
            logger.info("aegis gateway listening on ws://%s:%s", self.host, self.port)
            await asyncio.Future()  # run until cancelled
