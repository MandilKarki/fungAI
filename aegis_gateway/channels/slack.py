"""Slack channel adapter: real implementation of Slack's Socket Mode
protocol — opens a WebSocket via `apps.connections.open`, acks each
`events_api` envelope, and replies via the `chat.postMessage` Web API. Socket
Mode is used deliberately (not the Events API webhook) so no public HTTPS
endpoint is required.

Requires a real Slack app with Socket Mode enabled: a bot token
(SLACK_BOT_TOKEN, `xoxb-...`) and an app-level token
(SLACK_APP_TOKEN, `xapp-...`) with the `connections:write` scope. This
environment has neither, so it is not exercised end to end here. See
ROADMAP.md.

`SLACK_BOT_TOKEN=... SLACK_APP_TOKEN=... python -m aegis_gateway.channels.slack`
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Callable

from aegis_core.loop import Agent
from aegis_core.state import Message

AgentFactory = Callable[[], Agent]

API_BASE = "https://slack.com/api"


class SlackChannel:
    def __init__(
        self,
        agent_factory: AgentFactory,
        bot_token: str | None = None,
        app_token: str | None = None,
    ):
        self.agent_factory = agent_factory
        self.bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN")
        self.app_token = app_token or os.environ.get("SLACK_APP_TOKEN")
        if not self.bot_token or not self.app_token:
            raise RuntimeError(
                "SlackChannel requires SLACK_BOT_TOKEN and SLACK_APP_TOKEN "
                "(env vars or constructor args)"
            )
        self._sessions: dict[str, Agent] = {}
        self._bot_user_id: str | None = None

    def _agent_for_channel(self, channel_id: str) -> Agent:
        if channel_id not in self._sessions:
            self._sessions[channel_id] = self.agent_factory()
        return self._sessions[channel_id]

    async def _open_socket_url(self, http) -> str:
        resp = await http.post(
            f"{API_BASE}/apps.connections.open",
            headers={"Authorization": f"Bearer {self.app_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"apps.connections.open failed: {data}")
        return data["url"]

    async def _post_message(self, http, channel: str, text: str) -> None:
        await http.post(
            f"{API_BASE}/chat.postMessage",
            headers={"Authorization": f"Bearer {self.bot_token}"},
            json={"channel": channel, "text": text},
        )

    async def run_forever(self) -> None:
        import httpx
        from websockets.asyncio.client import connect

        async with httpx.AsyncClient() as http:
            auth = await http.post(
                f"{API_BASE}/auth.test", headers={"Authorization": f"Bearer {self.bot_token}"}
            )
            self._bot_user_id = auth.json().get("user_id")

            ws_url = await self._open_socket_url(http)
            async with connect(ws_url) as ws:
                async for raw in ws:
                    envelope = json.loads(raw)
                    if envelope.get("envelope_id"):
                        await ws.send(json.dumps({"envelope_id": envelope["envelope_id"]}))

                    if envelope.get("type") != "events_api":
                        continue
                    event = envelope.get("payload", {}).get("event", {})
                    if event.get("type") != "message" or event.get("bot_id"):
                        continue
                    if event.get("user") == self._bot_user_id:
                        continue

                    channel_id = event.get("channel")
                    text = event.get("text", "")
                    if not channel_id or not text:
                        continue

                    agent = self._agent_for_channel(channel_id)
                    agent.state.append(Message(role="user", content=text))
                    final_state = await agent.run()
                    reply = next(
                        (
                            m.content
                            for m in reversed(final_state.messages)
                            if m.role == "assistant" and m.content
                        ),
                        "(no response)",
                    )
                    await self._post_message(http, channel_id, reply)


if __name__ == "__main__":
    from aegis_core.providers.anthropic_provider import AnthropicProvider

    def _factory() -> Agent:
        return Agent(provider=AnthropicProvider())

    asyncio.run(SlackChannel(_factory).run_forever())
