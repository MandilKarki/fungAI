"""Discord channel adapter: a minimal real implementation of the Discord
Gateway v10 protocol (Hello/Identify/Heartbeat/Dispatch over a WebSocket)
plus the REST API for sending replies. Real protocol code, not a stub — but
intentionally minimal: no reconnect/RESUME handling, no sharding, only
MESSAGE_CREATE is handled. Requires a real DISCORD_BOT_TOKEN with the
MESSAGE CONTENT privileged intent enabled, which this environment doesn't
have, so it is not exercised end to end here. See ROADMAP.md.

`DISCORD_BOT_TOKEN=... python -m aegis_gateway.channels.discord`
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Callable

from aegis_core.loop import Agent
from aegis_core.state import Message

AgentFactory = Callable[[], Agent]

GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
API_BASE = "https://discord.com/api/v10"

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_HEARTBEAT_ACK = 11
OP_HELLO = 10

# Required to receive message text in MESSAGE_CREATE: GUILDS (1<<0) +
# GUILD_MESSAGES (1<<9) + MESSAGE_CONTENT (1<<15, privileged — must be
# enabled for the bot in the Discord developer portal).
INTENTS = (1 << 0) | (1 << 9) | (1 << 15)


class DiscordChannel:
    def __init__(self, agent_factory: AgentFactory, bot_token: str | None = None):
        self.agent_factory = agent_factory
        self.bot_token = bot_token or os.environ.get("DISCORD_BOT_TOKEN")
        if not self.bot_token:
            raise RuntimeError(
                "DiscordChannel requires DISCORD_BOT_TOKEN (env var or constructor arg)"
            )
        self._sessions: dict[str, Agent] = {}

    def _agent_for_channel(self, channel_id: str) -> Agent:
        if channel_id not in self._sessions:
            self._sessions[channel_id] = self.agent_factory()
        return self._sessions[channel_id]

    async def _send_reply(self, http, channel_id: str, text: str) -> None:
        await http.post(
            f"{API_BASE}/channels/{channel_id}/messages",
            json={"content": text[:2000]},
            headers={"Authorization": f"Bot {self.bot_token}"},
        )

    async def _heartbeat_loop(self, ws, interval_ms: float) -> None:
        while True:
            await asyncio.sleep(interval_ms / 1000)
            await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": None}))

    async def run_forever(self) -> None:
        import httpx
        from websockets.asyncio.client import connect

        async with httpx.AsyncClient() as http, connect(GATEWAY_URL) as ws:
            hello = json.loads(await ws.recv())
            assert hello["op"] == OP_HELLO
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(ws, hello["d"]["heartbeat_interval"])
            )

            await ws.send(
                json.dumps(
                    {
                        "op": OP_IDENTIFY,
                        "d": {
                            "token": self.bot_token,
                            "intents": INTENTS,
                            "properties": {"os": "aegis", "browser": "aegis", "device": "aegis"},
                        },
                    }
                )
            )

            try:
                async for raw in ws:
                    event = json.loads(raw)
                    if event.get("op") == OP_HEARTBEAT_ACK:
                        continue
                    if event.get("op") != OP_DISPATCH:
                        continue
                    if event.get("t") != "MESSAGE_CREATE":
                        continue

                    data = event["d"]
                    if data.get("author", {}).get("bot"):
                        continue
                    channel_id = data["channel_id"]
                    content = data.get("content", "")
                    if not content:
                        continue

                    agent = self._agent_for_channel(channel_id)
                    agent.state.append(Message(role="user", content=content))
                    final_state = await agent.run()
                    reply = next(
                        (
                            m.content
                            for m in reversed(final_state.messages)
                            if m.role == "assistant" and m.content
                        ),
                        "(no response)",
                    )
                    await self._send_reply(http, channel_id, reply)
            finally:
                heartbeat_task.cancel()


if __name__ == "__main__":
    from aegis_core.providers.anthropic_provider import AnthropicProvider

    def _factory() -> Agent:
        return Agent(provider=AnthropicProvider())

    asyncio.run(DiscordChannel(_factory).run_forever())
