"""A minimal programmatic gateway client: connect, send one user_message,
collect every event until `done`. Used for scripting against the gateway and
by examples/gateway_demo.py — see channels/cli_channel.py for the
interactive terminal client instead.
"""

from __future__ import annotations

from aegis_gateway.protocol import MSG_DONE, MSG_USER_MESSAGE, GatewayMessage


async def send_and_collect(uri: str, session_id: str, text: str) -> list[GatewayMessage]:
    from websockets.asyncio.client import connect

    events: list[GatewayMessage] = []
    async with connect(uri) as ws:
        await ws.send(GatewayMessage(MSG_USER_MESSAGE, session_id, {"text": text}).to_json())
        async for raw in ws:
            msg = GatewayMessage.from_json(raw)
            events.append(msg)
            if msg.type == MSG_DONE:
                break
    return events
