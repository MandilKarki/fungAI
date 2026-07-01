"""Telegram channel adapter: long-polls the Telegram Bot API and drives one
Agent per chat. Real implementation against the documented Bot API
(`getUpdates` long polling + `sendMessage`) — requires a real
TELEGRAM_BOT_TOKEN to run live, which this environment doesn't have, so it
is not exercised end to end here. See ROADMAP.md.

Get a token from @BotFather, then: `TELEGRAM_BOT_TOKEN=... python -m
aegis_gateway.channels.telegram`.
"""

from __future__ import annotations

import asyncio
import os
from typing import Callable

from aegis_core.loop import Agent
from aegis_core.state import Message

AgentFactory = Callable[[], Agent]


class TelegramChannel:
    def __init__(self, agent_factory: AgentFactory, bot_token: str | None = None):
        self.agent_factory = agent_factory
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not self.bot_token:
            raise RuntimeError(
                "TelegramChannel requires TELEGRAM_BOT_TOKEN (env var or constructor arg)"
            )
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._sessions: dict[int, Agent] = {}

    def _agent_for_chat(self, chat_id: int) -> Agent:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = self.agent_factory()
        return self._sessions[chat_id]

    async def run_forever(self) -> None:
        import httpx

        offset = 0
        async with httpx.AsyncClient(timeout=35.0) as client:
            while True:
                resp = await client.get(
                    f"{self.base_url}/getUpdates", params={"offset": offset, "timeout": 30}
                )
                resp.raise_for_status()
                data = resp.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message")
                    if not message or "text" not in message:
                        continue
                    chat_id = message["chat"]["id"]
                    agent = self._agent_for_chat(chat_id)
                    agent.state.append(Message(role="user", content=message["text"]))
                    final_state = await agent.run()
                    reply = next(
                        (
                            m.content
                            for m in reversed(final_state.messages)
                            if m.role == "assistant" and m.content
                        ),
                        "(no response)",
                    )
                    await client.post(
                        f"{self.base_url}/sendMessage",
                        json={"chat_id": chat_id, "text": reply[:4000]},
                    )


if __name__ == "__main__":
    from aegis_core.providers.anthropic_provider import AnthropicProvider

    def _factory() -> Agent:
        return Agent(provider=AnthropicProvider())

    asyncio.run(TelegramChannel(_factory).run_forever())
