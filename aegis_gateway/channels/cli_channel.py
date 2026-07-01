"""CLI channel: a thin interactive terminal client for the gateway daemon —
connects over WebSocket, sends typed lines as user_message, prints
text_delta/tool_start/tool_end/done events as they stream back. This is the
one channel that's a complete, real, locally-runnable surface with no
external account/credential needed. See ARCHITECTURE.md section 13.
"""

from __future__ import annotations

import asyncio
import sys

from aegis_gateway.protocol import (
    MSG_DONE,
    MSG_ERROR,
    MSG_TEXT_DELTA,
    MSG_TOOL_END,
    MSG_TOOL_START,
    MSG_USER_MESSAGE,
    GatewayMessage,
)


async def run_cli_channel(uri: str, session_id: str = "cli-session") -> None:
    from websockets.asyncio.client import connect

    async with connect(uri) as ws:

        async def reader() -> None:
            async for raw in ws:
                msg = GatewayMessage.from_json(raw)
                if msg.type == MSG_TEXT_DELTA:
                    print(msg.payload["text"], end="", flush=True)
                elif msg.type == MSG_TOOL_START:
                    print(f"\n[tool] {msg.payload['name']}({msg.payload['args']})", file=sys.stderr)
                elif msg.type == MSG_TOOL_END:
                    print(
                        f"[tool done] {msg.payload['name']} -> {msg.payload['result']}",
                        file=sys.stderr,
                    )
                elif msg.type == MSG_DONE:
                    print()
                elif msg.type == MSG_ERROR:
                    print(f"[error] {msg.payload['error']}", file=sys.stderr)

        reader_task = asyncio.create_task(reader())
        loop = asyncio.get_event_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                await ws.send(
                    GatewayMessage(MSG_USER_MESSAGE, session_id, {"text": line.rstrip()}).to_json()
                )
        finally:
            reader_task.cancel()


if __name__ == "__main__":
    uri = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8765"
    asyncio.run(run_cli_channel(uri))
